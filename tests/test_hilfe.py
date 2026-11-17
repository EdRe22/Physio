import pytest

from hilfe.facts import facts
from hilfe.topics import TOPICS, topic_for_path
from tests.conftest import verified_client


@pytest.fixture
def owner_client(user_alpha):
    user_alpha.role = "owner"
    user_alpha.save()
    return verified_client(user_alpha)


def test_every_topic_renders_for_owner_without_leftover_markup(owner_client):
    for t in TOPICS:
        r = owner_client.get(f"/hilfe/{t.slug}/")
        assert r.status_code == 200, t.slug
        html = r.content.decode()
        assert "{{" not in html and "{%" not in html and "None" not in html.split("<article")[1], t.slug


def test_owner_topics_hidden_from_colleagues(client_beta):
    for t in TOPICS:
        expected = 404 if t.owner_only else 200
        assert client_beta.get(f"/hilfe/{t.slug}/").status_code == expected, t.slug
    page = client_beta.get("/hilfe/").content.decode()
    assert "Ausscheiden und Praxisende" not in page and "Erste Schritte" in page


def test_search(client_beta, owner_client):
    page = client_beta.get("/hilfe/", {"q": "Mahnstufe"}).content.decode()
    assert "Honorarnoten" in page and "Treffer" in page
    assert "Nichts gefunden" in client_beta.get("/hilfe/", {"q": "xyzzyqq"}).content.decode()
    # Eigentümer-Inhalt wird Kollegen auch nicht über die Suche verraten
    assert "Treffer" in client_beta.get("/hilfe/", {"q": "Aliquotierung"}).content.decode() and "0 Treffer" in client_beta.get("/hilfe/", {"q": "Aliquotierung"}).content.decode()
    assert "Ausscheiden" not in client_beta.get("/hilfe/", {"q": "purge_tenant"}).content.decode()
    assert "Ausscheiden" in owner_client.get("/hilfe/", {"q": "purge_tenant"}).content.decode()
    assert client_beta.get("/hilfe/", {"q": "<script>"}).status_code == 200


def test_help_requires_login(client):
    assert client.get("/hilfe/").status_code == 302


def test_context_link_points_to_matching_topic(client_beta):
    assert topic_for_path("/raeume/frei/") == "raeume" and topic_for_path("/buchhaltung/bank/") == "bankabgleich"
    assert 'href="/hilfe/raeume/"' in client_beta.get("/raeume/").content.decode()
    assert 'href="/hilfe/"' in client_beta.get("/hilfe/").content.decode()


def test_help_numbers_come_from_code():
    from raeume import services

    f = facts()
    assert f["room_max_hours"] == services.MAX_HOURS and f["series_max"] == services.MAX_SERIES
