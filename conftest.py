import pytest
from django.core.cache import cache


@pytest.fixture(autouse=True)
def _local_cache(settings):
    # Em produção o cache é o do Postgres (D27); nos testes, memória local: rápido, isolado e sem exigir banco.
    settings.CACHES = {"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}}
    cache.clear()
