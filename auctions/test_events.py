import asyncio
import json
import sys
from datetime import timedelta

import pytest
from asgiref.sync import sync_to_async
from django.db import connection
from django.test import AsyncClient
from django.utils import timezone

from . import events
from .bidding import bidder_alias
from .events import hub
from .models import Status
from .settlement import settle_lot
from .test_bids import bid, move_end, new_bidder
from .tests import client_for, live, scheduled, seller  # noqa: F401 (fixture)

# Transação real: o Postgres só entrega o NOTIFY depois do COMMIT.
pytestmark = pytest.mark.django_db(transaction=True)


@pytest.fixture
def ana():
    return new_bidder("ana@example.com")


@pytest.fixture
def bia():
    return new_bidder("bia@example.com")


def parse(chunk):
    text = chunk.decode() if isinstance(chunk, bytes) else chunk
    fields = dict(line.partition(": ")[::2] for line in text.strip().splitlines())
    return fields.get("event"), fields.get("id"), json.loads(fields["data"]) if "data" in fields else None


class Room:
    """Um espectador da sala. Lê o stream numa tarefa, como o servidor ASGI faz."""

    def __init__(self, response):
        self._chunks = asyncio.Queue()
        self._reader = asyncio.create_task(self._read(response.streaming_content))

    async def _read(self, content):
        async for chunk in content:
            await self._chunks.put(chunk)

    async def raw(self, timeout=5):
        return await asyncio.wait_for(self._chunks.get(), timeout)

    async def next(self, timeout=5):
        while True:
            kind, event_id, data = parse(await self.raw(timeout))
            if kind:  # pula os pings
                return kind, event_id, data

    async def close(self):
        # Desconexão do cliente: o Django cancela a tarefa que lê o stream.
        self._reader.cancel()
        await asyncio.gather(self._reader, return_exceptions=True)


async def open_room(lot, last_event_id=None):
    headers = {"Last-Event-ID": str(last_event_id)} if last_event_id is not None else {}
    response = await AsyncClient().get(f"/api/lots/{lot.pk}/events/", headers=headers)
    assert response.status_code == 200
    assert response["Content-Type"].startswith("text/event-stream")
    assert response["Cache-Control"] == "no-cache"
    return Room(response)


def run(scenario):
    async def wrapper():
        try:
            await scenario()
        finally:
            await hub.close()
            await sync_to_async(lambda: connection.close())()  # fecha a conexão da thread do sync_to_async

    # No Windows o laço padrão (Proactor) não serve para o psycopg assíncrono; no Linux (Docker) é o padrão.
    asyncio.run(wrapper(), loop_factory=asyncio.SelectorEventLoop if sys.platform == "win32" else None)


def test_room_gets_state_then_live_bids(seller, ana):
    lot = live(seller)

    async def scenario():
        room = await open_room(lot)
        kind, _, state = await room.next()
        assert (kind, state["bid_count"], state["phase"], state["status"]) == ("state", 0, "live", "open")

        await sync_to_async(bid)(ana, lot, "100.00")

        kind, event_id, event = await room.next()
        assert (kind, event_id) == ("bid", "1")
        assert (event["bid"]["amount"], event["lot"]["current_bid"], event["lot"]["min_next_bid"]) == (
            "100.00", "100.00", "110.00")
        await room.close()

    run(scenario)


def test_rejected_bid_sends_nothing(seller, ana):
    lot = live(seller)

    async def scenario():
        room = await open_room(lot)
        await room.next()
        await sync_to_async(bid)(ana, lot, "1.00")  # abaixo do mínimo: desfeito, nada é avisado

        with pytest.raises(TimeoutError):
            await room.next(timeout=1)
        await room.close()

    run(scenario)


