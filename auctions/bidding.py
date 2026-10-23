"""Regras de lance (D08). Tudo numa transação, com o lote e a carteira travados."""
import logging
from datetime import timedelta
from decimal import Decimal

from django.db import transaction
from django.db.models import Sum
from django.utils import timezone
from django.utils.crypto import salted_hmac

from wallet.models import Wallet

from . import events, integrity
from .models import Bid, Lot, Phase, Status

security = logging.getLogger("verz.security")

ANTI_SNIPING_WINDOW = timedelta(minutes=2)
ANTI_SNIPING_EXTENSION = timedelta(minutes=2)


class BidRejected(Exception):
    def __init__(self, code, detail):
        super().__init__(detail)
        self.code = code
        self.detail = detail


def bidder_alias(lot_id, user_id):
    """Pseudônimo estável dentro do lote e diferente entre lotes (D09)."""
    digest = salted_hmac("verz.bidder-alias", f"{lot_id}:{user_id}", algorithm="sha256").hexdigest()
    return f"Licitante #{digest[:6].upper()}"


def committed_amount(user):
    """Comprometido = soma dos lances em que o usuário lidera em lotes ainda não liquidados."""
    total = Lot.objects.filter(leader=user, status=Status.OPEN).aggregate(total=Sum("current_bid"))["total"]
    return total or Decimal("0.00")


@transaction.atomic
def place_bid(lot_id, user, amount):
    # Sempre lote e depois carteira: a mesma ordem em todo lance evita deadlock.
    lot = Lot.objects.select_for_update().defer("image").exclude(status=Status.CANCELLED).get(pk=lot_id)
    wallet = Wallet.objects.select_for_update().get(user=user)
    now = timezone.now()

    head = integrity.head_of(lot)
    if not integrity.seal_matches(lot, head):
        # Nunca construir em cima de adulteração: o próximo selo a esconderia.
        security.error("lance_recusado_integridade lote=%s licitante=%s", lot.pk, user.pk)
        raise BidRejected("integrity_error", "Lote bloqueado para lances: a auditoria encontrou uma falha de integridade.")
    phase = lot.phase(now)
    if phase == Phase.SCHEDULED:
        raise BidRejected("not_started", "O lote ainda não começou.")
    if phase == Phase.ENDED:
        raise BidRejected("ended", "O lote já foi encerrado.")
    if lot.leader_id == user.pk:
        raise BidRejected("already_winning", "Você já está ganhando este lote; não é possível cobrir o próprio lance.")
    minimum = lot.min_next_bid()
    if amount < minimum:
        raise BidRejected("below_minimum", f"Lance abaixo do mínimo: o lance mínimo agora é {minimum:.2f}.")
    available = wallet.balance() - committed_amount(user)
    if amount > available:
        raise BidRejected("insufficient_balance",
                          f"Saldo disponível insuficiente: você tem {available:.2f} disponível.")

    lot.bid_count += 1
    bid = Bid(lot=lot, bidder=user, bidder_alias=bidder_alias(lot.pk, user.pk), sequence=lot.bid_count,
              amount=amount, created_at=now, prev_hash=head)
    bid.hash = integrity.bid_hash(bid, head)
    bid.save()
    lot.current_bid, lot.leader = amount, user
    if lot.ends_at - now <= ANTI_SNIPING_WINDOW:
        lot.ends_at += ANTI_SNIPING_EXTENSION
    integrity.reseal(lot, bid.hash)
    lot.save(update_fields=["current_bid", "leader", "bid_count", "ends_at", "seal"])
    events.notify(lot.pk, "bid", bid.sequence)
    return bid, lot
