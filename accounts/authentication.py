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

    def authenticate_header(self, request):
        # Com cabeçalho, o DRF responde 401 a quem está sem sessão (o front entende "sessão expirou");
        # falta de permissão continua 403. "Session" não é um esquema do navegador: não abre janela de login.
        return "Session"
