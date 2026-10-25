"""Dados dos testes ponta a ponta (verz-frontend/e2e): contas novas e lotes sob medida, sem o Met. Saída em JSON."""
import base64
import json
import secrets
from datetime import timedelta
from decimal import Decimal

from django.contrib.auth import BACKEND_SESSION_KEY, HASH_SESSION_KEY, SESSION_KEY
from django.contrib.sessions.backends.db import SessionStore
from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone

from accounts.models import Role, User
from auctions import integrity
from auctions.bidding import place_bid
from auctions.lots import create_lot
from auctions.management.commands.seed_demo import end_now
from auctions.settlement import settle_lot
from wallet.models import EntryKind, LedgerEntry

# JPEG 64×80 (moldura vermelha sobre papel), gerada pelo canvas do navegador: o lote de teste desenha de verdade.
IMAGE = base64.b64decode(
    "/9j/4AAQSkZJRgABAQAAAQABAAD/4gHYSUNDX1BST0ZJTEUAAQEAAAHIAAAAAAQwAABtbnRyUkdCIFhZWiAH4AABAAEAAAAAAABh"
    "Y3NwAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAQAA9tYAAQAAAADTLQAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
    "AAAAAAAAAAAAAAAAAAAAAAAAAAlkZXNjAAAA8AAAACRyWFlaAAABFAAAABRnWFlaAAABKAAAABRiWFlaAAABPAAAABR3dHB0AAAB"
    "UAAAABRyVFJDAAABZAAAAChnVFJDAAABZAAAAChiVFJDAAABZAAAAChjcHJ0AAABjAAAADxtbHVjAAAAAAAAAAEAAAAMZW5VUwAA"
    "AAgAAAAcAHMAUgBHAEJYWVogAAAAAAAAb6IAADj1AAADkFhZWiAAAAAAAABimQAAt4UAABjaWFlaIAAAAAAAACSgAAAPhAAAts9Y"
    "WVogAAAAAAAA9tYAAQAAAADTLXBhcmEAAAAAAAQAAAACZmYAAPKnAAANWQAAE9AAAApbAAAAAAAAAABtbHVjAAAAAAAAAAEAAAAM"
    "ZW5VUwAAACAAAAAcAEcAbwBvAGcAbABlACAASQBuAGMALgAgADIAMAAxADb/2wBDAAoHBwgHBgoICAgLCgoLDhgQDg0NDh0VFhEY"
    "Ix8lJCIfIiEmKzcvJik0KSEiMEExNDk7Pj4+JS5ESUM8SDc9Pjv/2wBDAQoLCw4NDhwQEBw7KCIoOzs7Ozs7Ozs7Ozs7Ozs7Ozs7"
    "Ozs7Ozs7Ozs7Ozs7Ozs7Ozs7Ozs7Ozs7Ozs7Ozs7Ozv/wAARCABQAEADASIAAhEBAxEB/8QAFwABAQEBAAAAAAAAAAAAAAAAAAcG"
    "BP/EACkQAAEBBgUCBwAAAAAAAAAAAAABAgMEBQeDBhETRcI2sxIXITFVpNL/xAAYAQEBAQEBAAAAAAAAAAAAAAAAAwEEAv/EACER"
    "AAIBAwQDAQAAAAAAAAAAAAABAgMEMhEzgbEhQfBx/9oADAMBAAIRAxEAPwCmgA5iwAAAAAAAAAJ7VXar3AoRPaq7Ve4HmeJ02m9H"
    "nozMvwdP5rAu42CgNWHe5+BvWds55KqL6K0i+6KdPl7ir4v7Dr9FIp70RL7vcaNIYqSa1L1L6pGbikvD+9kT8vcVfF/Ydfo5phg6"
    "fyqBeRsbAaUO6y8bes7ayzVET0RpV91Qupm6hdETC13GQ6SS1FO+qSmotLy/vZlKVbrZ5lCJ7SrdbPMoRsMSF3vS46BPaq7Ve4FC"
    "J7VXar3ATxFpvR56NXT3oiX3e40aQzdPeiJfd7jRpCkcUQr7sv1gzdQuiJha7jJpDN1C6ImFruMiWLFDdj+oylKt1s8yhE9pVutn"
    "mUInDEvd70uOgT2qu1XuBQie1V2q9wE8Rab0eejV096Il93uNGkIVL8Yz+VQLuCgo/Sh3WfgY0XbWWaqq+qsqvuqnT5hYq+U+u6/"
    "JiqpLQvUsakpuSa8v70WwzdQuiJha7jJN/MLFXyn13X5OaYYxn81gXkFGx+rDvcvGxou2c8lRU9UZRfdEDqprQU7GpGak2vD+9Gm"
    "pVutnmUIntKt1s8yhGwxIXe9LjoAA9HMAAAAAAAAAf/Z"
)
ITEM = {"external_id": 1, "title": "Obra de teste", "artist": "Ateliê verz", "date": "2026", "medium": "Têmpera",
        "dimensions": "64 × 80 px", "culture": "", "department": "Testes", "source_url": ""}
