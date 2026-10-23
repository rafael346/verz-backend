FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .
RUN useradd --no-create-home app && chown -R app /app
USER app

EXPOSE 8000
# ASGI: o tempo real (SSE) mantém conexões abertas sem prender uma thread cada; as views síncronas rodam em threads.
# Cache e limites de tentativa ficam no Postgres (D27). --no-proxy-headers: o IP do cliente é decidido
# pelo NUM_PROXIES do DRF, não pelo uvicorn.
CMD ["uvicorn", "config.asgi:application", "--host", "0.0.0.0", "--port", "8000", "--no-proxy-headers", "--no-server-header"]
