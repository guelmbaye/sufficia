"""Deterministic parser: table-driven phrasings (EN + FR), money formats, operators, claim synonyms,
freshness / source-count / conflict policies, and unparsed fragments (never silently dropped)."""

from __future__ import annotations

from decimal import Decimal

import pytest
from hypothesis import given, settings as hsettings, strategies as st

from intent_helpers import GRAND_PRIZE, GRAND_PRIZE_FR, conditions_of
from sufficia.domain.claims import CLAIMS
from sufficia.domain.enums import ActionType, ConflictPolicy
from sufficia.intent import parse_intent

IN, LIQ, PRICE = "smart_money_inflow_24h", "available_liquidity", "asset_price"
TKX = ("BUY", "TOKEN-X", "100", "USDC")


def C(claim: str, op: str, threshold: str | None = None, age: int | None = None):
    return (claim, op, threshold, age)


# (text, (action, asset, amount, currency), conditions, extras)
# extras: max_age / min_evidence / conflict / unparsed / language / tolerance / budget
CASES = [
    # ---- the Grand Prize sentence and close variants --------------------------------------------------
    ("grand-prize", GRAND_PRIZE, TKX, [C(IN, ">", "1000000"), C(LIQ, ">", "500000")], {}),
    ("doc02-s24", "Buy up to 100 USDC of TOKEN-X if inflows exceed $1M and liquidity exceeds $500K.", TKX,
     [C(IN, ">", "1000000"), C(LIQ, ">", "500000")], {}),
    ("lowercase-terse", "buy token-x with 100 usdc if inflows > 1m and liquidity > 500k", TKX,
     [C(IN, ">", "1000000"), C(LIQ, ">", "500000")], {}),
    ("dollar-amount-greater-than", "Buy $100 of TOKEN-X when smart money inflows are greater than $1.5M and pool liquidity is more than $500,000",
     TKX, [C(IN, ">", "1500000"), C(LIQ, ">", "500000")], {}),
    ("purchase-max-at-least", "Purchase TOKEN-X, max 100 USDC, if smart money inflow is at least 1M USD", TKX,
     [C(IN, ">=", "1000000")], {}),
    ("spend-on-remains-above", "Spend up to 100 USDC on TOKEN-X if liquidity remains above $500K", TKX,
     [C(LIQ, ">", "500000")], {}),
    ("usd-amount-million-thousand", "Buy 100 USD of TOKEN-X if SM inflows exceed 2.5 million and TVL is over $750 thousand", TKX,
     [C(IN, ">", "2500000"), C(LIQ, ">", "750000")], {}),
    ("weth-netflow-sources-abstain", "Buy up to 100 USDC of WETH if SM netflow > 2.5 million, TVL >= $750 thousand, at least 2 sources, abstain on conflict",
     ("BUY", "WETH", "100", "USDC"), [C(IN, ">", "2500000"), C(LIQ, ">=", "750000")],
     {"min_evidence": 2, "conflict": "ABSTAIN"}),
    ("net-flow-stays-above", "Buy up to 100 USDC of TOKEN-X if net flow is higher than 1,000,000 USD and available liquidity stays above $500k",
     TKX, [C(IN, ">", "1000000"), C(LIQ, ">", "500000")], {}),
    ("netflow-exceeds", "Buy up to 100 USDC of TOKEN-X if smart money netflow exceeds $1M", TKX, [C(IN, ">", "1000000")], {}),
    ("24h-inflows", "Buy up to 100 USDC of TOKEN-X if 24h smart money inflows exceed $1M", TKX, [C(IN, ">", "1000000")], {}),
    ("inflows-are-above", "Buy up to 100 USDC of TOKEN-X if inflows are above $1M", TKX, [C(IN, ">", "1000000")], {}),
    ("negative-inflow-threshold", "Buy up to 100 USDC of TOKEN-X if smart money inflows are above -$100K", TKX,
     [C(IN, ">", "-100000")], {}),
    ("billion", "Buy up to 100 USDC of TOKEN-X if liquidity exceeds 1.5M USD and inflows exceed $2B", TKX,
     [C(LIQ, ">", "1500000"), C(IN, ">", "2000000000")], {}),
    # ---- operators ------------------------------------------------------------------------------------
    ("at-least-and-price-below", "Buy up to 100 USDC of TOKEN-X if liquidity is at least $500,000 and price below $0.50", TKX,
     [C(LIQ, ">=", "500000"), C(PRICE, "<", "0.5")], {}),
    ("no-less-than", "Buy up to 100 USDC of TOKEN-X if liquidity is no less than $500K", TKX, [C(LIQ, ">=", "500000")], {}),
    ("minimum-of", "Buy up to 100 USDC of TOKEN-X if liquidity is a minimum of $500K", TKX, [C(LIQ, ">=", "500000")], {}),
    ("gte-words", "Buy up to 100 USDC of TOKEN-X if liquidity is greater than or equal to $500K", TKX, [C(LIQ, ">=", "500000")], {}),
    ("or-more", "Buy up to 100 USDC of TOKEN-X if liquidity is $500K or more", TKX, [C(LIQ, ">=", "500000")], {}),
    ("plus-suffix", "Buy 100 USDC of TOKEN-X if liquidity is $500K+", TKX, [C(LIQ, ">=", "500000")], {}),
    ("at-most", "Buy up to 100 USDC of TOKEN-X if price is at most $0.75", TKX, [C(PRICE, "<=", "0.75")], {}),
    ("no-more-than", "Buy up to 100 USDC of TOKEN-X if price is no more than $0.75", TKX, [C(PRICE, "<=", "0.75")], {}),
    ("maximum-of", "Buy up to 100 USDC of TOKEN-X if price is a maximum of $0.5", TKX, [C(PRICE, "<=", "0.5")], {}),
    ("lte-words", "Buy up to 100 USDC of TOKEN-X if price is less than or equal to $0.5", TKX, [C(PRICE, "<=", "0.5")], {}),
    ("lower-than-cents", "Buy up to 100 USDC of TOKEN-X if price is lower than 50 cents", TKX, [C(PRICE, "<", "0.5")], {}),
    ("higher-than", "Buy up to 100 USDC of TOKEN-X if liquidity is higher than $500K", TKX, [C(LIQ, ">", "500000")], {}),
    ("symbols", "Buy up to 100 USDC of TOKEN-X if liquidity >= 500000 USD and price <= 0.5 USD", TKX,
     [C(LIQ, ">=", "500000"), C(PRICE, "<=", "0.5")], {}),
    ("symbols-strict", "Buy up to 100 USDC of TOKEN-X if liquidity > $500K and price < $0.50", TKX,
     [C(LIQ, ">", "500000"), C(PRICE, "<", "0.5")], {}),
    ("bare-number", "Buy up to 100 USDC of TOKEN-X if price < 0.5", TKX, [C(PRICE, "<", "0.5")], {}),
    ("price-exists", "Buy up to 100 USDC of TOKEN-X if price exists", TKX, [C(PRICE, "EXISTS")], {}),
    ("price-must-exist", "Buy up to 100 USDC of TOKEN-X if liquidity exceeds $500K; price must exist", TKX,
     [C(LIQ, ">", "500000"), C(PRICE, "EXISTS")], {}),
    ("between", "Buy up to 100 USDC of TOKEN-X if price is between $0.40 and $0.60", TKX,
     [C(PRICE, ">=", "0.4"), C(PRICE, "<=", "0.6")], {}),
    ("same-claim-twice", "Buy up to 100 USDC of TOKEN-X if liquidity exceeds $500K and liquidity is below $5M", TKX,
     [C(LIQ, ">", "500000"), C(LIQ, "<", "5000000")], {}),
    ("shared-comparison", "Buy up to 100 USDC of TOKEN-X if smart money inflows and liquidity both exceed $1M", TKX,
     [C(IN, ">", "1000000"), C(LIQ, ">", "1000000")], {}),
    ("negated-fall-below", "Buy up to 100 USDC of TOKEN-X if liquidity does not fall below $500K", TKX,
     [C(LIQ, ">=", "500000")], {}),
    ("negated-not-above", "Buy up to 100 USDC of TOKEN-X if price is not above $1", TKX, [C(PRICE, "<=", "1")], {}),
    ("unless", "Buy up to 100 USDC of TOKEN-X unless price is above $2", TKX, [C(PRICE, "<=", "2")], {}),
    ("condition-first", "If liquidity exceeds $500K, buy up to 100 USDC of TOKEN-X", TKX, [C(LIQ, ">", "500000")], {}),
    ("asset-qualified-price", "Buy up to 100 USDC of TOKEN-X if TOKEN-X price is below $0.5", TKX, [C(PRICE, "<", "0.5")], {}),
    ("price-of-asset", "Buy up to 100 USDC of TOKEN-X if the price of TOKEN-X is below $0.5", TKX, [C(PRICE, "<", "0.5")], {}),
    # ---- claim synonyms -------------------------------------------------------------------------------
    ("tvl", "Buy up to 100 USDC of TOKEN-X if TVL exceeds $500K", TKX, [C(LIQ, ">", "500000")], {}),
    ("pool-liquidity-over", "Buy up to 100 USDC of TOKEN-X if pool liquidity is over $500K", TKX, [C(LIQ, ">", "500000")], {}),
    # ---- other actions ----------------------------------------------------------------------------------
    ("sell-evidence-age", "Sell up to $50 of MON if price is above $2.5 and evidence under 15 seconds",
     ("SELL", "MON", "50", "USDC"), [C(PRICE, ">", "2.5")], {"max_age": 15}),
    ("sell-amount-of", "Sell 50 USDC of MON if price is below $2", ("SELL", "MON", "50", "USDC"), [C(PRICE, "<", "2")], {}),
    ("buy-asset-for-amount", "Buy MON for 100 USDC if price is under $3", ("BUY", "MON", "100", "USDC"), [C(PRICE, "<", "3")], {}),
    ("transfer", "Transfer 25 USDC if liquidity exceeds $500K", ("TRANSFER", "USDC", "25", "USDC"), [C(LIQ, ">", "500000")], {}),
    # ---- freshness, sources, conflicts, tolerance, budget -------------------------------------------------
    ("per-condition-age", "Buy up to 100 USDC of TOKEN-X if smart money inflows exceed $1M (max age 15s) and liquidity exceeds $500K",
     TKX, [C(IN, ">", "1000000", 15), C(LIQ, ">", "500000")], {}),
    ("evidence-age-under", "Buy up to 100 USDC of TOKEN-X if inflows exceed $1M and liquidity exceeds $500K, evidence age under 20 seconds",
     TKX, [C(IN, ">", "1000000"), C(LIQ, ">", "500000")], {"max_age": 20}),
    ("evidence-no-older-than-sec", "Buy up to 100 USDC of TOKEN-X if liquidity exceeds $500K with evidence no older than 10 sec", TKX,
     [C(LIQ, ">", "500000")], {"max_age": 10}),
    ("evidence-within-s", "Buy up to 100 USDC of TOKEN-X if liquidity exceeds $500K, evidence within 30 s", TKX,
     [C(LIQ, ">", "500000")], {"max_age": 30}),
    ("data-no-older-than", "Buy up to 100 USDC of TOKEN-X if liquidity exceeds $500K and inflows exceed $1M with data no older than 45 seconds",
     TKX, [C(LIQ, ">", "500000"), C(IN, ">", "1000000")], {"max_age": 45}),
    ("sentences-policy", "Buy up to 100 USDC of TOKEN-X if liquidity exceeds $500K. Evidence must be less than 30 seconds old. At least 3 sources. Escalate conflicts.",
     TKX, [C(LIQ, ">", "500000")], {"max_age": 30, "min_evidence": 3, "conflict": "ESCALATE"}),
    ("abstain-on-conflict", "Buy up to 100 USDC of TOKEN-X if liquidity exceeds $500K, abstain on conflict", TKX,
     [C(LIQ, ">", "500000")], {"conflict": "ABSTAIN"}),
    ("word-number-sources", "Buy up to 100 USDC of TOKEN-X if liquidity exceeds $500K, at least three sources", TKX,
     [C(LIQ, ">", "500000")], {"min_evidence": 3}),
    ("tolerance", "Buy up to 100 USDC of TOKEN-X if liquidity exceeds $500K and sources agree within 5%", TKX,
     [C(LIQ, ">", "500000")], {"tolerance": "0.05"}),
    ("budget", "Buy up to 100 USDC of TOKEN-X if liquidity exceeds $500K and evidence budget $0.10", TKX,
     [C(LIQ, ">", "500000")], {"budget": "0.1"}),
    # ---- French ------------------------------------------------------------------------------------------
    ("fr-grand-prize", GRAND_PRIZE_FR, TKX, [C(IN, ">", "1000000"), C(LIQ, ">", "500000")], {"language": "fr"}),
    ("fr-sell-superieur-inferieur", "Vends jusqu'à 50 USDC de MON si le prix est supérieur à 2,5 $ et la liquidité est inférieure à 1 million de dollars",
     ("SELL", "MON", "50", "USDC"), [C(PRICE, ">", "2.5"), C(LIQ, "<", "1000000")], {"language": "fr"}),
    ("fr-au-dessus-en-dessous-age", "Achète 100 USDC de TOKEN-X si la liquidité est au-dessus de 500 000 $ et le prix en dessous de 0,50 $, avec des preuves de moins de 20 secondes",
     TKX, [C(LIQ, ">", "500000"), C(PRICE, "<", "0.5")], {"max_age": 20, "language": "fr"}),
    ("fr-au-moins", "Acheter jusqu'à 100 USDC de TOKEN-X uniquement si la liquidité est d'au moins 500K$", TKX,
     [C(LIQ, ">=", "500000")], {"language": "fr"}),
    ("fr-sources", "Achète jusqu'à 100 USDC de TOKEN-X si le prix est inférieur à 0,5 $ avec au moins 2 sources", TKX,
     [C(PRICE, "<", "0.5")], {"min_evidence": 2, "language": "fr"}),
    ("fr-millions-de-dollars", "Achète jusqu'à 100 USDC de WETH si les entrées smart money sont supérieures à 2 millions de dollars",
     ("BUY", "WETH", "100", "USDC"), [C(IN, ">", "2000000")], {"language": "fr"}),
    ("fr-abstain", "Achète jusqu'à 100 USDC de TOKEN-X si la liquidité dépasse 500K$, s'abstenir en cas de conflit", TKX,
     [C(LIQ, ">", "500000")], {"conflict": "ABSTAIN", "language": "fr"}),
    ("fr-superieure-ou-egale", "Achète jusqu'à 100 USDC de TOKEN-X si la liquidité est supérieure ou égale à 500 000 $", TKX,
     [C(LIQ, ">=", "500000")], {"language": "fr"}),
    ("fr-negation", "Achète jusqu'à 100 USDC de TOKEN-X si la liquidité n'est pas inférieure à 500K$", TKX,
     [C(LIQ, ">=", "500000")], {"language": "fr"}),
    ("fr-vends-au-dessus", "Vends jusqu'à 50 USDC de MON si le prix est au-dessus de 3 $", ("SELL", "MON", "50", "USDC"),
     [C(PRICE, ">", "3")], {"language": "fr"}),
]


