from datetime import timedelta
from decimal import Decimal

import pytest
from django.db import IntegrityError, connection, transaction
from django.test.utils import CaptureQueriesContext
from django.utils import timezone
from rest_framework.test import APIClient

from accounts.models import Role, User
from catalog import met

from . import integrity
from .models import Lot, Status

pytestmark = pytest.mark.django_db

PASSWORD = "senha-forte-123"
JPEG = b"\xff\xd8\xff\xe0imagem-de-teste"
ITEM = {
    "external_id": 436524, "title": "Sunflowers", "artist": "Vincent van Gogh", "date": "1887",
    "medium": "Oil on canvas", "dimensions": "17 x 24 in.", "culture": "",
    "department": "European Paintings", "image_url": "https://images.metmuseum.org/sunflowers.jpg",
    "source_url": "https://www.metmuseum.org/art/collection/search/436524",
}


@pytest.fixture
def museum(monkeypatch):
    """Museu falso: conta as chamadas e pode cair."""
    state = {"item": ITEM, "image": (JPEG, "image/jpeg"), "down": False, "calls": 0}

    def get_object(object_id, retries=met.RETRIES):
        state["calls"] += 1
        if state["down"]:
            raise met.MetUnavailable("fora do ar")
        return state["item"]

    def fetch_image(url):
        state["calls"] += 1
        if state["down"] or state["image"] is None:
            raise met.MetUnavailable("sem imagem")
        return state["image"]

    monkeypatch.setattr(met, "get_object", get_object)
    monkeypatch.setattr(met, "fetch_image", fetch_image)
    return state


def make_user(email, role):
    return User.objects.create_user(email, PASSWORD, role=role)


def client_for(user=None):
    client = APIClient()
    if user:
        client.force_authenticate(user)
    return client


@pytest.fixture
def seller():
    return make_user("leiloeiro@example.com", Role.SELLER)


def times(start_in, duration):
    start = timezone.now() + start_in
    return {"starts_at": start.isoformat(), "ends_at": (start + duration).isoformat()}


def payload(**changes):
    return {"external_id": 436524, "starting_bid": "100.00", "min_increment": "10.00",
            **times(timedelta(minutes=5), timedelta(hours=1)), **changes}


def make_lot(seller, starts_in, lasts=timedelta(hours=1), **fields):
    start = timezone.now() + starts_in
    values = {"external_id": 1, "title": "Obra", "image": JPEG, "image_type": "image/jpeg",
              "starting_bid": Decimal("100.00"), "min_increment": Decimal("10.00"), **fields}
    lot = Lot.objects.create(seller=seller, starts_at=start, ends_at=start + lasts, **values)
    integrity.reseal(lot)  # como a view de criação faz
    lot.save(update_fields=["seal"])
    return lot


def live(seller, **fields):
    return make_lot(seller, timedelta(minutes=-10), **fields)


def scheduled(seller, **fields):
    return make_lot(seller, timedelta(hours=1), **fields)


def ended(seller, **fields):
    return make_lot(seller, timedelta(hours=-2), **fields)


def titles(**params):
    return [lot["title"] for lot in APIClient().get("/api/lots/", params).json()["results"]]


# --- criação -------------------------------------------------------------------------------

def test_seller_creates_lot_with_snapshot_and_local_image(museum, seller):
    response = client_for(seller).post("/api/lots/", payload(), format="json")

    assert response.status_code == 201
    body = response.json()
    assert (body["title"], body["artist"], body["department"]) == ("Sunflowers", "Vincent van Gogh",
                                                                   "European Paintings")
    assert (body["starting_bid"], body["min_increment"]) == ("100.00", "10.00")
    assert (body["phase"], body["status"]) == ("scheduled", "open")
    lot = Lot.objects.get()
    assert (bytes(lot.image), lot.seller, lot.external_id) == (JPEG, seller, 436524)

    image = APIClient().get(body["image_url"])
    assert image.status_code == 200
    assert image["Content-Type"] == "image/jpeg"
    assert image.content == JPEG
    assert "max-age" in image["Cache-Control"]


def test_lot_can_start_right_now(museum, seller):
    response = client_for(seller).post(
        "/api/lots/", payload(**times(timedelta(0), timedelta(minutes=5))), format="json")

    assert response.status_code == 201
    assert response.json()["phase"] == "live"


