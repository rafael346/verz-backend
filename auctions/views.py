from django.http import HttpResponse
from django.shortcuts import get_object_or_404
from django.utils import timezone
from django.views.decorators.http import require_GET
from rest_framework import status
from rest_framework.pagination import PageNumberPagination
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.throttling import ScopedRateThrottle
from rest_framework.views import APIView

from accounts.permissions import IsSeller
from catalog import met
from catalog.views import UNAVAILABLE

from .models import Lot, Phase, Status
from .serializers import LotCreateSerializer, LotSerializer, SellerLotSerializer, ShowcaseParams

SNAPSHOT_FIELDS = ["title", "artist", "date", "medium", "dimensions", "culture", "department", "source_url"]
TERMS_FIELDS = ["starting_bid", "min_increment", "starts_at", "ends_at"]
NOT_LISTABLE = "Obra não encontrada ou indisponível para leilão: precisa ser de domínio público e ter imagem."


class LotPagination(PageNumberPagination):
    page_size = 20


def paginated(request, view, queryset, serializer_class):
    paginator = LotPagination()
    page = paginator.paginate_queryset(queryset, request, view=view)
    return paginator.get_paginated_response(serializer_class(page, many=True).data)


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
        lot = Lot.objects.create(
            seller=request.user, external_id=item["external_id"], image=image, image_type=image_type,
            **{field: item[field] for field in SNAPSHOT_FIELDS},
            **{field: data[field] for field in TERMS_FIELDS},
        )
        return Response(SellerLotSerializer(lot).data, status=status.HTTP_201_CREATED)


class LotDetailView(APIView):
    permission_classes = [AllowAny]

    def get(self, request, pk):
        return Response(LotSerializer(get_object_or_404(Lot.objects.showcase(), pk=pk)).data)


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
        if lot.status == Status.CANCELLED:
            return Response({"detail": "Este lote já foi cancelado."}, status=status.HTTP_400_BAD_REQUEST)
        if lot.phase() == Phase.ENDED:
            return Response({"detail": "Lote encerrado não pode ser cancelado."},
                            status=status.HTTP_400_BAD_REQUEST)
        lot.status = Status.CANCELLED
        lot.save(update_fields=["status"])
        return Response(SellerLotSerializer(lot).data)


class SellerLotsView(APIView):
    permission_classes = [IsSeller]

    def get(self, request):
        lots = Lot.objects.filter(seller=request.user).defer("image").order_by("-created_at", "-id")
        return paginated(request, self, lots, SellerLotSerializer)
