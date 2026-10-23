import pytest
from django.contrib.auth.hashers import identify_hasher, make_password


def test_health_returns_ok_with_security_headers(client):
    response = client.get("/api/health/", secure=True)

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
    assert response["X-Content-Type-Options"] == "nosniff"
    assert response["X-Frame-Options"] == "DENY"
    assert "default-src 'none'" in response["Content-Security-Policy"]


def test_passwords_use_argon2():
    assert identify_hasher(make_password("senha-segura-123")).algorithm == "argon2"


def test_session_cookie_is_httponly(settings):
    assert settings.SESSION_COOKIE_HTTPONLY is True


@pytest.mark.django_db
def test_database_is_postgresql(django_db_setup):
    from django.db import connection

    assert connection.vendor == "postgresql"
