from django.conf import settings
from django.db import migrations

CREATE_TRIGGER = """
CREATE FUNCTION wallet_ledger_immutable() RETURNS trigger AS $$
BEGIN
    RAISE EXCEPTION 'wallet_ledgerentry is append-only';
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER wallet_ledger_no_update_delete
BEFORE UPDATE OR DELETE ON wallet_ledgerentry
FOR EACH ROW EXECUTE FUNCTION wallet_ledger_immutable();
"""

DROP_TRIGGER = """
DROP TRIGGER wallet_ledger_no_update_delete ON wallet_ledgerentry;
DROP FUNCTION wallet_ledger_immutable();
"""


def backfill_wallets(apps, schema_editor):
    User = apps.get_model(*settings.AUTH_USER_MODEL.split("."))
    Wallet = apps.get_model("wallet", "Wallet")
    for user in User.objects.filter(wallet__isnull=True):
        Wallet.objects.create(user=user)


class Migration(migrations.Migration):
    dependencies = [("wallet", "0001_initial")]

    operations = [
        migrations.RunSQL(CREATE_TRIGGER, DROP_TRIGGER),
        migrations.RunPython(backfill_wallets, migrations.RunPython.noop),
    ]
