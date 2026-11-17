import pytest
from django.test import Client, override_settings

from audit.models import log
from core import keystore, tenancy

TOKEN = "t" * 40
AUTH = {"HTTP_AUTHORIZATION": f"Bearer {TOKEN}"}


@pytest.fixture(autouse=True)
def _token():
    with override_settings(PHYSIO_JOB_TOKEN=TOKEN):
        yield


def post(name, extra=None, **kw):
    return Client().post(f"/intern/job/{name}/", **{**AUTH, **(extra or {}), **kw})


def test_job_runs_inside_the_app_process(alpha, beta):
    for t in (alpha, beta):
        with tenancy.tenant_context(t):
            log("a")
    r = post("verify_audit")
    assert r.status_code == 200 and "alpha: OK" in r.content.decode() and "beta: OK" in r.content.decode()


def test_all_known_jobs_run(alpha):
    for name in ("send_reminders", "purge_imports", "verify_audit"):
        assert post(name).status_code == 200, name


def test_disabled_without_strong_token():
    with override_settings(PHYSIO_JOB_TOKEN=""):
        assert post("verify_audit").status_code == 404
    with override_settings(PHYSIO_JOB_TOKEN="kurz"):
        assert post("verify_audit").status_code == 404


def test_rejects_wrong_or_missing_token_and_methods(alpha):
    assert Client().post("/intern/job/verify_audit/").status_code == 403
    assert Client().post("/intern/job/verify_audit/", HTTP_AUTHORIZATION="Bearer " + "x" * 40).status_code == 403
    assert Client().get("/intern/job/verify_audit/", **AUTH).status_code == 405
    assert post("rm_rf").status_code == 404


def test_rejects_requests_coming_through_the_proxy_or_from_outside(alpha):
    assert post("verify_audit", {"HTTP_X_FORWARDED_FOR": "203.0.113.9"}).status_code == 403
    assert post("verify_audit", {"HTTP_X_REAL_IP": "203.0.113.9"}).status_code == 403
    assert post("verify_audit", {"REMOTE_ADDR": "203.0.113.9"}).status_code == 403


def test_job_failure_is_reported_as_500(alpha, monkeypatch):
    from django.core import management

    def boom(*a, **k):
        raise RuntimeError("kaputt")

    monkeypatch.setattr("core.jobs.call_command", boom)
    r = post("verify_audit")
    assert r.status_code == 500 and "kaputt" in r.content.decode()


def test_jobs_are_blocked_while_system_is_locked(tmp_path):
    keystore.reset_for_tests()
    with override_settings(PHYSIO_KEYFILE=str(tmp_path / "k.json")):
        assert post("verify_audit").status_code == 503
