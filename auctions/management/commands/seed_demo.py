"""Dados de demonstração: contas com senha conhecida e lotes em todas as fases (só para ambiente local)."""
from datetime import timedelta
from decimal import Decimal

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

from accounts.models import Role, User
from auctions import integrity
from auctions.bidding import committed_amount, place_bid
from auctions.lots import create_lot
from auctions.settlement import settle_lot
from catalog import met
from wallet.models import EntryKind, LedgerEntry

PASSWORD = "leilao-demo-2026"  # pública de propósito (README): estas contas só servem para demonstração local
ACCOUNTS = [
    ("demo.leiloeiro@example.com", Role.SELLER),
    ("demo.ana@example.com", Role.BIDDER),
    ("demo.bia@example.com", Role.BIDDER),
]
DEPOSIT = Decimal("10000.00")
MIN_AVAILABLE = Decimal("5000.00")  # uma execução usa até R$ 2.300: sobram ao menos R$ 2.700 para dar lances
WORKS = [436535, 436524, 45734, 45434]  # Van Gogh (2), Kiyohara Yukinobu, Hokusai: domínio público, com imagem


def fetch_works():
    """Tudo do museu antes de gravar: com o Met fora, nada fica pela metade."""
    works = []
    try:
        for object_id in WORKS:  # em sequência: rajadas fazem o Imperva do Met bloquear o IP (D23)
            item = met.get_object(object_id)
            if item is None:
                raise CommandError(f"A obra {object_id} não está mais disponível no Met.")
            works.append((item, *met.fetch_image(item["image_url"])))
    except met.MetUnavailable as error:
        raise CommandError("Acervo do Met indisponível agora; tente de novo em alguns minutos.") from error
    return works


def account(email, role):
    user, _ = User.objects.get_or_create(email=email, defaults={"role": role})
    user.set_password(PASSWORD)
    user.save()
    # Só quando falta: conta nova, ou antiga com o saldo gasto em testes (os lances da demonstração não falham).
    if role == Role.BIDDER and user.wallet.balance() - committed_amount(user) < MIN_AVAILABLE:
        LedgerEntry.objects.create(wallet=user.wallet, amount=DEPOSIT, kind=EntryKind.DEPOSIT)
    return user


def end_now(lot):
    """Antecipa o fim de um lote (com novo selo), para a liquidação real fechá-lo."""
    lot.refresh_from_db()
    lot.ends_at = timezone.now() - timedelta(seconds=1)
    integrity.reseal(lot)
    lot.save(update_fields=["ends_at", "seal"])


class Command(BaseCommand):
    help = "Cria contas e lotes de demonstração (senha pública: só para ambiente local)."

    def handle(self, *args, **options):
        works = fetch_works()
        now = timezone.now()
        with transaction.atomic():
            seller, ana, bia = (account(email, role) for email, role in ACCOUNTS)

            def lot(work, starts, ends, starting):
                item, image, image_type = works[work]
                return create_lot(seller, item, image, image_type, starting_bid=Decimal(starting),
                                  min_increment=Decimal("50.00"), starts_at=now + starts, ends_at=now + ends)

            disputed = lot(0, -timedelta(hours=1), timedelta(days=3), "1200.00")
            for bidder, amount in ((ana, "1200.00"), (bia, "1250.00"), (ana, "1400.00")):
                place_bid(disputed.pk, bidder, Decimal(amount))
            closing = lot(1, -timedelta(hours=2), timedelta(minutes=10), "800.00")
            place_bid(closing.pk, bia, Decimal("800.00"))
            scheduled = lot(2, timedelta(hours=2), timedelta(days=1), "500.00")
            sold = lot(3, -timedelta(hours=3), timedelta(hours=1), "900.00")
            place_bid(sold.pk, ana, Decimal("900.00"))
            unsold = lot(2, -timedelta(days=1), timedelta(hours=1), "700.00")
            end_now(sold)
            end_now(unsold)
        for ended in (sold, unsold):  # liquidação real, cada uma na sua transação (como o worker)
            settle_lot(ended.pk)
        self.report(disputed, closing, scheduled, sold, unsold)

    def report(self, disputed, closing, scheduled, sold, unsold):
        out = self.stdout.write
        out(self.style.SUCCESS("Demonstração pronta em http://localhost:8080"))
        out(f"Senha de todas as contas: {PASSWORD}")
        for email, role in ACCOUNTS:
            out(f"  {email} ({role.label})")
        out(f"  Ao vivo, disputado:     /lotes/{disputed.pk}")
        out(f"  Ao vivo, perto do fim:  /lotes/{closing.pk}")
        out(f"  Agendado:               /lotes/{scheduled.pk}")
        out(f"  Arrematado pela Ana:    /lotes/{sold.pk}  (auditoria: /lotes/{sold.pk}/auditoria)")
        out(f"  Encerrado sem lances:   /lotes/{unsold.pk}")
        out(self.style.WARNING("Senha pública: use estas contas só em ambiente local."))
