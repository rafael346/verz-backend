from django.db import migrations

CREATE_TRIGGER = """
CREATE FUNCTION auctions_cancellation_immutable() RETURNS trigger AS $$
BEGIN
    RAISE EXCEPTION 'auctions_cancellation is append-only';
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER auctions_cancellation_no_update_delete
BEFORE UPDATE OR DELETE ON auctions_cancellation
FOR EACH ROW EXECUTE FUNCTION auctions_cancellation_immutable();
"""

DROP_TRIGGER = """
DROP TRIGGER auctions_cancellation_no_update_delete ON auctions_cancellation;
DROP FUNCTION auctions_cancellation_immutable();
"""


class Migration(migrations.Migration):
    dependencies = [("auctions", "0007_cancellation")]

    operations = [migrations.RunSQL(CREATE_TRIGGER, DROP_TRIGGER)]
