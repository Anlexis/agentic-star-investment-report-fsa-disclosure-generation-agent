"""AgentCore Platform v1.0"""

# Domain contract for the investment-disclosure document generator.
#
# Everything that more than one node has to agree on lives here: the bounds a
# caller request has to satisfy, the product catalogue that decides the risk
# band, the inert renderer that every caller-supplied string passes through
# before it can reach the document, and the finite-number parser used for every
# caller-supplied figure.
#
# It holds no node logic and performs no I/O. Nodes import from here so that the
# request validator, the section writers and the assembler cannot drift apart
# about what a valid request is, or about what may be rendered.
#
# Why an inert renderer rather than escaping: the generated document is a
# statutory disclosure whose structure carries meaning. A caller string
# containing a newline and a section marker does not merely look wrong — it
# manufactures a section of the document that no rule in this template wrote.
# Structural characters are therefore removed, not escaped.

from __future__ import annotations

import math
import re
import unicodedata
from typing import Any, Dict, List, Mapping, Optional, Tuple

# ---------------------------------------------------------------------------
# Caller input bounds
#
# Every bound is enforced by rejection, never by silent truncation of the
# request: a disclosure document assembled from a truncated request would
# describe a product the caller did not name.
# ---------------------------------------------------------------------------

MAX_REQUEST_CHARS = 8_000
MAX_PRODUCT_NAME_CHARS = 120
MAX_METRIC_ENTRIES = 24
MAX_METRIC_NAME_CHARS = 32
MAX_ABS_METRIC_VALUE = 1e15
MAX_REPORT_CHARS = 40_000
MIN_REPORT_CHARS = 1_000
MAX_REPORT_CHARS_CEILING = 200_000

#: Metric names are rendered into the document, so they are restricted to an
#: alphabet in which no document structure can be expressed.
METRIC_NAME_RE = re.compile(r"^[a-z][a-z0-9_]{0,%d}$" % (MAX_METRIC_NAME_CHARS - 1))

#: Keys accepted inside the JSON request envelope. Anything else is refused
#: rather than ignored: a misspelt field silently dropped becomes a document
#: assembled from less information than the caller believed they supplied.
REQUEST_ENVELOPE_FIELDS = frozenset(
    {
        "product_name",
        "product_type",
        "target_investor_profile",
        "financial_data",
        "fund_performance_data",
    }
)

#: Keys accepted on the invocation's ``input_context``. The entry adapter drops
#: everything else before the graph is invoked.
CALLER_CONTEXT_FIELDS = frozenset({"channel", "review_policy"})

#: Channels recognised on ``input_context``; anything else is reported as
#: ``unknown`` rather than echoed.
CHANNEL_VALUES = frozenset({"web", "branch", "api", "batch", "unknown"})

#: ``strict`` forces the human-review flag on regardless of product risk. It is
#: the one context value that changes what the document says, which is what
#: makes the context channel observable end to end.
REVIEW_POLICY_VALUES = frozenset({"standard", "strict"})

#: The platform's input filter replaces personal-data shapes with this sentinel
#: before any template code runs. A sentinel is the absence of a value, not a
#: value: it must never be rendered into a disclosure document as the product
#: the caller named, and never certified as a validated field.
REDACTION_SENTINEL = "[MASKED]"


# ---------------------------------------------------------------------------
# Product catalogue
#
# ``product_type`` resolves to a catalogue key, and the rendered type text comes
# from the catalogue rather than from the caller. That removes a free-text
# render, and it makes the risk band a function of a validated value instead of
# a substring search over caller text.
# ---------------------------------------------------------------------------

RISK_BAND_STANDARD = "中リスク"
RISK_BAND_HIGH = "高リスク"

PRODUCT_CATALOGUE: Dict[str, Dict[str, Any]] = {
    "investment_trust": {"label": "投資信託", "high_risk": False},
    "equity": {"label": "株式", "high_risk": False},
    "bond": {"label": "債券", "high_risk": False},
    "etf": {"label": "ETF", "high_risk": False},
    "reit": {"label": "REIT", "high_risk": False},
    "fx": {"label": "外国為替証拠金取引 (FX)", "high_risk": True},
    "derivative": {"label": "デリバティブ", "high_risk": True},
    "option": {"label": "オプション", "high_risk": True},
    "future": {"label": "先物", "high_risk": True},
    "cfd": {"label": "差金決済取引 (CFD)", "high_risk": True},
    "crypto_asset": {"label": "暗号資産", "high_risk": True},
}