@pytest.mark.parametrize("text,head,conditions,extras", [pytest.param(*c[1:], id=c[0]) for c in CASES])
def test_phrasing_table(text, head, conditions, extras):
    p = parse_intent(text)
    action, asset, amount, currency = head
    assert (p.action.value if p.action else None, p.asset, str(p.max_amount), p.currency) == (action, asset, amount, currency)
    assert conditions_of(p) == conditions
    assert p.max_age_seconds == extras.get("max_age")
    assert p.minimum_required_evidence == extras.get("min_evidence")
    assert (p.conflict_policy.value if p.conflict_policy else None) == extras.get("conflict")
    assert (str(p.conflict_tolerance) if p.conflict_tolerance is not None else None) == extras.get("tolerance")
    assert (str(p.evidence_budget_usdc) if p.evidence_budget_usdc is not None else None) == extras.get("budget")
    assert p.unparsed == extras.get("unparsed", [])
    assert p.language == extras.get("language", "en")
    assert p.missing == []


def test_table_has_at_least_forty_phrasings():
    assert len(CASES) >= 40


def test_english_and_french_grand_prize_parse_identically():
    en, fr = parse_intent(GRAND_PRIZE), parse_intent(GRAND_PRIZE_FR)
    assert conditions_of(en) == conditions_of(fr)
    assert (en.action, en.asset, en.max_amount, en.currency) == (fr.action, fr.asset, fr.max_amount, fr.currency)