def test_reconnect_with_last_event_id_gets_only_missed_bids(seller, ana, bia):
    lot = live(seller)
    for user, amount in ((ana, "100.00"), (bia, "110.00"), (ana, "120.00")):
        bid(user, lot, amount)

    async def scenario():
        room = await open_room(lot, last_event_id=1)
        kind, _, state = await room.next()
        assert (kind, state["bid_count"]) == ("state", 3)
        received = [await room.next() for _ in range(2)]
        assert [(k, i, e["bid"]["amount"]) for k, i, e in received] == [("bid", "2", "110.00"), ("bid", "3", "120.00")]
        await room.close()

    run(scenario)


def test_settlement_is_pushed_as_closed(seller, ana):
    lot = live(seller)
    bid(ana, lot, "100.00")

    async def scenario():
        room = await open_room(lot, last_event_id=1)
        await room.next()
        await sync_to_async(move_end)(lot, timezone.now() - timedelta(seconds=1))
        await sync_to_async(settle_lot)(lot.pk)

        kind, _, event = await room.next()
        assert (kind, event["lot"]["status"], event["lot"]["phase"]) == ("closed", "settled", "ended")
        await room.close()

    run(scenario)


def test_cancellation_is_pushed(seller):
    lot = scheduled(seller)

    async def scenario():
        room = await open_room(lot)
        await room.next()
        await sync_to_async(lambda: client_for(seller).post(f"/api/lots/{lot.pk}/cancel/"))()

        kind, _, event = await room.next()
        assert (kind, event["lot"]["status"]) == ("cancelled", "cancelled")
        await room.close()

    run(scenario)


def test_unknown_or_cancelled_lot_has_no_room(seller):
    gone = live(seller, status=Status.CANCELLED)

    async def scenario():
        for pk in (gone.pk, 999999):
            assert (await AsyncClient().get(f"/api/lots/{pk}/events/")).status_code == 404
        assert hub._count() == 0

    run(scenario)


def test_closing_the_room_leaves_the_hub(seller):
    lot = live(seller)

    async def scenario():
        room = await open_room(lot)
        await room.next()
        assert hub._count() == 1
        await room.close()
        assert hub._count() == 0

    run(scenario)


def test_idle_room_gets_heartbeats(seller, monkeypatch):
    monkeypatch.setattr(events, "HEARTBEAT_SECONDS", 0.2)
    lot = live(seller)

    async def scenario():
        room = await open_room(lot)
        await room.next()
        assert await room.raw(timeout=2) in (": ping\n\n", b": ping\n\n")
        await room.close()

    run(scenario)


def test_my_alias_lets_the_room_know_which_bids_are_mine(seller, ana):
    lot = live(seller)

    alias = client_for(ana).get(f"/api/lots/{lot.pk}/").json()["me"]["alias"]

    assert alias == bidder_alias(lot.pk, ana.pk)
    assert bid(ana, lot, "100.00").json()["bid"]["bidder_alias"] == alias


def test_a_room_that_never_starts_reading_leaves_nothing_behind(seller):
    lot = live(seller)

    async def scenario():
        response = await AsyncClient().get(f"/api/lots/{lot.pk}/events/")  # cliente sai antes de ler
        assert response.status_code == 200
        assert hub._count() == 0

    run(scenario)


def test_database_failure_while_opening_the_room_does_not_leak_viewers(seller, monkeypatch):
    lot = live(seller)

    def broken_snapshot(lot_id, after):
        raise RuntimeError("banco piscou")

    monkeypatch.setattr(events, "snapshot", broken_snapshot)

    async def scenario():
        room = await open_room(lot)
        with pytest.raises(TimeoutError):
            await room.next(timeout=1)  # o stream terminou com erro, nada chega
        await room.close()
        assert hub._count() == 0

    run(scenario)


def test_one_client_cannot_take_every_slot(seller, monkeypatch):
    monkeypatch.setattr(events, "MAX_PER_CLIENT", 1)
    lot = live(seller)

    async def scenario():
        first = await open_room(lot)
        await first.next()
        assert (await AsyncClient().get(f"/api/lots/{lot.pk}/events/")).status_code == 503
        await first.close()
        again = await open_room(lot)
        await again.next()
        await again.close()

    run(scenario)
