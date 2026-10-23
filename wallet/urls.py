from django.urls import path

from . import views

urlpatterns = [
    path("", views.WalletView.as_view()),
    path("deposit/", views.DepositView.as_view()),
]