# ---- money formats ----------------------------------------------------------------------------------------

MONEY = [
    ("$1M", "1000000"),
    ("$1.5M", "1500000"),
    ("$500K", "500000"),
    ("$500k", "500000"),
    ("$500,000", "500000"),
    ("1,000,000 USD", "1000000"),
    ("2.5 million", "2500000"),
    ("$750 thousand", "750000"),
    ("500K$", "500000"),
    ("1M$", "1000000"),
    ("500 000 $", "500000"),
    ("1.5M USD", "1500000"),
    ("$2B", "2000000000"),
    ("2 millions de dollars", "2000000"),
    ("500000", "500000"),
    ("$1,250,000.50", "1250000.5"),
]


@pytest.mark.parametrize("money,expected", MONEY)
def test_money_formats(money, expected):
    p = parse_intent(f"Buy up to 100 USDC of TOKEN-X if liquidity exceeds {money}")
    assert conditions_of(p) == [(LIQ, ">", expected, None)]
    assert p.unparsed == []
    assert "E" not in str(p.conditions[0].threshold)  # plain decimals, no exponent


@pytest.mark.parametrize(
    "price,expected",
    [("$0.50", "0.5"), ("0,50 $", "0.5"), ("50 cents", "0.5"), ("$.25", "0.25"), ("$2.5", "2.5"), ("0.0001 USD", "0.0001")],
)
def test_small_prices(price, expected):
    p = parse_intent(f"Buy up to 100 USDC of TOKEN-X if price is below {price}")
    assert conditions_of(p) == [(PRICE, "<", expected, None)]


