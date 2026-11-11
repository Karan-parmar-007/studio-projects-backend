# Two images from one Dockerfile:
#   default (last stage, "api")  studio-backend: FastAPI, no torch
#   --target worker              studio-worker:  + Docling, GLiNER2, CPU torch, models baked in
FROM python:3.12-slim AS builder

WORKDIR /build

RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

COPY requirements.txt .
RUN pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir -r requirements.txt

FROM builder AS worker-builder

COPY requirements-worker.txt .
# CPU wheels only: the CUDA torch build is ~2 GB of nothing useful here.
RUN pip install --no-cache-dir --extra-index-url https://download.pytorch.org/whl/cpu -r requirements-worker.txt

FROM python:3.12-slim AS worker

LABEL org.opencontainers.image.source=https://github.com/Karan-parmar-007/studio-projects-backend
LABEL org.opencontainers.image.description="Studio worker: document ingestion and expiry sweeps"

WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/opt/venv/bin:$PATH" \
    HF_HOME=/opt/models/hf \
    DOCLING_ARTIFACTS_PATH=/opt/models/docling \
    OMP_NUM_THREADS=4

RUN apt-get update \
    && apt-get install -y --no-install-recommends libgl1 libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --create-home --uid 1000 appuser

COPY --from=worker-builder /opt/venv /opt/venv

# Models are fetched at build time so the worker starts offline and fast.
RUN docling-tools models download layout tableformer picture_classifier -o /opt/models/docling \
    && python -c "from gliner2 import GLiNER2; GLiNER2.from_pretrained('fastino/gliner2-base-v1')" \
    && chown -R appuser:appuser /opt/models

COPY --chown=appuser:appuser . .

USER appuser

CMD ["python", "worker.py"]

FROM python:3.12-slim AS api

LABEL org.opencontainers.image.source=https://github.com/Karan-parmar-007/studio-projects-backend
LABEL org.opencontainers.image.description="Studio API for studio.karanparmar.in"

WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/opt/venv/bin:$PATH"

RUN useradd --create-home --uid 1000 appuser

COPY --from=builder /opt/venv /opt/venv
COPY --chown=appuser:appuser . .

RUN chmod +x /app/entrypoint.sh

USER appuser

EXPOSE 8000

ENTRYPOINT ["/app/entrypoint.sh"]
