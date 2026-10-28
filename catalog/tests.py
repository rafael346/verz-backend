import io
import threading
from http.client import IncompleteRead
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.error import HTTPError, URLError

import pytest
from django.db import connection
from rest_framework.test import APIClient

from accounts.models import Role, User

from . import met

pytestmark = pytest.mark.django_db

PASSWORD = "senha-forte-123"


def obj(object_id, public=True, image=True):
    return {
        "objectID": object_id, "title": f"Obra {object_id}", "artistDisplayName": "Artista",
        "objectDate": "1889", "medium": "Óleo sobre tela", "dimensions": "10 x 10 cm",
        "culture": "", "department": "Pinturas", "isPublicDomain": public,
        "primaryImageSmall": f"https://images.metmuseum.org/{object_id}.jpg" if image else "",
        "objectURL": f"https://www.metmuseum.org/art/collection/search/{object_id}",
    }


@pytest.fixture
def fake_met(monkeypatch):
    """Troca o HTTP por respostas em memória e registra cada chamada."""
    state = {
        "search": {"total": 3, "objectIDs": [1, 2, 3]},
        "objects": {1: obj(1), 2: obj(2, public=False), 3: obj(3, image=False)},
        "fail_ids": set(),
        "down": False,
        "calls": [],
        "object_retries": [],
    }

    def fake_get_json(url, retries=met.RETRIES):
        state["calls"].append(url)
        if state["down"]:
            raise met.MetUnavailable("fora do ar")
        if "/search?" in url:
            return state["search"]
        state["object_retries"].append(retries)
        object_id = int(url.rsplit("/", 1)[1])
        if object_id in state["fail_ids"]:
            raise met.MetUnavailable("falha em uma obra")
        return state["objects"].get(object_id)

    monkeypatch.setattr(met, "_get_json", fake_get_json)
    return state


def client_with(role):
    client = APIClient()
    client.force_authenticate(User.objects.create_user(f"{role}@example.com", PASSWORD, role=role))
    return client


def search(client, **params):
    return client.get("/api/catalog/search/", {"q": "cat", **params})


def test_search_returns_only_public_domain_items_with_image(fake_met):
    response = search(client_with(Role.SELLER))

    assert response.status_code == 200
    assert response.json() == {
        "total": 3,
        "next_offset": None,
        "items": [{
            "external_id": 1, "title": "Obra 1", "artist": "Artista", "date": "1889",
            "medium": "Óleo sobre tela", "dimensions": "10 x 10 cm", "culture": "",
            "department": "Pinturas", "image_url": "https://images.metmuseum.org/1.jpg",
            "source_url": "https://www.metmuseum.org/art/collection/search/1",
        }],
    }


def test_search_points_to_next_page_when_there_is_more(fake_met):
    fake_met["search"] = {"total": 30, "objectIDs": [1]}

    assert search(client_with(Role.SELLER)).json()["next_offset"] == met.PAGE_SIZE


def test_search_without_results(fake_met):
    fake_met["search"] = {"total": 0, "objectIDs": None}

    assert search(client_with(Role.SELLER)).json() == {"total": 0, "items": [], "next_offset": None}


def test_same_search_is_served_from_cache(fake_met):
    client = client_with(Role.SELLER)
    search(client)
    calls = len(fake_met["calls"])

    assert search(client, q="CAT").status_code == 200
    assert len(fake_met["calls"]) == calls


def test_one_failing_object_is_skipped_and_page_is_not_cached(fake_met):
    fake_met["objects"][2] = obj(2)
    fake_met["fail_ids"] = {2}
    client = client_with(Role.SELLER)

    assert [i["external_id"] for i in search(client).json()["items"]] == [1]
    calls = len(fake_met["calls"])
    search(client)
    assert len(fake_met["calls"]) > calls


def test_api_down_returns_503_with_clear_message(fake_met):
    fake_met["down"] = True

    response = search(client_with(Role.SELLER))

    assert response.status_code == 503
    assert "indisponível" in response.json()["detail"]


def test_object_details_inside_a_search_are_not_retried(fake_met):
    search(client_with(Role.SELLER))

    assert fake_met["object_retries"] == [0, 0, 0]


def test_search_endpoint_gone_is_unavailable_not_empty(fake_met):
    fake_met["search"] = None  # 404

    assert search(client_with(Role.SELLER)).status_code == 503


def test_search_with_unexpected_shape_is_unavailable(fake_met):
    fake_met["search"] = {"message": "endpoint aposentado"}

    assert search(client_with(Role.SELLER)).status_code == 503


def test_bad_objects_from_the_museum_are_skipped_not_500(fake_met):
    fake_met["search"] = {"total": 3, "objectIDs": [1, 2, "abc"]}
    no_id = obj(2)
    del no_id["objectID"]
    fake_met["objects"][2] = no_id

    response = search(client_with(Role.SELLER))

    assert response.status_code == 200
    assert [i["external_id"] for i in response.json()["items"]] == [1]


def test_only_sellers_can_search(fake_met):
    assert search(client_with(Role.BIDDER)).status_code == 403
    assert search(APIClient()).status_code == 401


@pytest.mark.parametrize("params", [
    {"q": ""}, {"q": " a "}, {"q": "x" * 101},
    {"offset": "-1"}, {"offset": "abc"}, {"offset": "9989"},
])
def test_search_rejects_invalid_parameters(fake_met, params):
    assert search(client_with(Role.SELLER), **params).status_code == 400
    assert fake_met["calls"] == []


@pytest.fixture
def no_sleep(monkeypatch):
    monkeypatch.setattr(met.time, "sleep", lambda seconds: None)