@pytest.mark.parametrize(
    "amount_phrase",
    ["up to 100 USDC of TOKEN-X", "100 USDC of TOKEN-X", "$100 of TOKEN-X", "100 USD of TOKEN-X", "up to 100 USD worth of TOKEN-X",
     "TOKEN-X for 100 USDC", "TOKEN-X with up to $100", "TOKEN-X (max 100 USDC)"],
)
def test_amount_formats_map_to_usdc(amount_phrase):
    p = parse_intent(f"Buy {amount_phrase} if liquidity exceeds $500K")
    assert (p.action, p.asset, p.max_amount, p.currency) == (ActionType.BUY, "TOKEN-X", Decimal("100"), "USDC")
    assert p.unparsed == []


# ---- operators ---------------------------------------------------------------------------------------------

OPERATORS = [
    ("exceeds", ">"), ("exceed", ">"), ("is above", ">"), ("is over", ">"), ("is greater than", ">"), ("is more than", ">"),
    ("is higher than", ">"), (">", ">"), ("remains above", ">"), ("stays above", ">"),
    ("is at least", ">="), ("is no less than", ">="), ("is a minimum of", ">="), (">=", ">="),
    ("is below", "<"), ("is under", "<"), ("is less than", "<"), ("is lower than", "<"), ("<", "<"),
    ("is at most", "<="), ("is no more than", "<="), ("is a maximum of", "<="), ("<=", "<="),
]


