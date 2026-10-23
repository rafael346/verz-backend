from django.db import migrations

CREATE_TRIGGER = """
CREATE FUNCTION auctions_receipt_immutable() RETURNS trigger AS $$
BEGIN
    RAISE EXCEPTION 'auctions_receipt is append-only';
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER auctions_receipt_no_update_delete
BEFORE UPDATE OR DELETE ON auctions_receipt
FOR EACH ROW EXECUTE FUNCTION auctions_receipt_immutable();
"""

DROP_TRIGGER = """
DROP TRIGGER auctions_receipt_no_update_delete ON auctions_receipt;
DROP FUNCTION auctions_receipt_immutable();
"""


class Migration(migrations.Migration):
    dependencies = [("auctions", "0005_receipt")]

    operations = [migrations.RunSQL(CREATE_TRIGGER, DROP_TRIGGER)]
