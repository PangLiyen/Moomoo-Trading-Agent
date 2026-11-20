# Session 00 — Capability Probe & First Supervised Trade
**Date:** Fri 20 Nov 2026 · US session · Operator-supervised, NOT autonomous

The goal tonight is **not** profit and **not** automation. It is to establish ground truth
about what the simulated trading API will and will not do, before we design around
assumptions. Every answer below goes into `docs/API_CAPABILITIES.md` and is cited in the
submission.

> Run every step in Qoder Desktop with the moomoo connector enabled. Paste the raw tool
> output into the repo. Do not summarise — the raw responses are evidence.

---

## Timings (Malaysia / SGT, UTC+8)

| Event | Local time |
|---|---|
| US pre-market opens | 5:00 PM |
| US regular open | 10:30 PM |
| US regular close | 5:00 AM (+1d) |

Probe steps 1–5 can be done **any time** — they are read-only and do not need the market
open. Step 6 onward needs the regular session (from 10:30 PM).

---

## Step 1 — Identify the competition account (BLOCKING)

Nothing else matters if this is wrong. Trades in any other paper account do not count.

**Prompt:**
```
List all my simulated trading accounts using sim_trade_account_list.
Show the full raw response: every acc_id, account name, account type, market, and currency.
Do not place any orders.
```

**Record:**
- [ ] `competition_acc_id` = ____________________
- [ ] Exact account name as returned = ____________________
- [ ] Confirm this matches the name shown in the moomoo app under
      Discover → Paper Trade → Competition
- [ ] Note every OTHER acc_id so we can hard-blocklist them in config

⚠️ If more than one account looks plausible, **stop and check the app**. Do not guess.

---

## Step 2 — Confirm balance and buying power

**Prompt:**
```
Using sim_trade_cash_info for account <competition_acc_id>, show cash, total assets,
and buying power. Raw response please. Do not place any orders.
```

**Record:**
- [ ] Starting equity (expect ~USD 100,000) = ____________
- [ ] Buying power = ____________
- [ ] Does it report margin, or cash-only?

> Note: `timón`'s author reported `sim_trade_cash_info` was **blocked** because their
> connector was authorised for market data only. If you get a permission error here, the
> OAuth scopes are the problem — re-authorise the connector and tick the trading scopes.

---

## Step 3 — Order-type support (THE critical probe)

This determines whether we can rest a protective stop on the broker, or must enforce
stops in software.

**Prompt:**
```
Describe the sim_trade_input_order tool in full: list every parameter it accepts and
every value each parameter supports, especially order_type, time_in_force, and session.
Quote the tool schema exactly. Do not place any orders yet.
```

**Record which of these `sim_trade_input_order` actually accepts:**

| Capability | Supported? | Notes |
|---|---|---|
| `LIMIT` | | |
| `MARKET` | | |
| `STOP` | | ← decisive |
| `STOP_LIMIT` | | ← decisive |
| `TRAILING_STOP` | | |
| `time_in_force: DAY` | | |
| `time_in_force: GTC` | | |
| `session: RTH` | | |
| `session: RTH+Pre/Post-Mkt` | | |

**Decision rule:**
- If `STOP` / `STOP_LIMIT` **are** supported → rest protective stops on the broker.
  Software check becomes a backstop, not the primary control.
- If they are **not** supported → stops are software-only. The agent must then run on a
  tight cycle, and **max holding time becomes a hard risk parameter**, because an
  unmonitored position has no floor. We document this honestly as a known limitation.

Either answer is fine. An undocumented assumption is not.

---

## Step 4 — Fee and cost model

Almost nobody will do this, and it is cheap credibility.

**Prompt:**
```
For account <competition_acc_id>, check whether account_order_fees returns fee data for
simulated orders. Also check what trading_margin_ratio reports. Raw responses. No orders.
```

**Record:**
- [ ] Are simulated fills charged commission/fees? Y / N
- [ ] If yes, what structure?
- [ ] If no → our P&L is gross, and we must say so explicitly in the submission rather
      than quietly presenting gross returns as net

---

## Step 5 — Quote freshness and spread

**Prompt:**
```
Using quote_stock_quote and quote_order_book for US.SPY, US.QQQ, US.IWM, US.XLF, US.XLE:
show last price, bid, ask, bid size, ask size, and the exact timestamp of each quote.
Tell me how old each quote is relative to now. No orders.
```

**Record:**
- [ ] Does the response carry a usable timestamp we can compute staleness from? Y / N
- [ ] Typical bid-ask spread on each name during the session
- [ ] Is `quote_order_book` available on the paper/competition entitlement?

This feeds the `STALE_QUOTE` and `SPREAD_TOO_WIDE` rejection rules.

---

## Step 6 — First supervised trade (regular session only, after 10:30 PM)

Smallest possible real test. **You approve every step by hand.**

**Prompt:**
```
In account <competition_acc_id> ONLY, place a LIMIT BUY for 1 share of US.SPY at a
limit price 0.1% below the current bid. time_in_force DAY, session RTH.
Before submitting, show me the exact parameters you will send and wait for my approval.
After submission, show the raw response including order_id and need_order_confirm.
```

**Record:**
- [ ] Exact parameters sent
- [ ] Raw response
- [ ] Was `need_order_confirm` true? (If so, `trading_order_confirm` is required —
      note that this is a second round-trip the agent must handle)
- [ ] Order ID

Then:
```
Query the status of order <order_id> using account_orders_active and
sim_trade_history_order_list. Show raw responses. Did it fill? At what price?
```

**Record:**
- [ ] Time from submission to fill
- [ ] Fill price vs limit price → **this is your first real slippage datapoint**
- [ ] Does an unfilled order persist, or expire?

---

## Step 7 — Exit and cancel path

```
Cancel any unfilled portion using sim_trade_cancel_order. If filled, place a LIMIT SELL
for the same 1 share. Show raw responses for both. Then show sim_trade_position_list.
```

**Record:**
- [ ] Does cancel work and how fast?
- [ ] Does the position list reflect changes immediately, or with a lag?
- [ ] Round-trip cost (buy fill → sell fill) on 1 share

---

## Step 8 — Close out clean

- [ ] `sim_trade_position_list` shows **zero** open positions
- [ ] `account_orders_active` shows **zero** working orders
- [ ] Commit all raw outputs to the repo under `evidence/session-00/`

---

## What we learn and why it matters

| Probe | Feeds into |
|---|---|
| Account ID | Hard account guard — fail-closed if it doesn't match |
| Order types | Whether stops are broker-rested or software-enforced |
| Fees | Honest net-vs-gross P&L reporting |
| Quote timestamps | `STALE_QUOTE` rejection rule |
| Fill vs limit | Slippage assumption in the strategy spec |
| `need_order_confirm` | Execution agent must handle a two-step confirm |
| Cancel latency | How fast the kill switch can actually flatten |

Three of these are things the public competitor repos state as assumptions rather than
measurements. Measuring them is a differentiator on its own.