#: Caller-facing spellings accepted for each catalogue key, including the
#: Japanese spellings that were the published vocabulary of this template.
PRODUCT_TYPE_ALIASES: Dict[str, str] = {
    "投資信託": "investment_trust",
    "投信": "investment_trust",
    "株式": "equity",
    "株": "equity",
    "債券": "bond",
    "etf": "etf",
    "reit": "reit",
    "fx": "fx",
    "外国為替証拠金取引": "fx",
    "デリバティブ": "derivative",
    "オプション": "option",
    "先物": "future",
    "cfd": "cfd",
    "暗号資産": "crypto_asset",
    "仮想通貨": "crypto_asset",
}

INVESTOR_PROFILES: Dict[str, str] = {
    "retail": "個人投資家",
    "institutional": "機関投資家",
}


# ---------------------------------------------------------------------------
# Inert rendering
# ---------------------------------------------------------------------------

# The document's own structural vocabulary, plus every character that could
# open a new structural element in it. A caller string containing any of these
# is not escaped, it is stripped: a disclosure document has no legitimate need
# for a caller-supplied heading or section rule.
_MARKUP_CHARS = frozenset("■【】=─#*_`|>[]{}\\\r\n\t\v\f")


def render_inert(value: Any, limit: int) -> str:
    """Return ``value`` reduced to characters that cannot alter document structure.

    Control characters, line breaks and every structural character are removed
    rather than escaped, and the result is trimmed to ``limit``. Only the return
    value of this function may be interpolated into the generated document.
    """
    if not isinstance(value, str):
        return ""
    kept: List[str] = []
    for char in value:
        if char in _MARKUP_CHARS:
            continue
        if unicodedata.category(char).startswith("C"):
            continue
        kept.append(char)
    return "".join(kept).strip()[:limit]


# A product name is a name. Stripping the document's structural characters stops
# a caller manufacturing a SECTION, but it does not stop them writing a SENTENCE
# on the 商品名 line — and in a statutory disclosure a sentence such as
# "元本は保証されており損失は発生しません" is a false statement of exactly the kind
# the document exists to prevent. Sentence punctuation and list markers are
# therefore refused outright: no fund name contains them, and a forged claim
# cannot be written without them.
_PROSE_CHARS = frozenset("。、：:；;！!？?…")
_LIST_MARKER_RE = re.compile(r"\d+\s*[.)．）]\s*\S")

_IDENTIFIER_FILTER = re.compile(r"[^A-Za-z0-9_.-]")


def safe_identifier(value: Any, limit: int = MAX_METRIC_NAME_CHARS) -> str:
    """Reduce a caller-supplied name to an inert identifier fit to echo back.

    Used for the one thing an error message legitimately names — the field at
    fault. Unlike ``render_inert`` it keeps ``_``, because an identifier without
    its underscores names a different field, and it is never used for anything
    that reaches the document.
    """
    if not isinstance(value, str):
        return "?"
    return _IDENTIFIER_FILTER.sub("", value)[:limit] or "?"


def is_redacted(value: Any) -> bool:
    """True when the platform's input filter replaced this value with a sentinel."""
    return isinstance(value, str) and REDACTION_SENTINEL in value


# ---------------------------------------------------------------------------
# Injection screening
#
# The template owns this. The platform's own input policy blocks some of these
# forms and not others — ``<<SYS>>`` and a directive split by markup both pass
# it — and a template that relies on the platform alone returns success on the
# forms the platform misses. Screening happens on the raw text and again on the
# markup-stripped text, because stripping markup can re-assemble a directive
# that was not visible before it ran.
# ---------------------------------------------------------------------------

_CONTROL_TOKEN_PATTERNS: Tuple[re.Pattern[str], ...] = (
    re.compile(r"<\|[^|>]{1,64}\|>"),  # <|im_start|>, <|endoftext|> …
    re.compile(r"\[/?INST\]", re.IGNORECASE),  # [INST] … [/INST]
    re.compile(r"<</?SYS>>", re.IGNORECASE),  # <<SYS>>
    re.compile(r"<\|?/?s>", re.IGNORECASE),  # </s>, <|s|>
)

