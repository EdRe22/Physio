import io
import json

from django import forms
from django.contrib import messages
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from audit.models import log as audit_log
from patients.views import tenant_required

from .models import ImportJob
from .parsing import ImportFileError, read_table
from .profiles import PROFILES, process, suggest_mapping


class UploadForm(forms.Form):
    profile = forms.ChoiceField(label="Was soll importiert werden?", choices=[(k, v.title) for k, v in PROFILES.items()])
    file = forms.FileField(label="Datei (.xlsx oder .csv, höchstens 5 MB)")


@tenant_required
def import_start(request):
    form = UploadForm(request.POST or None, request.FILES or None)
    if request.method == "POST" and form.is_valid():
        try:
            headers, rows = read_table(form.cleaned_data["file"])
        except ImportFileError as exc:
            form.add_error("file", str(exc))
        else:
            job = ImportJob.objects.create(
                profile=form.cleaned_data["profile"],
                payload=json.dumps({"headers": headers, "rows": rows}, ensure_ascii=False),
                row_count=len(rows),
                created_by=request.user.pk,
            )
            audit_log("import.upload", job)
            return redirect("import_mapping", pk=job.pk)
    return render(request, "importer/start.html", {
        "form": form, "jobs": ImportJob.objects.all()[:10], "profiles": PROFILES.values(),
    })


def _open_job(pk, *statuses):
    job = get_object_or_404(ImportJob, pk=pk)
    return job if job.status in statuses else None


@tenant_required
def import_mapping(request, pk):
    job = _open_job(pk, ImportJob.Status.UPLOADED, ImportJob.Status.CHECKED)
    if job is None:
        return redirect("import_start")
    profile = PROFILES[job.profile]
    headers = json.loads(job.payload)["headers"]
    suggestion = suggest_mapping(profile.fields, headers)
    errors = []
    if request.method == "POST":
        mapping = {}
        for f in profile.fields:
            raw = request.POST.get(f.key, "")
            if raw.isdigit() and int(raw) < len(headers):
                mapping[f.key] = int(raw)
            elif f.required:
                errors.append(f"Bitte eine Spalte für „{f.label}“ wählen.")
        if len(set(mapping.values())) != len(mapping):
            errors.append("Jede Spalte darf nur einem Feld zugeordnet werden.")
        if not errors:
            job.mapping = json.dumps(mapping)
            report = process(job, commit=False)  # Trockenlauf: schreibt nichts
            job.report = report.as_json()
            job.status = ImportJob.Status.CHECKED
            job.save()
            return redirect("import_preview", pk=job.pk)
        suggestion = {k: int(v) for k, v in request.POST.items() if k in {f.key for f in profile.fields} and v.isdigit()}
    rows = [
        {"field": f, "options": [(i, h, suggestion.get(f.key) == i) for i, h in enumerate(headers)]}
        for f in profile.fields
    ]
    return render(request, "importer/mapping.html", {"job": job, "profile": profile, "rows": rows, "errors": errors})


@tenant_required
def import_preview(request, pk):
    job = get_object_or_404(ImportJob, pk=pk)
    return render(request, "importer/preview.html", {"job": job, "report": json.loads(job.report or "{}"),
                                                     "profile": PROFILES[job.profile]})


@tenant_required
@require_POST
def import_run(request, pk):
    job = _open_job(pk, ImportJob.Status.CHECKED)
    if job is None:  # schon ausgeführt, abgebrochen oder nicht geprüft: nie doppelt importieren
        return redirect("import_preview", pk=pk)
    report = process(job, commit=True)
    job.report = report.as_json()
    job.status = ImportJob.Status.DONE
    job.finished_at = timezone.now()
    job.wipe()
    job.save()
    audit_log("import.run", job)
    messages.success(request, f"{report.imported} Datensätze importiert.")
    return redirect("import_preview", pk=job.pk)


@tenant_required
@require_POST
def import_cancel(request, pk):
    job = _open_job(pk, ImportJob.Status.UPLOADED, ImportJob.Status.CHECKED)
    if job is not None:
        job.status = ImportJob.Status.CANCELLED
        job.finished_at = timezone.now()
        job.wipe()
        job.save()
        audit_log("import.cancel", job)
        messages.success(request, "Import abgebrochen, die Daten wurden verworfen.")
    return redirect("import_start")


@tenant_required
def import_template(request, profile):
    if profile not in PROFILES:
        return redirect("import_start")
    from openpyxl import Workbook

    workbook = Workbook()
    sheet = workbook.active
    sheet.append([f.label for f in PROFILES[profile].fields])
    out = io.BytesIO()
    workbook.save(out)
    response = HttpResponse(
        out.getvalue(), content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )
    response["Content-Disposition"] = f'attachment; filename="vorlage_{profile}.xlsx"'
    return response
