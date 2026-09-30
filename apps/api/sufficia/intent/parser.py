"""Deterministic, rule-based intent compiler.

This parser is the default compiler and the *reference* against which any LLM draft is checked
(``sufficia.intent.preservation``). It is intentionally conservative:

* it only recognises a closed vocabulary (actions, the canonical claims, operators, money formats,
  freshness / source-count / conflict / budget policies) in English and French;
* every character of the instruction is either *consumed* by a recognised construct or ends up in
  ``ParsedIntent.unparsed`` (unless it is pure filler such as "please", "only if", "and") — a
  constraint is never silently dropped;
* when a construct is only partly understood (a negation it cannot represent, a condition on
  another asset, a non-24h inflow window, an ``or`` between conditions) the fragment is reported
  and, where it would otherwise be misread, the condition is left out rather than guessed.

Matching runs on a *folded* copy of the text (lower-case, accents stripped, typographic quotes and
dashes normalised) that has exactly the same length as the original, so every span maps back to
the user's own words for display.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from decimal import Decimal
from typing import Optional

from ..canonical.canonicalize import format_decimal
from ..domain.claims import get_claim
from ..domain.enums import ActionType, ConflictPolicy, Operator
from .schema import USDC, ParsedCondition, ParsedIntent

INFLOW = "smart_money_inflow_24h"
LIQUIDITY = "available_liquidity"
PRICE = "asset_price"

DAY_SECONDS = 86_400

# =============================================================================================
# Text folding
# =============================================================================================

_CHAR_MAP = {
    "’": "'",
    "‘": "'",
    "ʼ": "'",
    "´": "'",
    "`": "'",
    "‐": "-",
    "‑": "-",
    "‒": "-",
    "–": "-",
    "—": "-",
    "−": "-",
    " ": " ",
    " ": " ",
    " ": " ",
    " ": " ",
    "＄": "$",
    "“": '"',
    "”": '"',
}


def fold(text: str) -> str:
    """Lower-case, strip accents, normalise quotes/dashes/spaces — one output char per input char."""
    out: list[str] = []
    for ch in text:
        mapped = _CHAR_MAP.get(ch)
        if mapped is not None:
            out.append(mapped)
            continue
        low = ch.lower()
        if len(low) != 1:
            low = ch
        base = "".join(c for c in unicodedata.normalize("NFKD", low) if not unicodedata.combining(c))
        out.append(base if len(base) == 1 else low)
    return "".join(out)


# =============================================================================================
# Lexicon (patterns run on folded text: lower-case, no accents)
# =============================================================================================

_B = r"(?<![\w$-])"  # left word boundary (also refuses to start inside "TOKEN-2" or "$...")
_E = r"(?![\w-])"  # right word boundary

_NUM = r"(?:\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d{1,3}(?: \d{3})+(?:[.,]\d+)?|\d+(?:[.,]\d+)?|[.,]\d+)"
_MULT = (
    r"(?:thousands?|milliers?|mille|millions?|milliards?|billions?|mln|mn|mm|bn|mds?|k|m|b)"
)
_CCY = r"(?:usdc|usd|us\$|dollars?|\$|eur|euros?|€|cents?|¢)"

_MULTIPLIERS = {
    "k": Decimal(1_000),
    "thousand": Decimal(1_000),
    "thousands": Decimal(1_000),
    "mille": Decimal(1_000),
    "millier": Decimal(1_000),
    "milliers": Decimal(1_000),
    "m": Decimal(1_000_000),
    "mm": Decimal(1_000_000),
    "mn": Decimal(1_000_000),
    "mln": Decimal(1_000_000),
    "million": Decimal(1_000_000),
    "millions": Decimal(1_000_000),
    "b": Decimal(1_000_000_000),
    "bn": Decimal(1_000_000_000),
    "billion": Decimal(1_000_000_000),
    "billions": Decimal(1_000_000_000),
    "md": Decimal(1_000_000_000),
    "mds": Decimal(1_000_000_000),
    "milliard": Decimal(1_000_000_000),
    "milliards": Decimal(1_000_000_000),
}


def _money(tag: str) -> str:
    """A money / number token: "$1M", "$1.5M", "500K$", "$500,000", "1,000,000 USD", "2.5 million",
    "$750 thousand", "0,50 $", "50 cents", "1 million de dollars"."""
    return (
        rf"{_B}(?P<{tag}_sign>-\s*)?(?P<{tag}_pre>us\$|\$|€|usdc\s|usd\s)?\s*(?P<{tag}_neg>-)?"
        rf"(?P<{tag}_num>{_NUM})(?![\d]|[.,]\d)"
        rf"(?:\s*(?P<{tag}_mult>{_MULT})(?![a-z0-9]))?"
        rf"(?:\s*(?:de\s+|d')?(?P<{tag}_ccy>{_CCY})(?![a-z0-9]))?"
        rf"(?!\s*%)"
    )


_TICKER = r"(?:token\s+x|[a-z][a-z0-9]*(?:[-_.][a-z0-9]+)*)"
_TICKER_B = rf"{_B}(?P<asset>{_TICKER}){_E}"

_KNOWN_ASSETS = {
    "mon", "wmon", "shmon", "gmon", "aprmon", "eth", "weth", "btc", "wbtc", "usdc", "usdt", "dai",
    "sol", "token-x", "token_x", "tokenx", "token x",
}
_USD_CURRENCIES = {"usdc", "usd", "$", "us$", "dollar", "dollars"}

# Words that never name an asset (English + French, folded).
_NOT_ASSETS = set(
    """
    a an the some any all both each every of in on into for with using worth to up at most least max maximum
    min minimum no not more than less only if and or but then when while unless once provided that as long
    buy sell purchase acquire spend invest swap convert exchange transfer send pay please kindly me my i we
    you us it is are be must should will would can could want like need price liquidity smart money inflow
    inflows netflow netflows tvl evidence data sources source usd dollar dollars eur euro euros cents
    usdc tops over above below under exceeds exceed agent now today order trade position token tokens coin
    coins stock shares units unit
    le la les l de du des d un une et ou si seulement uniquement que qu quand lorsque a au aux en pour avec
    moi je nous vous jusqu jusqua achete acheter achat vends vendre vente vend transfere transferer envoie
    envoyer prix liquidite entrees flux mais alors plus moins
    """.split()
)

# Pure filler: never reported as unparsed on its own.
_FILLER_TOKENS = set(
    """
    a an the please kindly only if and then when whenever provided that as long but both also while is are
    be must should shall will would has have to of with it i we you me my our us there this these those just
    so do does condition conditions which where can could want wants like need needs let lets go ahead
    execute trade now immediately right for on agent hey ok okay please monad testnet mainnet network chain
    once given assuming today asap away
    le la les l de du des d un une et si seulement uniquement que qu quand lorsque lorsqu a au aux en pour moi
    me je j nous vous veux voudrais voudrions stp svp plait il y ce cet cette ces mais alors avec est sont
    soit soient doit doivent puis aussi maintenant tout suite merci s
    """.split()
)

_MARKERS_RE = re.compile(
    _B
    + r"(?:but\s+only\s+if|if\s+and\s+only\s+if|only\s+if|only\s+when|if|when|whenever|once|provided(?:\s+that)?"
    r"|as\s+long\s+as|assuming(?:\s+that)?|given\s+that|on\s+condition\s+that|unless|except\s+if"
    r"|seulement\s+si|uniquement\s+si|s'ils?|si|quand|lorsqu'?|des\s+que|a\s+condition\s+qu'?(?:e)?"
    r"|pourvu\s+qu'?(?:e)?|tant\s+que|sauf\s+si)"
    + r"(?![\w-])"
)
_UNLESS_RE = re.compile(_B + r"(?:unless|except\s+if|sauf\s+si)(?![\w-])")
_SENTENCE_END_RE = re.compile(r"[;!?]|\.(?!\d)")

_WORD_NUMBERS = {
    "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9,
    "ten": 10, "un": 1, "une": 1, "deux": 2, "trois": 3, "quatre": 4, "cinq": 5, "sept": 7, "huit": 8,
    "neuf": 9, "dix": 10,
}
_NUMW = r"(?P<n>\d+|" + "|".join(sorted(_WORD_NUMBERS, key=len, reverse=True)) + r")"

# ---- durations ------------------------------------------------------------------------------
_DUR = (
    r"(?P<dn>\d+(?:[.,]\d+)?)\s*"
    r"(?P<du>secondes?|seconds?|secs?|sec\.?|s|minutes?|mins?|mn|m|hours?|heures?|hrs?|h)(?![a-z0-9])"
)


def _dur_seconds(m: re.Match) -> int:
    n = Decimal(m.group("dn").replace(",", "."))
    unit = m.group("du").rstrip(".")
    if unit.startswith(("min", "mn")) or unit == "m":
        n *= 60
    elif unit.startswith(("h", "heure")):
        n *= 3600
    return int(n)  # floor: fractional seconds round towards the stricter limit


_EVID = (
    r"(?:(?:all|the|any|each|every|with|toutes?\s+les|les)\s+)?"
    r"(?:evidence|data|observations?|signals?|inputs?|readings?|quotes?|preuves?|donnees|informations?)"
    r"(?:\s+items?|\s+points?)?"
)
_AGE_Q = (
    r"(?:under|less\s+than|below|<=|=<|<|≤|within|no\s+older\s+than|not\s+older\s+than|younger\s+than"
    r"|fresher\s+than|newer\s+than|at\s+most|no\s+more\s+than|max(?:imum)?(?:\s+of)?|of\s+at\s+most)"
)
_GLOBAL_AGE_RES = [
    re.compile(
        _B
        + _EVID
        + r"(?:\s+age)?\s*(?:(?:must|should|has\s+to|needs?\s+to|to)\s+be\s+|is\s+|are\s+|:\s*)?"
        + _AGE_Q
        + r"\s*"
        + _DUR
        + r"(?:\s+old)?"
    ),
    re.compile(
        _B
        + r"(?:max(?:imum)?|maximal)\s+(?:evidence|data|observation)\s+age(?:\s+of|\s*:|\s+is|\s*=)?\s*"
        + _DUR
    ),
    re.compile(
        _B
        + r"(?:fresh|recent)\s+(?:evidence|data)\s*(?:only\s*)?\(?\s*(?:under|within|<=|<|less\s+than|no\s+older\s+than)\s*"
        + _DUR
        + r"\s*\)?"
    ),
    re.compile(
        _B
        + _EVID
        + r"\s+(?:(?:must|should)\s+be\s+|is\s+|are\s+)?(?:fresh|recent)\s+(?:within|to\s+within|to)\s+"
        + _DUR
    ),
    re.compile(
        _B
        + r"(?:all|both|each|every)\s+(?:(?:must|should)\s+be\s+|are\s+|is\s+)?(?:fresh\s+)?"
        + r"(?:within|under|less\s+than|no\s+older\s+than)\s*"
        + _DUR
        + r"(?:\s+old)?"
    ),
    # French
    re.compile(
        _B
        + r"(?:toutes?\s+les\s+|les\s+|des\s+)?(?:preuves?|donnees|observations?)\s+(?:de|datant\s+de|agees?\s+de|vieilles\s+de"
        r"|doivent\s+avoir|doivent\s+dater\s+de|ayant)\s+(?:moins\s+de|au\s+plus|maximum|max)\s+"
        + _DUR
    ),
    re.compile(
        _B
        + r"(?:age|anciennete)\s+max(?:imum|imal|imale)?\s+(?:des\s+|de\s+la\s+|du\s+)?(?:preuves?|donnees|observations?)"
        r"(?:\s+de|\s*:|\s+est\s+de)?\s*"
        + _DUR
    ),
    re.compile(
        _B
        + r"(?:preuves?|donnees)\s+(?:fraiches|recentes)\s+(?:de\s+moins\s+de|a\s+moins\s+de|de\s+max(?:imum)?)\s+"
        + _DUR
    ),
]
# Bare freshness phrases: attach to the condition right before them, else global.
_BARE_AGE_RES = [
    re.compile(
        _B
        + r"\(?\s*(?:max(?:imum)?\s+age|age\s+max(?:imum|imal)?|age\s*(?:<=|<|≤|under|below|of\s+at\s+most))"
        + r"\s*(?:of\s+|:\s*|=\s*|de\s+)?"
        + _DUR
        + r"\s*\)?"
    ),
    re.compile(
        _B
        + r"(?P<verb>(?:observed|measured|seen|fetched|updated|reported|recorded|checked|refreshed|mesuree?s?|observee?s?)\s+)?"
        + r"(?P<prep>within|in|over|during|dans)\s+(?:the\s+|les\s+)?(?P<last>(?:last|past|dernieres?)\s+)?"
        + _DUR
    ),
    re.compile(
        _B
        + r"(?:(?:no|not)\s+older\s+than|(?:younger|fresher|newer)\s+than)\s*"
        + _DUR
        + r"(?:\s+old)?"
    ),
    re.compile(_B + r"(?:less\s+than|under|at\s+most|no\s+more\s+than)\s*" + _DUR + r"\s+old"),
    re.compile(_B + r"\(?\s*(?:fresh(?:ness)?)\s*(?:<=|<|≤|:|within|under)?\s*" + _DUR + r"\s*\)?"),
    re.compile(_B + r"(?:de\s+)?moins\s+de\s+" + _DUR + r"(?:\s+d'anciennete)?"),
]

# ---- claim windows (the inflow claim is a 24h window) ----------------------------------------
_WINDOW_RE = re.compile(
    _B
    + r"(?:"
    r"(?:(?:in|over|during|for|across|within)\s+(?:the\s+)?(?:last|past|previous)\s+|within\s+(?=\d))(?:(?P<n1>\d+)\s*-?\s*)?"
    r"(?P<u1>hours?|hrs?|h|days?|d|weeks?|wks?|w|months?)"
    r"|(?:sur|dans|pendant|au\s+cours\s+des?)\s+(?:les\s+)?(?:dernieres?\s+)?(?:(?P<n2>\d+)\s*)?"
    r"(?P<u2>heures?|h|jours?|j|semaines?|mois)"
    r"|\(?(?P<n3>\d+)\s*-?\s*(?P<u3>hours?|hrs?|hr|h|days?|d|weeks?|w)\)?"
    r"|(?P<u4>daily|weekly|monthly|quotidiennes?|hebdomadaires?)"
    r")(?![\w'-])"
)


def _window_seconds(m: re.Match) -> int:
    n_txt = m.group("n1") or m.group("n2") or m.group("n3")
    unit = m.group("u1") or m.group("u2") or m.group("u3") or m.group("u4") or ""
    n = int(n_txt) if n_txt else 1
    if unit.startswith(("h", "heure")):
        per = 3600
    elif unit.startswith(("d", "j", "quotidien")) or unit == "daily":
        per = DAY_SECONDS
    elif unit.startswith(("w", "semaine", "hebdo")) or unit == "weekly":
        per = 7 * DAY_SECONDS
    else:  # month / mois
        per = 30 * DAY_SECONDS
    return n * per


# ---- source counts ----------------------------------------------------------------------------
_SRC_QUAL = r"(?P<q>(?:independent|distinct|different|separate|independantes?|distinctes?|differentes?)\s+)?(?:valid\s+|valides?\s+)?"
_SRC_NOUN = (
    r"(?:sources?|providers?|evidence\s+items?|pieces\s+of\s+evidence|evidences?|observations?|data\s+points?"
    r"|preuves?|fournisseurs?|elements?\s+de\s+preuve)"
)
_SRC_QUAL_POST = r"(?:\s+(?P<q2>independantes?|distinctes?|differentes?|independent))?"
_SOURCES_RES = [
    re.compile(
        _B
        + r"(?:(?:from|with|using|across|on|requir(?:e|es|ing)|needs?|de|avec|sur)\s+)?"
        r"(?:at\s+least|a\s+minimum\s+of|minimum(?:\s+of)?|min\.?|no\s+fewer\s+than|not\s+fewer\s+than|no\s+less\s+than"
        r"|au\s+moins|au\s+minimum|minimum\s+de|pas\s+moins\s+de)\s+"
        + _NUMW
        + r"\s+"
        + _SRC_QUAL
        + _SRC_NOUN
        + _SRC_QUAL_POST
        + _E
    ),
    re.compile(
        _B
        + r"(?:(?:from|with|using|across|de|avec)\s+)?"
        + _NUMW
        + r"\s*(?:\+|or\s+more|ou\s+plus)\s+"
        + _SRC_QUAL
        + _SRC_NOUN
        + _SRC_QUAL_POST
        + _E
    ),
    re.compile(
        _B
        + r"(?:(?:from|with|using|across|de|avec)\s+)?"
        + _NUMW
        + r"\s+"
        + _SRC_QUAL
        + _SRC_NOUN
        + _SRC_QUAL_POST
        + r"\s+(?:or\s+more|minimum|at\s+least|ou\s+plus|au\s+moins|minimum)"
        + _E
    ),
    re.compile(
        _B
        + r"(?:from|with|using|across|de|avec|aupres\s+de)\s+"
        + _NUMW
        + r"\s+"
        + _SRC_QUAL
        + r"(?:sources?|providers?|fournisseurs?)"
        + _SRC_QUAL_POST
        + _E
    ),
]

# ---- conflict policy ----------------------------------------------------------------------------
_CONFLICT_NOUN = r"(?:(?:any|a|all|source|critical)\s+)*(?:conflicts?|disagreements?|conflicting\s+(?:sources|evidence|data))"
_DISAGREE = r"(?:sources?|providers?|evidence|data)\s+(?:conflict|disagree|diverge)"
_POLICY_RES: list[tuple[re.Pattern, ConflictPolicy]] = [
    (
        re.compile(
            _B
            + r"abstain\s+(?:on|if|when|in\s+case\s+of|upon|for)\s+(?:"
            + _CONFLICT_NOUN
            + "|"
            + _DISAGREE
            + ")"
            + _E
        ),
        ConflictPolicy.ABSTAIN,
    ),
    (
        re.compile(
            _B
            + r"(?:on|if|when|in\s+case\s+of|upon)\s+(?:"
            + _CONFLICT_NOUN
            + "|"
            + _DISAGREE
            + r")\s*,?\s*(?:then\s+)?abstain"
            + _E
        ),
        ConflictPolicy.ABSTAIN,
    ),
    (re.compile(_B + r"conflict\s+policy\s*(?::|=|is)?\s*abstain" + _E), ConflictPolicy.ABSTAIN),
    (
        re.compile(
            _B
            + r"(?:s'abstenir|abstiens-toi|abstention|abstiens\s+toi)\s+en\s+cas\s+de\s+(?:conflits?|desaccords?)"
            r"(?:\s+entre\s+(?:les\s+)?sources)?"
            + _E
        ),
        ConflictPolicy.ABSTAIN,
    ),
    (
        re.compile(_B + r"en\s+cas\s+de\s+(?:conflits?|desaccords?)\s*,?\s*(?:s'abstenir|abstiens-toi|abstention)" + _E),
        ConflictPolicy.ABSTAIN,
    ),
    (
        re.compile(
            _B
            + r"escalate\s+(?:to\s+(?:a\s+)?(?:human|reviewer|operator)\s+)?(?:(?:on|if|when|in\s+case\s+of|upon|for)\s+)?(?:"
            + _CONFLICT_NOUN
            + "|"
            + _DISAGREE
            + ")"
            + _E
        ),
        ConflictPolicy.ESCALATE,
    ),
    (
        re.compile(
            _B
            + r"(?:on|if|when|in\s+case\s+of|upon)\s+(?:"
            + _CONFLICT_NOUN
            + "|"
            + _DISAGREE
            + r")\s*,?\s*(?:then\s+)?escalate(?:\s+to\s+(?:a\s+)?(?:human|reviewer|operator))?"
            + _E
        ),
        ConflictPolicy.ESCALATE,
    ),
    (re.compile(_B + r"conflict\s+policy\s*(?::|=|is)?\s*escalate" + _E), ConflictPolicy.ESCALATE),
    (
        re.compile(_B + r"escalader\s+(?:les\s+conflits|en\s+cas\s+de\s+(?:conflits?|desaccords?))" + _E),
        ConflictPolicy.ESCALATE,
    ),
]

# ---- conflict tolerance & evidence budget ----------------------------------------------------------
_TOLERANCE_RE = re.compile(
    _B
    + r"(?:(?:sources?|providers?)\s+(?:must\s+|should\s+)?agree\s+(?:within|to\s+within)"
    r"|(?:conflict\s+)?tolerance(?:\s+of|\s*:|\s+is)?"
    r"|max(?:imum)?\s+disagreement(?:\s+of)?"
    r"|disagreement\s+(?:under|below|of\s+at\s+most|<=|<)"
    r"|tolerance(?:\s+de|\s*:)?"
    r"|ecart\s+max(?:imum|imal)?(?:\s+de)?)"
    r"\s*(?P<pct>\d+(?:[.,]\d+)?)\s*%"
)
_BUDGET_RE = re.compile(
    _B
    + r"(?:"
    r"(?:max(?:imum)?\s+)?evidence\s+(?:budget|cost|spend(?:ing)?)(?:\s+(?:of|is|under|below|at\s+most|max(?:imum)?|<=|<)|\s*:|\s*=)?"
    r"|budget\s+(?:for|on)\s+(?:evidence|data)(?:\s+of|\s*:|\s+is)?"
    r"|spend(?:ing)?\s+(?:at\s+most|up\s+to|no\s+more\s+than|max(?:imum)?)\s+(?P<pre_money_marker>)"
    r"|budget\s+(?:de\s+|des\s+|pour\s+les\s+)?(?:preuves?|donnees)(?:\s+de|\s*:)?"
    r"|(?:cout|depense)\s+max(?:imum|imal)?\s+(?:des\s+|de\s+|en\s+)?(?:preuves?|donnees)(?:\s+de|\s*:)?"
    r")\s*"
    + _money("bud")
    + r"(?(pre_money_marker)\s+(?:on|for)\s+(?:evidence|data)"
    + _E
    + r"|)"
)

# ---- actions ------------------------------------------------------------------------------------
_ACTION_RE = re.compile(
    _B
    + r"(?:(?P<buy>buy|purchase|acquire|achete|acheter|achetez|achat|acquiers|acquerir)"
    r"|(?P<spend>spend|invest|depense|depenser|investis|investir)"
    r"|(?P<swap>swap|convert|exchange|echange|echanger|convertis|convertir)"
    r"|(?P<sell>sell|liquidate|vends|vendre|vendez|vend|vente|liquider)"
    r"|(?P<transfer>transfer|send|pay|transfere|transferer|transfert|envoie|envoyer|envoyez|paie|payer))"
    + r"(?![\w'-])"
)

_MAX_SPEND = r"max(?:imum)?\s+(?:spend(?:ing)?|amount|order(?:\s+size)?|trade\s+size|size)(?:\s+of|\s*:|\s+is|\s*=)?"
_CAP = (
    r"(?:" + _MAX_SPEND + r"|for\s+up\s+to|up\s+to|upto|at\s+most|no\s+more\s+than|not\s+more\s+than|a\s+maximum\s+of|maximum\s+of"
    r"|a\s+max\s+of|max(?:imum)?\.?|jusqu'?\s*a|jusqu'?\s*au|pour\s+(?:au\s+plus|au\s+maximum|max(?:imum)?)"
    r"|au\s+plus|au\s+maximum|pour|for|worth)"
)
_CAP_STRICT = (
    r"(?:" + _MAX_SPEND + r"|up\s+to|upto|at\s+most|no\s+more\s+than|not\s+more\s+than|a\s+maximum\s+of|maximum\s+of|a\s+max\s+of"
    r"|max(?:imum)?\.?|jusqu'?\s*a|au\s+plus|au\s+maximum)"
)
_ART = r"(?:(?:some|the|du|de\s+la|des|de|d'|le|la|les|l')\s*)?(?:token\s+(?=token)|tokens?\s+(?!x(?![\w-])))?"

_P_MONEY_OF_ASSET = re.compile(
    r"\s*(?:(?:me|us|moi|nous)\s+)?(?:(?P<cap>" + _CAP + r")\s*)?" + _money("m")
    + r"\s*(?:worth\s+)?(?:of|in|into|on|de|du|des|d'|en)\s*(?:the\s+|le\s+|la\s+|les\s+|l')?" + _TICKER_B
)
_P_ASSET_MONEY = re.compile(
    r"\s*(?:(?:me|us|moi|nous)\s+)?" + _ART + _TICKER_B + r"\s*(?:,\s*|\(\s*)?"
    r"(?:(?:with|for|using|pour|avec|contre|worth|valued\s+at|for\s+a\s+total\s+of)\s+)?"
    + r"(?:(?P<cap>" + _CAP + r")\s*)?" + _money("m")
    + r"(?:\s*(?P<capsuf>max(?:imum)?\.?|at\s+most|tops|au\s+maximum|au\s+plus)(?![a-z]))?\s*\)?"
)
_P_QTY_ASSET = re.compile(
    r"\s*(?:(?P<cap>" + _CAP + r")\s*)?(?P<qty>" + _NUM + r")(?:\s*(?P<qmult>" + _MULT + r")(?![a-z0-9]))?\s+" + _TICKER_B
)
_P_ASSET = re.compile(r"\s*(?:(?:me|us|moi|nous)\s+)?" + _ART + _TICKER_B)
_P_SPEND = re.compile(
    r"\s*(?:(?P<cap>" + _CAP + r")\s*)?" + _money("m")
    + r"\s*(?:on|in|into|for|buying|to\s+buy|pour\s+acheter|en|dans)\s+" + _ART + _TICKER_B
)
_P_SWAP = re.compile(
    r"\s*(?:(?P<cap>" + _CAP + r")\s*)?" + _money("m")
    + r"\s*(?:for|into|to|to\s+get|contre|en)\s+" + _ART + _TICKER_B
)
_P_TRANSFER = re.compile(
    r"\s*(?:(?P<cap>" + _CAP + r")\s*)?" + _money("m")
    + r"(?:\s*(?:worth\s+)?(?:of|de|d')\s*" + _TICKER_B + r")?"
)
_P_TRANSFER_QTY = re.compile(r"\s*(?:(?P<cap>" + _CAP + r")\s*)?(?P<qty>" + _NUM + r")\s+" + _TICKER_B)
_AMOUNT_CAP_RE = re.compile(
    _B + r"(?:(?:spending|spend|using|with|for|pour|avec|while\s+spending)\s+)?(?P<cap>" + _CAP_STRICT + r")\s*(?:of\s+)?"
    + _money("m")
)

# ---- claims ----------------------------------------------------------------------------------------
_SM = r"smart[\s-]*money"
_CLAIM_PATTERNS: list[tuple[str, list[str]]] = [
    (
        INFLOW,
        [
            rf"(?:net\s+)?{_SM}(?:'s)?\s+(?:net\s*)?(?:in)?flows?",
            rf"(?:net\s+)?(?:in)?flows?\s+(?:from|of|by)\s+{_SM}(?:\s+wallets)?",
            rf"(?:les\s+)?(?:entrees|flux(?:\s+entrants?)?)(?:\s+nett?e?s?)?\s+(?:de\s+|du\s+|des\s+)?(?:la\s+|le\s+)?{_SM}",
            rf"{_SM}(?:\s+net\s*flows?)?",
            r"sm\s+(?:net\s*)?(?:in)?flows?",
            r"sm\s+netflows?",
            r"net\s+inflows?",
            r"net\s*flows?",
            r"inflows?",
            r"flux\s+(?:nets?\s+)?entrants?",
            r"entrees\s+nettes?",
            r"entrees?",
        ],
    ),
    (
        LIQUIDITY,
        [
            r"(?:available|pool|on[\s-]?chain|dex|total|current|usd|market)\s+liquidity",
            r"liquidity(?:\s+(?:depth|available))?",
            r"total\s+value\s+locked",
            r"tvl",
            r"liquidites?(?:\s+disponibles?)?(?:\s+(?:du|de\s+la)\s+pool)?",
        ],
    ),
    (
        PRICE,
        [
            r"(?:spot|token|asset|current|market|reference|oracle|usd)\s+price",
            r"price",
            r"prix(?:\s+(?:spot|actuel|du\s+marche))?",
            r"cours",
        ],
    ),
]
_CLAIM_RES = [(claim, [re.compile(_B + p + r"(?![\w-])") for p in pats]) for claim, pats in _CLAIM_PATTERNS]

# ---- operators ---------------------------------------------------------------------------------------
_OP_GTE = (
    r">=|=>|≥|greater\s+than\s+or\s+equal\s+to|more\s+than\s+or\s+equal\s+to|equal\s+to\s+or\s+(?:greater|more|higher)\s+than"
    r"|(?:is\s+)?at\s+or\s+above|at\s+least|no\s+less\s+than|not\s+less\s+than|no\s+lower\s+than|not\s+lower\s+than"
    r"|a\s+minimum\s+of|minimum\s+of|min(?:imum)?\.?(?=\s*[-$€\d])"
    r"|superieure?s?\s+ou\s+egale?s?\s+a|au\s+moins|au\s+minimum|minimum(?:\s+de)?|pas\s+moins\s+de"
)
_OP_LTE = (
    r"<=|=<|≤|less\s+than\s+or\s+equal\s+to|equal\s+to\s+or\s+(?:less|lower)\s+than"
    r"|(?:is\s+)?at\s+or\s+below|at\s+most|no\s+more\s+than|not\s+more\s+than|no\s+higher\s+than|not\s+higher\s+than"
    r"|no\s+greater\s+than|not\s+greater\s+than|a\s+maximum\s+of|maximum\s+of|max(?:imum)?\.?(?=\s*[-$€\d])"
    r"|inferieure?s?\s+ou\s+egale?s?\s+a|au\s+plus|au\s+maximum|maximum(?:\s+de)?|pas\s+plus\s+de"
    r"|ne\s+depasse(?:nt)?\s+pas|n'excede(?:nt)?\s+pas"
)
_OP_GT = (
    r">(?!=)|(?:strictly\s+)?(?:greater|more|higher|larger|bigger)\s+than|exceed(?:s|ed|ing)?|surpass(?:es|ed)?"
    r"|in\s+excess\s+of|above|over"
    r"|depasse(?:nt|r)?|excede(?:nt|r)?|(?:strictement\s+)?superieure?s?\s+a|au[\s-]dessus\s+(?:de|du|d')"
    r"|plus\s+(?:de|que|d')|plus\s+elevee?s?\s+que"
)
_OP_LT = (
    r"<(?!=)|(?:strictly\s+)?(?:less|lower|fewer|smaller)\s+than|below|under|beneath"
    r"|(?:strictement\s+)?inferieure?s?\s+a|en[\s-]dessous\s+(?:de|du|d')|au[\s-]dessous\s+(?:de|du|d')|sous"
    r"|moins\s+(?:de|que|d')|plus\s+bas(?:se)?s?\s+que"
)
_OPS = (
    r"(?P<op>(?P<gte>" + _OP_GTE + r")|(?P<lte>" + _OP_LTE + r")|(?P<gt>" + _OP_GT + r")|(?P<lt>" + _OP_LT + r"))"
    r"(?![a-z])"
)
_NEG = (
    r"(?P<neg>(?:(?:does|do|did|is|are|was|were|will|should|must|can|has|have)\s*(?:not|n't)|doesn't|don't|isn't"
    r"|aren't|won't|shouldn't|mustn't|never|not|n'est\s+pas|ne\s+sont\s+pas|ne\s+soit\s+pas|ne\s+soient\s+pas"
    r"|n'est\s+plus|ne\s+doit\s+pas|ne\s+doivent\s+pas)\s+"
    r"(?:(?:be|go|goes|get|gets|rise|rises|fall|falls|drop|drops|dip|dips|climb|climbs|trade|trades|etre|tomber|descendre|passer)\s+)?)"
)
_FILLER_WORD = (
    r"(?:is|are|be|being|been|must|should|shall|will|would|needs?\s+to|has\s+to|have\s+to|to|remains?|stays?|keeps?"
    r"|currently|still|both|all|goes|go|gets?|rises?|climbs?|drops?|falls?|trades?|trading|sits?|level|value|reading"
    r"|of|'s|est|sont|soit|soient|doit|doivent|etre|reste(?:nt)?|demeure(?:nt)?|toujours|actuel(?:le)?(?:ment)?"
    r"|se\s+situe(?:nt)?|de|d'|la|le|les)"
)
_FILLER = (
    r"(?P<filler>(?:[\s,:(]|(?:d|l|qu)'|" + _FILLER_WORD + r"(?![\w-])|(?:of|for|on|de|du|d'|pour)\s*(?P<fasset>" + _TICKER + r")(?![\w-]))*)"
)
_SUFFIX = (
    r"(?P<suffix>\s*(?:or\s+(?:more|above|higher|greater|less|below|lower)|ou\s+(?:plus|moins)"
    r"|\+(?!\s*\d)|minimum|maximum|min|max|au\s+moins|au\s+plus)(?![a-z]))?"
)
_EXISTS = (
    r"(?P<exists>exists?|is\s+available|are\s+available|is\s+present|is\s+known|is\s+defined|is\s+reported"
    r"|available|existe|est\s+disponible|disponible|est\s+connu)(?![a-z])"
)
_COND_RE = re.compile(
    _FILLER
    + r"(?:"
    + _NEG
    + r")?(?:"
    + r"(?P<between>between|entre)\s+"
    + _money("a")
    + r"\s+(?:and|et|to|-|a)\s+"
    + _money("b")
    + r"|"
    + _OPS
    + r"\s*(?:of\s+|:\s*|the\s+)?"
    + _money("v")
    + _SUFFIX
    + r"|"
    + _EXISTS
    + r"|"
    + _money("w")
    + r"(?P<suffix2>\s*(?:or\s+(?:more|above|higher|greater|less|below|lower)|ou\s+(?:plus|moins)|\+|minimum|maximum|min|max|au\s+moins|au\s+plus)(?![a-z]))"
    + r")"
)
_VALUE_BEFORE_RE = re.compile(
    r"(?:" + _NEG + r")?" + _OPS + r"\s*(?:of\s+)?" + _money("v")
    + r"\s*(?:worth\s+of|of|in|de|d'|en)?\s*(?:(?:the|la|le|les|available|total)\s+)*$"
)
_EXISTS_BEFORE_RE = re.compile(r"(?:a|an|une?)\s+(?:valid|current|live|fresh|known|reliable|valide|actuel)\s+$")
_PREFIX_NEG_RE = re.compile(r"(?:^|\s)(?:not|no|never|without|pas|non|sans|jamais)\s+(?:(?:the|a|an|any|le|la|les|de)\s+)?$")
_NEG_TOKEN_RE = re.compile(r"(?<![a-z])(?:not|no|never|n't|ne|n'|pas|jamais|without|sans)(?![a-z])")

_INVERT = {
    Operator.GT: Operator.LTE,
    Operator.GTE: Operator.LT,
    Operator.LT: Operator.GTE,
    Operator.LTE: Operator.GT,
}

_FRENCH_HINT = re.compile(
    _B
    + r"(?:achete|acheter|achetez|achat|vends|vendre|vendez|transfere|transferer|envoie|envoyer|jusqu'?\s*a|seulement"
    r"|uniquement|si|liquidite|depasse(?:nt)?|superieure?|inferieure?|prix|entrees|preuves|donnees|et|les|la|de)"
    + r"(?![\w-])"
)
_SEPARATORS_RE = re.compile(r",(?!\d)|[;!?()\[\]{}\"]|\.(?!\d)|(?<![\w-])(?:and|et|but|mais|then|puis)(?![\w-])|&")
_TOKEN_RE = re.compile(r"[a-z0-9$€%<>=≥≤+'-]+")


# =============================================================================================
# Helpers
# =============================================================================================


def plain(value: Decimal) -> Decimal:
    """Exponent-free, trailing-zero-free Decimal (Decimal("1E+6") -> Decimal("1000000"))."""
    return Decimal(format_decimal(value))


def _to_decimal(num: str) -> Decimal:
    s = num.replace(" ", "")
    if re.fullmatch(r"\d{1,3}(?:,\d{3})+(?:\.\d+)?", s):
        s = s.replace(",", "")
    else:
        s = s.replace(",", ".")
    if s.startswith("."):
        s = "0" + s
    return Decimal(s)


def _currency(raw: Optional[str]) -> Optional[str]:
    if not raw:
        return None
    r = raw.strip().lower()
    if r in ("$", "us$", "usd", "usdc", "dollar", "dollars"):
        return USDC
    if r in ("eur", "euro", "euros", "€"):
        return "EUR"
    if r in ("cent", "cents", "¢"):
        return USDC
    return raw.strip().upper()


def _money_value(m: re.Match, tag: str) -> tuple[Decimal, Optional[str]]:
    value = _to_decimal(m.group(f"{tag}_num"))
    mult = (m.group(f"{tag}_mult") or "").strip()
    if mult:
        value *= _MULTIPLIERS[mult]
    ccy_raw = m.group(f"{tag}_ccy") or m.group(f"{tag}_pre")
    if ccy_raw and ccy_raw.strip().lower() in ("cent", "cents", "¢"):
        value = value / 100
    if m.group(f"{tag}_sign") or m.group(f"{tag}_neg"):
        value = -value
    return plain(value), _currency(ccy_raw)


def _looks_like_ticker(original: str) -> bool:
    low = fold(original).strip()
    if not low or low in _NOT_ASSETS:
        return False
    if low in _KNOWN_ASSETS:
        return True
    letters = [c for c in original if c.isalpha()]
    if not letters:
        return False
    if len(original) >= 2 and all(c.isupper() for c in letters):
        return True
    if any(ch in original for ch in "-_"):
        return True
    return any(c.isdigit() for c in original)


def _normalize_asset(original: str) -> str:
    text = re.sub(r"\s+", " ", original.strip()).upper()
    if text == "TOKEN X":
        return "TOKEN-X"
    return text


@dataclass
class _Cond:
    claim: str
    operator: Operator
    threshold: Optional[Decimal]
    start: int
    end: int
    claim_start: int
    claim_end: int
    spans: list[tuple[int, int]]
    max_age: Optional[int] = None
    negated_scope: bool = False
    pair: Optional["_Cond"] = None  # lower bound of a "between" (accepted/rejected together)


@dataclass
class _Pending:
    kind: str  # "age" | "window"
    start: int
    end: int
    seconds: int


# =============================================================================================
# Parser
# =============================================================================================


class _Parser:
    def __init__(self, text: str) -> None:
        self.original = text
        self.folded = fold(text)
        self.consumed = bytearray(len(text))
        self.result = ParsedIntent(text=text, source="deterministic")
        self.global_ages: list[int] = []
        self.pending: list[_Pending] = []
        self.conds: list[_Cond] = []
        self.unparsed: list[str] = []
        self.warnings: list[str] = []
        self.action_end: Optional[int] = None
        self.clause_end: Optional[int] = None
        self._masked_cache: Optional[str] = None

    # ---- bookkeeping ------------------------------------------------------------------------
    @property
    def masked(self) -> str:
        if self._masked_cache is None:
            self._masked_cache = "".join(" " if c else ch for ch, c in zip(self.folded, self.consumed))
        return self._masked_cache

    def consume(self, start: int, end: int) -> None:
        for i in range(max(0, start), min(end, len(self.consumed))):
            self.consumed[i] = 1
        self._masked_cache = None

    def free(self, start: int, end: int) -> bool:
        return not any(self.consumed[start:end])

    def orig(self, start: int, end: int) -> str:
        return self.original[start:end].strip(" \t\n,;:.!?()[]\"'")

    def report(self, start: int, end: int) -> None:
        frag = self.orig(start, end)
        if frag and frag not in self.unparsed:
            self.unparsed.append(frag)

    def warn(self, message: str) -> None:
        if message not in self.warnings:
            self.warnings.append(message)

    def _finditer_free(self, pattern: re.Pattern):
        """Matches on the masked text that do not overlap consumed spans (re-scanned after each hit)."""
        pos = 0
        while True:
            m = pattern.search(self.masked, pos)
            if not m:
                return
            if m.end() == m.start():
                pos = m.end() + 1
                continue
            if self.free(m.start(), m.end()):
                yield m
            pos = m.end()

    # ---- 1. policy-level phrases ----------------------------------------------------------------
    def parse_global_ages(self) -> None:
        for pattern in _GLOBAL_AGE_RES:
            for m in list(self._finditer_free(pattern)):
                seconds = _dur_seconds(m)
                self.global_ages.append(seconds)
                self.consume(m.start(), m.end())

    def parse_sources(self) -> None:
        for pattern in _SOURCES_RES:
            for m in list(self._finditer_free(pattern)):
                n_txt = m.group("n")
                n = int(n_txt) if n_txt.isdigit() else _WORD_NUMBERS[n_txt]
                cur = self.result.minimum_required_evidence
                self.result.minimum_required_evidence = n if cur is None else max(cur, n)
                q = m.group("q") or m.group("q2")
                if q:
                    self.warn(
                        f"'{self.orig(m.start(), m.end())}': source independence is not enforced by the Evidence "
                        f"Contract; compiled as a minimum of {n} valid evidence items."
                    )
                    qs = m.start("q") if m.group("q") else m.start("q2")
                    qe = m.end("q") if m.group("q") else m.end("q2")
                    self.consume(m.start(), qs)
                    self.consume(qe, m.end())
                    self.report(qs, qe)
                else:
                    self.consume(m.start(), m.end())

    def parse_conflict_policy(self) -> None:
        found: list[ConflictPolicy] = []
        for pattern, policy in _POLICY_RES:
            for m in list(self._finditer_free(pattern)):
                found.append(policy)
                self.consume(m.start(), m.end())
        if found:
            if len(set(found)) > 1:
                # Both stated: ESCALATE sends the decision to a human, ABSTAIN never executes. Keep the
                # one stated first but make the contradiction visible.
                self.warn("Both 'abstain' and 'escalate' were requested for conflicts; the first one was used.")
            self.result.conflict_policy = found[0]

    def parse_tolerance(self) -> None:
        for m in list(self._finditer_free(_TOLERANCE_RE)):
            pct = Decimal(m.group("pct").replace(",", ".")) / 100
            cur = self.result.conflict_tolerance
            self.result.conflict_tolerance = pct if cur is None else min(cur, pct)
            self.consume(m.start(), m.end())

    def parse_budget(self) -> None:
        for m in list(self._finditer_free(_BUDGET_RE)):
            value, ccy = _money_value(m, "bud")
            if ccy not in (None, USDC) or value < 0:
                continue
            cur = self.result.evidence_budget_usdc
            self.result.evidence_budget_usdc = value if cur is None else min(cur, value)
            self.consume(m.start(), m.end())

    def parse_bare_ages_and_windows(self) -> None:
        for pattern in _BARE_AGE_RES:
            for m in list(self._finditer_free(pattern)):
                seconds = _dur_seconds(m)
                gd = m.groupdict()
                if "prep" in gd:
                    # "in the last 24 hours" is a claim window, "within 15 seconds" / "observed in the
                    # last 15 s" is freshness.
                    is_age = bool(gd.get("verb")) or seconds < 3600
                    if not is_age:
                        continue
                    if gd.get("prep") in ("in", "over", "during", "dans") and not gd.get("last") and not gd.get("verb"):
                        continue  # "in 30 s" alone is too vague to be a freshness bound
                self.pending.append(_Pending("age", m.start(), m.end(), seconds))
                self.consume(m.start(), m.end())
        for m in list(self._finditer_free(_WINDOW_RE)):
            self.pending.append(_Pending("window", m.start(), m.end(), _window_seconds(m)))
            self.consume(m.start(), m.end())

    # ---- 2. action / amount / asset ------------------------------------------------------------
    def parse_action(self) -> None:
        first: Optional[tuple[re.Match, str]] = None
        for m in list(self._finditer_free(_ACTION_RE)):
            kind = next(k for k in ("buy", "spend", "swap", "sell", "transfer") if m.group(k))
            if first is None:
                first = (m, kind)
                if self._action_patterns(m, kind):
                    continue
                self.consume(m.start(), m.end())
                continue
            first_type = self._type_for(first[1])
            if re.search(r"max(?:imum)?\s*$", self.masked[: m.start()]):
                continue  # "max spend 100 USDC" is an amount cap, parsed later
            if self._type_for(kind) == first_type and kind in ("buy", "spend", "swap"):
                # "spend 100 USDC to buy TOKEN-X": same action, restated.
                self.consume(m.start(), m.end())
            # A different action ("... and sell MON") stays unconsumed -> reported as unparsed.
        if first is not None:
            self.result.action = self._type_for(first[1])
            self.action_end = first[0].end()
            clause_end = len(self.masked)
            for pattern in (_MARKERS_RE, _SENTENCE_END_RE):
                mm = pattern.search(self.folded, self.action_end)
                if mm:
                    clause_end = min(clause_end, mm.start())
            self.clause_end = clause_end

    @staticmethod
    def _type_for(kind: str) -> ActionType:
        return {
            "buy": ActionType.BUY,
            "spend": ActionType.BUY,
            "swap": ActionType.BUY,
            "sell": ActionType.SELL,
            "transfer": ActionType.TRANSFER,
        }[kind]

    def _asset_at(self, m: re.Match, *, loose: bool = False) -> Optional[str]:
        if m.group("asset") is None:
            return None
        original = self.original[m.start("asset") : m.end("asset")]
        if not _looks_like_ticker(original):
            # "100 USDC of pepe": the grammatical position is unambiguous even in lower case.
            low = fold(original)
            if not (loose and len(low) >= 2 and low not in _NOT_ASSETS and low not in _FILLER_TOKENS):
                return None
        return _normalize_asset(original)

    def _action_patterns(self, verb: re.Match, kind: str) -> bool:
        """Tries the amount/asset shapes anchored right after the action verb."""
        masked = self.masked
        pos = verb.end()
        action = self._type_for(kind)
        if kind == "transfer":
            candidates = [("money", _P_TRANSFER), ("qty", _P_TRANSFER_QTY)]
        elif kind == "spend":
            candidates = [("money", _P_SPEND), ("asset", _P_ASSET)]
        elif kind == "swap":
            candidates = [("money", _P_SWAP)]
        else:
            candidates = [("money", _P_MONEY_OF_ASSET), ("money", _P_ASSET_MONEY), ("qty", _P_QTY_ASSET), ("asset", _P_ASSET)]
        for shape, pattern in candidates:
            m = pattern.match(masked, pos)
            if not m or not self.free(m.start(), m.end()):
                continue
            asset = self._asset_at(m, loose=pattern is _P_MONEY_OF_ASSET)
            if shape == "money":
                value, ccy = _money_value(m, "m")
                if pattern is _P_ASSET_MONEY and not (ccy or m.group("cap") or m.group("capsuf")):
                    continue
                if kind == "transfer" and asset is None:
                    if m.group("asset") is not None:
                        continue  # "transfer 25 USDC of <not-a-ticker>"
                    asset = ccy if ccy and ccy != "EUR" else None
                elif asset is None:
                    continue
                if action != ActionType.TRANSFER and asset and fold(asset) in _USD_CURRENCIES | {"usdc"}:
                    continue
                self.result.max_amount = value
                self.result.currency = ccy
            elif shape == "qty":
                if asset is None:
                    continue
                qty = _to_decimal(m.group("qty"))
                mult = m.group("qmult") if "qmult" in m.groupdict() else None
                if mult:
                    qty *= _MULTIPLIERS[mult.strip()]
                self.result.max_amount = plain(qty)
                self.result.currency = _currency(asset) if fold(asset) in _USD_CURRENCIES else asset
                if action != ActionType.TRANSFER and fold(asset) in _USD_CURRENCIES:
                    asset = None  # "buy 100 USDC" -> amount, asset still unknown
            else:  # asset only
                if asset is None or fold(asset) in _USD_CURRENCIES | {"usdc"}:
                    continue
            if asset:
                self.result.asset = asset
            self.consume(verb.start(), m.end())
            return True
        return False

    def parse_amount_fallback(self) -> None:
        """Explicit caps anywhere ("max 100 USDC", "spending at most $100"), else an amount in the
        action clause. Several caps -> the smallest one wins (never the more permissive)."""
        caps: list[tuple[Decimal, str, re.Match]] = []
        for m in list(self._finditer_free(_AMOUNT_CAP_RE)):
            value, ccy = _money_value(m, "m")
            if ccy is None:
                continue
            caps.append((value, ccy, m))
        if caps:
            usd = [c for c in caps if c[1] == USDC]
            pool = usd or caps
            best = min(pool, key=lambda c: c[0])
            if self.result.max_amount is None:
                self.result.max_amount, self.result.currency = best[0], best[1]
            elif self.result.currency == best[1] and best[0] < self.result.max_amount:
                self.warn(
                    f"Two maximum amounts were given; the smaller one ({best[0]} {best[1]}) was used."
                )
                self.result.max_amount = best[0]
            elif self.result.currency == best[1] and best[0] != self.result.max_amount:
                self.warn(
                    f"Two maximum amounts were given; the smaller one ({self.result.max_amount} {self.result.currency}) was used."
                )
            for value, ccy, m in caps:
                if ccy == self.result.currency:
                    self.consume(m.start(), m.end())
            return
        if self.result.max_amount is not None or self.action_end is None:
            return
        money_re = re.compile(_money("m"))
        for m in money_re.finditer(self.masked, self.action_end, self.clause_end or len(self.masked)):
            if not self.free(m.start(), m.end()):
                continue
            value, ccy = _money_value(m, "m")
            if ccy is None:
                continue
            self.result.max_amount, self.result.currency = value, ccy
            self.consume(m.start(), m.end())
            return

    def parse_asset_fallback(self) -> None:
        if self.result.asset is not None or self.action_end is None:
            return
        region_end = self.clause_end or len(self.masked)
        ticker_re = re.compile(_TICKER_B)
        for m in ticker_re.finditer(self.masked, self.action_end, region_end):
            if not self.free(m.start(), m.end()):
                continue
            asset = self._asset_at(m)
            if asset is None:
                continue
            if self.result.action != ActionType.TRANSFER and fold(asset) in _USD_CURRENCIES | {"usdc"}:
                continue
            self.result.asset = asset
            self.consume(m.start(), m.end())
            return
        if self.result.action == ActionType.TRANSFER and self.result.currency == USDC:
            self.result.asset = USDC

    # ---- 3. conditions -----------------------------------------------------------------------------
    def _claim_mentions(self) -> list[tuple[int, int, str]]:
        found: list[tuple[int, int, str]] = []
        masked = self.masked
        for claim, patterns in _CLAIM_RES:
            for p in patterns:
                for m in p.finditer(masked):
                    if self.free(m.start(), m.end()):
                        found.append((m.start(), m.end(), claim))
        found.sort(key=lambda t: (t[0], -(t[1] - t[0])))
        chosen: list[tuple[int, int, str]] = []
        last_end = -1
        for s, e, c in found:
            if s >= last_end:
                chosen.append((s, e, c))
                last_end = e
        return chosen

    def _asset_ok(self, fasset_span: Optional[tuple[int, int]]) -> bool:
        if fasset_span is None:
            return True
        original = self.original[fasset_span[0] : fasset_span[1]]
        if self.result.asset is None:
            return _looks_like_ticker(original)
        return _normalize_asset(original) == self.result.asset

    def _foreign_asset_before(self, claim_start: int) -> tuple[bool, int]:
        """'TOKEN-X price' is fine (consumed with the claim); 'WETH price' while trading TOKEN-X is not."""
        m = re.search(r"(?P<asset>" + _TICKER + r")(?:'s)?\s+$", self.masked[:claim_start])
        if not m:
            return False, claim_start
        original = self.original[m.start("asset") : m.end("asset")]
        if not _looks_like_ticker(original):
            return False, claim_start
        if self.result.asset is not None and _normalize_asset(original) == self.result.asset:
            return False, m.start("asset")
        return True, m.start("asset")

    def _followed_by_unit(self, end: int) -> bool:
        m = re.match(r"\s*(?P<w>" + _TICKER + r")(?![\w-])", self.masked[end:])
        if not m:
            return False
        original = self.original[end + m.start("w") : end + m.end("w")]
        return _looks_like_ticker(original) and fold(original) not in _USD_CURRENCIES

    def parse_conditions(self) -> None:
        mentions = self._claim_mentions()
        masked = self.masked
        pending_shared: list[tuple[int, int, str]] = []
        prev_end = 0
        for i, (cs, ce, claim) in enumerate(mentions):
            next_start = mentions[i + 1][0] if i + 1 < len(mentions) else len(masked)
            window_end = next_start
            se = _SENTENCE_END_RE.search(masked, ce, next_start)
            if se:
                window_end = se.start()

            foreign, claim_start = self._foreign_asset_before(cs)
            prefix = masked[prev_end:claim_start]
            prefix_negated = bool(_PREFIX_NEG_RE.search(prefix))

            cond = self._claim_first(claim, claim_start, cs, ce, window_end)
            if cond is None:
                cond = self._value_before(claim, claim_start, cs, ce, prev_end)
            if cond is None:
                # "inflows and liquidity both exceed ..." -> wait for the next claim's comparison.
                gap = masked[ce:window_end]
                if re.fullmatch(r"\s*(?:,|and|et|&)?\s*", gap) and i + 1 < len(mentions):
                    pending_shared.append((cs, ce, claim))
                else:
                    pending_shared.clear()
                prev_end = ce
                continue
            if foreign or prefix_negated or cond is False:
                # Understood the comparison, but not what it applies to / a negation around it.
                if cond not in (None, False):
                    self.report(claim_start, cond.end)
                    for s, e in cond.spans:
                        self.consume(s, e)
                pending_shared.clear()
                prev_end = ce
                continue
            if cond.pair is not None:
                self.conds.append(cond.pair)
            self.conds.append(cond)
            for s, e in cond.spans:
                self.consume(s, e)
            for ps, pe, pclaim in pending_shared:
                self.conds.append(
                    _Cond(pclaim, cond.operator, cond.threshold, ps, pe, ps, pe, [(ps, pe)])
                )
                self.consume(ps, pe)
            pending_shared.clear()
            masked = self.masked
            prev_end = cond.end

    def _claim_first(self, claim: str, claim_start: int, cs: int, ce: int, window_end: int):
        m = _COND_RE.match(self.masked, ce, window_end)
        if not m:
            return None
        if not any(m.group(g) for g in ("between", "op", "exists", "suffix2")):
            return None
        fasset = (m.start("fasset"), m.end("fasset")) if m.group("fasset") else None
        filler = m.group("filler") or ""
        if _NEG_TOKEN_RE.search(filler):
            return False
        neg = bool(m.group("neg"))
        spans = [(claim_start, m.end())]
        if not self._asset_ok(fasset):
            self.report(claim_start, m.end())
            self.consume(claim_start, m.end())
            return False
        if m.group("between"):
            lo, lo_ccy = _money_value(m, "a")
            hi, hi_ccy = _money_value(m, "b")
            if neg or lo_ccy == "EUR" or hi_ccy == "EUR" or self._followed_by_unit(m.end()):
                self.report(claim_start, m.end())
                self.consume(claim_start, m.end())
                return False
            if lo > hi:
                lo, hi = hi, lo
            lower = _Cond(claim, Operator.GTE, lo, claim_start, m.end(), cs, ce, spans)
            return _Cond(claim, Operator.LTE, hi, claim_start, m.end(), cs, ce, spans, pair=lower)
        if m.group("exists"):
            if neg:
                self.report(claim_start, m.end())
                self.consume(claim_start, m.end())
                return False
            return _Cond(claim, Operator.EXISTS, None, claim_start, m.end(), cs, ce, spans)
        if m.group("op"):
            op = self._op_from(m)
            value, ccy = _money_value(m, "v")
            suffix = (m.group("suffix") or "").strip()
            if suffix:
                op = self._apply_suffix(op, suffix)
        else:
            value, ccy = _money_value(m, "w")
            op = self._apply_suffix(None, (m.group("suffix2") or "").strip())
        if op is None or ccy == "EUR" or self._followed_by_unit(m.end()):
            self.report(claim_start, m.end())
            self.consume(claim_start, m.end())
            return False
        if neg:
            op = _INVERT[op]
        return _Cond(claim, op, value, claim_start, m.end(), cs, ce, spans)

    def _value_before(self, claim: str, claim_start: int, cs: int, ce: int, prev_end: int):
        prefix = self.masked[prev_end:claim_start]
        m = _VALUE_BEFORE_RE.search(prefix)
        if m:
            start = prev_end + m.start()
            op = self._op_from(m)
            value, ccy = _money_value(m, "v")
            if ccy == "EUR":
                return None
            if m.group("neg"):
                op = _INVERT[op]
            return _Cond(claim, op, value, start, ce, cs, ce, [(start, ce)])
        m = _EXISTS_BEFORE_RE.search(prefix)
        if m and claim == PRICE:
            start = prev_end + m.start()
            return _Cond(claim, Operator.EXISTS, None, start, ce, cs, ce, [(start, ce)])
        return None

    @staticmethod
    def _op_from(m: re.Match) -> Operator:
        if m.group("gte"):
            return Operator.GTE
        if m.group("lte"):
            return Operator.LTE
        if m.group("gt"):
            return Operator.GT
        return Operator.LT

    @staticmethod
    def _apply_suffix(op: Optional[Operator], suffix: str) -> Optional[Operator]:
        s = re.sub(r"\s+", " ", suffix)
        up = s in ("or more", "or above", "or higher", "or greater", "ou plus", "+", "minimum", "min", "au moins")
        down = s in ("or less", "or below", "or lower", "ou moins", "maximum", "max", "au plus")
        if op is None:
            return Operator.GTE if up else Operator.LTE if down else None
        if up and op in (Operator.GT, Operator.GTE):
            return Operator.GTE
        if down and op in (Operator.LT, Operator.LTE):
            return Operator.LTE
        if up or down:
            return None  # "below $1M or more": contradictory
        return op

    # ---- 4. scopes, attachments, disjunctions ----------------------------------------------------
    def apply_unless(self) -> None:
        folded = self.folded
        for um in _UNLESS_RE.finditer(folded):
            if not self.free(um.start(), um.end()):
                continue
            scope_end = len(folded)
            for pattern in (_MARKERS_RE, _SENTENCE_END_RE):
                nm = pattern.search(folded, um.end())
                if nm:
                    scope_end = min(scope_end, nm.start())
            scoped = [c for c in self.conds if um.end() <= c.start < scope_end]
            if not scoped:
                continue
            scoped.sort(key=lambda c: c.start)
            joined_by_and = any(
                re.search(r"(?<![\w-])(?:and|et|&)(?![\w-])", self.masked[a.end : b.start])
                for a, b in zip(scoped, scoped[1:])
            )
            if joined_by_and or any(c.operator not in _INVERT for c in scoped):
                # "unless A and B" is a disjunction (not A or not B): not representable.
                self.warn("An 'unless' clause could not be represented; its conditions were not compiled.")
                for c in scoped:
                    self.conds.remove(c)
                self.report(um.start(), scoped[-1].end)
            else:
                for c in scoped:
                    c.operator = _INVERT[c.operator]
                    c.negated_scope = True
                for a, b in zip(scoped, scoped[1:]):
                    om = re.search(r"(?<![\w-])(?:or|ou)(?![\w-])", self.masked[a.end : b.start])
                    if om:
                        self.consume(a.end + om.start(), a.end + om.end())
            self.consume(um.start(), um.end())

    def attach_pending(self) -> None:
        conds = sorted(self.conds, key=lambda c: c.start)
        for p in sorted(self.pending, key=lambda p: p.start):
            if p.kind == "age":
                target = self._cond_before(conds, p.start)
                if target is not None:
                    for c in conds:
                        if c.start == target.start and c.end == target.end:  # both halves of a "between"
                            c.max_age = p.seconds if c.max_age is None else min(c.max_age, p.seconds)
                else:
                    self.global_ages.append(p.seconds)
                continue
            # claim window: only meaningful next to an inflow condition
            target = self._cond_around(conds, p)
            if target is None or target.claim != INFLOW:
                self.report(p.start, p.end)
                continue
            if p.seconds != DAY_SECONDS:
                self.warn(
                    f"'{self.orig(p.start, p.end)}': only a 24h smart-money inflow window is available; "
                    "that condition was not compiled."
                )
                self.report(min(target.start, p.start), max(target.end, p.end))
                for c in [x for x in self.conds if x is target or x.pair is target or target.pair is x]:
                    self.conds.remove(c)
                    conds.remove(c)

    def _cond_before(self, conds: list[_Cond], pos: int) -> Optional[_Cond]:
        best = None
        for c in conds:
            if c.end <= pos and re.fullmatch(r"[\s,(]*", self.masked[c.end : pos]):
                best = c
        return best

    def _cond_around(self, conds: list[_Cond], p: _Pending) -> Optional[_Cond]:
        for c in conds:
            if c.start <= p.start and p.end <= c.end:
                return c
            if c.end <= p.start and re.fullmatch(r"[\s,(]*", self.masked[c.end : p.start]):
                return c
            if p.end <= c.start and re.fullmatch(r"[\s,(]*(?:of\s+|des\s+|de\s+)?", self.masked[p.end : c.start]):
                return c
            if c.start <= p.start and re.fullmatch(r"[\s,(]*", self.masked[c.claim_end : p.start]):
                return c
        return None

    def detect_disjunctions(self) -> None:
        conds = sorted(self.conds, key=lambda c: c.start)
        for a, b in zip(conds, conds[1:]):
            if a.negated_scope and b.negated_scope:
                continue
            gap = self.masked[a.end : b.start]
            om = re.search(r"(?<![\w-])(?:or|ou|either)(?![\w-])", gap)
            if om:
                self.consume(a.end + om.start(), a.end + om.end())
                self.report(a.start, b.end)
                self.warn(
                    "'or' between conditions is not supported: every condition was compiled as mandatory "
                    "(more restrictive than an 'or')."
                )

    # ---- 5. leftovers -------------------------------------------------------------------------------
    def collect_leftovers(self) -> None:
        n = len(self.original)
        i = 0
        while i < n:
            if self.consumed[i]:
                i += 1
                continue
            j = i
            while j < n and not self.consumed[j]:
                j += 1
            self._report_run(i, j)
            i = j

    def _report_run(self, start: int, end: int) -> None:
        segment = self.folded[start:end]
        cuts = [0]
        for m in _SEPARATORS_RE.finditer(segment):
            cuts.extend([m.start(), m.end()])
        cuts.append(len(segment))
        for a, b in zip(cuts[::2], cuts[1::2]):
            chunk = segment[a:b]
            spans = [
                (tm.start(), tm.end())
                for tm in _TOKEN_RE.finditer(chunk)
                if tm.group().strip("-+") and not all(part in _FILLER_TOKENS for part in tm.group().split("'") if part)
            ]
            if spans:
                self.report(start + a + spans[0][0], start + a + spans[-1][1])

    # ---- driver ----------------------------------------------------------------------------------------
    def run(self) -> ParsedIntent:
        self.parse_global_ages()
        self.parse_sources()
        self.parse_conflict_policy()
        self.parse_tolerance()
        self.parse_budget()
        self.parse_bare_ages_and_windows()
        self.parse_action()
        self.parse_conditions()
        self.apply_unless()
        self.attach_pending()
        self.detect_disjunctions()
        self.parse_amount_fallback()
        self.parse_asset_fallback()
        self.collect_leftovers()

        r = self.result
        if self.global_ages:
            r.max_age_seconds = min(self.global_ages)
        conditions: list[ParsedCondition] = []
        for c in sorted(self.conds, key=lambda c: c.start):  # stable: "between" keeps >= before <=
            spec = get_claim(c.claim)
            if spec is None or c.operator not in spec.allowed_operators:  # pragma: no cover - lexicon is closed
                self.report(c.start, c.end)
                continue
            conditions.append(
                ParsedCondition(
                    claim=c.claim,
                    operator=c.operator,
                    threshold=c.threshold,
                    max_age_seconds=c.max_age,
                    text=self.orig(c.start, c.end) or None,
                )
            )
        r.conditions = conditions
        r.unparsed = self.unparsed
        r.warnings = self.warnings
        r.language = "fr" if len(_FRENCH_HINT.findall(self.folded)) >= 2 else "en"
        return r


def parse_intent(text: str) -> ParsedIntent:
    """Deterministically parses one instruction. Never raises on odd input: whatever is not
    understood is reported in ``unparsed`` and missing mandatory parts in ``ParsedIntent.missing``."""
    if not isinstance(text, str):
        raise TypeError("intent text must be a string")
    return _Parser(text).run()


__all__ = ["INFLOW", "LIQUIDITY", "PRICE", "fold", "parse_intent", "plain"]
