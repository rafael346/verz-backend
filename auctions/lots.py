"""Criação de lote, usada pela API e pelos dados de demonstração."""
from django.db import transaction

from . import integrity
from .models import Lot

SNAPSHOT_FIELDS = ["title", "artist", "date", "medium", "dimensions", "culture", "department", "source_url"]


def create_lot(seller, item, image, image_type, *, starting_bid, min_increment, starts_at, ends_at):
    """Lote com a ficha e a cópia da imagem da obra; nasce selado (nunca existe sem selo)."""
    with transaction.atomic():
        lot = Lot.objects.create(
            seller=seller, external_id=item["external_id"], image=image, image_type=image_type,
            starting_bid=starting_bid, min_increment=min_increment, starts_at=starts_at, ends_at=ends_at,
            **{field: item[field] for field in SNAPSHOT_FIELDS},
        )
        integrity.reseal(lot, integrity.genesis(lot.pk))
        lot.save(update_fields=["seal"])
    return lot
