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

# One image, two processes (ADR-0044). The default is the HTTP surface; the worker service in
# docker-compose.yml overrides the command and runs the queue loop from the same build, because a
# handler that executes an approved action must be the same code the API validated it against.
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
