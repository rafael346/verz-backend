"""Cancelamento de lote (D29): libera o comprometido e, se já arrematado, estorna no livro-razão."""
import logging
from datetime import timedelta

from django.db import transaction
from django.utils import timezone

from wallet.models import EntryKind, LedgerEntry, Wallet

from . import events, integrity
from .models import Cancellation, Lot, Phase, Status

REFUND_WINDOW = timedelta(days=7)
MAX_REASON = 200
security = logging.getLogger("verz.security")


class CancelRejected(Exception):
    def __init__(self, detail, status=400):
        super().__init__(detail)
        self.detail, self.status = detail, status


@transaction.atomic
def cancel_lot(lot_id, seller, reason=""):
    # Mesma ordem do lance e da liquidação: lote primeiro, carteiras depois (sem deadlock).
    lot = Lot.objects.select_for_update().defer("image").get(pk=lot_id, seller=seller)
    if not integrity.seal_matches(lot):
        raise CancelRejected("Lote bloqueado: a auditoria encontrou uma falha de integridade.", status=409)
    if lot.status == Status.CANCELLED:
        raise CancelRejected("Este lote já foi cancelado.")
    receipt = getattr(lot, "receipt", None)
    # Liquidado sem vencedor, ou vencido sem lances esperando o worker: nos dois casos não há o que desfazer.
    if (lot.status == Status.SETTLED and receipt is None) or (not lot.bid_count and lot.phase() == Phase.ENDED):
        raise CancelRejected("Lote encerrado sem lances não tem o que cancelar.")
    if receipt is not None and timezone.now() > receipt.issued_at + REFUND_WINDOW:
        raise CancelRejected("O prazo para cancelar este arremate (7 dias) terminou.")
    reason = reason.strip()
    if lot.bid_count and not reason:
        raise CancelRejected("Informe o motivo: quem deu lance vai ver.")
    if len(reason) > MAX_REASON:
        raise CancelRejected(f"O motivo pode ter até {MAX_REASON} caracteres.")

    refunded = None
    if receipt is not None:
        winner = Wallet.objects.select_for_update().get(user_id=receipt.winner_id)
        seller_wallet = Wallet.objects.select_for_update().get(user_id=lot.seller_id)
        # Um por lote e tipo (restrição do banco): estornar duas vezes é impossível.
        LedgerEntry.objects.create(wallet=winner, amount=receipt.amount, kind=EntryKind.REFUND_CREDIT, lot=lot)
        LedgerEntry.objects.create(wallet=seller_wallet, amount=-receipt.amount, kind=EntryKind.REFUND_DEBIT, lot=lot)
        refunded = receipt.amount
    cancellation = Cancellation(lot=lot, reason=reason, refunded=refunded)
    cancellation.signature = integrity.cancellation_signature(cancellation)
    cancellation.save()
    lot.status = Status.CANCELLED
    integrity.reseal(lot)
    lot.save(update_fields=["status", "seal"])
    events.notify(lot.pk, "cancelled")
    security.warning("lote_cancelado lote=%s estorno=%s", lot.pk, refunded or "0")
    return lot
