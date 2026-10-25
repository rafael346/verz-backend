from datetime import timedelta
from decimal import Decimal

import pytest
from django.db import DatabaseError, IntegrityError, connection, transaction
from django.utils import timezone

from wallet.models import EntryKind, LedgerEntry

from .bidding import committed_amount
from .models import Bid, Cancellation, Lot, Receipt, Status
from .settlement import settle_lot
from .test_audit import audit
from .test_bids import bid, move_end, new_bidder, run_at_the_same_time
from .tests import client_for, live, scheduled, seller  # noqa: F401 (fixture)

pytestmark = pytest.mark.django_db
REASON = "A obra foi retirada pelo dono."


def cancel(seller, lot, reason=None):
    body = {} if reason is None else {"reason": reason}
    return client_for(seller).post(f"/api/lots/{lot.pk}/cancel/", body, format="json")


def sold(seller, ana, amount="120.00"):
    """Lote arrematado e liquidado pela ana."""
    lot = live(seller)
    assert bid(ana, lot, amount).status_code == 201
    move_end(lot, timezone.now() - timedelta(seconds=1))
    assert settle_lot(lot.pk) == "sold"
    lot.refresh_from_db()
    return lot


def balance(user):
    user.wallet.refresh_from_db()
    return user.wallet.balance()


def test_cancel_with_bids_needs_a_reason_up_to_200_characters(seller):
    lot = live(seller)
    bid(new_bidder("ana@example.com"), lot, "100.00")

    assert cancel(seller, lot).status_code == 400
    assert cancel(seller, lot, "   ").status_code == 400
    response = cancel(seller, lot, "x" * 201)
    assert response.status_code == 400 and "200" in response.json()["detail"]
    lot.refresh_from_db()
    assert lot.status == Status.OPEN


def test_cancel_with_bids_releases_the_committed_balance(seller):
    ana = new_bidder("ana@example.com")
    lot = live(seller)
    bid(ana, lot, "100.00")
    assert committed_amount(ana) == Decimal("100.00")

    response = cancel(seller, lot, REASON)

    assert response.status_code == 200
    assert committed_amount(ana) == 0
    assert Cancellation.objects.get(lot=lot).refunded is None
    assert not LedgerEntry.objects.filter(lot=lot).exists()  # nenhum dinheiro tinha saído


def test_cancel_without_bids_needs_no_reason(seller):
    lot = scheduled(seller)

    assert cancel(seller, lot).status_code == 200
    assert Cancellation.objects.get(lot=lot).reason == ""


def test_cancel_ended_lot_waiting_for_settlement_and_worker_skips_it(seller):
    ana = new_bidder("ana@example.com")
    lot = live(seller)
    bid(ana, lot, "100.00")
    move_end(lot, timezone.now() - timedelta(seconds=1))

    assert cancel(seller, lot, REASON).status_code == 200
    assert settle_lot(lot.pk) is None
    assert not LedgerEntry.objects.filter(lot=lot).exists()


def test_cancel_sold_lot_refunds_winner_and_debits_seller(seller):
    ana = new_bidder("ana@example.com")
    lot = sold(seller, ana, "120.00")
    assert (balance(ana), balance(seller)) == (Decimal("880.00"), Decimal("120.00"))

    response = cancel(seller, lot, REASON)

    assert response.status_code == 200
    assert (balance(ana), balance(seller)) == (Decimal("1000.00"), Decimal("0.00"))
    assert Cancellation.objects.get(lot=lot).refunded == Decimal("120.00")
    lot.refresh_from_db()
    assert lot.status == Status.CANCELLED


