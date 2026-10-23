FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .
RUN useradd --no-create-home app && chown -R app /app
USER app

EXPOSE 8000
# Threads: uma requisição lenta (ex.: museu demorando) não trava as demais.
# Um só processo: os limites de tentativa usam cache em memória, compartilhado só entre threads.
CMD ["gunicorn", "config.wsgi:application", "--bind", "0.0.0.0:8000", "--no-control-socket", "--workers", "1", "--threads", "8"]
