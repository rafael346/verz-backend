from django.db import migrations

from auctions import integrity

CREATE_TRIGGER = """
CREATE FUNCTION auctions_bid_immutable() RETURNS trigger AS $$
BEGIN
    RAISE EXCEPTION 'auctions_bid is append-only';
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER auctions_bid_no_update_delete
BEFORE UPDATE OR DELETE ON auctions_bid
FOR EACH ROW EXECUTE FUNCTION auctions_bid_immutable();
"""

DROP_TRIGGER = """
DROP TRIGGER auctions_bid_no_update_delete ON auctions_bid;
DROP FUNCTION auctions_bid_immutable();
"""


def chain_existing_lots(apps, schema_editor):
    """Lotes criados antes da cadeia: encadeia os lances na ordem e sela o estado atual."""
    Lot = apps.get_model("auctions", "Lot")
    for lot in Lot.objects.all():
        head = integrity.genesis(lot.pk)
        for bid in lot.bids.order_by("sequence"):
            bid.prev_hash = head
            bid.hash = head = integrity.bid_hash(bid, head)
            bid.save(update_fields=["prev_hash", "hash"])
        lot.seal = integrity.lot_seal(lot, head)
        lot.save(update_fields=["seal"])


class Migration(migrations.Migration):
    dependencies = [("auctions", "0003_bid_hash_bid_prev_hash_lot_seal_alter_bid_created_at")]

    operations = [
        migrations.RunPython(chain_existing_lots, migrations.RunPython.noop),
        migrations.RunSQL(CREATE_TRIGGER, DROP_TRIGGER),
    ]
