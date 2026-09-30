# ADR 0002 — Exact decimals and an injective canonical encoding

**Status:** accepted

**Context.** Hashes bind authority to evidence. Floats, 28-digit decimal contexts, case folding and lenient parsing
all create two different inputs with one commitment — or one input with two readings after a database round trip.

**Decision.** All authority numbers are `Decimal`; canonical JSON emits exact plain-notation number tokens (tagged
scientific form for extreme magnitudes, tagged non-finite values), keeps strings byte-exact, and sorts keys. Evidence
values are bounded (|v| ≤ 1e18, ≤ 30 fractional digits) and the engine computes in a 200-digit context, so every
comparison is exact. Timestamps are datetimes only when unambiguous (ISO-8601 with offset). The HTTP API serializes
Decimals as strings and integers above 2^53 as strings.

**Consequences.** Findings F1–F3 and F10 are closed; replays cannot diverge; clients must not use `parseFloat` for
comparisons.