def test_refund_window_is_seven_days(seller):
    ana = new_bidder("ana@example.com")
    inside, outside = sold(seller, ana, "100.00"), sold(seller, ana, "100.00")
    with connection.cursor() as cursor:  # o comprovante é imutável: só reescrevendo a data por baixo do trigger
        cursor.execute("SET CONSTRAINTS ALL IMMEDIATE")  # FKs adiadas do Django impedem o ALTER TABLE
        cursor.execute("ALTER TABLE auctions_receipt DISABLE TRIGGER auctions_receipt_no_update_delete")
        for lot, age in ((inside, timedelta(days=7, minutes=-1)), (outside, timedelta(days=7, minutes=1))):
            cursor.execute("UPDATE auctions_receipt SET issued_at = %s WHERE lot_id = %s",
                           [timezone.now() - age, lot.pk])
        cursor.execute("ALTER TABLE auctions_receipt ENABLE TRIGGER auctions_receipt_no_update_delete")

    assert cancel(seller, inside, REASON).status_code == 200
    response = cancel(seller, outside, REASON)
    assert response.status_code == 400 and "prazo" in response.json()["detail"]


def test_ended_lot_without_bids_cannot_be_cancelled(seller):
    waiting = live(seller)
    move_end(waiting, timezone.now() - timedelta(seconds=1))
    settled = live(seller)
    move_end(settled, timezone.now() - timedelta(seconds=1))
    assert settle_lot(settled.pk) == "unsold"

    assert cancel(seller, waiting).status_code == 400
    assert cancel(seller, settled, REASON).status_code == 400


def test_already_cancelled_is_rejected(seller):
    lot = scheduled(seller)
    cancel(seller, lot)

    assert cancel(seller, lot).status_code == 400


def test_bid_after_cancellation_is_not_found(seller):
    ana = new_bidder("ana@example.com")
    lot = live(seller)
    bid(ana, lot, "100.00")
    cancel(seller, lot, REASON)

    assert bid(new_bidder("bia@example.com"), lot, "200.00").status_code == 404


def test_refund_is_never_written_twice(seller):
    ana = new_bidder("ana@example.com")
    lot = sold(seller, ana)
    cancel(seller, lot, REASON)

    with pytest.raises(IntegrityError), transaction.atomic():
        LedgerEntry.objects.create(wallet=ana.wallet, amount=Decimal("120.00"), kind=EntryKind.REFUND_CREDIT, lot=lot)


def test_cancellation_record_is_immutable(seller):
    lot = scheduled(seller)
    cancel(seller, lot)

    with pytest.raises(DatabaseError), transaction.atomic():
        Cancellation.objects.filter(lot=lot).update(reason="outro")
    with pytest.raises(DatabaseError), transaction.atomic():
        Cancellation.objects.filter(lot=lot).delete()


def test_tampered_cancellation_shows_in_the_audit(seller):
    ana = new_bidder("ana@example.com")
    lot = sold(seller, ana)
    cancel(seller, lot, REASON)
    assert audit(lot)["intact"] is True

    with connection.cursor() as cursor:
        cursor.execute("SET CONSTRAINTS ALL IMMEDIATE")
        cursor.execute("ALTER TABLE auctions_cancellation DISABLE TRIGGER auctions_cancellation_no_update_delete")
        cursor.execute("UPDATE auctions_cancellation SET refunded = 1 WHERE lot_id = %s", [lot.pk])
        cursor.execute("ALTER TABLE auctions_cancellation ENABLE TRIGGER auctions_cancellation_no_update_delete")

    report = audit(lot)
    assert report["intact"] is False
    assert any("cancelamento" in problem for problem in report["problems"])


@pytest.mark.django_db(transaction=True)
def test_cancel_and_bid_at_the_same_time(seller):
    ana, bia = new_bidder("ana@example.com"), new_bidder("bia@example.com")
    lot = live(seller)
    bid(ana, lot, "100.00")

    responses = run_at_the_same_time(lambda: cancel(seller, lot, REASON), lambda: bid(bia, lot, "110.00"))

    lot.refresh_from_db()
    assert lot.status == Status.CANCELLED and responses[0].status_code == 200
    # Ou o lance entrou antes (e o cancelamento veio depois), ou chegou depois e foi recusado: nunca os dois pela metade.
    assert Bid.objects.filter(lot=lot).count() == (2 if responses[1].status_code == 201 else 1)
    assert committed_amount(bia) == 0 and committed_amount(ana) == 0


