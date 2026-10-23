"""Cliente da Met Collection API (D14): timeout, novas tentativas com espera e cache.

Só o painel do leiloeiro usa isto; o resto da plataforma lê o snapshot do lote no banco.
"""
import hashlib
import json
import time
from concurrent.futures import ThreadPoolExecutor
from http.client import HTTPException
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from django.core.cache import cache

BASE_URL = "https://collectionapi.metmuseum.org/public/collection"
USER_AGENT = "verz-leilao/1.0"
# Pior caso de uma busca: 3 s + 0,5 s + 3 s na busca e 2 rodadas de 3 s nas obras ≈ 12,5 s,
# bem abaixo dos 30 s em que o gunicorn derruba a requisição.
TIMEOUT = 3  # segundos por requisição
RETRIES = 1  # novas tentativas em falha temporária
PAGE_SIZE = 12
SEARCH_TTL = 10 * 60
OBJECT_TTL = 24 * 60 * 60
_NOT_LISTABLE = "not-listable"  # obra inexistente ou fora do filtro; também vai ao cache
_FAILED = object()


class MetUnavailable(Exception):
    """A API do museu falhou, demorou demais ou respondeu algo inesperado."""


def _get_json(url, retries=RETRIES):
    """GET com timeout e novas tentativas. Devolve None em 404."""
    for attempt in range(retries + 1):
        try:
            with urlopen(Request(url, headers={"User-Agent": USER_AGENT}), timeout=TIMEOUT) as response:
                return json.load(response)
        except HTTPError as error:  # antes de OSError, que é a classe-mãe
            if error.code == 404:
                return None
            if error.code != 429 and error.code < 500:
                raise MetUnavailable(f"HTTP {error.code}") from error
            last_error = error
        # OSError cobre URLError, timeout e conexão caída; HTTPException cobre resposta cortada
        # (IncompleteRead) durante a leitura do corpo, que o urlopen não embrulha em URLError.
        except (OSError, HTTPException, ValueError) as error:
            last_error = error
        if attempt < retries:
            time.sleep(0.5 * 2**attempt)
    raise MetUnavailable(str(last_error)) from last_error


def _to_item(data):
    """Normaliza a obra; None se não for de domínio público, não tiver imagem ou vier sem ID."""
    if (not isinstance(data, dict) or not isinstance(data.get("objectID"), int)
            or not data.get("isPublicDomain") or not data.get("primaryImageSmall")):
        return None
    return {
        "external_id": data["objectID"],
        "title": data.get("title") or "Sem título",
        "artist": data.get("artistDisplayName") or "",
        "date": data.get("objectDate") or "",
        "medium": data.get("medium") or "",
        "dimensions": data.get("dimensions") or "",
        "culture": data.get("culture") or "",
        "department": data.get("department") or "",
        "image_url": data["primaryImageSmall"],
        "source_url": data.get("objectURL") or "",
    }


def get_object(object_id, retries=RETRIES):
    """Obra normalizada, ou None se não existir ou não puder virar lote."""
    key = f"met:object:{int(object_id)}"
    cached = cache.get(key)
    if cached is not None:
        return None if cached == _NOT_LISTABLE else cached
    item = _to_item(_get_json(f"{BASE_URL}/v1/objects/{int(object_id)}", retries=retries))
    cache.set(key, item or _NOT_LISTABLE, OBJECT_TTL)
    return item


def _get_object_or_failed(object_id):
    try:
        # Sem novas tentativas: são até 12 obras por página e o tempo total precisa caber no limite.
        return get_object(object_id, retries=0)
    except (MetUnavailable, ValueError, TypeError):  # ValueError/TypeError: ID que não é número
        return _FAILED  # uma obra com falha não derruba a página inteira


def search(query, offset=0):
    key = "met:search:" + hashlib.sha256(f"{query.lower()}|{offset}".encode()).hexdigest()
    cached = cache.get(key)
    if cached is not None:
        return cached
    params = urlencode({"q": query, "hasImages": "true", "offset": offset, "limit": PAGE_SIZE})
    data = _get_json(f"{BASE_URL}/v1.1/search?{params}")
    if not isinstance(data, dict) or "total" not in data:
        # Endpoint movido (404) ou formato novo: é falha do museu, não "nenhum resultado".
        raise MetUnavailable("resposta inesperada da busca")
    with ThreadPoolExecutor(max_workers=6) as pool:
        results = list(pool.map(_get_object_or_failed, data.get("objectIDs") or []))
    total = data["total"] or 0
    page = {
        "total": total,
        "items": [item for item in results if item and item is not _FAILED],
        "next_offset": offset + PAGE_SIZE if offset + PAGE_SIZE < total else None,
    }
    if _FAILED not in results:  # página incompleta não fica 10 min no cache
        cache.set(key, page, SEARCH_TTL)
    return page
