# sufficia (API)

FastAPI service implementing SUFFICIA's evidence-bound execution: intent compiler, evidence acquisition with x402
payments, the deterministic Decision Engine, the Monad/EVM gateway for `EvidenceGate`, receipts, replay and the Demo Lab.

```bash
pip install -e ".[dev]"
python -m uvicorn sufficia.api.app:app --port 8000     # docs at /docs
python -m pytest -q                                     # unit, property, adversarial, EVM, API tests (live tests excluded)
python -m sufficia.benchmark                            # 50-case benchmark -> docs/evidence
python -m sufficia.evidence_pack                        # full evidence pack from real runs
python -m alembic upgrade head                          # PostgreSQL / SQLite schema
python scripts/deploy.py --network monad-testnet        # deploy the contract suite
```

Layout: `sufficia/{domain,canonical,decision_engine,intent,evidence,payments,execution,blockchain,services,api,db,benchmark,devtools}`,
tests in `tests/{engine,intent,evidence,x402,contracts,blockchain,api,live}`. See the repository README and `docs/`.
