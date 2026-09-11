# Backend image. Referenced by docker-compose.yml.
#
# Note what is absent: no model provider key, no outbound network allowance beyond the app and data
# networks. The agent runtime that arrives in Phase 3 gets its own image precisely so that this one
# never needs those things (ADR-0002).

FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /srv

RUN apt-get update \
 && apt-get install -y --no-install-recommends curl \
 && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml ./
RUN pip install --upgrade pip && pip install .

COPY . .

ENV PYTHONPATH=/srv

# Checkpoint 4 replaces this with uvicorn once routers exist. Until then the container is useful for
# running migrations and tests, not for serving traffic.
CMD ["python", "-c", "print('AI WorkOS backend image. No HTTP surface until Checkpoint 4.')"]
