from django.db import transaction
from django.urls import path

from . import views

urlpatterns = [
    # Sem transação aberta enquanto espera o museu (até ~15 s com as novas tentativas).
    path("search/", transaction.non_atomic_requests(views.CatalogSearchView.as_view())),
]
