from decimal import Decimal

from django.db import transaction
from django.db.models import Max, Sum
from django.http import Http404, HttpResponse
from django.shortcuts import get_object_or_404
from django.utils import timezone
from django.views.decorators.http import require_GET
from rest_framework import status
from rest_framework.pagination import PageNumberPagination
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.throttling import ScopedRateThrottle
from rest_framework.views import APIView

from accounts.models import Role
from accounts.permissions import IsBidder, IsSeller
from catalog import met
from catalog.views import UNAVAILABLE
from wallet.models import EntryKind, LedgerEntry

from . import bidding, integrity
from .models import Bid, Lot, Phase, Receipt, Status
from .serializers import (AuditBidSerializer, BidCreateSerializer, BidSerializer, LotCreateSerializer,
                          LotSerializer, ParticipationSerializer, SellerLotSerializer, ShowcaseParams)

SNAPSHOT_FIELDS = ["title", "artist", "date", "medium", "dimensions", "culture", "department", "source_url"]
TERMS_FIELDS = ["starting_bid", "min_increment", "starts_at", "ends_at"]
NOT_LISTABLE = "Obra não encontrada ou indisponível para leilão: precisa ser de domínio público e ter imagem."


class LotPagination(PageNumberPagination):
    page_size = 20


def paginated(request, view, queryset, serializer_class):
    paginator = LotPagination()
    page = paginator.paginate_queryset(queryset, request, view=view)
    return paginator.get_paginated_response(serializer_class(page, many=True, context={"request": request}).data)


class LotListCreateView(APIView):
    throttle_scope = "lot_create"

    def get_permissions(self):
        return [AllowAny()] if self.request.method == "GET" else [IsSeller()]

    def get_throttles(self):
        # A criação baixa a imagem do museu e tem limite próprio; a vitrine usa os limites padrão.
        return [ScopedRateThrottle()] if self.request.method == "POST" else super().get_throttles()

    def get(self, request):
        params = ShowcaseParams(data=request.query_params)
        params.is_valid(raise_exception=True)
        lots = Lot.objects.showcase().in_phase(params.validated_data["phase"], timezone.now())
        if params.validated_data["q"]:
            lots = lots.search(params.validated_data["q"])
        return paginated(request, self, lots, LotSerializer)

    def post(self, request):
        serializer = LotCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        try:
            item = met.get_object(data["external_id"])
            if item is None:
                return Response({"external_id": [NOT_LISTABLE]}, status=status.HTTP_400_BAD_REQUEST)
            image, image_type = met.fetch_image(item["image_url"])
        except met.MetUnavailable:
            return Response({"detail": UNAVAILABLE}, status=status.HTTP_503_SERVICE_UNAVAILABLE)
        with transaction.atomic():  # o lote nunca existe sem selo
            lot = Lot.objects.create(
                seller=request.user, external_id=item["external_id"], image=image, image_type=image_type,
                **{field: item[field] for field in SNAPSHOT_FIELDS},
                **{field: data[field] for field in TERMS_FIELDS},
            )
            integrity.reseal(lot, integrity.genesis(lot.pk))
            lot.save(update_fields=["seal"])
        return Response(SellerLotSerializer(lot).data, status=status.HTTP_201_CREATED)


class LotDetailView(APIView):
    permission_classes = [AllowAny]

    def get(self, request, pk):
        lot = get_object_or_404(Lot.objects.showcase(), pk=pk)
        return Response({**LotSerializer(lot).data, "me": my_standing(lot, request.user)})


def my_standing(lot, user):
    """Situação do licitante no lote: ganhando/superado e posição entre os licitantes."""
    if not (user.is_authenticated and user.role == Role.BIDDER):
        return None
    ranking = list(lot.bids.values("bidder").annotate(top=Max("amount")).order_by("-top")
                   .values_list("bidder", flat=True))
    if user.pk not in ranking:
        return {"status": "none", "position": None, "bidders": len(ranking)}
    return {"status": "winning" if lot.leader_id == user.pk else "outbid",
            "position": ranking.index(user.pk) + 1, "bidders": len(ranking)}


@require_GET
def lot_image(request, pk):
    lot = get_object_or_404(Lot.objects.only("image", "image_type"), pk=pk)
    response = HttpResponse(bytes(lot.image), content_type=lot.image_type)
    response["Cache-Control"] = "public, max-age=86400, immutable"  # a imagem de um lote nunca muda
    return response


