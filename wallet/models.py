from decimal import Decimal

from django.conf import settings
from django.db import models
from django.db.models import Q, Sum


class Wallet(models.Model):
    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.PROTECT,
                                related_name="wallet")

    def balance(self):
        """Saldo total = soma do livro-razão (nunca uma coluna que possa divergir)."""
        return self.entries.aggregate(total=Sum("amount"))["total"] or Decimal("0.00")


class EntryKind(models.TextChoices):
    DEPOSIT = "deposit", "Depósito"
    SETTLEMENT_DEBIT = "settlement_debit", "Débito de arremate"
    SETTLEMENT_CREDIT = "settlement_credit", "Crédito de venda"
    REFUND_CREDIT = "refund_credit", "Estorno de arremate cancelado"
    REFUND_DEBIT = "refund_debit", "Retirada por arremate cancelado"


class LedgerEntry(models.Model):
    wallet = models.ForeignKey(Wallet, on_delete=models.PROTECT, related_name="entries")
    amount = models.DecimalField(max_digits=12, decimal_places=2)  # com sinal: crédito > 0, débito < 0
    kind = models.CharField(max_length=20, choices=EntryKind.choices)
    created_at = models.DateTimeField(auto_now_add=True)
    lot = models.ForeignKey("auctions.Lot", on_delete=models.PROTECT, null=True, blank=True,
                            related_name="ledger_entries")

    class Meta:
        constraints = [
            models.CheckConstraint(condition=~Q(amount=0), name="ledger_amount_not_zero"),
            # Um débito e um crédito por lote, no máximo: o banco impede liquidar duas vezes.
            models.UniqueConstraint(fields=["lot", "kind"], condition=Q(lot__isnull=False),
                                    name="ledger_one_settlement_entry_per_lot"),
        ]