@pytest.mark.parametrize("phrase,op", OPERATORS)
def test_operator_phrases(phrase, op):
    p = parse_intent(f"Buy up to 100 USDC of TOKEN-X if liquidity {phrase} $500K")
    assert conditions_of(p) == [(LIQ, op, "500000", None)]
    assert p.unparsed == []


FR_OPERATORS = [
    ("dépasse", ">"), ("dépassent", ">"), ("est supérieure à", ">"), ("est au-dessus de", ">"),
    ("est inférieure à", "<"), ("est en dessous de", "<"), ("est au moins", ">="), ("est au plus", "<="),
]


@pytest.mark.parametrize("phrase,op", FR_OPERATORS)
def test_french_operator_phrases(phrase, op):
    p = parse_intent(f"Achète jusqu'à 100 USDC de TOKEN-X seulement si la liquidité {phrase} 500K$")
    assert conditions_of(p) == [(LIQ, op, "500000", None)]
    assert p.unparsed == []


# ---- claim synonyms ----------------------------------------------------------------------------------------

SYNONYMS = [
    ("smart money inflows", IN), ("smart-money inflows", IN), ("smart money inflow", IN), ("SM inflows", IN), ("SM inflow", IN),
    ("SM netflow", IN), ("netflow", IN), ("net flow", IN), ("smart money netflow", IN), ("inflows", IN), ("net inflows", IN),
    ("liquidity", LIQ), ("pool liquidity", LIQ), ("available liquidity", LIQ), ("TVL", LIQ),
    ("price", PRICE), ("spot price", PRICE),
]


