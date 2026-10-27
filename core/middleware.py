import hmac

from django.conf import settings
from django.http import JsonResponse


class ProxySecretMiddleware:
    """Só aceita requisições que passaram pelo front: o proxy da Vercel manda o segredo em X-Verz-Proxy (D36).

    Sem isso, quem chama a API direto forja o X-Forwarded-For e escapa dos limites por IP (NUM_PROXIES=3).
    Sem PROXY_SECRET (Docker local, testes), não faz nada.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        secret = settings.PROXY_SECRET
        sent = request.headers.get("X-Verz-Proxy", "")
        if secret and not hmac.compare_digest(sent.encode(), secret.encode()):
            return JsonResponse({"detail": "Acesso direto à API não é permitido."}, status=403)
        return self.get_response(request)


class SecurityHeadersMiddleware:
    """Cabeçalhos extras (OWASP) que o SecurityMiddleware do Django não cobre.

    A API só devolve JSON, então a CSP bloqueia tudo.
    """

    HEADERS = {
        "Content-Security-Policy": "default-src 'none'; frame-ancestors 'none'",
        "Permissions-Policy": "geolocation=(), camera=(), microphone=()",
        "Cross-Origin-Resource-Policy": "same-origin",
    }

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)
        for name, value in self.HEADERS.items():
            response.headers.setdefault(name, value)
        return response
