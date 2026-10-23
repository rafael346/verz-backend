from django.conf import settings
from django.db import models
from django.db.models import F, Q
from django.utils import timezone


class Status(models.TextChoices):
    OPEN = "open", "Aberto"
    CANCELLED = "cancelled", "Cancelado"
    SETTLED = "settled", "Liquidado"


class Phase(models.TextChoices):
    SCHEDULED = "scheduled", "Agendado"
    LIVE = "live", "Ao vivo"
    ENDED = "ended", "Encerrado"


class LotQuerySet(models.QuerySet):
    def showcase(self):
        """Lotes públicos: cancelados nunca aparecem e os bytes da imagem não são carregados."""
        return self.exclude(status=Status.CANCELLED).defer("image")

    def in_phase(self, phase, now):
        if phase == Phase.LIVE:
            return self.filter(status=Status.OPEN, starts_at__lte=now, ends_at__gt=now).order_by("ends_at", "id")
        if phase == Phase.SCHEDULED:
            return self.filter(status=Status.OPEN, starts_at__gt=now).order_by("starts_at", "id")
        return self.filter(Q(status=Status.SETTLED) | Q(status=Status.OPEN, ends_at__lte=now)).order_by(
            "-ends_at", "-id")

    def search(self, query):
        # shortcut: ILIKE sem índice percorre a tabela; com milhares de lotes, usar índice trigram (pg_trgm).
        return self.filter(Q(title__icontains=query) | Q(artist__icontains=query)
                           | Q(culture__icontains=query) | Q(department__icontains=query))


class Lot(models.Model):
    seller = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="lots")
    external_id = models.PositiveIntegerField()
    # Snapshot da obra na criação (D14): vitrine, sala e comprovante não dependem mais do museu.
    title = models.TextField()
    artist = models.TextField(blank=True)
    date = models.TextField(blank=True)
    medium = models.TextField(blank=True)
    dimensions = models.TextField(blank=True)
    culture = models.TextField(blank=True)
    department = models.TextField(blank=True)
    source_url = models.URLField(max_length=500, blank=True)
    image = models.BinaryField()
    image_type = models.CharField(max_length=20)
    starting_bid = models.DecimalField(max_digits=12, decimal_places=2)
    min_increment = models.DecimalField(max_digits=12, decimal_places=2)
    starts_at = models.DateTimeField()
    ends_at = models.DateTimeField()
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.OPEN)
    created_at = models.DateTimeField(auto_now_add=True)

    objects = LotQuerySet.as_manager()

    class Meta:
        constraints = [
            models.CheckConstraint(condition=Q(ends_at__gt=F("starts_at")), name="lot_ends_after_start"),
            models.CheckConstraint(condition=Q(starting_bid__gt=0) & Q(min_increment__gt=0),
                                   name="lot_positive_amounts"),
        ]
        indexes = [models.Index(fields=["status", "ends_at"]), models.Index(fields=["status", "starts_at"])]

    def phase(self, now=None):
        now = now or timezone.now()
        if self.status == Status.SETTLED or now >= self.ends_at:
            return Phase.ENDED
        return Phase.LIVE if now >= self.starts_at else Phase.SCHEDULED
