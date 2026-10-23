import hashlib
import logging

from django.contrib.auth import authenticate, login, logout
from django.middleware.csrf import get_token
from rest_framework import status
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.throttling import BaseThrottle, ScopedRateThrottle
from rest_framework.views import APIView

from .serializers import LoginSerializer, RegisterSerializer, UserSerializer
from .throttles import LoginEmailThrottle

security = logging.getLogger("verz.security")


class CsrfView(APIView):
    """Entrega o cookie csrftoken ao front antes do primeiro POST."""

    permission_classes = [AllowAny]

    def get(self, request):
        return Response({"csrfToken": get_token(request)})


class RegisterView(APIView):
    permission_classes = [AllowAny]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "register"

    def post(self, request):
        serializer = RegisterSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        user = serializer.save()
        login(request, user)
        return Response(UserSerializer(user).data, status=status.HTTP_201_CREATED)


class LoginView(APIView):
    permission_classes = [AllowAny]
    throttle_classes = [ScopedRateThrottle, LoginEmailThrottle]
    throttle_scope = "login"

    def post(self, request):
        serializer = LoginSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        user = authenticate(request, username=serializer.validated_data["email"].lower(),
                            password=serializer.validated_data["password"])
        if user is None:
            security.warning("login_falhou ip=%s email_sha256=%s", BaseThrottle().get_ident(request),
                             hashlib.sha256(serializer.validated_data["email"].lower().encode()).hexdigest()[:16])
            # Mesma resposta para e-mail inexistente e senha errada (evita enumeração).
            return Response({"detail": "E-mail ou senha inválidos."},
                            status=status.HTTP_400_BAD_REQUEST)
        login(request, user)
        return Response(UserSerializer(user).data)


class LogoutView(APIView):
    def post(self, request):
        logout(request)
        return Response(status=status.HTTP_204_NO_CONTENT)


class MeView(APIView):
    # Visitante sem login não é erro: 204, sem usuário (evita um 403 no console de toda página).
    permission_classes = [AllowAny]

    def get(self, request):
        if not request.user.is_authenticated:
            return Response(status=status.HTTP_204_NO_CONTENT)
        return Response(UserSerializer(request.user).data)
