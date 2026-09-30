# SUFFICIA SDK

One clean interface (Document 02 §40) — not a production SDK suite.

```python
from sufficia_sdk import EvidenceGate, SufficiaError

gate = EvidenceGate("http://localhost:8000")
decision = gate.evaluate("Buy up to 100 USDC of TOKEN-X only if smart-money inflows exceed $1M and liquidity exceeds $500K.")
if decision.state == "AUTHORIZE":
    execution = gate.execute(decision)          # authorize + protected execution on the EvidenceGate
    print(gate.replay(execution["receipt_id"])["result"])   # MATCH
else:
    print(decision.state, decision.summary)     # money did not move; gate.execute() would raise 403
```

Install: `pip install -e packages/sdk/python`. A TypeScript client ships with the web app (`apps/web`).
