from datetime import timedelta
from decimal import Decimal

from django.utils import timezone
from django.utils.dateparse import parse_datetime
from rest_framework import serializers

from .cancellation import REFUND_WINDOW
from .models import Bid, Lot, Phase, Status

CLOCK_SKEW = timedelta(minutes=1)
MIN_DURATION = timedelta(minutes=1)
MAX_DURATION = timedelta(days=30)
MAX_LEAD = timedelta(days=90)


class AwareDateTimeField(serializers.DateTimeField):
    """Exige fuso explícito: sem ele o horário seria lido como UTC e o lote mudaria de hora."""

    default_error_messages = {"naive": "Informe o fuso horário (ex.: 2026-10-23T15:00:00-03:00)."}

    def to_internal_value(self, value):
        if isinstance(value, str):
            try:
                parsed = parse_datetime(value)
            except ValueError:
                parsed = None  # data impossível: o DateTimeField padrão dá o erro certo
            if parsed is not None and parsed.tzinfo is None:
                self.fail("naive")
        return super().to_internal_value(value)


class LotCreateSerializer(serializers.Serializer):
    external_id = serializers.IntegerField(min_value=1)
    starting_bid = serializers.DecimalField(max_digits=12, decimal_places=2, min_value=Decimal("1.00"),
                                            max_value=Decimal("1000000.00"))
    min_increment = serializers.DecimalField(max_digits=12, decimal_places=2, min_value=Decimal("1.00"),
                                             max_value=Decimal("100000.00"))
    starts_at = AwareDateTimeField()
    ends_at = AwareDateTimeField()

    def validate(self, attrs):
        now = timezone.now()
        errors = {}
        if attrs["starts_at"] < now - CLOCK_SKEW:
            errors["starts_at"] = "O início não pode estar no passado."
        elif attrs["starts_at"] > now + MAX_LEAD:
            errors["starts_at"] = "O início deve ser em até 90 dias."
        duration = attrs["ends_at"] - attrs["starts_at"]
        if duration < MIN_DURATION:
            errors["ends_at"] = "O lote deve durar pelo menos 1 minuto."
        elif duration > MAX_DURATION:
            errors["ends_at"] = "O lote pode durar no máximo 30 dias."
        if errors:
            raise serializers.ValidationError(errors)
        return attrs


class LotSerializer(serializers.ModelSerializer):
    image_url = serializers.SerializerMethodField()
    phase = serializers.SerializerMethodField()
    min_next_bid = serializers.SerializerMethodField()

    class Meta:
        model = Lot
        fields = ["id", "title", "artist", "date", "medium", "dimensions", "culture", "department",
                  "source_url", "image_url", "starting_bid", "min_increment", "starts_at", "ends_at", "phase",
                  "current_bid", "bid_count", "min_next_bid"]

    def get_image_url(self, lot):
        return f"/api/lots/{lot.pk}/image/"

    def get_phase(self, lot):
        return lot.phase()

    def get_min_next_bid(self, lot):
        return f"{lot.min_next_bid():.2f}"


def cancellation_payload(lot, show_refund=True):
    """Motivo, data e valor estornado de um lote cancelado (D29), ou None."""
    cancellation = getattr(lot, "cancellation", None) if lot.status == Status.CANCELLED else None
    if cancellation is None:
        return None
    refunded = f"{cancellation.refunded:.2f}" if show_refund and cancellation.refunded is not None else None
    return {"reason": cancellation.reason, "refunded": refunded,
            "cancelled_at": serializers.DateTimeField().to_representation(cancellation.cancelled_at)}


class SellerLotSerializer(LotSerializer):
    result = serializers.SerializerMethodField()
    cancellation = serializers.SerializerMethodField()
    refundable_until = serializers.SerializerMethodField()

    class Meta(LotSerializer.Meta):
        fields = LotSerializer.Meta.fields + ["external_id", "status", "created_at", "result", "cancellation",
                                              "refundable_until"]

    def get_cancellation(self, lot):
        return cancellation_payload(lot)

    def get_refundable_until(self, lot):
        """Até quando um arremate ainda pode ser cancelado com estorno (None se não pode)."""
        receipt = getattr(lot, "receipt", None) if lot.status == Status.SETTLED else None
        if receipt is None or timezone.now() > receipt.issued_at + REFUND_WINDOW:
            return None
        return serializers.DateTimeField().to_representation(receipt.issued_at + REFUND_WINDOW)

    def get_result(self, lot):
        """Resultado do lote encerrado: arrematado e por quanto (None enquanto não liquidado)."""
        if lot.status != Status.SETTLED:
            return None
        receipt = getattr(lot, "receipt", None)  # sem comprovante: fechou sem lances
        return {"sold": receipt is not None, "amount": f"{receipt.amount:.2f}" if receipt else None}


class ShowcaseParams(serializers.Serializer):
    phase = serializers.ChoiceField(choices=Phase.choices, default=Phase.LIVE)
    q = serializers.CharField(max_length=100, required=False, allow_blank=True, default="")


class CancelSerializer(serializers.Serializer):
    # Limite e obrigatoriedade ficam no cancel_lot (dependem de o lote ter lances); aqui só o tipo e um teto.
    reason = serializers.CharField(required=False, allow_blank=True, default="", max_length=1000, trim_whitespace=False)


class BidCreateSerializer(serializers.Serializer):
    amount = serializers.DecimalField(max_digits=12, decimal_places=2, min_value=Decimal("0.01"))


class BidSerializer(serializers.ModelSerializer):
    is_you = serializers.SerializerMethodField()

    class Meta:
        model = Bid
        fields = ["sequence", "bidder_alias", "amount", "created_at", "hash", "is_you"]

    def get_is_you(self, bid):
        request = self.context.get("request")
        return bool(request and request.user.is_authenticated and bid.bidder_id == request.user.pk)


class ParticipationSerializer(LotSerializer):
    my_status = serializers.SerializerMethodField()
    receipt_token = serializers.SerializerMethodField()
    cancellation = serializers.SerializerMethodField()

    class Meta(LotSerializer.Meta):
        fields = LotSerializer.Meta.fields + ["my_status", "receipt_token", "cancellation"]

    def get_cancellation(self, lot):
        receipt = getattr(lot, "receipt", None)  # o estorno só interessa a quem pagou
        return cancellation_payload(lot, show_refund=receipt is not None
                                    and receipt.winner_id == self.context["request"].user.pk)

    def get_receipt_token(self, lot):
        receipt = getattr(lot, "receipt", None)
        return receipt.token if receipt and receipt.winner_id == self.context["request"].user.pk else None

    def get_my_status(self, lot):
        if lot.status == Status.CANCELLED:
            return "cancelled"
        leading = lot.leader_id == self.context["request"].user.pk
        if lot.phase() == Phase.ENDED:
            return "won" if leading else "lost"
        return "winning" if leading else "outbid"


class AuditBidSerializer(serializers.ModelSerializer):
    class Meta:
        model = Bid
        fields = ["sequence", "bidder_alias", "amount", "created_at", "prev_hash", "hash"]
