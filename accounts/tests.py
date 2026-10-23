import pytest
from rest_framework.exceptions import ValidationError
from rest_framework.test import APIClient, APIRequestFactory

from .models import Role, User
from .permissions import IsBidder, IsSeller

pytestmark = pytest.mark.django_db

PASSWORD = "senha-forte-123"


@pytest.fixture
def api():
    return APIClient()


def register(api, email="ana@example.com", role="bidder", password=PASSWORD):
    return api.post("/api/auth/register/", {"email": email, "password": password, "role": role},
                    format="json")


def test_register_creates_user_and_logs_in(api):
    response = register(api)

    assert response.status_code == 201
    assert response.json()["role"] == "bidder"
    assert "password" not in response.json()
    assert api.get("/api/auth/me/").json()["email"] == "ana@example.com"
    assert User.objects.get().password.startswith("argon2")


def test_register_cannot_escalate_privileges(api):
    assert register(api, role="admin").status_code == 400
    response = api.post("/api/auth/register/", {"email": "x@example.com", "password": PASSWORD,
                                                 "role": "seller", "is_staff": True,
                                                 "is_superuser": True}, format="json")
    assert response.status_code == 201
    user = User.objects.get(email="x@example.com")
    assert not user.is_staff and not user.is_superuser


@pytest.mark.parametrize("password", ["curta1", "1234567890123", "password1234"])
def test_register_rejects_weak_passwords(api, password):
    assert register(api, password=password).status_code == 400


def test_register_rejects_duplicate_email_case_insensitive(api):
    register(api, email="ana@example.com")

    assert register(APIClient(), email="ANA@example.com").status_code == 400


def test_login_success_and_logout(api):
    User.objects.create_user("ana@example.com", PASSWORD)

    ok = api.post("/api/auth/login/", {"email": "Ana@Example.com", "password": PASSWORD}, format="json")
    assert ok.status_code == 200
    assert api.get("/api/auth/me/").status_code == 200

    assert api.post("/api/auth/logout/").status_code == 204
    assert api.get("/api/auth/me/").status_code == 204


def test_login_failure_does_not_reveal_whether_email_exists(api):
    User.objects.create_user("ana@example.com", PASSWORD)

    wrong_pw = api.post("/api/auth/login/", {"email": "ana@example.com", "password": "errada-1234"}, format="json")
    no_user = api.post("/api/auth/login/", {"email": "zzz@example.com", "password": "errada-1234"}, format="json")

    assert wrong_pw.status_code == no_user.status_code == 400
    assert wrong_pw.json() == no_user.json()


def test_login_is_rate_limited(api):
    for _ in range(5):
        api.post("/api/auth/login/", {"email": "a@example.com", "password": "x"}, format="json")

    assert api.post("/api/auth/login/", {"email": "a@example.com", "password": "x"},
                    format="json").status_code == 429


def test_csrf_token_rotates_on_login_so_front_must_reread_cookie():
    User.objects.create_user("ana@example.com", PASSWORD)
    client = APIClient(enforce_csrf_checks=True)
    old = client.get("/api/auth/csrf/").json()["csrfToken"]
    client.post("/api/auth/login/", {"email": "ana@example.com", "password": PASSWORD},
                format="json", HTTP_X_CSRFTOKEN=old)

    assert client.post("/api/auth/logout/", HTTP_X_CSRFTOKEN=old).status_code == 403
    new = client.cookies["csrftoken"].value
    assert client.post("/api/auth/logout/", HTTP_X_CSRFTOKEN=new).status_code == 204


def test_login_limit_ignores_forwarded_for_when_no_proxy(api):
    # Padrão (NUM_PROXIES=0): backend sem proxy confiável, X-Forwarded-For é só do cliente.
    for i in range(5):
        api.post("/api/auth/login/", {"email": f"u{i}@example.com", "password": "x"},
                 format="json", HTTP_X_FORWARDED_FOR=f"forjado{i}")

    response = api.post("/api/auth/login/", {"email": "outro@example.com", "password": "x"},
                        format="json", HTTP_X_FORWARDED_FOR="forjado99")
    assert response.status_code == 429


def test_login_limit_ignores_forged_prefix_behind_one_proxy(api, settings):
    settings.REST_FRAMEWORK = {**settings.REST_FRAMEWORK, "NUM_PROXIES": 1}
    # O proxy confiável acrescenta o IP real por último; o prefixo forjado muda a cada tentativa.
    for i in range(5):
        api.post("/api/auth/login/", {"email": f"u{i}@example.com", "password": "x"},
                 format="json", HTTP_X_FORWARDED_FOR=f"forjado{i}, 9.9.9.9")

    response = api.post("/api/auth/login/", {"email": "outro@example.com", "password": "x"},
                        format="json", HTTP_X_FORWARDED_FOR="forjado99, 9.9.9.9")
    assert response.status_code == 429


def test_login_is_limited_per_email_across_ips(api, settings):
    settings.REST_FRAMEWORK = {**settings.REST_FRAMEWORK, "NUM_PROXIES": 1}
    for i in range(10):
        api.post("/api/auth/login/", {"email": "alvo@example.com", "password": "x"},
                 format="json", HTTP_X_FORWARDED_FOR=f"10.0.0.{i}")

    response = api.post("/api/auth/login/", {"email": "ALVO@example.com", "password": "x"},
                        format="json", HTTP_X_FORWARDED_FOR="10.0.1.1")
    assert response.status_code == 429


def test_register_race_on_same_email_returns_400_not_500():
    from .serializers import RegisterSerializer

    data = {"email": "corrida@example.com", "password": PASSWORD, "role": "bidder"}
    first, second = RegisterSerializer(data=data), RegisterSerializer(data=data)
    assert first.is_valid() and second.is_valid()  # ambos passam pela checagem prévia
    first.save()

    with pytest.raises(ValidationError):
        second.save()
    assert User.objects.filter(email="corrida@example.com").count() == 1


def test_login_email_limit_accepts_huge_email_without_error(api):
    huge = "a" * 100_000 + "@example.com"

    assert api.post("/api/auth/login/", {"email": huge, "password": "x"}, format="json").status_code == 400


def test_me_has_no_user_for_visitors(api):
    response = api.get("/api/auth/me/")

    assert response.status_code == 204
    assert response.content == b""


def test_csrf_is_enforced_even_for_anonymous_posts():
    client = APIClient(enforce_csrf_checks=True)

    assert client.post("/api/auth/login/", {"email": "a@example.com", "password": "x"},
                       format="json").status_code == 403

    token = client.get("/api/auth/csrf/").json()["csrfToken"]
    response = client.post("/api/auth/login/", {"email": "a@example.com", "password": "x"},
                           format="json", HTTP_X_CSRFTOKEN=token)
    assert response.status_code == 400  # passou do CSRF; falhou só nas credenciais


def test_role_permissions():
    seller = User.objects.create_user("s@example.com", PASSWORD, role=Role.SELLER)
    bidder = User.objects.create_user("b@example.com", PASSWORD, role=Role.BIDDER)
    request = APIRequestFactory().get("/")

    request.user = seller
    assert IsSeller().has_permission(request, None) and not IsBidder().has_permission(request, None)
    request.user = bidder
    assert IsBidder().has_permission(request, None) and not IsSeller().has_permission(request, None)
