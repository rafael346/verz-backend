import threading
from datetime import timedelta
from decimal import Decimal

import pytest
from django.db import connection
from django.utils import timezone
from rest_framework.test import APIClient

from accounts.models import Role
from wallet.models import EntryKind, LedgerEntry

from . import integrity
from .bidding import bidder_alias
from .models import Bid, Lot, Status
from .tests import client_for, ended, live, make_lot, make_user, scheduled, seller  # noqa: F401 (fixture)

pytestmark = pytest.mark.django_db


def deposit(user, amount):
    LedgerEntry.objects.create(wallet=user.wallet, amount=Decimal(amount), kind=EntryKind.DEPOSIT)


def new_bidder(email, balance="1000.00"):
    user = make_user(email, Role.BIDDER)
    deposit(user, balance)
    return user


@pytest.fixture
def ana():
    return new_bidder("ana@example.com")


@pytest.fixture
def bia():
    return new_bidder("bia@example.com")


def move_end(lot, when):
    """Simula o relógio andando (não um atacante): muda o fim e re-sela o lote."""
    lot.refresh_from_db()
    lot.ends_at = when
    integrity.reseal(lot)
    lot.save(update_fields=["ends_at", "seal"])


def bid(user, lot, amount):
    return client_for(user).post(f"/api/lots/{lot.pk}/bids/", {"amount": amount}, format="json")


def wallet(user):
    return client_for(user).get("/api/wallet/").json()


def assert_rejected(response, code):
    assert response.status_code == 400
    assert response.json()["code"] == code
    assert response.json()["detail"]


# --- regras ---------------------------------------------------------------------------------

def test_first_bid_at_starting_bid_is_accepted_and_updates_the_lot(seller, ana):
    lot = live(seller)  # inicial 100, incremento 10

    response = bid(ana, lot, "100.00")

    assert response.status_code == 201
    body = response.json()
    assert (body["lot"]["current_bid"], body["lot"]["min_next_bid"], body["lot"]["bid_count"]) == (
        "100.00", "110.00", 1)
    assert body["bid"]["is_you"] is True
    assert body["bid"]["bidder_alias"].startswith("Licitante #")


def test_bids_above_the_minimum_are_accepted(seller, ana, bia):
    lot = live(seller)

    assert bid(ana, lot, "150.00").status_code == 201
    assert_rejected(bid(bia, lot, "159.99"), "below_minimum")
    assert bid(bia, lot, "160.00").status_code == 201


def test_first_bid_below_starting_bid_is_rejected_with_the_minimum(seller, ana):
    response = bid(ana, live(seller), "99.99")

    assert_rejected(response, "below_minimum")
    assert "100.00" in response.json()["detail"]


def test_bid_before_start_is_rejected(seller, ana):
    assert_rejected(bid(ana, scheduled(seller), "100.00"), "not_started")


def test_bid_after_end_is_rejected(seller, ana):
    assert_rejected(bid(ana, ended(seller), "100.00"), "ended")


def test_leader_cannot_cover_own_bid(seller, ana):
    lot = live(seller)
    bid(ana, lot, "100.00")

    assert_rejected(bid(ana, lot, "200.00"), "already_winning")


def test_bid_over_available_balance_is_rejected(seller, ana):
    assert_rejected(bid(ana, live(seller), "1000.01"), "insufficient_balance")


def test_sellers_and_anonymous_cannot_bid(seller):
    lot = live(seller)

    assert bid(seller, lot, "100.00").status_code == 403
    assert bid(make_user("outro@example.com", Role.SELLER), lot, "100.00").status_code == 403
    assert APIClient().post(f"/api/lots/{lot.pk}/bids/", {"amount": "100.00"}, format="json").status_code == 403


def test_bid_on_cancelled_lot_is_not_found(seller, ana):
    assert bid(ana, live(seller, status=Status.CANCELLED), "100.00").status_code == 404