class LotCancelView(APIView):
    permission_classes = [IsSeller]

    def post(self, request, pk):
        lot = get_object_or_404(Lot.objects.select_for_update().defer("image"), pk=pk, seller=request.user)
        if not integrity.seal_matches(lot):
            return Response({"detail": "Lote bloqueado: a auditoria encontrou uma falha de integridade."},
                            status=status.HTTP_409_CONFLICT)
        if lot.status == Status.CANCELLED:
            return Response({"detail": "Este lote já foi cancelado."}, status=status.HTTP_400_BAD_REQUEST)
        if lot.bid_count:
            return Response({"detail": "Lote com lances não pode ser cancelado."},
                            status=status.HTTP_400_BAD_REQUEST)
        if lot.phase() == Phase.ENDED:
            return Response({"detail": "Lote encerrado não pode ser cancelado."},
                            status=status.HTTP_400_BAD_REQUEST)
        lot.status = Status.CANCELLED
        integrity.reseal(lot)
        lot.save(update_fields=["status", "seal"])
        return Response(SellerLotSerializer(lot).data)


class LotAuditView(APIView):
    """Auditoria pública: recalcula a cadeia e o selo e diz se o histórico está íntegro."""

    permission_classes = [AllowAny]

    def get(self, request, pk):
        lot = get_object_or_404(Lot.objects.defer("image"), pk=pk)
        problems, rows, head = integrity.verify(lot)
        return Response({
            "lot": {**LotSerializer(lot).data, "status": lot.status},
            "intact": not problems,
            "problems": problems,
            "head_hash": head,
            "bids": [{**AuditBidSerializer(bid).data, "valid": valid} for bid, valid in rows],
        })


class SellerLotsView(APIView):
    permission_classes = [IsSeller]

    def get(self, request):
        lots = (Lot.objects.filter(seller=request.user).select_related("receipt").defer("image")
                .order_by("-created_at", "-id"))
        return paginated(request, self, lots, SellerLotSerializer)


class BidListCreateView(APIView):
    throttle_scope = "bid"

    def get_permissions(self):
        return [AllowAny()] if self.request.method == "GET" else [IsBidder()]

    def get_throttles(self):
        return [ScopedRateThrottle()] if self.request.method == "POST" else super().get_throttles()

    def get(self, request, pk):
        lot = get_object_or_404(Lot.objects.showcase(), pk=pk)
        return paginated(request, self, lot.bids.order_by("-sequence"), BidSerializer)

    def post(self, request, pk):
        serializer = BidCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            bid, lot = bidding.place_bid(pk, request.user, serializer.validated_data["amount"])
        except Lot.DoesNotExist:
            raise Http404
        except bidding.BidRejected as rejection:
            return Response({"code": rejection.code, "detail": rejection.detail},
                            status=status.HTTP_400_BAD_REQUEST)
        context = {"request": request}
        return Response({"bid": BidSerializer(bid, context=context).data, "lot": LotSerializer(lot).data},
                        status=status.HTTP_201_CREATED)


class ParticipatingLotsView(APIView):
    permission_classes = [IsBidder]

    def get(self, request):
        lots = (Lot.objects.filter(pk__in=Bid.objects.filter(bidder=request.user).values("lot"))
                .exclude(status=Status.CANCELLED).select_related("receipt").defer("image")
                .order_by("-ends_at", "-id"))
        return paginated(request, self, lots, ParticipationSerializer)


class ReceiptView(APIView):
    """Comprovante público: quem tem o link vê o arremate e confere a autenticidade."""

    permission_classes = [AllowAny]

    def get(self, request, token):
        receipt = get_object_or_404(Receipt.objects.select_related("lot").defer("lot__image"), token=token)
        lot = receipt.lot
        problems, _, head = integrity.verify(lot)
        return Response({
            "token": receipt.token,
            "issued_at": receipt.issued_at,
            "amount": f"{receipt.amount:.2f}",
            "winner_alias": receipt.winner_alias,
            "head_hash": receipt.head_hash,
            "lot": {"id": lot.pk, "title": lot.title, "artist": lot.artist, "image_url": f"/api/lots/{lot.pk}/image/"},
            "authentic": integrity.receipt_is_authentic(receipt),
            "audit": {"intact": not problems, "head_matches": head == receipt.head_hash,
                      "url": f"/api/lots/{lot.pk}/audit/"},
        })


class SellerSummaryView(APIView):
    permission_classes = [IsSeller]

    def get(self, request):
        lots = Lot.objects.filter(seller=request.user, status=Status.SETTLED)
        received = (LedgerEntry.objects.filter(wallet__user=request.user, kind=EntryKind.SETTLEMENT_CREDIT)
                    .aggregate(total=Sum("amount"))["total"] or Decimal("0.00"))
        return Response({"total_received": f"{received:.2f}",
                         "sold": lots.filter(receipt__isnull=False).count(),
                         "unsold": lots.filter(receipt__isnull=True).count()})
