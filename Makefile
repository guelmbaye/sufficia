# SUFFICIA — one-command developer & judge experience (Document 06 §112).
#   make install   install Python, contract and web toolchains
#   make verify    contracts + backend tests + benchmark 50/50 + integration + fixture/schema gates + web lint/types/unit/build
#   make e2e       browser end-to-end tests (real API + production web server); writes screenshots A/B/C
#   make api       run the API on http://127.0.0.1:8000 (docs at /docs)
#   make web       run the web app on http://127.0.0.1:3000 (needs `make api` in another terminal)
#   make evidence  regenerate docs/evidence/* from real runs

PY      ?= python3
API_DIR := apps/api
WEB_DIR := apps/web
export PYTHONPATH := $(CURDIR)/$(API_DIR)

.PHONY: install install-py install-contracts contracts contracts-check test test-contracts benchmark integration \
        fixtures-check schemas schemas-check verify api demo evidence deploy-local deploy-testnet migrate live clean docker \
        install-web web web-build web-check e2e screenshots preflight-testnet fund-gate \
        testnet-keys testnet-status testnet-distribute

install: install-py install-contracts install-web

install-py:
	$(PY) -m pip install -e "$(API_DIR)[dev]" -e packages/sdk/python

install-contracts:
	cd contracts && npm ci

install-web:
	cd $(WEB_DIR) && npm ci

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

web-check:
	cd $(WEB_DIR) && npm run lint && npm run typecheck && npm run test

web-build:
	cd $(WEB_DIR) && npm run build

e2e: web-build
	cd $(WEB_DIR) && PYTHON=$(PY) npx playwright test

screenshots: web-build
	cd $(WEB_DIR) && PYTHON=$(PY) npx playwright test e2e/grand-prize.spec.ts

verify: contracts contracts-check fixtures-check schemas-check test benchmark integration web-check web-build
	@echo "SUFFICIA verify: OK"

api:
	cd $(API_DIR) && $(PY) -m uvicorn sufficia.api.app:app --host 127.0.0.1 --port 8000

web:
	cd $(WEB_DIR) && npm run dev

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

testnet-keys:
	cd $(API_DIR) && $(PY) scripts/testnet.py keys

testnet-status:
	cd $(API_DIR) && $(PY) scripts/testnet.py status

testnet-distribute:
	cd $(API_DIR) && $(PY) scripts/testnet.py distribute

preflight-testnet:
	cd $(API_DIR) && $(PY) scripts/preflight.py --network monad-testnet

fund-gate:
	cd $(API_DIR) && $(PY) scripts/fund_gate.py --amount $(or $(AMOUNT),10000)

live:
	cd $(API_DIR) && SUFFICIA_LIVE=1 $(PY) -m pytest -q -m live tests/live

docker:
	docker compose up --build

clean:
	find . -name __pycache__ -type d -prune -exec rm -rf {} + ; rm -rf $(API_DIR)/.pytest_cache $(API_DIR)/.hypothesis $(API_DIR)/sufficia.db* \
	  $(WEB_DIR)/.next $(WEB_DIR)/test-results $(WEB_DIR)/playwright-report $(WEB_DIR)/tsconfig.tsbuildinfo
