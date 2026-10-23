from django.urls import include, path

from core.views import health

urlpatterns = [
    path("api/health/", health),
    path("api/auth/", include("accounts.urls")),
]
