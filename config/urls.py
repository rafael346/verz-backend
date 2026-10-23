from django.urls import include, path

from auctions.views import ReceiptView
from core.views import health

urlpatterns = [
    path("api/health/", health),
    path("api/auth/", include("accounts.urls")),
    path("api/wallet/", include("wallet.urls")),
    path("api/catalog/", include("catalog.urls")),
    path("api/lots/", include("auctions.urls")),
    path("api/receipts/<slug:token>/", ReceiptView.as_view()),
]
