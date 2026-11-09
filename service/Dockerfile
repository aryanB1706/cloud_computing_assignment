# CPU-only inference image. Model weights are NOT in the image; they are
# downloaded from SeaweedFS at start-up.
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

COPY service/requirements.txt /app/service/requirements.txt
RUN pip install --no-cache-dir -r /app/service/requirements.txt

COPY service/ /app/service/
COPY worker/storage.py /app/worker/storage.py
COPY worker/__init__.py /app/worker/__init__.py

RUN useradd --create-home --uid 10001 app \
    && chown -R app:app /app
USER 10001

EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=3s --start-period=60s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/healthz', timeout=2)"

CMD ["uvicorn", "service.app:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
