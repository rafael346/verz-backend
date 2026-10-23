from django.db import transaction
from django.urls import path

from . import views

urlpatterns = [
    # Sem transação aberta enquanto a criação espera o museu.
    path("", transaction.non_atomic_requests(views.LotListCreateView.as_view())),
    path("mine/", views.SellerLotsView.as_view()),
    path("participating/", views.ParticipatingLotsView.as_view()),
    path("<int:pk>/", views.LotDetailView.as_view()),
    path("<int:pk>/bids/", views.BidListCreateView.as_view()),
    path("<int:pk>/image/", views.lot_image),
    path("<int:pk>/cancel/", views.LotCancelView.as_view()),
]