def test_create_lot_with_museum_down_returns_503_and_saves_nothing(museum, seller):
    museum["down"] = True

    response = client_for(seller).post("/api/lots/", payload(), format="json")

    assert response.status_code == 503
    assert "indisponível" in response.json()["detail"]
    assert Lot.objects.count() == 0


def test_create_lot_when_image_download_fails_returns_503(museum, seller):
    museum["image"] = None

    assert client_for(seller).post("/api/lots/", payload(), format="json").status_code == 503
    assert Lot.objects.count() == 0


def test_create_lot_for_artwork_that_cannot_be_auctioned(museum, seller):
    museum["item"] = None

    response = client_for(seller).post("/api/lots/", payload(), format="json")

    assert response.status_code == 400
    assert "domínio público" in response.json()["external_id"][0]


def test_only_sellers_create_lots(museum):
    assert client_for(make_user("b@example.com", Role.BIDDER)).post(
        "/api/lots/", payload(), format="json").status_code == 403
    assert client_for().post("/api/lots/", payload(), format="json").status_code == 403
    assert museum["calls"] == 0


@pytest.mark.parametrize("changes", [
    lambda: {"starting_bid": "0.99"},
    lambda: {"starting_bid": "1000000.01"},
    lambda: {"min_increment": "0"},
    lambda: {"min_increment": "10.555"},
    lambda: {"external_id": 0},
    lambda: {"external_id": "abc"},
    lambda: {"ends_at": "amanhã"},
    lambda: times(timedelta(minutes=-5), timedelta(hours=1)),  # começa no passado
    lambda: times(timedelta(minutes=5), timedelta(seconds=30)),  # curto demais
    lambda: times(timedelta(minutes=5), timedelta(days=31)),  # longo demais
    lambda: times(timedelta(days=91), timedelta(hours=1)),  # longe demais
])
def test_create_lot_validates_input_before_calling_the_museum(museum, seller, changes):
    response = client_for(seller).post("/api/lots/", payload(**changes()), format="json")

    assert response.status_code == 400
    assert museum["calls"] == 0


def test_times_without_time_zone_are_refused(museum, seller):
    start = (timezone.now() + timedelta(minutes=5)).replace(tzinfo=None)
    naive = {"starts_at": start.isoformat(), "ends_at": (start + timedelta(hours=1)).isoformat()}

    response = client_for(seller).post("/api/lots/", payload(**naive), format="json")

    assert response.status_code == 400
    assert "fuso" in response.json()["starts_at"][0]
    assert museum["calls"] == 0


def test_times_with_explicit_offset_are_converted_correctly(museum, seller):
    brt = timezone.get_fixed_timezone(-180)
    start = (timezone.now() + timedelta(minutes=5)).astimezone(brt)

    response = client_for(seller).post(
        "/api/lots/", payload(starts_at=start.isoformat(), ends_at=(start + timedelta(hours=1)).isoformat()),
        format="json")

    assert response.status_code == 201
    assert abs(Lot.objects.get().starts_at - start) < timedelta(seconds=1)


def test_database_refuses_lot_ending_before_it_starts(seller):
    with pytest.raises(IntegrityError), transaction.atomic():
        make_lot(seller, timedelta(hours=1), lasts=timedelta(0))


# --- vitrine -------------------------------------------------------------------------------

def test_showcase_separates_live_scheduled_and_ended_and_hides_cancelled(seller):
    live(seller, title="Ao vivo")
    scheduled(seller, title="Agendado")
    ended(seller, title="Encerrado")
    live(seller, title="Cancelado ao vivo", status=Status.CANCELLED)
    ended(seller, title="Cancelado encerrado", status=Status.CANCELLED)

    assert titles(phase="live") == ["Ao vivo"]
    assert titles(phase="scheduled") == ["Agendado"]
    assert titles(phase="ended") == ["Encerrado"]
    assert titles() == ["Ao vivo"]  # padrão: ao vivo


def test_live_lots_ending_soonest_come_first(seller):
    make_lot(seller, timedelta(minutes=-10), lasts=timedelta(hours=2), title="Depois")
    make_lot(seller, timedelta(minutes=-10), lasts=timedelta(hours=1), title="Antes")

    assert titles(phase="live") == ["Antes", "Depois"]


