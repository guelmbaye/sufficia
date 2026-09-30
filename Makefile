# SUFFICIA — one-command developer & judge experience (Document 06 §112).
#   make install   install Python + contract toolchains
#   make verify    contracts build + all backend tests + benchmark 50/50 + integration + fixture/schema gates
#   make api       run the API on http://127.0.0.1:8000 (docs at /docs)
#   make evidence  regenerate docs/evidence/* from real runs

PY      ?= python3
API_DIR := apps/api
export PYTHONPATH := $(CURDIR)/$(API_DIR)

.PHONY: install install-py install-contracts contracts contracts-check test test-contracts benchmark integration \
        fixtures-check schemas schemas-check verify api demo evidence deploy-local deploy-testnet migrate live clean docker

install: install-py install-contracts

install-py:
	$(PY) -m pip install -e "$(API_DIR)[dev]" -e packages/sdk/python

install-contracts:
	cd contracts && npm ci

contracts:
	cd contracts && npm run build

contracts-check:
	cd contracts && npm run check:foundry

test:
	cd $(API_DIR) && $(PY) -m pytest -q -p no:cacheprovider

test-contracts:
	cd $(API_DIR) && $(PY) -m pytest -q -p no:cacheprovider tests/contracts tests/blockchain

benchmark:
	cd $(API_DIR) && $(PY) -m sufficia.benchmark

integration:
	$(PY) -m pytest -q -p no:cacheprovider tests/integration

fixtures-check:
	$(PY) tests/benchmark/generate_fixtures.py --check

schemas:
	$(PY) $(API_DIR)/scripts/export_schemas.py

schemas-check:
	$(PY) $(API_DIR)/scripts/export_schemas.py --check

verify: contracts contracts-check fixtures-check schemas-check test benchmark integration
	@echo "SUFFICIA verify: OK"

api:
	cd $(API_DIR) && $(PY) -m uvicorn sufficia.api.app:app --host 127.0.0.1 --port 8000

demo:
	curl -s -X POST http://127.0.0.1:8000/api/v1/demo/scenarios/grand-prize-v1/run | $(PY) -m json.tool

evidence:
	cd $(API_DIR) && $(PY) -m sufficia.evidence_pack --out ../../docs/evidence

migrate:
	cd $(API_DIR) && $(PY) -m alembic upgrade head

deploy-local:
	cd $(API_DIR) && $(PY) scripts/deploy.py --network local

deploy-testnet:
	cd $(API_DIR) && $(PY) scripts/deploy.py --network monad-testnet

live:
	cd $(API_DIR) && SUFFICIA_LIVE=1 $(PY) -m pytest -q -m live tests/live

docker:
	docker compose up --build

clean:
	find . -name __pycache__ -type d -prune -exec rm -rf {} + ; rm -rf $(API_DIR)/.pytest_cache $(API_DIR)/.hypothesis $(API_DIR)/sufficia.db*
