# Decision Log Schema

**File:** `logs/decisions.jsonl` — append-only, one JSON object per line, hash-chained.

This is the primary evidence artifact of the entry. The handbook says judges ask *what
failed and why*, and that a rejection log answers that better than any slide. So the log
records **every decision, especially the ones where nothing happened.**

A session with 390 evaluations and 2 trades should produce 390 lines, not 2.

---

## Why rejections are the point

Most entrants will log fills. Fills tell a judge what happened. **Rejections tell a judge
what the system refused to do, and why** — which is the only way to show that the risk
controls are real rather than decorative. An agent that never declines anything has not
demonstrated a risk framework; it has demonstrated an absence of opportunities to fail.

---

## Record structure

```jsonc
{
  "seq": 1042,
  "ts_utc": "2026-11-23T19:35:00Z",
  "ts_et": "2026-11-23T14:35:00-05:00",
  "session_date": "2026-11-23",
  "cycle_id": "c-20261123-0087",       // one scan cycle across the universe
  "stage": "risk_review",              // scan | propose | risk_review | execute | exit | halt
  "symbol": "US.QQQ",

  "verdict": "BLOCK",                  // see Verdicts below
  "reason_code": "CLUSTER_LIMIT",      // see Reason Codes below
  "reason_text": "SPY position already open; index_broad cluster allows 1.",

  "inputs": {
    "last_price": 412.18,
    "bid": 412.16, "ask": 412.20,
    "spread_pct_of_mid": 0.0097,
    "sma20": 414.90,
    "atr20": 1.74,
    "dislocation_atr": -1.56,
    "regime": "OK",
    "regime_basis": "SPY 612.40 > SMA60 611.02",
    "quote_ts_utc": "2026-11-23T19:34:58Z",
    "quote_age_s": 2
  },

  "proposed": {                        // null when no proposal was formed
    "side": "BUY",
    "qty": 201,
    "limit_price": 412.18,
    "stop_price": 410.44,
    "target_price": 414.90,
    "risk_usd": 349.74,
    "risk_pct": 0.35,
    "notional": 82848.18,
    "sizing_source": "deterministic"   // NEVER "model"
  },

  "risk_checks": [
    {"check": "account_guard",        "pass": true},
    {"check": "quote_age",            "pass": true},
    {"check": "spread",               "pass": true},
    {"check": "cluster_limit",        "pass": false, "detail": "index_broad 1/1 used"},
    {"check": "max_positions",        "pass": true},
    {"check": "gross_exposure",       "pass": true},
    {"check": "daily_loss_limit",     "pass": true},
    {"check": "drawdown_halt",        "pass": true},
    {"check": "order_budget",         "pass": true},
    {"check": "session_window",       "pass": true}
  ],

  "order": null,                       // populated at execute/exit stages
  // {
  //   "order_id": "...", "status": "FILLED",
  //   "submitted_qty": 201, "filled_qty": 201,
  //   "limit_price": 412.18, "avg_fill_price": 412.21,
  //   "slippage_usd": 6.03, "slippage_pct_of_notional": 0.0073,
  //   "fees": 0.0, "fees_source": "account_order_fees",
  //   "need_order_confirm": false,
  //   "submitted_at_utc": "...", "filled_at_utc": "...", "latency_ms": 840
  // }

  "account_snapshot": {
    "equity": 100641.22,
    "cash": 17793.04,
    "gross_exposure_pct": 32.4,
    "open_positions": 1,
    "session_pnl_pct": 0.21,
    "peak_equity": 100812.55,
    "drawdown_from_peak_pct": 0.17
  },

  "provenance": {
    "agent": "risk-auditor",
    "agent_version": 3,                // PINNED, never "latest"
    "model": "...",
    "skill_versions": {"moomoo-skill": 2},
    "rules_hash": "sha256:...",        // ties decision to the exact committed rules
    "code_commit": "a1b2c3d"
  },

  "prev_hash": "sha256:...",
  "hash": "sha256:..."                 // sha256(canonical_json(record_without_hash))
}
```