def test_showcase_search_matches_title_artist_culture_and_department(seller):
    live(seller, title="Sunflowers", artist="Vincent van Gogh")
    live(seller, title="Quail and Millet", culture="Japan")
    live(seller, title="Elmo", department="Arms and Armor")

    assert titles(q="GOGH") == ["Sunflowers"]
    assert titles(q="japan") == ["Quail and Millet"]
    assert titles(q="armor") == ["Elmo"]
    assert titles(q="nada disso") == []


@pytest.mark.parametrize("params", [{"phase": "cancelled"}, {"phase": "x"}, {"q": "x" * 101}])
def test_showcase_rejects_invalid_filters(params):
    assert APIClient().get("/api/lots/", params).status_code == 400


def test_showcase_is_paginated(seller):
    for i in range(21):
        live(seller, title=f"Lote {i}")

    page = APIClient().get("/api/lots/").json()

    assert (page["count"], len(page["results"])) == (21, 20)
    assert page["next"]


def test_showcase_never_loads_image_bytes(seller):
    live(seller)

    with CaptureQueriesContext(connection) as queries:
        APIClient().get("/api/lots/")

    assert not any('"image"' in query["sql"] for query in queries.captured_queries)


def test_lot_detail_is_public_and_hides_seller_and_cancelled_lots(seller):
    lot = live(seller)
    cancelled = live(seller, status=Status.CANCELLED)

    response = APIClient().get(f"/api/lots/{lot.pk}/")

    assert response.json()["phase"] == "live"
    assert "seller" not in response.json()
    assert seller.email not in response.content.decode()
    assert APIClient().get(f"/api/lots/{cancelled.pk}/").status_code == 404


def test_showcase_detail_and_image_keep_working_with_museum_down(museum, seller):
    museum["down"] = True
    lot = live(seller)

    for url in ("/api/lots/", f"/api/lots/{lot.pk}/", f"/api/lots/{lot.pk}/image/"):
        assert APIClient().get(url).status_code == 200
    assert museum["calls"] == 0


# --- cancelamento e painel -----------------------------------------------------------------

def test_seller_cancels_own_lot_and_it_leaves_the_showcase(seller):
    lot = scheduled(seller)

    response = client_for(seller).post(f"/api/lots/{lot.pk}/cancel/")

    assert response.status_code == 200
    assert response.json()["status"] == "cancelled"
    assert titles(phase="scheduled") == []


def test_live_lot_can_be_cancelled(seller):
    lot = live(seller)

    assert client_for(seller).post(f"/api/lots/{lot.pk}/cancel/").status_code == 200


def test_cannot_cancel_ended_or_already_cancelled_lot(seller):
    finished = ended(seller)
    cancelled = scheduled(seller, status=Status.CANCELLED)
    client = client_for(seller)

    assert client.post(f"/api/lots/{finished.pk}/cancel/").status_code == 400
    assert client.post(f"/api/lots/{cancelled.pk}/cancel/").status_code == 400
    finished.refresh_from_db()
    assert finished.status == Status.OPEN


def test_only_the_owner_can_cancel(seller):
    lot = scheduled(seller)

    assert client_for(make_user("outro@example.com", Role.SELLER)).post(
        f"/api/lots/{lot.pk}/cancel/").status_code == 404
    assert client_for(make_user("b@example.com", Role.BIDDER)).post(
        f"/api/lots/{lot.pk}/cancel/").status_code == 403
    assert client_for().post(f"/api/lots/{lot.pk}/cancel/").status_code == 403
    lot.refresh_from_db()
    assert lot.status == Status.OPEN


def test_seller_panel_lists_only_own_lots_including_cancelled(seller):
    scheduled(seller, title="A")
    live(seller, title="B", status=Status.CANCELLED)
    scheduled(make_user("outro@example.com", Role.SELLER), title="Alheio")

    results = client_for(seller).get("/api/lots/mine/").json()["results"]

    assert [lot["title"] for lot in results] == ["B", "A"]  # mais novo primeiro
    assert [lot["status"] for lot in results] == ["cancelled", "open"]
    assert client_for(make_user("b@example.com", Role.BIDDER)).get("/api/lots/mine/").status_code == 403