class BrokenBody(io.BytesIO):
    """Resposta que conecta, mas quebra no meio da leitura do corpo."""

    def __init__(self, error):
        super().__init__()
        self.error = error

    def read(self, *args):
        raise self.error


@pytest.mark.parametrize("error, during_read", [
    (URLError("sem rede"), False), (TimeoutError(), False),
    (HTTPError("u", 503, "Unavailable", None, None), False),
    (HTTPError("u", 429, "Too Many Requests", None, None), False),
    (IncompleteRead(b"{"), True), (ConnectionResetError(), True), (TimeoutError(), True),
])
def test_get_json_retries_temporary_failures(monkeypatch, no_sleep, error, during_read):
    attempts = []

    def fake_urlopen(request, timeout):
        attempts.append(timeout)
        if len(attempts) <= met.RETRIES:
            if during_read:
                return BrokenBody(error)
            raise error
        return io.BytesIO(b'{"ok": true}')

    monkeypatch.setattr(met, "_open", fake_urlopen)

    assert met._get_json("https://example.org") == {"ok": True}
    assert attempts == [met.TIMEOUT] * (met.RETRIES + 1)


def test_get_json_gives_up_after_the_retries(monkeypatch, no_sleep):
    attempts = []

    def fake_urlopen(request, timeout):
        attempts.append(1)
        raise URLError("sem rede")

    monkeypatch.setattr(met, "_open", fake_urlopen)

    with pytest.raises(met.MetUnavailable):
        met._get_json("https://example.org")
    assert len(attempts) == met.RETRIES + 1


@pytest.mark.parametrize("code, expected", [(404, None), (400, met.MetUnavailable)])
def test_get_json_does_not_retry_client_errors(monkeypatch, no_sleep, code, expected):
    attempts = []

    def fake_urlopen(request, timeout):
        attempts.append(1)
        raise HTTPError("u", code, "erro", None, None)

    monkeypatch.setattr(met, "_open", fake_urlopen)

    if expected is None:
        assert met._get_json("https://example.org") is None
    else:
        with pytest.raises(expected):
            met._get_json("https://example.org")
    assert len(attempts) == 1


@pytest.mark.parametrize("body, content_type", [
    (b"\xff\xd8\xff\xe0resto", "image/jpeg"), (b"\x89PNG\r\n\x1a\nresto", "image/png"),
])
def test_fetch_image_returns_bytes_and_type(monkeypatch, body, content_type):
    monkeypatch.setattr(met, "_open", lambda request, timeout: io.BytesIO(body))

    assert met.fetch_image("https://images.metmuseum.org/a.jpg") == (body, content_type)


@pytest.mark.parametrize("url", [
    "http://images.metmuseum.org/a.jpg", "https://evil.example/a.jpg",
    "https://images.metmuseum.org@evil.example/a.jpg", "file:///etc/passwd", "",
])
def test_fetch_image_refuses_other_hosts_without_any_request(monkeypatch, url):
    calls = []
    monkeypatch.setattr(met, "_open", lambda request, timeout: calls.append(request))

    with pytest.raises(met.MetUnavailable):
        met.fetch_image(url)
    assert calls == []


def test_fetch_image_rejects_files_that_are_not_images(monkeypatch):
    monkeypatch.setattr(met, "_open", lambda request, timeout: io.BytesIO(b"<html>nope</html>"))

    with pytest.raises(met.MetUnavailable):
        met.fetch_image("https://images.metmuseum.org/a.jpg")


def test_fetch_image_rejects_files_over_the_limit(monkeypatch):
    monkeypatch.setattr(met, "MAX_IMAGE_BYTES", 10)
    monkeypatch.setattr(met, "_open", lambda request, timeout: io.BytesIO(b"\xff\xd8\xff" + b"0" * 20))

    with pytest.raises(met.MetUnavailable):
        met.fetch_image("https://images.metmuseum.org/a.jpg")


def test_fetch_image_of_missing_file_is_unavailable(monkeypatch, no_sleep):
    def not_found(request, timeout):
        raise HTTPError("u", 404, "Not Found", None, None)

    monkeypatch.setattr(met, "_open", not_found)

    with pytest.raises(met.MetUnavailable):
        met.fetch_image("https://images.metmuseum.org/a.jpg")


def test_redirects_are_refused_and_never_followed(no_sleep):
    hits = []

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            hits.append(self.path)
            self.send_response(302)
            self.send_header("Location", "/interno")
            self.end_headers()

        def log_message(self, *args):
            pass

    server = HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        with pytest.raises(met.MetUnavailable):
            met._fetch(f"http://127.0.0.1:{server.server_port}/inicio")
    finally:
        server.shutdown()
    assert hits == ["/inicio"]


@pytest.mark.parametrize("url", [
    "javascript:alert(1)", "https://evil.example/x", "http://www.metmuseum.org/x",
    "https://www.metmuseum.org.evil.example/x", None, 42,
])
def test_untrusted_museum_links_are_dropped(fake_met, url):
    fake_met["objects"][1] = {**obj(1), "objectURL": url}

    assert search(client_with(Role.SELLER)).json()["items"][0]["source_url"] == ""


@pytest.mark.django_db(transaction=True)  # sem a transação que o pytest põe em volta de cada teste
def test_search_runs_outside_the_request_transaction(fake_met, monkeypatch):
    seen = []
    real = met.search

    def spy(*args, **kwargs):
        seen.append(connection.in_atomic_block)
        return real(*args, **kwargs)

    monkeypatch.setattr(met, "search", spy)
    search(client_with(Role.SELLER))

    # Fora da transação: cada gravação no cache se confirma na hora, sem segurar trava enquanto espera o museu.
    assert seen == [False]
