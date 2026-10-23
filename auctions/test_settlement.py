import io
from datetime import timedelta
from decimal import Decimal

import pytest
from django.core.management import call_command
from django.db import DatabaseError, IntegrityError, connection, transaction
from django.utils import timezone
from rest_framework.test import APIClient

from accounts.models import Role
from wallet.models import EntryKind, LedgerEntry

from .models import Lot, Receipt, Status
from . import settlement
from .settlement import settle_due_lots, settle_lot
from .test_bids import bid, move_end, new_bidder, run_at_the_same_time, wallet
from .tests import client_for, live, make_user, seller  # noqa: F401 (fixture)

pytestmark = pytest.mark.django_db


@pytest.fixture
def ana():
    return new_bidder("ana@example.com")


@pytest.fixture
def bia():
    return new_bidder("bia@example.com")


def end_now(lot):
    move_end(lot, timezone.now() - timedelta(seconds=1))


@pytest.fixture
def sold_lot(seller, ana, bia):
    """Lote vencido: ana 100, bia 110, ana 120 — ana ganha por 120."""
    lot = live(seller)
    for user, amount in ((ana, "100.00"), (bia, "110.00"), (ana, "120.00")):
        assert bid(user, lot, amount).status_code == 201
    end_now(lot)
    return lot


def audit(lot):
    return APIClient().get(f"/api/lots/{lot.pk}/audit/").json()


# --- liquidação -----------------------------------------------------------------------------

def test_sold_lot_debits_winner_credits_seller_and_issues_receipt(sold_lot, seller, ana, bia):
    assert settle_due_lots() == [(sold_lot.pk, "sold")]

    assert wallet(ana) == {"balance": "880.00", "committed": "0.00", "available": "880.00"}
    assert wallet(bia) == {"balance": "1000.00", "committed": "0.00", "available": "1000.00"}
    assert client_for(seller).get("/api/wallet/").json()["balance"] == "120.00"
    sold_lot.refresh_from_db()
    assert sold_lot.status == Status.SETTLED
    receipt = Receipt.objects.get()
    assert (receipt.lot, receipt.winner, receipt.amount) == (sold_lot, ana, Decimal("120.00"))
    assert audit(sold_lot)["intact"] is True


def test_settlement_happens_only_once(sold_lot):
    settle_due_lots()

    assert settle_due_lots() == []
    assert settle_lot(sold_lot.pk) is None
    assert LedgerEntry.objects.filter(lot=sold_lot).count() == 2
    assert Receipt.objects.count() == 1


def test_database_refuses_a_second_settlement_entry(sold_lot, ana):
    settle_due_lots()

    with pytest.raises(IntegrityError), transaction.atomic():
        LedgerEntry.objects.create(wallet=ana.wallet, amount=Decimal("-120.00"),
                                   kind=EntryKind.SETTLEMENT_DEBIT, lot=sold_lot)


def test_lot_without_bids_closes_without_winner(seller):
    lot = live(seller)
    end_now(lot)

    assert settle_due_lots() == [(lot.pk, "unsold")]

    lot.refresh_from_db()
    assert lot.status == Status.SETTLED
    assert (Receipt.objects.count(), LedgerEntry.objects.count()) == (0, 0)
    assert audit(lot)["intact"] is True


def test_lots_still_running_are_not_touched(seller, ana):
    lot = live(seller)
    bid(ana, lot, "100.00")

    assert settle_due_lots() == []
    lot.refresh_from_db()
    assert lot.status == Status.OPEN


def test_tampered_lot_is_not_settled_and_no_money_moves(sold_lot, bia):
    Lot.objects.filter(pk=sold_lot.pk).update(leader=bia)  # bia "rouba" a liderança

    assert settle_due_lots() == [(sold_lot.pk, "blocked")]
    assert LedgerEntry.objects.filter(lot=sold_lot).count() == 0
    assert Receipt.objects.count() == 0
    assert settle_due_lots(exclude={sold_lot.pk}) == []


def test_a_failing_lot_does_not_block_the_others(seller, ana, monkeypatch):
    broken, healthy = live(seller), live(seller)
    bid(ana, healthy, "100.00")
    end_now(broken)
    end_now(healthy)
    real_settle = settlement.settle_lot

    def settle_or_explode(lot_id):
        if lot_id == broken.pk:
            raise RuntimeError("erro inesperado")
        return real_settle(lot_id)

    monkeypatch.setattr(settlement, "settle_lot", settle_or_explode)

    assert settlement.settle_due_lots() == [(broken.pk, "failed"), (healthy.pk, "sold")]
    broken.refresh_from_db()
    assert broken.status == Status.OPEN  # volta na próxima rodada


