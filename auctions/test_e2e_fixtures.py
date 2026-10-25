import json
from datetime import timedelta
from io import StringIO

import pytest
from django.contrib.auth import SESSION_KEY
from django.contrib.sessions.backends.db import SessionStore
from django.core.management import call_command
from django.utils import timezone

from accounts.models import User
from auctions import integrity
from auctions.models import Lot, Phase, Receipt, Status

pytestmark = pytest.mark.django_db


def run(*args):
    out = StringIO()
    call_command("e2e_fixtures", *args, stdout=out)
    return json.loads(out.getvalue())


def test_creates_fresh_accounts_with_a_random_password_and_the_lots():
    first, second = run(), run()

    assert first["password"] != second["password"] and first["accounts"] != second["accounts"]
    for email in first["accounts"].values():
        assert User.objects.get(email=email).check_password(first["password"])
    lots = {name: Lot.objects.get(pk=pk) for name, pk in first["lots"].items()}
    assert lots["open"].bid_count == 0 and lots["open"].phase() == Phase.LIVE
    assert lots["disputed"].bid_count == 2
    assert lots["scheduled"].phase() == Phase.SCHEDULED
    for name in ("sold", "sold_to_cancel"):
        assert lots[name].status == Status.SETTLED and Receipt.objects.filter(lot=lots[name]).exists()
    assert first["receipts"]["sold"] == Receipt.objects.get(lot=lots["sold"]).token
    assert bytes(lots["open"].image).startswith(b"\xff\xd8\xff")  # JPEG de verdade: o navegador desenha
    for name, key in first["sessions"].items():  # sessões já logadas, sem passar pelo limite de login
        assert SessionStore(session_key=key)[SESSION_KEY] == str(User.objects.get(email=first["accounts"][name]).pk)


def test_timed_lots_reuse_the_run_accounts():
    base = run()
    closing = run("--timed", "prorrogacao", "--run", base["run"])
    settling = run("--timed", "liquidacao", "--run", base["run"])

    lot = Lot.objects.get(pk=closing["lot"])
    assert lot.seller.email == base["accounts"]["seller"] and lot.bid_count == 0 and lot.phase() == Phase.LIVE
    lot = Lot.objects.get(pk=settling["lot"])
    assert lot.bid_count == 1 and lot.leader.email == base["accounts"]["ana"]
    # Termina mesmo em ~20 s: o lance não pode ter acionado o anti-sniping.
    assert lot.ends_at - timezone.now() < timedelta(seconds=25)
    assert not integrity.verify(lot)[0]
