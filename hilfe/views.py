import re
from functools import lru_cache

from django.contrib.auth.decorators import login_required
from django.http import Http404
from django.shortcuts import render
from django.template.loader import render_to_string
from django.utils.html import strip_tags

from .facts import facts
from .topics import BY_SLUG, visible_topics


@lru_cache(maxsize=None)
def _plain(slug: str) -> str:
    """Klartext eines Themas für die Suche (Fakten eingesetzt, Markup entfernt)."""
    html = render_to_string(f"hilfe/topics/{slug}.html", {"f": facts()})
    return re.sub(r"\s+", " ", strip_tags(html)).strip()


def search(query: str, user):
    words = [w for w in re.split(r"\s+", query.casefold().strip()) if len(w) >= 2]
    if not words:
        return []
    hits = []
    for topic in visible_topics(user):
        text = _plain(topic.slug)
        haystack = f"{topic.title} {topic.summary} {text}".casefold()
        if all(w in haystack for w in words):
            first = haystack.find(words[0], len(topic.title) + len(topic.summary) + 2)
            start = max(first - 60, 0) if first >= 0 else 0
            snippet = text[start:start + 160] if first >= 0 else topic.summary
            hits.append((topic, ("… " if start else "") + snippet + " …"))
    return hits


@login_required
def index(request):
    query = request.GET.get("q", "").strip()[:100]
    return render(request, "hilfe/index.html", {
        "topics": visible_topics(request.user), "q": query, "hits": search(query, request.user) if query else None,
    })


@login_required
def topic(request, slug):
    t = BY_SLUG.get(slug)
    if t is None or (t.owner_only and not request.user.is_owner):
        raise Http404  # Eigentümer-Themen sind für andere nicht vorhanden
    return render(request, "hilfe/topic.html", {"topic": t, "topics": visible_topics(request.user), "f": facts()})