_DIRECTIVE_PATTERNS: Tuple[re.Pattern[str], ...] = (
    re.compile(r"ignore\s+(?:all\s+|any\s+)?(?:previous|prior|above)\s+instruction", re.IGNORECASE),
    re.compile(r"disregard\s+(?:the\s+)?(?:system|previous|prior)\b", re.IGNORECASE),
    re.compile(r"you\s+are\s+now\s+\w", re.IGNORECASE),
    re.compile(r"\bact\s+as\s+(?:an?\s+)?(?:unrestricted|jailbroken|dan)\b", re.IGNORECASE),
    re.compile(r"(?:reveal|print|output)\s+(?:the\s+)?system\s+prompt", re.IGNORECASE),
    re.compile(r"前の指示(?:を|は).{0,8}(?:無視|忘れ)"),
    re.compile(r"システムプロンプトを(?:表示|出力|教え)"),
)

# Markup removal used only for the second screening pass. It is deliberately
# cruder than render_inert: its job is to re-assemble a directive that was
# split by tags ("ig<b>nore</b> all previous instructions"), not to sanitise.
_MARKUP_TAG_RE = re.compile(r"<[^<>]{0,64}>")

_SCREEN_KEY_LIMIT = 64


def _screen_text(text: str) -> Optional[str]:
    """Return a closed-set label for the first screening failure, else ``None``."""
    for pattern in _CONTROL_TOKEN_PATTERNS:
        if pattern.search(text):
            return "control_token"
    stripped = _MARKUP_TAG_RE.sub("", text)
    for candidate in (text, stripped):
        for pattern in _DIRECTIVE_PATTERNS:
            if pattern.search(candidate):
                return "directive_phrase"
    return None


def screen_injection(value: Any, _depth: int = 0) -> Optional[str]:
    """Screen a parsed payload depth-first, keys included.

    Keys are screened as well as values: a hostile field name reaches the audit
    log and, in an unvalidated design, the document. Returns a closed-set label
    naming the class of the finding, never any part of the caller's text.
    """
    if _depth > 8:
        return "structure_too_deep"
    if isinstance(value, str):
        return _screen_text(value)
    if isinstance(value, Mapping):
        for key, item in value.items():
            if isinstance(key, str):
                found = _screen_text(key[:_SCREEN_KEY_LIMIT])
                if found:
                    return found
            found = screen_injection(item, _depth + 1)
            if found:
                return found
        return None
    if isinstance(value, (list, tuple)):
        for item in value:
            found = screen_injection(item, _depth + 1)
            if found:
                return found
    return None


# ---------------------------------------------------------------------------
# Personal-data screening
#
# The platform masks the shapes it recognises before this template runs, so
# these patterns exist for the shapes it does not. The Japanese individual
# number written without separators is the important one: the platform's word
# boundary is computed over a character class that includes Kana and Kanji, so
# the unspaced form — which is how Japanese is actually written — is not
# detected there.
# ---------------------------------------------------------------------------