@pytest.mark.parametrize("phrase,claim", SYNONYMS)
def test_claim_synonyms(phrase, claim):
    p = parse_intent(f"Buy up to 100 USDC of TOKEN-X if {phrase} exceeds $1M")
    assert conditions_of(p) == [(claim, ">", "1000000", None)]


@pytest.mark.parametrize(
    "phrase,claim",
    [("les entrées smart money", IN), ("les flux entrants", IN), ("la liquidité", LIQ), ("la liquidité du pool", LIQ), ("le prix", PRICE)],
)
def test_french_claim_synonyms(phrase, claim):
    p = parse_intent(f"Achète jusqu'à 100 USDC de TOKEN-X si {phrase} dépasse 1M$")
    assert conditions_of(p) == [(claim, ">", "1000000", None)]


def test_every_parsed_claim_is_registered():
    for case in CASES:
        for c in parse_intent(case[1]).conditions:
            assert c.claim in CLAIMS


# ---- assets & actions ------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "text,action,asset",
    [
        ("Buy up to 100 USDC of MON if price exists", "BUY", "MON"),
        ("buy up to 100 usdc of weth if price exists", "BUY", "WETH"),
        ("Buy up to 100 USDC of token x if price exists", "BUY", "TOKEN-X"),
        ("Acheter jusqu'à 100 USDC de TOKEN-X si le prix existe", "BUY", "TOKEN-X"),
        ("Vendre jusqu'à 100 USDC de MON si le prix existe", "SELL", "MON"),
        ("Sell up to 100 USDC of wBTC if price exists", "SELL", "WBTC"),
        ("Transfer up to 20 USDC if price exists", "TRANSFER", "USDC"),
    ],
)
def test_actions_and_assets(text, action, asset):
    p = parse_intent(text)
    assert (p.action.value, p.asset) == (action, asset)


# ---- unparsed: never silently dropped ----------------------------------------------------------------------------

UNKNOWN_CONSTRAINTS = [
    "volume exceeds $2M",
    "holders exceed 10,000",
    "market cap is above $10M",
    "funding rate is below 0.01%",
    "open interest is over $5M",
    "RSI is under 30",
    "whales are accumulating",
    "gas is below 50 gwei",
    "slippage is under 1%",
    "MON price is above $3",
]


@pytest.mark.parametrize("fragment", UNKNOWN_CONSTRAINTS)
def test_unknown_constraints_are_reported_not_dropped(fragment):
    p = parse_intent(f"Buy up to 100 USDC of TOKEN-X if liquidity exceeds $500K and {fragment}")
    assert conditions_of(p) == [(LIQ, ">", "500000", None)]  # nothing invented
    assert p.unparsed == [fragment]
    assert not p.complete


