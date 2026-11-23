"""Handling text that a third party can influence.

THE CLAIM THIS MODULE SUPPORTS, AND WHAT ACTUALLY SUPPORTS IT

The differentiating claim is that attacker-influenceable text can never reach
order-execution logic. The honest basis for that claim is **not** this module.
It is that nothing in the decision path accepts text at all:

    size_position(cfg, symbol, price: float, atr20: float, equity: float)
    evaluate_entry(indicators, regime_ok: bool, quote_age: float, ...)
    RiskGate.evaluate_entry(proposal, acct, sess, ...)   -> numbers only
    build_order(...)                                     -> enums and strings
                                                            we construct

There is no parameter anywhere in that chain that carries free text from a
feed. A payload cannot be filtered into harmlessness if it was never a
possible input, and that is a stronger property than any filter.

So this module exists for the narrow case where untrusted text IS read —
regime commentary, research context for a human — and its job is to make that
reading recordable rather than safe-by-inspection. It classifies, quarantines
verbatim, and produces a ledger record. It does not sanitise text for reuse,
because a sanitiser invites the belief that cleaned text is then safe to act
on.

MEASURED ATTACK SURFACE, 2026-11-20

  quote_community_search.title   unmoderated retail post text, ~47 chars
  news_search.content            ~3,000 chars of HTML, anchors, futunn://
                                 deep links, base64 attributes
  news_search.abstract           first 200 chars of content, truncated
                                 mid-token with no ellipsis or flag

And the finding that makes this a real surface rather than a theoretical one:
**keyword matching is naive substring.** `keyword="SPY"` returned SPY×FAMILY
anime licensing notices. An attacker does not need to write about SPY to
appear in the SPY feed; the token merely has to occur in their text.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, asdict, field
from typing import Any, Iterable, Mapping

# Fields measured to carry third-party text, by tool. Anything not listed is
# platform-assigned (ids, timestamps, enums) and not attacker-shaped.
UNTRUSTED_FIELDS = {
    "news_search": ("title", "content", "abstract", "source",
                    "source_url", "cover_image"),
    "quote_community_search": ("title",),
}

# String fields the platform assigns rather than a third party supplying.
# Listed so `unexpected_text_fields` reports a genuinely new surface instead
# of flagging every id and enum as attacker text.
PLATFORM_FIELDS = {
    "news_search": ("news_id", "news_type", "publish_time", "source_id"),
    "quote_community_search": ("id", "community_type", "publish_time"),
}

# Patterns that indicate text is addressing a reader rather than describing a
# market. Detection is for the RECORD, not for defence -- the defence is that
# this text has nowhere to go. A payload these patterns miss is equally inert.
_SIGNALS: tuple[tuple[str, str], ...] = (
    ("instruction_to_agent",
     r"(?i)\b(ignore|disregard|forget|override)\b[^.]{0,40}"
     r"\b(previous|prior|above|earlier|all)\b[^.]{0,40}"
     r"\b(instruction|prompt|rule|constraint|directive)s?\b"),
    ("role_assertion",
     r"(?i)\b(you are now|act as|pretend to be|from now on you)\b"),
    ("authority_claim",
     r"(?i)\b(system|admin|administrator|developer|operator|anthropic|openai)\b"
     r"[^.]{0,20}\b(message|override|instruction|says|command)\b"),
    ("trade_directive",
     r"(?i)\b(buy|sell|short|long|liquidate|close|flatten|double)\b"
     r"[^.]{0,30}\b(immediately|now|all|everything|position|at market)\b"),
    ("limit_tampering",
     r"(?i)\b(increase|raise|remove|disable|widen|loosen|ignore)\b"
     r"[^.]{0,30}\b(limit|stop|risk|cap|size|position|budget)\b"),
    ("credential_probe",
     r"(?i)\b(api[_ ]?key|token|secret|password|credential|acc_id|account id)\b"),
    ("exfiltration",
     r"(?i)(https?://|futunn://|curl\s|\bPOST\b|\bwebhook\b)"),
    ("encoded_payload",
     r"(?:[A-Za-z0-9+/]{40,}={0,2})"),
    ("prompt_delimiter",
     r"(?:<\|[^|]{1,20}\|>|\[/?INST\]|###\s*(system|instruction)|```\s*system)"),
)


@dataclass(frozen=True)
class Quarantined:
    """Untrusted text, recorded rather than used."""

    source_tool: str
    field: str
    item_id: str | None
    length: int
    signals: tuple[str, ...]
    payload_verbatim: str
    truncated: bool
    # Stated in the record so a reader need not infer it.
    reached_decision_path: bool = False
    blocked_by: str = (
        "structural: no function in engine/ accepts text from a feed"
    )

    @property
    def suspicious(self) -> bool:
        return bool(self.signals)

    def to_ledger(self) -> dict[str, Any]:
        return asdict(self)


def classify(text: str) -> tuple[str, ...]:
    """Which signals the text matches. Empty is not a safety claim."""
    if not text:
        return ()
    return tuple(name for name, pat in _SIGNALS if re.search(pat, text))


def quarantine(
    text: str, *, source_tool: str, field: str, item_id: str | None = None,
    max_recorded: int = 2000,
) -> Quarantined:
    """Record untrusted text without passing it anywhere.

    The payload is kept **verbatim** up to `max_recorded`. Redacting it would
    destroy the evidence: a record saying "an injection was detected" is an
    assertion, while a record carrying the exact bytes is a finding someone
    else can check.
    """
    body = text or ""
    return Quarantined(
        source_tool=source_tool, field=field, item_id=item_id,
        length=len(body), signals=classify(body),
        payload_verbatim=body[:max_recorded],
        truncated=len(body) > max_recorded,
    )


def scan_items(
    items: Iterable[Mapping[str, Any]], *, source_tool: str,
) -> list[Quarantined]:
    """Quarantine every untrusted field of every item.

    Fields are taken from the measured list rather than discovered, so a feed
    that starts returning a new text field is not silently trusted. A field
    present in the payload and absent from `UNTRUSTED_FIELDS` is reported by
    `unexpected_text_fields` below.
    """
    fields = UNTRUSTED_FIELDS.get(source_tool, ())
    out: list[Quarantined] = []
    for item in items:
        item_id = str(item.get("news_id") or item.get("id") or "") or None
        for f in fields:
            value = item.get(f)
            if isinstance(value, str) and value:
                out.append(quarantine(value, source_tool=source_tool,
                                      field=f, item_id=item_id))
    return out


def unexpected_text_fields(
    items: Iterable[Mapping[str, Any]], *, source_tool: str,
) -> tuple[str, ...]:
    """String fields the feed returned that we have not classified.

    A feed adding a text field is a change in the attack surface. Detecting it
    is cheap; assuming the surface is static is how a surface grows unnoticed.
    """
    known = set(UNTRUSTED_FIELDS.get(source_tool, ()))
    known |= set(PLATFORM_FIELDS.get(source_tool, ()))
    seen: set[str] = set()
    for item in items:
        for k, v in item.items():
            if isinstance(v, str) and v and k not in known:
                seen.add(k)
    return tuple(sorted(seen))


def injection_record(
    q: Quarantined, *, cycle_id: str, attempted_influence: str | None = None,
) -> dict[str, Any]:
    """A ledger record for a suspicious payload.

    `INJECTION_SUSPECTED` carries the payload verbatim and states plainly that
    it did not reach the decision path and why. This is the record the
    submission points at, so it has to be checkable rather than reassuring.
    """
    return {
        "cycle_id": cycle_id,
        "stage": "untrusted_input",
        "verdict": "QUARANTINED",
        "reason_code": "INJECTION_SUSPECTED" if q.suspicious else "RECORDED",
        "reason_text": (
            f"{q.source_tool}.{q.field} matched {list(q.signals)}; recorded "
            f"verbatim and not passed to any decision function"
            if q.suspicious else
            f"{q.source_tool}.{q.field} recorded; no signals matched"
        ),
        "attempted_influence": attempted_influence,
        "quarantined": q.to_ledger(),
    }