ROLES = {"seller": Role.SELLER, "ana": Role.BIDDER, "bia": Role.BIDDER}


def emails(run):
    return {name: f"e2e-{run}-{name}@example.test" for name in ROLES}


def session(user):
    """Sessão já logada: a suíte não passa pelo login (limite de 5 por minuto), só o teste do formulário passa."""
    store = SessionStore()
    store[SESSION_KEY], store[BACKEND_SESSION_KEY] = str(user.pk), "django.contrib.auth.backends.ModelBackend"
    store[HASH_SESSION_KEY] = user.get_session_auth_hash()
    store.create()
    return store.session_key


def lot(seller, run, starts, ends, starting="1000.00"):
    now = timezone.now()
    # Título com o código da execução: cada execução deixa lotes no banco, e a busca da vitrine acha só os desta.
    return create_lot(seller, {**ITEM, "title": f"Obra de teste {run}"}, IMAGE, "image/jpeg", starting_bid=Decimal(starting),
                      min_increment=Decimal("50.00"), starts_at=now + starts, ends_at=now + ends)


class Command(BaseCommand):
    help = "Cria contas e lotes para os testes ponta a ponta e imprime JSON (só ambiente local)."

    def add_arguments(self, parser):
        parser.add_argument("--timed", choices=["prorrogacao", "liquidacao"],
                            help="cria só um lote com relógio, para as contas de uma execução (--run)")
        parser.add_argument("--run", help="identificador da execução, devolvido pela chamada sem --timed")

    def handle(self, *args, timed=None, run=None, **options):
        self.stdout.write(json.dumps(self.timed(timed, run) if timed else self.base()))

    def base(self):
        run, password = secrets.token_hex(4), secrets.token_urlsafe(12)  # senha nova a cada execução
        hour, day = timedelta(hours=1), timedelta(days=1)
        with transaction.atomic():
            users = {name: User.objects.create_user(email, password, role=ROLES[name])
                     for name, email in emails(run).items()}
            for name in ("ana", "bia"):
                LedgerEntry.objects.create(wallet=users[name].wallet, amount=Decimal("10000.00"),
                                           kind=EntryKind.DEPOSIT)
            seller, ana, bia = users["seller"], users["ana"], users["bia"]
            lots = {
                "open": lot(seller, run, -hour, day),
                "disputed": lot(seller, run, -hour, day),
                "scheduled": lot(seller, run, hour, day),
                "sold": lot(seller, run, -hour, hour, "900.00"),
                "sold_to_cancel": lot(seller, run, -hour, hour, "800.00"),
            }
            place_bid(lots["disputed"].pk, ana, Decimal("1000.00"))
            place_bid(lots["disputed"].pk, bia, Decimal("1050.00"))
            for name, amount in (("sold", "900.00"), ("sold_to_cancel", "800.00")):
                place_bid(lots[name].pk, ana, Decimal(amount))
                end_now(lots[name])
        receipts = {}
        for name in ("sold", "sold_to_cancel"):  # liquidação real, como o worker
            settle_lot(lots[name].pk)
            lots[name].refresh_from_db()
            receipts[name] = lots[name].receipt.token
        return {"run": run, "password": password, "accounts": emails(run),
                "sessions": {name: session(user) for name, user in users.items()},
                "lots": {name: item.pk for name, item in lots.items()}, "receipts": receipts}

    def timed(self, kind, run):
        users = {name: User.objects.get(email=email) for name, email in emails(run).items()}
        if kind == "prorrogacao":  # 90 s: um lance já cai nos últimos 2 minutos
            created = lot(users["seller"], run, -timedelta(minutes=5), timedelta(seconds=90))
        else:  # lance da ana e fim em 20 s: o worker liquida e emite o comprovante
            # O lance vem antes de encurtar o prazo: dado nos últimos 2 minutos, o anti-sniping empurraria o fim.
            created = lot(users["seller"], run, -timedelta(minutes=5), timedelta(hours=1), "500.00")
            place_bid(created.pk, users["ana"], Decimal("500.00"))
            created.refresh_from_db()
            created.ends_at = timezone.now() + timedelta(seconds=20)
            integrity.reseal(created)
            created.save(update_fields=["ends_at", "seal"])
        return {"lot": created.pk}