@pytest.mark.parametrize("amount", ["0", "-1", "abc", "100.001", None, "NaN"])
def test_invalid_amounts_are_rejected(seller, ana, amount):
    response = bid(ana, live(seller), amount)

    assert response.status_code == 400
    assert "code" not in response.json()
    assert Bid.objects.count() == 0


def test_rejected_bid_changes_nothing(seller, ana):
    lot = live(seller)
    bid(ana, lot, "99.00")

    lot.refresh_from_db()
    assert (lot.current_bid, lot.leader, lot.bid_count, Bid.objects.count()) == (None, None, 0, 0)


# --- saldo comprometido ---------------------------------------------------------------------

def test_leading_commits_balance_and_being_outbid_releases_it_immediately(seller, ana, bia):
    lot = live(seller)

    bid(ana, lot, "300.00")
    assert wallet(ana) == {"balance": "1000.00", "committed": "300.00", "available": "700.00"}

    bid(bia, lot, "310.00")
    assert wallet(ana) == {"balance": "1000.00", "committed": "0.00", "available": "1000.00"}
    assert wallet(bia)["committed"] == "310.00"


def test_bid_must_fit_in_available_balance_not_total(seller, ana):
    first, second = live(seller), live(seller)
    bid(ana, first, "800.00")

    assert_rejected(bid(ana, second, "300.00"), "insufficient_balance")
    assert bid(ana, second, "200.00").status_code == 201


# --- anti-sniping ---------------------------------------------------------------------------

def test_bid_in_last_two_minutes_extends_end_by_two_minutes(seller, ana):
    lot = make_lot(seller, timedelta(minutes=-10), lasts=timedelta(minutes=11))  # termina em 1 min
    original_end = lot.ends_at

    bid(ana, lot, "100.00")

    lot.refresh_from_db()
    assert lot.ends_at == original_end + timedelta(minutes=2)


def test_every_late_bid_extends_again(seller, ana, bia):
    lot = make_lot(seller, timedelta(minutes=-10), lasts=timedelta(minutes=11))
    bid(ana, lot, "100.00")  # fim passa para ~3 min à frente
    almost_over = timezone.now() + timedelta(seconds=30)
    move_end(lot, almost_over)  # o tempo passou: faltam 30 s de novo

    bid(bia, lot, "110.00")

    lot.refresh_from_db()
    assert lot.ends_at == almost_over + timedelta(minutes=2)


def test_late_bid_that_leaves_more_than_two_minutes_does_not_extend_again(seller, ana, bia):
    lot = make_lot(seller, timedelta(minutes=-10), lasts=timedelta(minutes=11))
    bid(ana, lot, "100.00")  # fim passa para ~3 min à frente
    lot.refresh_from_db()
    after_first = lot.ends_at

    bid(bia, lot, "110.00")  # ainda faltam ~3 min: fora da janela

    lot.refresh_from_db()
    assert lot.ends_at == after_first


def test_bid_before_last_two_minutes_does_not_extend(seller, ana):
    lot = make_lot(seller, timedelta(minutes=-10), lasts=timedelta(minutes=20))
    original_end = lot.ends_at

    bid(ana, lot, "100.00")

    lot.refresh_from_db()
    assert lot.ends_at == original_end


# --- cancelamento, histórico e situação -----------------------------------------------------

def test_lot_with_bids_cannot_be_cancelled(seller, ana):
    lot = live(seller)
    bid(ana, lot, "100.00")

    response = client_for(seller).post(f"/api/lots/{lot.pk}/cancel/")

    assert response.status_code == 400
    assert "lances" in response.json()["detail"]
    lot.refresh_from_db()
    assert lot.status == Status.OPEN


def test_history_is_public_newest_first_and_uses_only_pseudonyms(seller, ana, bia):
    lot = live(seller)
    bid(ana, lot, "100.00")
    bid(bia, lot, "110.00")
    bid(ana, lot, "120.00")

    response = APIClient().get(f"/api/lots/{lot.pk}/bids/")
    bids = response.json()["results"]

    assert [b["amount"] for b in bids] == ["120.00", "110.00", "100.00"]
    assert bids[0]["bidder_alias"] == bids[2]["bidder_alias"] != bids[1]["bidder_alias"]
    assert all(b["is_you"] is False for b in bids)
    assert ana.email not in response.content.decode()
    assert "bidder" not in bids[0]


