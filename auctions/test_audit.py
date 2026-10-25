from datetime import timedelta
from decimal import Decimal

import pytest
from django.db import DatabaseError, connection, transaction
from django.utils import timezone
from rest_framework.test import APIClient

from .models import Bid, Lot
from .test_bids import bid, new_bidder
from .tests import client_for, live, scheduled, seller  # noqa: F401 (fixture)

pytestmark = pytest.mark.django_db


@pytest.fixture
def ana():
    return new_bidder("ana@example.com")


@pytest.fixture
def bia():
    return new_bidder("bia@example.com")


@pytest.fixture
def disputed(seller, ana, bia):
    """Lote ao vivo com 3 lances: ana 100, bia 110, ana 120."""
    lot = live(seller)
    for user, amount in ((ana, "100.00"), (bia, "110.00"), (ana, "120.00")):
        assert bid(user, lot, amount).status_code == 201
    return lot


def audit(lot):
    return APIClient().get(f"/api/lots/{lot.pk}/audit/").json()


def raw_sql(sql, params=()):
    """Adultera direto no banco, como um atacante com acesso a ele (desliga o trigger)."""
    with connection.cursor() as cursor:
        cursor.execute("SET CONSTRAINTS ALL IMMEDIATE")  # FKs adiadas do Django impedem o ALTER TABLE
        cursor.execute("ALTER TABLE auctions_bid DISABLE TRIGGER auctions_bid_no_update_delete")
        cursor.execute(sql, params)
        cursor.execute("ALTER TABLE auctions_bid ENABLE TRIGGER auctions_bid_no_update_delete")


def test_untouched_history_is_intact_public_and_anchored(disputed, ana):
    report = audit(disputed)

    assert report["intact"] is True
    assert report["problems"] == []
    assert [b["amount"] for b in report["bids"]] == ["100.00", "110.00", "120.00"]
    assert all(b["valid"] for b in report["bids"])
    assert report["head_hash"] == report["bids"][-1]["hash"]
    assert report["bids"][1]["prev_hash"] == report["bids"][0]["hash"]
    assert ana.email not in str(report)
    assert "bidder" not in report["bids"][0]


def test_bid_response_carries_the_hash_shown_in_the_audit(seller, ana):
    lot = live(seller)

    receipt = bid(ana, lot, "100.00").json()["bid"]["hash"]

    assert audit(lot)["head_hash"] == receipt


def test_lot_without_bids_is_intact(seller):
    assert audit(scheduled(seller))["intact"] is True


def test_changed_amount_is_detected(disputed):
    raw_sql("UPDATE auctions_bid SET amount = 1 WHERE lot_id = %s AND sequence = 2", [disputed.pk])

    report = audit(disputed)

    assert report["intact"] is False
    assert [b["valid"] for b in report["bids"]] == [True, False, True]
    assert any("#2" in problem for problem in report["problems"])


def test_detected_tampering_is_logged(disputed, bia, caplog):
    Lot.objects.filter(pk=disputed.pk).update(leader=bia)  # quebra o selo do lote: audita e trava lances
    audit(disputed)
    bid(new_bidder("c@example.com"), disputed, "500.00")

    messages = [r.getMessage() for r in caplog.records if r.name == "verz.security"]
    assert f"auditoria_falhou lote={disputed.pk} problemas=1" in messages
    assert any(m.startswith(f"lance_recusado_integridade lote={disputed.pk}") for m in messages)


def test_changed_bidder_is_detected(disputed, bia):
    raw_sql("UPDATE auctions_bid SET bidder_id = %s WHERE lot_id = %s AND sequence = 1", [bia.pk, disputed.pk])

    assert audit(disputed)["bids"][0]["valid"] is False


def test_removed_middle_bid_is_detected(disputed):
    raw_sql("DELETE FROM auctions_bid WHERE lot_id = %s AND sequence = 2", [disputed.pk])

    report = audit(disputed)

    assert report["intact"] is False
    assert any("removido" in problem for problem in report["problems"])


def test_removed_last_bid_with_lot_rolled_back_is_detected(disputed, bia):
    raw_sql("DELETE FROM auctions_bid WHERE lot_id = %s AND sequence = 3", [disputed.pk])
    Lot.objects.filter(pk=disputed.pk).update(bid_count=2, current_bid=Decimal("110.00"), leader=bia)

    report = audit(disputed)

    assert report["intact"] is False
    assert all(b["valid"] for b in report["bids"])  # o que sobrou é coerente...
    assert any("selo" in problem.lower() for problem in report["problems"])  # ...mas o selo denuncia