@pytest.mark.parametrize(
    "text,unparsed,expect_conditions,warns",
    [
        ("Buy up to 100 USDC of TOKEN-X if liquidity exceeds $500K and the moon is full", ["moon is full"],
         [(LIQ, ">", "500000", None)], False),
        # a condition on another asset is not a condition on the traded asset
        ("Buy up to 100 USDC of TOKEN-X if WETH price is above $3000", ["WETH price is above $3000"], [], False),
        # only a 24h inflow claim exists: a 7-day window is not silently turned into 24h
        ("Buy up to 100 USDC of TOKEN-X if inflows over the last 7 days exceed $1M",
         ["inflows over the last 7 days exceed $1M"], [], True),
        # "or" cannot be represented: both conditions compiled (stricter), and the fragment is shown
        ("Buy up to 100 USDC of TOKEN-X if inflows exceed $1M or liquidity exceeds $500K",
         ["inflows exceed $1M or liquidity exceeds $500K"], [(IN, ">", "1000000", None), (LIQ, ">", "500000", None)], True),
        ("Buy up to 100 USDC of TOKEN-X if liquidity exceeds 5.5% of supply", ["liquidity exceeds 5.5% of supply"], [], False),
        ("Buy up to 100 USDC of TOKEN-X if liquidity exceeds 500K EUR", ["liquidity exceeds 500K EUR"], [], False),
        # independence is not enforceable by the contract: the count is kept, "independent" is reported
        ("Buy up to 100 USDC of TOKEN-X if liquidity exceeds $500K from 3 independent sources", ["independent"],
         [(LIQ, ">", "500000", None)], True),
        # a second, different action is not silently merged
        ("Buy up to 100 USDC of TOKEN-X if liquidity exceeds $500K and sell MON", ["sell MON"], [(LIQ, ">", "500000", None)], False),
        # a partially negated range: the understood half is compiled, the rest is shown
        ("Buy up to 100 USDC of TOKEN-X if price is below $1 but not below $0.2", ["not below $0.2"], [(PRICE, "<", "1", None)], False),
        ("Transfer 25 USDC to 0x000000000000000000000000000000000000dEaD if price exists",
         ["0x000000000000000000000000000000000000dEaD"], [(PRICE, "EXISTS", None, None)], False),
    ],
)
def test_unparsed_fragments(text, unparsed, expect_conditions, warns):
    p = parse_intent(text)
    assert p.unparsed == unparsed
    assert conditions_of(p) == expect_conditions
    assert bool(p.warnings) is warns


def test_independent_sources_keeps_minimum_count():
    p = parse_intent("Buy up to 100 USDC of TOKEN-X if liquidity exceeds $500K from 3 independent sources")
    assert p.minimum_required_evidence == 3


# ---- missing parts ------------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "text,missing_prefixes",
    [
        ("Buy TOKEN-X", ["maximum amount"]),
        ("Buy up to 100 USDC if liquidity exceeds $500K", ["asset"]),
        ("liquidity exceeds $500K", ["action", "asset", "maximum amount"]),
        ("hello there", ["action", "asset", "maximum amount"]),
        ("", ["action", "asset", "maximum amount"]),
    ],
)
def test_missing_parts_are_listed(text, missing_prefixes):
    p = parse_intent(text)
    assert [m.split(" (")[0] for m in p.missing] == missing_prefixes


def test_non_usd_currency_is_kept_not_converted():
    p = parse_intent("Buy up to 100 EUR of TOKEN-X if liquidity > 500K")
    assert (p.max_amount, p.currency) == (Decimal("100"), "EUR")


def test_rejects_non_string():
    with pytest.raises(TypeError):
        parse_intent(None)  # type: ignore[arg-type]


def test_parse_is_deterministic():
    for case in CASES:
        assert parse_intent(case[1]) == parse_intent(case[1])


def test_conflicting_policies_warn_and_keep_first():
    p = parse_intent("Buy up to 100 USDC of TOKEN-X if liquidity exceeds $500K, abstain on conflict, escalate conflicts")
    assert p.conflict_policy == ConflictPolicy.ABSTAIN
    assert p.warnings


def test_two_amount_caps_keep_the_smaller():
    p = parse_intent("Buy up to 100 USDC of TOKEN-X if liquidity exceeds $500K, max 80 USDC")
    assert p.max_amount == Decimal("80")
    assert p.warnings


def test_between_on_foreign_asset_is_not_half_compiled():
    p = parse_intent("Buy up to 100 USDC of TOKEN-X if WETH price is between $3000 and $4000")
    assert p.conditions == []
    assert p.unparsed


@hsettings(database=None, max_examples=60, deadline=None)
@given(
    threshold=st.integers(min_value=1, max_value=10**12),
    amount=st.integers(min_value=1, max_value=1000),
    op=st.sampled_from([("exceeds", ">"), ("is at least", ">="), ("is below", "<"), ("is at most", "<=")]),
)
def test_property_integer_thresholds_round_trip(threshold, amount, op):
    phrase, symbol = op
    p = parse_intent(f"Buy up to {amount} USDC of TOKEN-X if liquidity {phrase} ${threshold:,}")
    assert p.max_amount == Decimal(amount)
    assert conditions_of(p) == [(LIQ, symbol, str(threshold), None)]
    assert p.unparsed == []
