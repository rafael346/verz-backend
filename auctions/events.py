"""Tempo real (D03): NOTIFY do Postgres no commit → um hub por processo → SSE para cada sala."""
import asyncio
import contextvars
import json
import logging

import psycopg
from asgiref.sync import sync_to_async
from django.core.serializers.json import DjangoJSONEncoder
from django.db import close_old_connections, connection, transaction
from django.http import Http404, HttpResponse, StreamingHttpResponse
from rest_framework.throttling import BaseThrottle

from .models import Lot

logger = logging.getLogger(__name__)

CHANNEL = "lot_events"
HEARTBEAT_SECONDS = 15
MAX_SUBSCRIBERS = 2000
MAX_PER_CLIENT = 20  # um script sozinho não ocupa todas as vagas
QUEUE_SIZE = 100


def notify(lot_id, kind, sequence=None):
    """Avisa os espectadores do lote. Dentro da transação: o Postgres só entrega depois do COMMIT."""
    payload = json.dumps({"lot": lot_id, "type": kind, "sequence": sequence})
    with connection.cursor() as cursor:
        cursor.execute("SELECT pg_notify(%s, %s)", [CHANNEL, payload])


def lot_state(lot):
    from .serializers import LotSerializer  # import tardio: serializers importa models

    return {**LotSerializer(lot).data, "status": lot.status}


def bid_data(bid):
    from .serializers import BidSerializer

    return BidSerializer(bid).data


def build_event(lot_id, kind, sequence):
    # Roda fora de qualquer requisição: abre e fecha a conexão, para nunca usar uma conexão morta.
    close_old_connections()
    try:
        lot = Lot.objects.defer("image").get(pk=lot_id)
        event = {"type": kind, "lot": lot_state(lot)}
        if kind == "bid":
            event["bid"] = bid_data(lot.bids.get(sequence=sequence))
        return event
    finally:
        close_old_connections()


def snapshot(lot_id, after):
    """Estado atual e os lances com sequência maior que `after` (retomada pelo Last-Event-ID)."""
    lot = Lot.objects.showcase().filter(pk=lot_id).first()
    if lot is None:
        return None, []
    missed = [bid_data(bid) for bid in lot.bids.filter(sequence__gt=after).order_by("sequence")[:500]]
    return lot_state(lot), missed


class Hub:
    """Uma conexão LISTEN por processo, distribuindo para filas por lote."""

    def __init__(self):
        self._loop = None
        self._task = None
        self._ready = None
        self._subscribers = {}
        self._clients = {}

    def _count(self):
        return sum(len(queues) for queues in self._subscribers.values())

    def has_room(self, client):
        return self._count() < MAX_SUBSCRIBERS and self._clients.get(client, 0) < MAX_PER_CLIENT

    async def subscribe(self, lot_id, client):
        loop = asyncio.get_running_loop()
        if self._loop is not loop or self._task is None or self._task.done():
            self._loop, self._ready, self._subscribers, self._clients = loop, asyncio.Event(), {}, {}
            # Contexto próprio: a tarefa vive além da requisição que a criou.
            self._task = loop.create_task(self._listen(), context=contextvars.Context())
        await asyncio.wait_for(self._ready.wait(), timeout=5)
        if not self.has_room(client):
            raise OverflowError("muitas conexões abertas")
        queue = asyncio.Queue(maxsize=QUEUE_SIZE)
        self._subscribers.setdefault(lot_id, set()).add(queue)
        self._clients[client] = self._clients.get(client, 0) + 1
        return queue

    def unsubscribe(self, lot_id, queue, client):
        queues = self._subscribers.get(lot_id, set())
        queues.discard(queue)
        if not queues:
            self._subscribers.pop(lot_id, None)
        remaining = self._clients.get(client, 0) - 1
        if remaining > 0:
            self._clients[client] = remaining
        else:
            self._clients.pop(client, None)

    async def close(self):
        if self._task:
            self._task.cancel()
            await asyncio.gather(self._task, return_exceptions=True)
        self._task = None

    @staticmethod
    def _deliver(queues, event):
        for queue in queues:
            try:
                queue.put_nowait(event)
            except asyncio.QueueFull:  # cliente lento: descarta o atraso e pede para recarregar
                while not queue.empty():
                    queue.get_nowait()
                queue.put_nowait({"type": "resync"})

    async def _listen(self):
        db = connection.settings_dict
        conninfo = psycopg.conninfo.make_conninfo(dbname=db["NAME"], user=db["USER"], password=db["PASSWORD"],
                                                  host=db["HOST"], port=db["PORT"] or None)
        while True:
            try:
                async with await psycopg.AsyncConnection.connect(conninfo, autocommit=True) as conn:
                    await conn.execute(f"LISTEN {CHANNEL}")
                    if self._ready.is_set():  # reconectou: algo pode ter passado sem aviso
                        for queues in self._subscribers.values():
                            self._deliver(queues, {"type": "resync"})
                    self._ready.set()
                    async for note in conn.notifies():
                        message = json.loads(note.payload)
                        queues = self._subscribers.get(message["lot"])
                        if queues:
                            event = await sync_to_async(build_event)(message["lot"], message["type"],
                                                                     message["sequence"])
                            self._deliver(queues, event)
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("Hub de eventos perdeu a conexão com o banco; reconectando.")
                await asyncio.sleep(1)


hub = Hub()


def sse(kind, data, event_id=None):
    head = f"id: {event_id}\n" if event_id is not None else ""
    return f"{head}event: {kind}\ndata: {json.dumps(data, cls=DjangoJSONEncoder)}\n\n"


def last_event_id(request):
    try:
        return max(int(request.headers.get("Last-Event-ID", "0")), 0)
    except ValueError:
        return 0


@transaction.non_atomic_requests  # view async não pode ficar dentro de ATOMIC_REQUESTS
async def lot_events(request, pk):
    after = last_event_id(request)
    client = BaseThrottle().get_ident(request)  # mesmo IP que os limites da API (respeita NUM_PROXIES)
    if not await sync_to_async(Lot.objects.showcase().filter(pk=pk).exists)():
        raise Http404
    if not hub.has_room(client):
        return HttpResponse("Tempo real indisponível no momento.", status=503, content_type="text/plain")

    async def stream():
        # A inscrição vive dentro do stream: se ele nunca começar (cliente saiu antes), não sobra inscrição.
        try:
            queue = await hub.subscribe(pk, client)
        except (OverflowError, TimeoutError):
            yield sse("unavailable", {"detail": "Tempo real indisponível no momento; tente de novo em instantes."})
            return
        try:
            # Inscrito antes de ler o banco: o que chegar durante a leitura fica na fila.
            state, missed = await sync_to_async(snapshot)(pk, after)
            if state is None:
                return  # cancelado no meio do caminho
            last = after
            yield sse("state", state)
            for bid in missed:
                last = bid["sequence"]
                yield sse("bid", {"bid": bid}, event_id=last)
            while True:
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=HEARTBEAT_SECONDS)
                except TimeoutError:
                    yield ": ping\n\n"
                    continue
                if event["type"] == "bid":
                    if event["bid"]["sequence"] <= last:
                        continue  # já enviado na retomada
                    last = event["bid"]["sequence"]
                    yield sse("bid", event, event_id=last)
                else:
                    yield sse(event["type"], event)
        finally:
            hub.unsubscribe(pk, queue, client)

    response = StreamingHttpResponse(stream(), content_type="text/event-stream")
    response["Cache-Control"] = "no-cache"
    response["X-Accel-Buffering"] = "no"  # proxies como o nginx não seguram os eventos
    return response