def test_history_marks_my_own_bids(seller, ana, bia):
    lot = live(seller)
    bid(ana, lot, "100.00")
    bid(bia, lot, "110.00")

    bids = client_for(ana).get(f"/api/lots/{lot.pk}/bids/").json()["results"]

    assert [b["is_you"] for b in bids] == [False, True]


def test_same_bidder_has_a_different_pseudonym_in_each_lot(seller, ana):
    first, second = live(seller), live(seller)

    alias_one = bid(ana, first, "100.00").json()["bid"]["bidder_alias"]
    alias_two = bid(ana, second, "100.00").json()["bid"]["bidder_alias"]

    assert alias_one != alias_two


def test_detail_tells_each_bidder_if_winning_or_outbid_and_position(seller, ana, bia):
    lot = live(seller)
    bid(ana, lot, "100.00")
    bid(bia, lot, "110.00")
    url = f"/api/lots/{lot.pk}/"

    carla = new_bidder("c@example.com")
    assert client_for(ana).get(url).json()["me"] == {
        "status": "outbid", "position": 2, "bidders": 2, "alias": bidder_alias(lot.pk, ana.pk)}
    assert client_for(bia).get(url).json()["me"] == {
        "status": "winning", "position": 1, "bidders": 2, "alias": bidder_alias(lot.pk, bia.pk)}
    assert client_for(carla).get(url).json()["me"] == {
        "status": "none", "position": None, "bidders": 2, "alias": bidder_alias(lot.pk, carla.pk)}
    assert APIClient().get(url).json()["me"] is None


def test_participating_lists_my_lots_with_my_status(seller, ana, bia):
    winning, outbid, won = live(seller), live(seller), live(seller)
    live(seller, title="Sem lance meu")
    bid(ana, winning, "100.00")
    bid(ana, outbid, "100.00")
    bid(bia, outbid, "110.00")
    bid(ana, won, "100.00")
    Lot.objects.filter(pk=won.pk).update(ends_at=timezone.now() - timedelta(minutes=1))

    results = client_for(ana).get("/api/lots/participating/").json()["results"]

    assert {lot["id"]: lot["my_status"] for lot in results} == {
        winning.pk: "winning", outbid.pk: "outbid", won.pk: "won"}
    assert client_for(seller).get("/api/lots/participating/").status_code == 403


# --- concorrência ---------------------------------------------------------------------------

def run_at_the_same_time(*calls):
    barrier = threading.Barrier(len(calls))
    results = [None] * len(calls)

    def worker(index, call):
        barrier.wait()
        try:
            results[index] = call()
        finally:
            connection.close()

    threads = [threading.Thread(target=worker, args=(i, call)) for i, call in enumerate(calls)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    return results


@pytest.mark.django_db(transaction=True)
def test_two_simultaneous_equal_bids_only_one_is_accepted(seller):
    lot = live(seller)
    ana, bia = new_bidder("ana@example.com"), new_bidder("bia@example.com")

    responses = run_at_the_same_time(lambda: bid(ana, lot, "100.00"), lambda: bid(bia, lot, "100.00"))

    assert sorted(r.status_code for r in responses) == [201, 400]
    lot.refresh_from_db()
    assert (lot.bid_count, Bid.objects.count()) == (1, 1)


@pytest.mark.django_db(transaction=True)
def test_same_bidder_cannot_spend_the_same_balance_twice_at_once(seller):
    first, second = live(seller), live(seller)
    ana = new_bidder("ana@example.com", balance="150.00")

    responses = run_at_the_same_time(lambda: bid(ana, first, "100.00"), lambda: bid(ana, second, "100.00"))

    codes = sorted(r.status_code for r in responses)
    assert codes == [201, 400]
    rejected = next(r for r in responses if r.status_code == 400)
    assert rejected.json()["code"] == "insufficient_balance"
    assert Bid.objects.count() == 1
