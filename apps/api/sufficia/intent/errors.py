"""Errors raised while turning a human instruction into an Evidence Contract."""

from __future__ import annotations

EXAMPLE_INTENT = (
    "Buy up to 100 USDC of TOKEN-X only if smart-money inflows exceed $1M and liquidity exceeds $500K."
)


class IntentCompilationError(ValueError):
    """The instruction cannot be compiled into an authoritative Evidence Contract.

    ``missing`` lists the mandatory parts the deterministic compiler could not find (action, asset,
    maximum amount); ``problems`` lists other blocking issues (amount cap, invalid values). The
    message is written for the person who typed the instruction.
    """

    def __init__(
        self,
        *,
        missing: list[str] | None = None,
        problems: list[str] | None = None,
        unparsed: list[str] | None = None,
        code: str = "INTENT_NOT_COMPILABLE",
    ) -> None:
        self.missing = list(missing or [])
        self.problems = list(problems or [])
        self.unparsed = list(unparsed or [])
        self.code = code
        super().__init__(self._message())

    def _message(self) -> str:
        parts: list[str] = []
        if self.missing:
            parts.append("Could not find: " + ", ".join(self.missing) + ".")
        if self.problems:
            parts.append("; ".join(self.problems) + ".")
        if self.unparsed:
            parts.append("Not understood: " + "; ".join(f"'{u}'" for u in self.unparsed) + ".")
        if self.missing:
            parts.append(f"Example: '{EXAMPLE_INTENT}'")
        return " ".join(parts) or "The intent could not be compiled."
