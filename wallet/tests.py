from decimal import Decimal

import pytest
from django.db import DatabaseError, IntegrityError, transaction
from rest_framework.test import APIClient

from accounts.models import Role, User

from .models import EntryKind, LedgerEntry

pytestmark = pytest.mark.django_db

PASSWORD = "senha-forte-123"


def make_user(email="ana@example.com", role=Role.BIDDER):
    return User.objects.create_user(email, PASSWORD, role=role)


def client_for(user):
    client = APIClient()
    client.force_authenticate(user)
    return client


@pytest.mark.parametrize("role", [Role.BIDDER, Role.SELLER])
def test_every_new_user_gets_a_wallet_with_zero_balance(role):
    user = make_user(role=role)

    assert user.wallet.balance() == Decimal("0.00")
    assert client_for(user).get("/api/wallet/").json() == {"balance": "0.00"}


def test_deposits_add_up_in_the_balance():
    client = client_for(make_user())

    first = client.post("/api/wallet/deposit/", {"amount": "10.50"}, format="json")
    second = client.post("/api/wallet/deposit/", {"amount": "5.25"}, format="json")

    assert first.status_code == second.status_code == 201
    assert second.json() == {"balance": "15.75"}
    assert client.get("/api/wallet/").json() == {"balance": "15.75"}
    assert LedgerEntry.objects.filter(kind=EntryKind.DEPOSIT).count() == 2


@pytest.mark.parametrize("amount", ["0", "0.99", "-5", "0.001", "10.555", "100000.01",
                                    "NaN", "Infinity", "abc", "", None])
def test_deposit_rejects_invalid_amounts(amount):
    client = client_for(make_user())

    assert client.post("/api/wallet/deposit/", {"amount": amount}, format="json").status_code == 400
    assert LedgerEntry.objects.count() == 0


def test_deposit_without_amount_is_rejected():
    assert client_for(make_user()).post("/api/wallet/deposit/", {}, format="json").status_code == 400


def test_deposit_accepts_the_limits():
    client = client_for(make_user())

    assert client.post("/api/wallet/deposit/", {"amount": "1.00"}, format="json").status_code == 201
    assert client.post("/api/wallet/deposit/", {"amount": "100000.00"}, format="json").status_code == 201


def test_seller_cannot_deposit():
    client = client_for(make_user("s@example.com", Role.SELLER))

    assert client.post("/api/wallet/deposit/", {"amount": "10.00"}, format="json").status_code == 403


def test_anonymous_cannot_read_or_deposit():
    client = APIClient()

    assert client.get("/api/wallet/").status_code == 403
    assert client.post("/api/wallet/deposit/", {"amount": "10.00"}, format="json").status_code == 403


def test_users_only_see_their_own_balance():
    ana, bia = make_user("ana@example.com"), make_user("bia@example.com")
    client_for(ana).post("/api/wallet/deposit/", {"amount": "50.00"}, format="json")

    assert client_for(bia).get("/api/wallet/").json() == {"balance": "0.00"}


def test_ledger_entries_cannot_be_changed_or_deleted_even_with_raw_orm():
    entry = LedgerEntry.objects.create(wallet=make_user().wallet, amount=Decimal("5.00"),
                                       kind=EntryKind.DEPOSIT)
    actions = (
        lambda: LedgerEntry.objects.filter(pk=entry.pk).update(amount=Decimal("999.00")),
        lambda: LedgerEntry.objects.filter(pk=entry.pk).delete(),
    )

    for action in actions:
        with pytest.raises(DatabaseError), transaction.atomic():
            action()
    entry.refresh_from_db()
    assert entry.amount == Decimal("5.00")


def test_ledger_rejects_zero_amount():
    with pytest.raises(IntegrityError), transaction.atomic():
        LedgerEntry.objects.create(wallet=make_user().wallet, amount=Decimal("0.00"),
                                   kind=EntryKind.DEPOSIT)
