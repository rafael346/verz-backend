from rest_framework import serializers, status
from rest_framework.response import Response
from rest_framework.throttling import ScopedRateThrottle
from rest_framework.views import APIView

from accounts.permissions import IsSeller

from . import met

UNAVAILABLE = "O catálogo do museu está indisponível no momento. Tente novamente em instantes."


class SearchParams(serializers.Serializer):
    q = serializers.CharField(min_length=2, max_length=100)
    # A busca do Met não passa de offset + limit = 10000.
    offset = serializers.IntegerField(min_value=0, max_value=10_000 - met.PAGE_SIZE, default=0)


class CatalogSearchView(APIView):
    permission_classes = [IsSeller]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "catalog"

    def get(self, request):
        params = SearchParams(data=request.query_params)
        params.is_valid(raise_exception=True)
        try:
            page = met.search(params.validated_data["q"], params.validated_data["offset"])
        except met.MetUnavailable:
            return Response({"detail": UNAVAILABLE}, status=status.HTTP_503_SERVICE_UNAVAILABLE)
        return Response(page)
