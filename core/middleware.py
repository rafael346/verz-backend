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