---

## Verdicts

| Verdict | Meaning |
|---|---|
| `NO_SETUP` | Rules evaluated, conditions not met. The most common line by far. |
| `PROPOSE` | Engine formed a candidate order |
| `APPROVE` | Auditor passed it unchanged |
| `SHRINK` | Auditor reduced size, with a written reason |
| `BLOCK` | Auditor refused |
| `ESCALATE` | Sent to a human via Slack; no action taken until answered |
| `SUBMITTED` | Order sent to the paper account |
| `FILLED` / `PARTIAL` / `REJECTED` / `CANCELLED` | Broker outcome |
| `EXIT` | Position closed, with which exit rule fired |
| `HALT` | Kill switch tripped |

---

## Reason codes

Enumerated so rejections are **countable**, not just readable. The distribution of these
across the week is a chart on the dashboard and a slide in the deck.

| Code | Trips when |
|---|---|
| `NO_SETUP` | Signal conditions not met |
| `REGIME_BLOCK` | SPY below SMA60 — downtrend, mean reversion stands down |
| `STALE_QUOTE` | Quote age > 120s |
| `SPREAD_TOO_WIDE` | Spread > 0.05% of mid |
| `MISSING_DATA` | Fewer than 20 bars, or a required field absent |
| `CLUSTER_LIMIT` | Correlation cluster already occupied |
| `MAX_POSITIONS` | 3 concurrent positions open |
| `NOTIONAL_CAP` | Position would exceed 15% of equity |
| `GROSS_EXPOSURE_CAP` | Would exceed 40% gross |
| `SIZE_BELOW_MIN` | Computed qty < 1 |
| `ORDER_BUDGET` | 20 orders used this session |
| `ENTRY_BUDGET` | 6 entries used this session |
| `LATE_SESSION_NO_ENTRY` | After 15:00 ET |
| `SESSION_CLOSED` | Outside RTH, or a calendar-override closed day |
| `DAILY_LOSS_LIMIT` | Session loss ≥ 1.5% |
| `DRAWDOWN_HALT` | ≥ 3.5% from peak — flatten and disable |
| `CONSEC_LOSS_THROTTLE` | 3 consecutive losses → half size |
| `ACCOUNT_GUARD_FAIL` | acc_id or account name did not match config |
| `AUDITOR_VETO` | Auditor's judgement, reason written out |
| `INJECTION_SUSPECTED` | Untrusted text attempted to influence a protected field |
| `MANUAL_HALT` | Operator set `AGENT_MODE=halt` |

---

## `INJECTION_SUSPECTED` — the record that carries the thesis

When news or community text tries to steer a protected field, the log keeps the payload
**verbatim** and records what it tried to do:

```jsonc
{
  "stage": "scan",
  "verdict": "BLOCK",
  "reason_code": "INJECTION_SUSPECTED",
  "reason_text": "Retrieved text contained an imperative directed at the agent and referenced a symbol outside the declared universe.",
  "untrusted_input": {
    "source": "news_search",
    "retrieved_at_utc": "2026-11-24T15:02:11Z",
    "payload_verbatim": "<<< quarantined, stored as data, never re-interpreted >>>",
    "attempted_influence": ["universe_membership", "order_submission"],
    "blocked_by": "untrusted_input.may_never_influence"
  },
  "action_taken": "none"
}
```

That single record type is the demo: a live attempt, caught, logged, and provably unable
to reach the order path — because the order path takes its inputs from deterministic code
and config, not from retrieved text.

---

## Integrity

- `hash` = SHA-256 over the canonical JSON of the record with `hash` omitted
- `prev_hash` = the previous line's `hash`; genesis line uses `"sha256:0"`
- `verify` walks the chain and reports the first broken link

This means a judge can be handed the log and told: *you can check we didn't edit this
after the fact.* Note honestly in the submission what the chain does and does not prove —
it detects post-hoc tampering of earlier entries; it does not prove the entries were
truthful when written. Overclaiming here would undercut the entire entry.
