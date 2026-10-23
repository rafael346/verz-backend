from decimal import Decimal

from rest_framework import serializers


class DepositSerializer(serializers.Serializer):
    amount = serializers.DecimalField(max_digits=12, decimal_places=2,
                                      min_value=Decimal("1.00"), max_value=Decimal("100000.00"))
