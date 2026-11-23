# Runbook — one live cycle

Standing procedure. Follow it as written; it is not a suggestion of an
approach. Where it says STOP, stop and report rather than deciding.

**Account:** `5832647` only. **Ledger:** `logs/decisions_live.jsonl`.
**Window:** entries 10:00–15:00 ET, flat by 15:50 ET (12:45 ET on 27 Nov).

---

## 0. Pre-flight, once per session

```
git pull
PATH=/tmp/mta-venv/bin:$PATH python3 -m pytest tests/ -q
git status --short
```

**STOP if:** the suite fails, or `engine/` has uncommitted changes. A dirty
engine means the commit the ledger pins does not describe the running code.
Commit first, or report and wait.

---

## 1. Gather inputs — from API responses only

Never fill any of these from `config/risk_limits.yaml`. The account guard
compares what the broker says against what the config says; sourcing both
from the config makes the comparison vacuous and it will always pass.

### a) Bars → `inputs/live_<SYM>.json`

All five symbols: `US.SPY`, `US.QQQ`, `US.IWM`, `US.XLF`, `US.XLE`.

- `quote_history_kline` with `ktype: 6`, `extended_time: 0`, merged with
  `quote_cur_kline` for today
- dedupe on `time_key`, sort ascending
- drop the 16:00 closing print
- SPY needs **≥ 80 bars** — the regime filter uses SMA60

The driver drops bars that have not closed, so including the forming bar is
harmless. It will appear as `dropped_not_yet_closed`.

### b) Books → `inputs/quotes.json`

`quote_order_book` per symbol. The venue shape is **not stable**: a
NASDAQ+ARCA pair when the market is open, a single consolidated `"US"` book
when closed. Handle both.

```json
{"US.SPY": {"bid": <max across venues>,
            "ask": <min across venues>,
            "book_age_seconds": <now − freshest exchange_data_time_ms>}}
```

`book_age_seconds` comes from the **book** timestamp, never from `data_time`
— that field is the last-trade time and is a different, older number.

### c) Account → `inputs/acct.json`

Identity from `sim_trade_account_list`, figures from `sim_trade_cash_info`:

```json
{"acc_id", "account_title", "account_type",
 "total_asset", "balance", "hold", "market_value"}
```

### d) Positions → `inputs/positions.json`

From `sim_trade_position_list`. **Drop zero-quantity stub rows** — moomoo
leaves them after a close, and a stub read as a position blocks re-entry in
that symbol for the rest of the day.

### e) Orders → `inputs/orders.json`

Every order id in the account today, as a JSON list.

---

## 2. Run

```
/tmp/mta-venv/bin/python run_cycle.py \
  --now-et "<actual ET now, YYYY-MM-DD HH:MM>" \
  --bars-dir inputs \
  --account-json inputs/acct.json \
  --positions-json inputs/positions.json \
  --order-ids-json inputs/orders.json \
  --quotes-json inputs/quotes.json \
  --ledger logs/decisions_live.jsonl \
  --intents-out outbox
```

Add `--fills-json inputs/fills.json` when there are fills to report (§4).

---

## 3. Act on the outcome

### No intents file

Normal. Report the stdout and wait for the next cycle. Measured base rate is
~2.4 qualifying signals per session per symbol, and the regime filter removes
most of them, so most cycles are `NO_ACTION`.

### An intents file appeared

**STOP.** Report, and wait for approval:

- full stdout
- the intents JSON verbatim
- the `risk` record for that symbol from the ledger

Then, once approved, submit **exactly** the JSON in the intents file —
unchanged quantity, unchanged price, unchanged `text`. The ledger records
the approved quantity; a submitted quantity that differs is a §8 violation
and is detectable from the record.

Record the submission time in ET, and the `order_id` the broker returns.

---

## 4. After a fill

Capture `sim_trade_cash_info` **immediately before and after** the fill. The
cash delta is the only way the fee is measurable — `account_order_fees` and
`account_cash_logs` both return MCP error −32603. This is prediction P6.

`inputs/fills.json`:

```json
[{"order_id": "...", "symbol": "US.SPY", "side": "BUY",
  "verdict": "FILLED", "filled_qty": 20, "avg_fill_price": 714.62,
  "fill_time_et": "2026-11-23T11:15:07", "limit_price": 714.61,
  "cash_before": 99990.765, "cash_after": 85698.37,
  "submitted_at_et": "2026-11-23T11:15:01"}]
```

Pass it on the **next** cycle with `--fills-json`. The engine derives its
position state from these records, so an unreported fill means the engine
believes it is flat while it is not — and the position has no stop.

A fill also adds an `order_id` the ledger now knows, so it stops reading as a
foreign order.

---

## 5. Cancels

The cancel response carries **only an order-id echo, no status**. `ret_code: 0`
does not mean cancelled. Poll the order record until `status == 5`, up to 30
seconds. Measured server-side latency is ~2 s.

Cancel an unfilled entry if it has rested more than 300 s. Report it as a
fill record with `"verdict": "CANCELLED"` and `filled_qty: 0`.

---

## 6. Session close

Run a final cycle at **15:50 ET** (12:45 ET on 27 Nov). Any open position
must flatten — that is the session stop and it is not negotiable. If the
driver emits an exit intent, submit it on approval; if a position is still
open after that, escalate immediately rather than leaving it overnight.

Then once, at the close:

```
git add logs/decisions_live.jsonl
git commit -m "Live session <date>: <n> cycles, <n> entries, <n> exits, P&L <x>"
git push
```

Once per session, not per cycle.

---

## 7. Failure modes

| Symptom | Meaning | Do |
|---|---|---|
| `ConfigError: commit … does not contain [engine/…]` | the engine is uncommitted | commit and push, then rerun. Do **not** work around it |
| `engine files modified on disk` warning | tree dirty; the pin does not describe running code | report. Safe for a read-only cycle, not for a live one |
| `HALT — account guard` | broker identity ≠ config | **STOP.** Never substitute an account. Report verbatim |
| `FOREIGN_ORDER_DETECTED` | an order in the account the ledger cannot attribute | **STOP.** Report the order id and its `text`. This is the second-writer control; do not add ids to config to silence it |
| `ACCOUNT_SNAPSHOT_INCONSISTENT` | `balance + mv + hold ≠ total_asset` | snapshot is mid-transition. Refetch and rerun; do not reconcile by hand |
| `STALE_OR_INVALID_BARS` | newest closed bar older than 30 min | refetch. If it persists, the feed is behind — report |
| `ESCALATE … NO_QUOTE_FOR_EXIT` | an exit is required and there is no quote to price it | **STOP and report immediately.** A position needs to leave and the engine cannot price it |
| `CYCLE_GAP_EXCEEDED` | a position was held across a gap longer than 180 s | expected when cycles are manual and something is held. New entries are blocked; exits are not |
| `DAILY_LOSS_HALT` / `DRAWDOWN_FLATTEN` / `CONSECUTIVE_LOSS_STOP` | a kill switch tripped | **STOP.** Report. Do not override, do not edit config to continue |
| `ATR_BELOW_COST_GATE` / `COST_GATE_FAIL` | the setup does not clear its own costs | correct refusal. Log it and move on |
| `NOT_A_TRADING_DAY` | weekend or holiday | nothing to do |
| position mismatch warning | ledger-derived positions ≠ broker | report both. The disagreement is the finding; do not pick a side |

### The standing rule

If the engine refuses, the refusal is the output. Do not edit
`config/risk_limits.yaml`, do not pass different inputs to get a different
answer, and do not submit an order the driver did not emit. A limit that can
be worked around under time pressure was never a limit.