def test_inserted_bid_is_detected(disputed, bia):
    raw_sql(
        "INSERT INTO auctions_bid (lot_id, bidder_id, bidder_alias, sequence, amount, created_at, prev_hash, hash)"
        " VALUES (%s, %s, 'Licitante #FALSO', 4, 999, now(), %s, %s)",
        [disputed.pk, bia.pk, "0" * 64, "f" * 64])

    report = audit(disputed)

    assert report["intact"] is False
    assert report["bids"][-1]["valid"] is False


def test_changed_leader_is_detected_and_blocks_new_bids(disputed, bia):
    Lot.objects.filter(pk=disputed.pk).update(leader=bia)  # bia "rouba" a liderança de ana

    assert audit(disputed)["intact"] is False
    response = bid(new_bidder("c@example.com"), disputed, "500.00")
    assert response.status_code == 400
    assert response.json()["code"] == "integrity_error"


def test_changed_end_time_is_detected(disputed):
    Lot.objects.filter(pk=disputed.pk).update(ends_at=timezone.now() + timedelta(days=3))

    assert audit(disputed)["intact"] is False


def test_tampered_lot_cannot_be_cancelled(seller):
    lot = scheduled(seller)
    Lot.objects.filter(pk=lot.pk).update(starting_bid=Decimal("1.00"))

    assert client_for(seller).post(f"/api/lots/{lot.pk}/cancel/").status_code == 409


def test_cancelled_lot_stays_auditable_and_intact(seller):
    lot = scheduled(seller)
    client_for(seller).post(f"/api/lots/{lot.pk}/cancel/")

    report = audit(lot)

    assert (report["intact"], report["lot"]["status"]) == (True, "cancelled")


def test_chain_depends_on_the_secret_key(disputed, settings):
    settings.AUDIT_HMAC_KEY = "outra-chave-que-o-atacante-teria-que-adivinhar-000"

    assert audit(disputed)["intact"] is False


def test_bids_cannot_be_changed_or_deleted_through_the_orm(disputed):
    for action in (lambda: Bid.objects.filter(lot=disputed).update(amount=Decimal("1.00")),
                   lambda: Bid.objects.filter(lot=disputed).delete()):
        with pytest.raises(DatabaseError), transaction.atomic():
            action()


def test_audit_of_unknown_lot_is_404():
    assert APIClient().get("/api/lots/999999/audit/").status_code == 404


def test_forged_non_ascii_hash_is_reported_not_crashing(disputed):
    raw_sql("UPDATE auctions_bid SET hash = %s WHERE lot_id = %s AND sequence = 3", ["é" * 64, disputed.pk])

    response = APIClient().get(f"/api/lots/{disputed.pk}/audit/")

    assert response.status_code == 200
    assert response.json()["intact"] is False
    assert bid(new_bidder("c@example.com"), disputed, "500.00").json()["code"] == "integrity_error"


# --- paginação: a cadeia é verificada inteira, a lista vem em páginas -----------------------

@pytest.fixture
def long_lot(seller, ana, bia):
    """60 lances alternados (100, 110, …, 690)."""
    from .bidding import place_bid

    lot = live(seller)
    for n in range(60):
        place_bid(lot.pk, (ana, bia)[n % 2], Decimal("100.00") + 10 * n)
    return lot


def audit_page(lot, page):
    return APIClient().get(f"/api/lots/{lot.pk}/audit/", {"page": page})


def test_audit_lists_bids_in_pages_of_50(long_lot):
    first, second = audit_page(long_lot, 1).json(), audit_page(long_lot, 2).json()

    assert (first["bid_count"], first["page"], first["pages"]) == (60, 1, 2)
    assert [b["sequence"] for b in first["bids"]] == list(range(1, 51))
    assert [b["sequence"] for b in second["bids"]] == list(range(51, 61))
    assert audit(long_lot)["page"] == 1  # sem ?page: a primeira


def test_verdict_covers_the_whole_chain_on_every_page(long_lot):
    raw_sql("UPDATE auctions_bid SET amount = 1 WHERE lot_id = %s AND sequence = 55", [long_lot.pk])

    first = audit_page(long_lot, 1).json()

    assert first["intact"] is False and any("#55" in p for p in first["problems"])
    assert all(b["valid"] for b in first["bids"])  # o lance alterado está na página 2


def test_audit_page_out_of_range_or_invalid_is_404(long_lot):
    assert audit_page(long_lot, 3).status_code == 404
    assert audit_page(long_lot, "x").status_code == 404


def test_lot_without_bids_has_one_empty_page(seller):
    report = audit(live(seller))
    assert (report["bid_count"], report["page"], report["pages"], report["bids"]) == (0, 1, 1, [])
