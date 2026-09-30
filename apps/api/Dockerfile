# SUFFICIA API — single process (in-process signer queues and LOCAL_EVM chain are per-process state).
FROM python:3.11-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /srv

# Dependencies first for layer caching
COPY apps/api/pyproject.toml apps/api/README.md /srv/apps/api/
COPY apps/api/sufficia/__init__.py /srv/apps/api/sufficia/__init__.py
RUN pip install --upgrade pip && pip install "/srv/apps/api[local-evm,postgres]" && pip uninstall -y sufficia

COPY apps/api /srv/apps/api
COPY demo /srv/demo
COPY docs/evidence /srv/docs/evidence
RUN pip install --no-deps -e /srv/apps/api \
 && useradd --create-home --uid 10001 sufficia \
 && mkdir -p /data && chown sufficia /data

USER sufficia
WORKDIR /srv/apps/api
ENV DATABASE_URL=sqlite:////data/sufficia.db \
    SCENARIOS_DIR=/srv/demo/scenarios \
    RECORDED_FIXTURES_DIR=/srv/demo/fixtures/recorded \
    EVIDENCE_REPORTS_DIR=/srv/docs/evidence

EXPOSE 8000
HEALTHCHECK --interval=15s --timeout=3s --retries=5 CMD python -c "import urllib.request,sys;sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/health').status==200 else 1)"
CMD ["python", "-m", "uvicorn", "sufficia.api.app:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