def test_cancellation_is_logged(seller, caplog):
    lot = scheduled(seller)
    cancel(seller, lot)

    assert any(r.name == "verz.security" and r.getMessage().startswith(f"lote_cancelado lote={lot.pk}")
               for r in caplog.records)


def test_receipt_survives_the_cancellation(seller):
    ana = new_bidder("ana@example.com")
    lot = sold(seller, ana)
    cancel(seller, lot, REASON)

    assert Receipt.objects.filter(lot=lot).exists()  # imutável: a página passa a mostrar o cancelamento
    assert Lot.objects.get(pk=lot.pk).status == Status.CANCELLED


# --- o que cada tela lê ---------------------------------------------------------------------

def test_seller_panel_shows_the_cancellation_and_the_refund_deadline(seller):
    ana = new_bidder("ana@example.com")
    refundable, cancelled = sold(seller, ana), sold(seller, ana)
    cancel(seller, cancelled, REASON)

    lots = {lot["id"]: lot for lot in client_for(seller).get("/api/lots/mine/").json()["results"]}

    assert lots[refundable.pk]["cancellation"] is None
    deadline = Receipt.objects.get(lot=refundable).issued_at + timedelta(days=7)
    assert lots[refundable.pk]["refundable_until"] == deadline.isoformat().replace("+00:00", "Z")
    assert lots[cancelled.pk]["cancellation"]["reason"] == REASON
    assert lots[cancelled.pk]["cancellation"]["refunded"] == "120.00"
    assert lots[cancelled.pk]["refundable_until"] is None


def test_seller_summary_discounts_refunds(seller):
    ana = new_bidder("ana@example.com")
    sold(seller, ana, "100.00")
    cancel(seller, sold(seller, ana, "120.00"), REASON)

    summary = client_for(seller).get("/api/lots/mine/summary/").json()

    assert (summary["total_received"], summary["sold"]) == ("100.00", 1)


def test_bidders_see_the_cancellation_and_only_the_winner_sees_the_refund(seller):
    ana, bia = new_bidder("ana@example.com"), new_bidder("bia@example.com")
    lot = live(seller)
    bid(bia, lot, "100.00")
    bid(ana, lot, "110.00")
    move_end(lot, timezone.now() - timedelta(seconds=1))
    settle_lot(lot.pk)
    cancel(seller, lot, REASON)

    def mine(user):
        return client_for(user).get("/api/lots/participating/").json()["results"][0]

    assert mine(ana)["my_status"] == mine(bia)["my_status"] == "cancelled"
    assert mine(ana)["cancellation"] == {"reason": REASON, "refunded": "110.00",
                                         "cancelled_at": mine(ana)["cancellation"]["cancelled_at"]}
    assert mine(bia)["cancellation"]["refunded"] is None


def test_receipt_and_audit_show_the_cancellation(seller):
    ana = new_bidder("ana@example.com")
    lot = sold(seller, ana)
    cancel(seller, lot, REASON)
    token = Receipt.objects.get(lot=lot).token

    receipt = client_for().get(f"/api/receipts/{token}/").json()
    report = audit(lot)

    assert receipt["cancellation"]["reason"] == report["cancellation"]["reason"] == REASON
    assert receipt["cancellation"]["refunded"] == "120.00"
    assert receipt["authentic"] is True and receipt["audit"]["intact"] is True


def test_cancelled_event_carries_the_reason(seller):
    from .events import lot_state

    lot = scheduled(seller)
    cancel(seller, lot, REASON)

    assert lot_state(Lot.objects.get(pk=lot.pk))["cancellation"]["reason"] == REASON
