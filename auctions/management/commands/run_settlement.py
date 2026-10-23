import logging
import time
from datetime import timedelta

from django.core.management import BaseCommand, call_command
from django.db import connection
from django.utils import timezone

from auctions import settlement

logger = logging.getLogger(__name__)

POLL_SECONDS = 1
SESSION_CLEANUP_EVERY = timedelta(hours=24)


class Command(BaseCommand):
    help = "Encerra e liquida os lotes vencidos (D05). Roda em loop no container worker."

    def add_arguments(self, parser):
        parser.add_argument("--once", action="store_true", help="Uma volta só (testes e manutenção).")

    def handle(self, *args, once=False, **options):
        blocked, last_cleanup = set(), None
        while True:
            try:
                for lot_id, outcome in settlement.settle_due_lots(exclude=blocked):
                    self.stdout.write(f"lote {lot_id}: {outcome}")
                    if outcome == settlement.BLOCKED:
                        blocked.add(lot_id)  # adulterado: não tentar de novo a cada segundo
                if last_cleanup is None or timezone.now() - last_cleanup >= SESSION_CLEANUP_EVERY:
                    call_command("clearsessions")
                    last_cleanup = timezone.now()
            except Exception:
                if once:
                    raise
                logger.exception("Falha numa volta da liquidação; tentando de novo.")  # ex.: banco fora
                connection.close()
            if once:
                return
            time.sleep(POLL_SECONDS)
