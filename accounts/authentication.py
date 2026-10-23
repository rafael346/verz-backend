from rest_framework.authentication import SessionAuthentication


class CsrfSessionAuthentication(SessionAuthentication):
    """Exige CSRF também de requisições anônimas (evita login CSRF).

    O DRF só confere o token quando já há usuário autenticado.
    """

    def authenticate(self, request):
        result = super().authenticate(request)
        if result is None:
            self.enforce_csrf(request)
        return result
