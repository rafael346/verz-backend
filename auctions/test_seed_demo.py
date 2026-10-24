import pytest
from django.core.management import CommandError, call_command
from django.utils import timezone

from accounts.models import Role, User
from wallet.models import EntryKind, LedgerEntry

from . import integrity
from .management.commands.seed_demo import ACCOUNTS, DEPOSIT, PASSWORD
from .models import Lot, Phase, Receipt, Status
from .tests import museum  # noqa: F401 (fixture)

pytestmark = pytest.mark.django_db


def test_creates_accounts_that_can_log_in(museum):
    call_command("seed_demo")

    for email, role in ACCOUNTS:
        user = User.objects.get(email=email)
        assert user.role == role and user.check_password(PASSWORD)


def test_creates_one_lot_in_each_situation(museum):
    call_command("seed_demo")

    now = timezone.now()
    lots = list(Lot.objects.order_by("id"))
    assert [lot.phase(now) for lot in lots] == [Phase.LIVE, Phase.LIVE, Phase.SCHEDULED, Phase.ENDED, Phase.ENDED]
    disputed, closing, _, sold, unsold = lots
    assert disputed.bid_count == 3 and closing.bid_count == 1
    assert sold.status == Status.SETTLED and Receipt.objects.filter(lot=sold).exists()
    assert unsold.status == Status.SETTLED and not Receipt.objects.filter(lot=unsold).exists()
    assert all(not integrity.verify(lot)[0] for lot in lots)  # auditoria íntegra em todos


def test_met_down_creates_nothing(museum):
    museum["down"] = True

    with pytest.raises(CommandError, match="Met"):
        call_command("seed_demo")
    assert not User.objects.exists() and not Lot.objects.exists()


def test_running_twice_deposits_once(museum):
    call_command("seed_demo")
    call_command("seed_demo")

    bidders = [email for email, role in ACCOUNTS if role == Role.BIDDER]
    assert LedgerEntry.objects.filter(wallet__user__email__in=bidders, kind=EntryKind.DEPOSIT).count() == 2
    assert Lot.objects.count() == 10  # um conjunto novo por execução


def test_bidders_with_little_money_are_topped_up(museum):
    call_command("seed_demo")
    ana = User.objects.get(email="demo.ana@example.com")
    LedgerEntry.objects.create(wallet=ana.wallet, amount=-ana.wallet.balance(), kind=EntryKind.DEPOSIT)

    call_command("seed_demo")  # contas antigas, com o saldo gasto: a demonstração ainda sobe

    assert Lot.objects.count() == 10
    assert LedgerEntry.objects.filter(wallet=ana.wallet, kind=EntryKind.DEPOSIT, amount=DEPOSIT).count() == 2
