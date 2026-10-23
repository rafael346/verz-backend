from django.db import transaction
from django.http import JsonResponse


@transaction.non_atomic_requests
def health(request):
    return JsonResponse({"status": "ok"})
