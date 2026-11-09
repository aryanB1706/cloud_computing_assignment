FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

COPY worker/requirements.txt /app/worker/requirements.txt
RUN pip install --no-cache-dir -r /app/worker/requirements.txt

COPY worker/ /app/worker/
COPY prompts/ /app/prompts/

RUN useradd --create-home --uid 10001 worker \
    && chown -R worker:worker /app
USER worker

ENTRYPOINT ["python", "-m", "worker.worker"]
