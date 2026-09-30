# ADR 0001 — A pure, deterministic decision engine with fixed precedence and no score

**Status:** accepted

**Context.** Agents act on probabilistic reasoning. Authority over money must not be probabilistic, must be explainable,
and must be reproducible long after the fact.

**Decision.** `evaluate(contract, evidence_set, evaluation_time, engine_version)` is a pure function: no clock, network,
database, randomness or model call. Outcomes follow a fixed precedence (ABSTAIN → REFRESH → ESCALATE → DENY →
AUTHORIZE) with machine reason codes; explanations are templates over those codes and the exact structured values.
There is no aggregate score, so a failed mandatory condition cannot be averaged away.

**Consequences.** Decisions replay byte-for-byte from snapshots (INV-13); the 50-case benchmark is hermetic; an LLM can
draft a contract but can never influence an outcome (INV-11). New semantics require a new engine version.
