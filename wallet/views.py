from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView

from accounts.permissions import IsBidder

from .models import EntryKind, LedgerEntry
from .serializers import DepositSerializer


def balance_payload(wallet):
    # Texto com 2 casas: o encoder do DRF converteria Decimal em float e perderia precisão.
    return {"balance": f"{wallet.balance():.2f}"}


class WalletView(APIView):
    def get(self, request):
        return Response(balance_payload(request.user.wallet))


class DepositView(APIView):
    permission_classes = [IsBidder]

    def post(self, request):
        serializer = DepositSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        wallet = request.user.wallet
        # Só insere uma linha no livro: sem leitura-e-escrita, logo sem lock nem corrida.
        LedgerEntry.objects.create(wallet=wallet, amount=serializer.validated_data["amount"],
                                   kind=EntryKind.DEPOSIT)
        return Response(balance_payload(wallet), status=status.HTTP_201_CREATED)