def test_worker_command_settles_due_lots(sold_lot):
    out = io.StringIO()

    call_command("run_settlement", "--once", stdout=out)

    sold_lot.refresh_from_db()
    assert sold_lot.status == Status.SETTLED
    assert f"lote {sold_lot.pk}: sold" in out.getvalue()


@pytest.mark.django_db(transaction=True)
def test_two_workers_at_once_settle_a_lot_only_once(seller):
    lot = live(seller)
    ana = new_bidder("ana@example.com")
    bid(ana, lot, "100.00")
    end_now(lot)

    results = run_at_the_same_time(lambda: settle_lot(lot.pk), lambda: settle_lot(lot.pk))

    assert sorted(results, key=str) == [None, "sold"]
    assert LedgerEntry.objects.filter(lot=lot).count() == 2
    assert Receipt.objects.count() == 1


# --- comprovante ----------------------------------------------------------------------------

def test_receipt_is_public_authentic_and_anchored_to_the_audit(sold_lot, ana):
    settle_due_lots()
    receipt = Receipt.objects.get()

    response = APIClient().get(f"/api/receipts/{receipt.token}/")

    body = response.json()
    assert response.status_code == 200
    assert body["authentic"] is True
    assert body["audit"] == {"intact": True, "head_matches": True, "url": f"/api/lots/{sold_lot.pk}/audit/"}
    assert (body["amount"], body["winner_alias"], body["lot"]["title"]) == ("120.00", receipt.winner_alias, "Obra")
    assert body["head_hash"] == audit(sold_lot)["head_hash"]
    assert ana.email not in response.content.decode()


def test_receipt_token_is_long_and_random(sold_lot):
    settle_due_lots()

    assert len(Receipt.objects.get().token) >= 24


def test_unknown_receipt_is_404():
    assert APIClient().get("/api/receipts/nao-existe/").status_code == 404


def test_forged_receipt_is_not_authentic(sold_lot):
    settle_due_lots()
    receipt = Receipt.objects.get()
    with connection.cursor() as cursor:
        cursor.execute("SET CONSTRAINTS ALL IMMEDIATE")
        cursor.execute("ALTER TABLE auctions_receipt DISABLE TRIGGER auctions_receipt_no_update_delete")
        cursor.execute("UPDATE auctions_receipt SET amount = 1 WHERE id = %s", [receipt.pk])
        cursor.execute("ALTER TABLE auctions_receipt ENABLE TRIGGER auctions_receipt_no_update_delete")

    assert APIClient().get(f"/api/receipts/{receipt.token}/").json()["authentic"] is False


def test_receipts_cannot_be_changed_through_the_orm(sold_lot):
    settle_due_lots()

    with pytest.raises(DatabaseError), transaction.atomic():
        Receipt.objects.update(amount=Decimal("1.00"))


# --- painéis --------------------------------------------------------------------------------

def test_winner_sees_won_lot_with_receipt_and_loser_does_not(sold_lot, ana, bia):
    settle_due_lots()

    mine = client_for(ana).get("/api/lots/participating/").json()["results"][0]
    theirs = client_for(bia).get("/api/lots/participating/").json()["results"][0]

    assert (mine["my_status"], mine["receipt_token"]) == ("won", Receipt.objects.get().token)
    assert (theirs["my_status"], theirs["receipt_token"]) == ("lost", None)


def test_seller_panel_shows_each_result_and_total_received(seller, ana):
    first, second, unsold = live(seller), live(seller), live(seller)
    bid(ana, first, "100.00")
    bid(ana, second, "200.00")
    running = live(seller)
    for lot in (first, second, unsold):
        end_now(lot)
    settle_due_lots()
    client = client_for(seller)

    assert client.get("/api/lots/mine/summary/").json() == {"total_received": "300.00", "sold": 2, "unsold": 1}
    results = {lot["id"]: lot["result"] for lot in client.get("/api/lots/mine/").json()["results"]}
    assert results == {first.pk: {"sold": True, "amount": "100.00"}, second.pk: {"sold": True, "amount": "200.00"},
                       unsold.pk: {"sold": False, "amount": None}, running.pk: None}
    assert client_for(make_user("b@example.com", Role.BIDDER)).get("/api/lots/mine/summary/").status_code == 403
