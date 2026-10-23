"""Encerramento e liquidação (D05): cada lote vencido é liquidado numa transação, uma única vez."""
import logging
import secrets

from django.db import transaction
from django.utils import timezone

from wallet.models import EntryKind, LedgerEntry, Wallet

from . import integrity
from .models import Lot, Receipt, Status

logger = logging.getLogger(__name__)

SOLD, UNSOLD, BLOCKED, FAILED = "sold", "unsold", "blocked", "failed"


def settle_due_lots(exclude=(), limit=50):
    """Uma volta do worker: tenta liquidar os lotes vencidos. Devolve [(id, resultado)]."""
    ids = list(Lot.objects.filter(status=Status.OPEN, ends_at__lte=timezone.now()).exclude(pk__in=exclude)
               .order_by("ends_at", "id").values_list("pk", flat=True)[:limit])
    results = []
    for lot_id in ids:
        try:
            outcome = settle_lot(lot_id)
        except Exception:
            # Um lote com erro não pode travar a fila: os demais seguem e ele volta na próxima rodada.
            logger.exception("Falha ao liquidar o lote %s.", lot_id)
            outcome = FAILED
        if outcome:
            results.append((lot_id, outcome))
    return results


@transaction.atomic
def settle_lot(lot_id):
    """Liquida um lote vencido. SOLD/UNSOLD/BLOCKED, ou None se já foi liquidado, não venceu ou está ocupado."""
    now = timezone.now()
    # skip_locked: um lance de último segundo (ou outro worker) está com o lote; ele volta na próxima rodada.
    # O filtro é reconferido sob a trava: um lance que prorrogou o fim tira o lote daqui.
    lot = (Lot.objects.select_for_update(skip_locked=True).defer("image")
           .filter(pk=lot_id, status=Status.OPEN, ends_at__lte=now).first())
    if lot is None:
        return None
    head = integrity.head_of(lot)
    if not integrity.seal_matches(lot, head):
        logger.error("Lote %s não liquidado: selo de integridade inválido.", lot.pk)
        return BLOCKED
    outcome = UNSOLD
    if lot.leader_id:
        # Mesma ordem do lance (lote e depois carteira do licitante); a do leiloeiro por último.
        winner = Wallet.objects.select_for_update().get(user_id=lot.leader_id)
        seller = Wallet.objects.select_for_update().get(user_id=lot.seller_id)
        if winner.balance() < lot.current_bid:
            logger.error("Lote %s não liquidado: saldo do vencedor menor que o lance.", lot.pk)
            return BLOCKED
        LedgerEntry.objects.create(wallet=winner, amount=-lot.current_bid, kind=EntryKind.SETTLEMENT_DEBIT, lot=lot)
        LedgerEntry.objects.create(wallet=seller, amount=lot.current_bid, kind=EntryKind.SETTLEMENT_CREDIT, lot=lot)
        receipt = Receipt(lot=lot, token=secrets.token_urlsafe(18), winner_id=lot.leader_id,
                          winner_alias=lot.bids.get(sequence=lot.bid_count).bidder_alias,
                          amount=lot.current_bid, head_hash=head, issued_at=now)
        receipt.signature = integrity.receipt_signature(receipt)
        receipt.save()
        outcome = SOLD
    lot.status = Status.SETTLED
    integrity.reseal(lot, head)
    lot.save(update_fields=["status", "seal"])
    return outcome
