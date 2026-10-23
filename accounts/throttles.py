import hashlib

from rest_framework.throttling import SimpleRateThrottle


class LoginEmailThrottle(SimpleRateThrottle):
    """Limita tentativas por e-mail, qualquer que seja o IP (força bruta distribuída).

    Contrapartida: quem erra 10 vezes o e-mail de outra pessoa bloqueia o login dela por até 1 hora.
    """

    scope = "login_email"

    def get_cache_key(self, request, view):
        data = request.data
        email = data.get("email") if isinstance(data, dict) else None
        if not isinstance(email, str) or not email:
            return None
        # Hash: o e-mail ainda não foi validado, pode ser enorme ou ter caracteres inválidos em chave de cache.
        ident = hashlib.sha256(email.strip().lower().encode()).hexdigest()
        return self.cache_format % {"scope": self.scope, "ident": ident}