_PII_PATTERNS: Tuple[Tuple[str, re.Pattern[str]], ...] = (
    ("email", re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")),
    ("credit_card", re.compile(r"(?<!\d)(?:\d[ -]?){13,16}(?!\d)")),
    ("phone", re.compile(r"(?<!\d)(?:\+?\d{1,3}[-\s]?)?\(?\d{2,4}\)?[-\s]\d{2,4}[-\s]\d{3,4}(?!\d)")),
    ("my_number_jp", re.compile(r"(?<![0-9])\d{4}[-\s]?\d{4}[-\s]?\d{4}(?![0-9])")),
)


def scan_pii(text: str) -> Optional[str]:
    """Return the personal-data category label if a pattern matches, else ``None``."""
    if not isinstance(text, str):
        return None
    for label, pattern in _PII_PATTERNS:
        if pattern.search(text):
            return label
    return None


def scan_pii_payload(value: Any, _depth: int = 0) -> Optional[str]:
    """Screen the string leaves and keys of a parsed payload for personal data.

    Numeric leaves are deliberately not screened. Every personal-data shape in
    this domain is written as text, whereas a fund's figures are numbers — and a
    screen applied to bare digits refuses legitimate ones. A twelve-digit net
    asset value is a plausible figure and an implausible individual number, so
    scanning the parsed payload rather than the raw request keeps the screen
    closed on the risk without closing it on the work.
    """
    if _depth > 8:
        return None
    if isinstance(value, str):
        return scan_pii(value)
    if isinstance(value, Mapping):
        for key, item in value.items():
            if isinstance(key, str):
                found = scan_pii(key)
                if found:
                    return found
            found = scan_pii_payload(item, _depth + 1)
            if found:
                return found
        return None
    if isinstance(value, (list, tuple)):
        for item in value:
            found = scan_pii_payload(item, _depth + 1)
            if found:
                return found
    return None


# ---------------------------------------------------------------------------
# Finite-number parsing
# ---------------------------------------------------------------------------


def finite_in_range(value: Any, limit: float = MAX_ABS_METRIC_VALUE) -> Optional[float]:
    """Parse a caller-supplied number, rejecting non-finite and out-of-range values.

    ``float("nan")`` and ``float("inf")`` parse without complaint and then
    compare false against every threshold, so an unchecked figure turns a bounds
    test into a silent pass. Anything not finite, or beyond ``limit``, comes back
    as ``None`` so the caller of this function has to decide explicitly.
    """
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        candidate = float(value)
    elif isinstance(value, str):
        try:
            candidate = float(value.strip())
        except (TypeError, ValueError):
            return None
    else:
        return None
    if math.isnan(candidate) or math.isinf(candidate):
        return None
    if abs(candidate) > limit:
        return None
    return candidate


def resolve_report_limit(declared: Any) -> int:
    """Resolve the operator's document-size limit, clamped to a workable range.

    Declared in ``config/config.yaml`` as ``max_report_chars`` and forwarded to
    the inner graph. An absent or unusable declaration falls back to the default
    rather than to no limit at all — a size cap that can be switched off by a
    typo is not a cap.
    """
    parsed = finite_in_range(declared)
    if parsed is None:
        return MAX_REPORT_CHARS
    return int(min(max(parsed, MIN_REPORT_CHARS), MAX_REPORT_CHARS_CEILING))


def format_metric_value(value: float) -> str:
    """Render a validated figure without exponent notation or trailing noise."""
    if value == int(value) and abs(value) < 1e15:
        return f"{int(value):,}"
    return f"{value:,.4f}".rstrip("0").rstrip(".")


# ---------------------------------------------------------------------------
# Request normalisation
# ---------------------------------------------------------------------------


class RequestError(ValueError):
    """A caller request that cannot be served as given.

    Carries the field name so the caller learns what to change. It never carries
    the offending value: that value may be exactly what the request was refused
    for.
    """

    def __init__(self, field: str, reason: str) -> None:
        super().__init__(f"{field}: {reason}")
        self.field = field
        self.reason = reason


def resolve_product_type(raw: Any) -> Optional[str]:
    """Map a caller-supplied product type onto a catalogue key, or ``None``."""
    if not isinstance(raw, str):
        return None
    token = raw.strip()
    if token in PRODUCT_CATALOGUE:
        return token
    return PRODUCT_TYPE_ALIASES.get(token.lower())


def normalise_metrics(raw: Any, field: str) -> List[Tuple[str, float]]:
    """Validate a caller-supplied metric mapping into ordered ``(name, value)`` pairs.

    Metric names are restricted to an inert identifier alphabet because they are
    rendered into the document; values go through the finite parser because a
    non-finite figure printed in a statutory disclosure is a false statement
    about the product.
    """
    if raw is None or raw == "":
        return []
    if not isinstance(raw, Mapping):
        raise RequestError(field, "must be an object mapping metric names to numbers")
    if len(raw) > MAX_METRIC_ENTRIES:
        raise RequestError(field, f"at most {MAX_METRIC_ENTRIES} entries are accepted")
    metrics: List[Tuple[str, float]] = []
    seen: set[str] = set()
    for key in raw:
        if not isinstance(key, str) or not METRIC_NAME_RE.match(key):
            raise RequestError(
                field,
                f"each metric name must match [a-z][a-z0-9_]* and be at most " f"{MAX_METRIC_NAME_CHARS} characters",
            )
        if is_redacted(raw[key]):
            # The platform's input filter reads a bare twelve-digit run as an
            # individual number and replaces it. A sentinel is not a figure, and
            # a disclosure document may not carry one, so say what happened
            # instead of failing as an unparseable value.
            raise RequestError(
                field,
                f"the value of '{safe_identifier(key)}' was replaced by the platform's "
                "redaction filter; send it in a form that is not read as personal data",
            )
        parsed = finite_in_range(raw[key])
        if parsed is None:
            raise RequestError(field, "each metric value must be a finite number within range")
        if key in seen:
            continue
        seen.add(key)
        metrics.append((key, parsed))
    metrics.sort(key=lambda pair: pair[0])
    return metrics


def normalise_request(raw: Any) -> Dict[str, Any]:
    """Turn a parsed request envelope into the validated product descriptor.

    Returns the descriptor the pipeline works from. Every string in it is either
    drawn from this module's catalogues or has passed through ``render_inert``;
    nothing the caller wrote reaches the document by any other route.
    """
    if not isinstance(raw, Mapping):
        raise RequestError("request", "must be a JSON object")

    unknown = sorted(str(key) for key in raw if key not in REQUEST_ENVELOPE_FIELDS)
    if unknown:
        # A rejected field name is caller data too, so it is echoed only through
        # the identifier filter — an unrecognised field cannot smuggle text into
        # the error message either.
        listed = ", ".join(safe_identifier(name) for name in unknown[:5])
        raise RequestError("request", f"unrecognised field(s): {listed}")

    raw_name = raw.get("product_name")
    if not isinstance(raw_name, str):
        raise RequestError("product_name", "is required and must be a string")
    if len(raw_name) > MAX_PRODUCT_NAME_CHARS:
        raise RequestError("product_name", f"must be at most {MAX_PRODUCT_NAME_CHARS} characters")
    if is_redacted(raw_name):
        raise RequestError(
            "product_name",
            "was replaced by the platform's redaction sentinel and cannot be used as a product name",
        )
    product_name = render_inert(raw_name, MAX_PRODUCT_NAME_CHARS)
    if not product_name:
        raise RequestError("product_name", "must contain at least one renderable character")
    if any(char in _PROSE_CHARS for char in product_name) or _LIST_MARKER_RE.search(product_name):
        raise RequestError(
            "product_name",
            "must be a product name: sentence punctuation and list markers are not accepted",
        )

    product_key = resolve_product_type(raw.get("product_type"))
    if product_key is None:
        raise RequestError("product_type", "is required and must name a supported product type")

    raw_profile = raw.get("target_investor_profile", "retail")
    if not isinstance(raw_profile, str) or raw_profile.strip().lower() not in INVESTOR_PROFILES:
        raise RequestError(
            "target_investor_profile",
            "must be one of: " + ", ".join(sorted(INVESTOR_PROFILES)),
        )
    profile = raw_profile.strip().lower()

    financial = normalise_metrics(raw.get("financial_data"), "financial_data")
    performance = normalise_metrics(raw.get("fund_performance_data"), "fund_performance_data")

    entry = PRODUCT_CATALOGUE[product_key]
    return {
        "product_name": product_name,
        "product_key": product_key,
        "product_label": str(entry["label"]),
        "high_risk": bool(entry["high_risk"]),
        "target_investor_profile": profile,
        "financial_data": financial,
        "fund_performance_data": performance,
    }


def normalise_context(raw: Any) -> Dict[str, str]:
    """Reduce the caller context to the declared, closed-set contract.

    Unrecognised keys and unrecognised values are dropped rather than echoed:
    the context channel is not masked by the platform's input filter, so an
    unvalidated value from it would be the least screened text in the document.
    """
    channel = "unknown"
    review_policy = "standard"
    if isinstance(raw, Mapping):
        candidate_channel = raw.get("channel")
        if isinstance(candidate_channel, str) and candidate_channel.strip().lower() in CHANNEL_VALUES:
            channel = candidate_channel.strip().lower()
        candidate_policy = raw.get("review_policy")
        if isinstance(candidate_policy, str) and candidate_policy.strip().lower() in REVIEW_POLICY_VALUES:
            review_policy = candidate_policy.strip().lower()
    return {"channel": channel, "review_policy": review_policy}


def render_metric_block(metrics: List[Tuple[str, float]], heading: str, empty_text: str) -> str:
    """Render validated metrics as a bounded, single-line-per-entry block."""
    if not metrics:
        return empty_text
    lines = [heading]
    for name, value in metrics:
        lines.append(f"  - {name}: {format_metric_value(value)}")
    return "\n".join(lines)


class Service:
    """Domain service for the investment-disclosure generator.

    The document is assembled deterministically from the validated request, so
    no external data source is configured. ``fetch`` returns a well-typed empty
    result set rather than raising, so a caller never hits a dead stub; wire a
    concrete source here if a later revision needs external domain data, and
    keep any credential in the platform secret manager.
    """

    async def fetch(self, query: str, context: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """Return domain data for the given query."""
        return {"query": query, "results": [], "source": None}
