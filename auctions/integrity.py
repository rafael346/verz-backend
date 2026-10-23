"""Histórico à prova de adulteração (D06): HMAC encadeado por lote + selo do estado do lote."""
import hmac
from datetime import timezone as dt_timezone
from hashlib import sha256

from django.conf import settings


def _mac(*parts):
    message = "|".join(str(part) for part in parts).encode()
    return hmac.new(settings.AUDIT_HMAC_KEY.encode(), message, sha256).hexdigest()


def _iso(moment):
    return moment.astimezone(dt_timezone.utc).isoformat(timespec="microseconds")


def _same(stored, expected):
    # Bytes, não texto: compare_digest com str só aceita ASCII e um hash forjado com acento derrubaria a auditoria.
    return hmac.compare_digest(str(stored).encode(), expected.encode())


def _money(value):
    return "" if value is None else f"{value:.2f}"


def genesis(lot_id):
    return _mac("genesis", lot_id)


def bid_hash(bid, prev_hash):
    return _mac("bid", bid.lot_id, bid.sequence, bid.bidder_id, bid.bidder_alias, _money(bid.amount),
                _iso(bid.created_at), prev_hash)


def lot_seal(lot, head):
    return _mac("seal", lot.pk, lot.status, _money(lot.starting_bid), _money(lot.min_increment),
                _iso(lot.starts_at), _iso(lot.ends_at), lot.bid_count, _money(lot.current_bid),
                lot.leader_id or "", head)


def head_of(lot):
    """Hash do último lance (ou o genesis, se não houver lances)."""
    return lot.bids.order_by("-sequence").values_list("hash", flat=True).first() or genesis(lot.pk)


def seal_matches(lot, head=None):
    return _same(lot.seal, lot_seal(lot, head or head_of(lot)))


def reseal(lot, head=None):
    lot.seal = lot_seal(lot, head or head_of(lot))


def verify(lot):
    """Recalcula a cadeia inteira e o selo. Devolve (problemas, [(lance, válido)], último hash)."""
    problems, rows = [], []
    prev, expected = genesis(lot.pk), 1
    for bid in lot.bids.order_by("sequence"):
        if bid.sequence != expected:
            problems.append(f"Sequência quebrada: esperado o lance #{expected}, encontrado o #{bid.sequence} "
                            "(lance removido ou inserido).")
        content_ok = _same(bid.hash, bid_hash(bid, bid.prev_hash))
        link_ok = _same(bid.prev_hash, prev)
        if not content_ok:
            problems.append(f"Lance #{bid.sequence} foi alterado depois de registrado.")
        elif not link_ok:
            problems.append(f"Lance #{bid.sequence} não se liga ao anterior (lance removido ou inserido antes dele).")
        rows.append((bid, content_ok and link_ok))
        prev, expected = bid.hash, bid.sequence + 1
    if not _same(lot.seal, lot_seal(lot, prev)):
        problems.append("O selo do lote não confere: o estado do lote (lance atual, líder, prazos, valores ou "
                        "contagem de lances) foi alterado, ou lances foram removidos do fim.")
    return problems, rows, prev
