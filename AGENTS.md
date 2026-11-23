# AGENTS.md — read this before any tool call

2026 Moomoo x Qoder AI Trading Agent Challenge. This file is the standing brief for
every agent session in this repo. It is short on purpose. The authoritative detail
lives in the files listed at the bottom.

---

## 1. Account guard — the one rule that is never relaxed

**The competition account is `5832647`.** Verify all three before any order:

| Check | Required value |
|---|---|
| `acc_id` | `5832647` |
| `account_title` | `Moomoo x Qoder AI Paper Trading Agent Challenge` (exact) |
| `account_type` | `2` |

If any check fails, **stop and report**. Do not substitute another account.

**Blocklisted — never trade, never read balances from:** `5852462`, `5852463`, `5852464`.

> On the first probe run an agent chose `5852463` ("US Margin Account [Paper Trading]")
> unprompted, because it is the obvious US paper account, and reported its $1,000,000
> balance as the competition balance. The real balance is $100,000. Every position size
> derives from that number, so the error would have sized every trade 10x too large.
> A plausible-looking account is the dangerous kind.

**Field-name trap:** `sim_trade_account_list` returns the id as `account_id`.
`sim_trade_cash_info` and `sim_trade_input_order` take it as `acc_id`. Same value,
two spellings.

---

## 2. Tag every order

Every order this project places carries an `S`-prefixed tag in the `text` field
(100 bytes), e.g. `S00-PROBE-001`. Untagged orders are **not ours**.

This is not bookkeeping. On 20 Nov two untagged orders appeared in `5832647` from an
unidentified second actor, one of them 2.5 minutes after our last API write. The tag
is how we detected it. Reconcile the full order list against our own records every
cycle; **halt on any order we cannot account for.** Foreign orders also used
`order_trade_time_type: 2` — we always set `1`, so that is a second, independent
fingerprint.

---

## 3. Default to read-only

Ask before placing, cancelling, or modifying anything. When an instruction says
"read only", that governs over any earlier approval in the conversation — including
one given for a different step. Prefer stopping over guessing.

Show the exact JSON you intend to send, and wait, before any order.

---

## 4. Measured API facts that change how you call things

Do not re-derive these; they cost a live probe session. Full working in
`docs/API_CAPABILITIES.md`.

- **No stop orders exist.** `order_type` enum is `{1: Limit, 3: Market}` only. No
  STOP, STOP_LIMIT or TRAILING_STOP. Stops are enforced in software, by polling.
- **No `time_in_force` input.** The parameter does not exist on
  `sim_trade_input_order`. Order records *return* `time_in_force: 0`, which we cannot
  set. Use `order_trade_time_type: 1` (intraday only) on every order.
- **Commission is $0.99 per filled order.** Reserved in `hold` at submission as
  `limit_price + 0.99`, exactly. Charged on fill only; a cancel costs nothing.
- **Aggressive limits fill at prevailing market, not at the limit.** Fill-vs-limit is
  price *improvement*, not slippage. Never log it as slippage. Benchmark slippage
  against the decision-time quote.
- **Cancel is not confirmed by its response.** `sim_trade_cancel_order` returns only
  an order-id echo, no status. Poll until `status == 5`. ~2s server-side.
- **Status enum is undocumented:** `2` resting, `4` filled, `5` cancelled. Anything
  else is unknown — halt.
- **An order record can be mid-transition**, reporting `avg_fill_price` while still
  `status 2, cum_qty 0`. Require `status == 4` **and** `cum_qty == qty` before treating
  a fill as real.
- **`balance` and `hold` are two buckets of one pot.** A submission moves balance into
  hold; `total_asset` does not change. `total_asset = balance + mv + hold` reconciles
  exactly — assert it, and refuse to size off a snapshot where it fails.
- **`realized_profit` / `unrealized_profit` are `null`**, even after a closed round
  trip. Never coerce to `0`. Compute the equity curve from `total_asset` and the ledger.
- **`max_power_long` is 2x balance** (margin available). `allow_margin` is false, so
  that headroom does not exist. Size off `total_asset`, never off buying power.
- **Dead tools:** `account_order_fees`, `trading_margin_ratio` — both `-32603`
  regardless of parameters. Do not retry them.
- **Quote timestamps:** `data_time` on `quote_stock_quote` is the *last trade* time.
  The order book carries its own, fresher timestamp. Judge staleness on the book.
- **The order book is NASDAQ + ARCA only**, not consolidated NBBO. Do not let displayed
  size drive a sizing decision.

---

## 5. Universe

`US.SPY`, `US.QQQ`, `US.IWM`, `US.XLF`, `US.XLE`. Nothing else, ever.
Long only. No shorting, no options, no margin, no leveraged or inverse products.

Two correlation clusters, both enforced by the gate:
- `SPY`/`QQQ`/`IWM` — at most **one** concurrent position across the three
- all five symbols — at most **two** concurrent positions in total

The second was added 2026-11-23. XLF and XLE previously sat outside the index
cluster, so three simultaneous long-equity-beta positions were permitted. Under
the stressed correlations of 1997 or 2008 that is one trade at triple size.
Reasoning in `docs/RESEARCH_CRISIS_AND_ALLOCATORS.md`.

---

## 6. Division of labour

The model proposes. Code decides. `config/risk_limits.yaml` is read and enforced by
code — it is never handed to a model to interpret. Position quantity is computed by
formula; a model may not choose it. The risk auditor may make any limit stricter at
runtime and may never loosen one.

Anything not explicitly permitted in the config is forbidden.

---

## 7. Authoritative files

| File | Holds |
|---|---|
| `config/risk_limits.yaml` | Every enforced bound. Single source of truth |
| `docs/STRATEGY.md` | The trading rule, pre-committed before trading began |
| `docs/API_CAPABILITIES.md` | What the API was *measured* to do, vs what docs claim |
| `docs/DECISION_LOG_SCHEMA.md` | Format of the hash-chained decision ledger |
| `docs/SESSION_00_PROBE.md` | The capability-probe runbook |
| `docs/COMPETITION_BRIEF.md` | Scoring, field, timeline, team structure |
| `CONTEXT.md` | Project state: what is decided, measured, and still open |
| `runbooks/LIVE_CYCLE.md` | **The live trading procedure.** Follow it as written |
| `engine/` | Deterministic core. No network I/O, no free text, no model |
| `tests/` | 310 tests. The evidence for "the model cannot break the rules" |

If this file and one of those disagree, those win — and fix this file.

---

## 8. Before you change engine/ or config/

Run the suite: `python3 -m pytest tests/ -q`. It should report **310 passed**.

If that number and the suite disagree, the number is what is stale -- the count
is quoted in several files and drifts when tests are added. Trust pytest, then fix
the prose. A stale figure in a document a judge reads costs more than it saves.

These are not regression cover. Each test names a rule and demonstrates the
engine enforcing it, so a failure means either the code stopped enforcing
something or a limit changed without the reasoning being updated. Both need a
human, not a fix to the test.

Two invariants are enforced in code as well as tested, and must stay that way:

- `risk_gate.evaluate_entry` asserts the returned quantity never exceeds the
  proposed one. The gate may only tighten.
- `config.RiskConfig` refuses to load rather than defaulting a missing limit.
  Absence is not permission.
