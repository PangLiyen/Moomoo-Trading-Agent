# Measured API Capabilities

**Probed:** Fri 20 Nov 2026, ~22:35–22:38 MYT (09:35–09:38 ET, regular session open)
**Method:** read-only MCP calls from Qoder Desktop. No orders placed.

Everything here is **measured**, not assumed. Where a probe failed, it is recorded as
unverified rather than guessed. Raw responses are in `evidence/session-00/`.

---

## 1. Accounts

```json
{"accounts":[
 {"account_id":"5852462","account_title":"Malaysia Stock Paper Trading","account_type":1,"market_id":17},
 {"account_id":"5852463","account_title":"US Margin Account [Paper Trading]","account_type":1,"market_id":100},
 {"account_id":"5852464","account_title":"HK Stock Paper Trading","account_type":1,"market_id":1},
 {"account_id":"5832647","account_title":"Moomoo x Qoder AI Paper Trading Agent Challenge","account_type":2,"market_id":100}
]}
```

| Field | Value |
|---|---|
| **Competition account** | **`5832647`** |
| Exact name | `Moomoo x Qoder AI Paper Trading Agent Challenge` |
| `market_id` | `100` (US) |
| `account_type` | `2` |
| Blocklisted | `5852462`, `5852463`, `5852464` |

### Finding: the agent selected the wrong account unprompted

On first run the agent chose `5852463` ("US Margin Account [Paper Trading]") on the
reasoning that it is *the* US paper account, and reported its balance of 1,000,000 as if
it were the competition balance. It was not.

This is why `account.require_name_match` is `true` and why the guard is **fail-closed**
and re-verified on **every cycle**, not once at startup. A plausible-looking account is
the most dangerous kind.

### Finding: `account_type` is a second, independent discriminator

`5832647` is the only account with `account_type: 2`; the three generic paper accounts
are all `account_type: 1`. Name matching alone relies on a string that can be edited in
the moomoo UI. Checking the type as well means the guard needs two independent signals
to agree before any order is proposed. Added to config as `expected_account_type: 2`.

### Finding: the id field has two different names

`sim_trade_account_list` returns it as **`account_id`**. `sim_trade_cash_info` and
`sim_trade_input_order` accept it as **`acc_id`**. Same value, two spellings depending on
direction. Code must map between them explicitly rather than assume one name holds
throughout — a silent `KeyError` here fails open if the surrounding call is written
defensively, which is exactly the wrong direction for an account guard.

### Not measured

- `sim_trade_account_list` returns **no currency field**. Currency is inferred from
  `market_id` (100→USD) only. Recorded as inferred, not measured.
- `account_type` has **no documented enum**. `1` = generic paper, `2` = competition is
  read off the names, not off documentation.
- `market_id: 17` on the Malaysia account is inconsistent with the `enable_market` enum
  used by `account_authorized_trd_accs`, where 17 is event-driven contracts and Malaysia
  is 11. The sim-trade market enum is undocumented. Irrelevant to us — we trade `100`
  only — but it confirms that undocumented enums on this API should not be assumed to
  agree across tool families.

---

## 2. `sim_trade_input_order` — order capability (DECISIVE)

Schema as returned:

```json
{"type":"object","properties":{
  "acc_id":{"type":"string"},
  "market":{"type":"integer"},
  "order_side":{"enum":[1,2],"type":"integer"},
  "order_trade_time_type":{"enum":[0,1,2],"type":"integer"},
  "order_type":{"enum":[1,3],"type":"integer"},
  "price":{"type":"string"},
  "qty":{"type":"string"},
  "symbol":{"type":"string"},
  "text":{"maxLength":100,"type":"string"}
},
"required":["acc_id","market","symbol","order_type","order_side","qty"]}
```

| Capability | Supported | Evidence |
|---|---|---|
| `LIMIT` | **Yes** (`order_type: 1`) | enum |
| `MARKET` | **Yes** (`order_type: 3`) | enum |
| `STOP` | **No** | absent from enum |
| `STOP_LIMIT` | **No** | absent from enum |
| `TRAILING_STOP` | **No** | absent from enum |
| `time_in_force` | **Parameter does not exist** | absent from schema |
| Session control | `order_trade_time_type`: `0` not set, `1` intraday only, `2` incl. pre/post | enum |

Note: the full `trading_order_place` tool (live trading) advertises stop, trailing-stop
and algo order types. **The simulated tool does not.** Capability on the live endpoint
tells you nothing about the paper endpoint — a trap worth stating in the submission.

### Consequences

**C1 — Stops are software-enforced.** No protective order can rest at the broker.
A position is unprotected between agent cycles.

**C2 — Cycle cadence is a risk parameter.** `max_holding_minutes` and
`software_stop_check_interval_seconds` are now risk controls, not conveniences.

**C3 — Scheduling can silently drop a protective cycle.** Qoder Forward Mode schedules
default to `max_concurrent_runs: 1`. If a run overruns its interval, the next trigger is
recorded as **skipped** with `concurrency_limit_reached`. A slow cycle can therefore
swallow the exact cycle that would have closed a losing position.
**Mitigations:** keep the cycle short; monitor skipped runs; and treat a missed cycle as
a risk event via `max_cycle_gap_seconds` — if the gap is exceeded, flatten rather than
continue, because the position was unmonitored for an unknown period.

**C4 — Size for a slipping stop.** The stop *triggers* at 1.0 × ATR20 but may *fill*
worse, because execution waits for the next cycle. Sizing therefore uses
`1.0 + 0.5 = 1.5 × ATR20` as the assumed risk distance, keeping realised risk inside
0.35% even when the stop is late.

**C5 — No TIF means unfilled-order lifetime is unknown.** Must be measured empirically
in step 6: does an unfilled limit order persist across cycles, or expire at the close?
Until measured, the executor cancels its own unfilled orders after 300s rather than
relying on broker behaviour.

### Two capabilities worth using

- **`order_trade_time_type: 1`** (intraday only) is set on *every* order. Extended-hours
  fills in thin liquidity become impossible by construction, not by convention. The deck
  warns that extended-hours volume causes wide spreads, partial fills and stranded limit
  orders; this closes that off structurally.
- **`text` (100 bytes, stored on the order)** carries the decision's ledger hash. This
  binds moomoo's own order record to our hash chain from outside our system — provenance
  a judge can verify against the broker's data rather than only against our log.

> `acc_id` is documented as "Full 64 Bits" but the account list returns 7-digit IDs, and
> those are what work. Documentation and behaviour disagree; behaviour wins.

---

## 3. Fees — MEASURED

The documented fee endpoints are dead:

| Tool | Result |
|---|---|
| `account_order_fees` | `MCP error -32603: MCP tool invocation did not complete` |
| `trading_margin_ratio` | `MCP error -32603` across four parameter combinations |

Identical error for a valid paper `acc_id` and an invalid one, so this is a tool-level
failure, not an account rejection. These are live-trading endpoints and appear not to be
entitled under the current OAuth scope. They are not needed.

### Commission = **$0.99 per filled order**

Confirmed three independent ways from the 20 Nov 10:02 ET round trip.

**1. The `hold` reservation, exact.** On submission the API freezes limit price plus
commission:

| Order | Limit | `hold` | Difference |
|---|---|---|---|
| 3128953 buy | 716.75 | 717.74 | **0.99** |
| 3128955 buy | 709.50 | 710.49 | **0.99** |

Two orders, two exact hits, no rounding. This is the strongest of the three because it
is not a residual — the API states it directly.

**2. The cash residual across all fills.**

```
total_asset  100000.000 -> 99996.565      = -3.435
  SPY round trip  715.655 - 715.83        = -0.175
  ANT unrealised   91.36  - 91.64         = -0.280
  explained                                 -0.455
  unexplained                               -2.980   / 3 fills = -0.9933
```

Three *filled* orders (ANT buy, SPY buy, SPY sell). The 0.9933 rather than 0.9900 is
ANT's mark drifting between snapshots, not a different fee.

**3. The resting order was not charged.** Order 3128955 never filled and consumed no
commission — only the `hold` reservation. Commission is charged **on fill, not on
submission**.

### Consequences

- **Pre-trade cost is exact, not estimated.** Because the fee is reserved in `hold` at
  submission, the risk gate can check cost before the fill rather than reconcile after.
- **Round-trip cost floor is $1.98.** `fees.min_expected_edge_to_cost_ratio: 5.0` means
  a setup must expect to clear roughly $10 of move to be worth taking.
- **Small positions are fee-dominated.** One share of a ~$715 instrument costs 0.28%
  round trip in commission alone — more than the entire expected edge of a mean-reversion
  scalp. Hence `fees.min_position_notional_usd: 2000`.
- **P&L is reported net, with the measurement shown.** No ambiguity about gross vs net
  in the submission.

> `total_asset = balance + mv + hold` reconciled **exactly** in both snapshots. Adopted
> as a per-cycle invariant assertion: if it fails, the account snapshot is mid-transition
> and must not be used for a sizing decision.

---

## 3b. Execution behaviour — MEASURED

### Aggressive limits fill at prevailing market, not at the limit

Four fills, every one improved on its limit price:

| Order | Side | Limit | Fill | Improvement |
|---|---|---|---|---|
| 3128953 SPY | buy | 716.750 | 715.830 | **+0.920** |
| 3128956 SPY | sell | 715.300 | 715.655 | **+0.355** |
| 3128921 ANT | buy | 91.761 | 91.640 | +0.121 |
| 3128965 ANT | sell | 91.620 | 91.625 | +0.005 |

The limit functions as a cap/floor only. A limit priced through the book does not
pay the limit — it pays the market.

**This is the single easiest number to misreport.** "Fill vs limit" looks like
slippage and is the opposite of it: price *improvement* from an aggressive limit.
Logging it as slippage would show the agent beating the market on every trade,
which is both wrong and the kind of flattering error a judge will find. Real
slippage is measured against the **decision-time quote**. The ledger records both
fields, separately and distinguishably.

### Round-trip cost, and why the minimum position size went up

SPY 1-share round trip: bought 715.83, sold 715.655 → **$0.175/share**, 0.0244% of
notional, on a quoted spread of ~0.0056%. Commission is fixed at $1.98 per round
trip, so total cost falls steeply with size:

| Shares | Notional | Exec cost | Commission | Total | % of notional | Cost / 0.5·ATR20 edge |
|---|---|---|---|---|---|---|
| 1 | $716 | 0.175 | 1.98 | 2.155 | 0.3010% | — |
| 3 | $2,147 | 0.525 | 1.98 | 2.505 | 0.1166% | **23.9% — fails** |
| 10 | $7,158 | 1.750 | 1.98 | 3.730 | 0.0521% | 10.7% |
| 20 | $14,317 | 3.500 | 1.98 | 5.480 | 0.0383% | 7.8% |

`min_expected_edge_to_cost_ratio: 5.0` requires cost ≤ 20% of gross edge. At the
original `min_position_notional_usd: 2000` a SPY trade **fails that test** — so the
floor was raised to 3000. The real gate is the ratio, evaluated per trade with that
trade's own ATR; the notional floor is a backstop for when ATR is unavailable.

> ATR20 ≈ $7 on SPY is an assumption, not a measurement. The ratio gate makes the
> error self-correcting — a smaller true ATR raises the required size automatically —
> but the number itself still needs measuring.

---

## 3c. Account integrity — a second writer

Two orders in `5832647` were not ours:

| order_id | Time ET | Symbol | Side | `text` | `order_trade_time_type` |
|---|---|---|---|---|---|
| 3128921 | 09:55:36 | ANT | buy | `""` | **2** |
| 3128965 | 10:04:55 | ANT | sell | `""` | **2** |

`3128965` arrived **2.5 minutes after our last API write**, so this is a concurrent
actor, not an artefact of our own calls. Source unidentified at time of writing;
the leading candidates are a teammate exploring the API on the shared login, or an
already-live Forward Mode schedule.

**Why this is a stop-work item rather than a curiosity.** The hash chain proves our
records were not edited after the fact. It proves nothing about orders we never
placed. With a second writer, account P&L cannot be attributed to our decisions,
and attribution is the entire basis of the submission. An agent that averages
someone else's trades into its own reported performance is not making an honest
claim, however good its logging.

**Detection, two independent fingerprints.** Our orders always carry an `S`-prefixed
tag in `text`; both foreign orders had `text: ""`. Our orders always set
`order_trade_time_type: 1`; both foreign orders used `2`. Either alone identifies a
foreign write. Config now reconciles the full order list and the position list on
**every cycle**, and `on_untagged_order_detected: halt`.

> The `text` field was added for ledger-hash stamping. It turned out to be the thing
> that detected an unknown actor in the account on day one. Worth saying plainly in
> the submission: the provenance mechanism earned its place before the strategy ran.

---

## 3d. Measured market structure — 9 sessions of 5-minute bars

Window 2026-11-09 to 11-19, 693 RTH bars per symbol. `quote_history_kline` with
`ktype: 6`, `extended_time: 0`. **Only 9 trading days were available** — the feed had
no bars for 11-20 — so "current" is the 11-19 15:55 ET bar.

| | SPY | QQQ | IWM | XLF | XLE |
|---|---|---|---|---|---|
| Price | 717.56 | 647.00 | 264.92 | 55.645 | 60.986 |
| ATR20 current | 1.6840\* | 2.1417\* | 0.5666 | 0.0963 | 0.1092 |
| ATR20 median | 1.0673 | 1.5938 | 0.4259 | 0.0704 | 0.1173 |
| ATR20 min | 0.4651 | 0.7120 | 0.2003 | 0.0352 | 0.0586 |
| ATR20 as % of price | 0.33% | 0.34% | 0.21% | 0.17% | 0.18% |

\* bad ticks excluded — see below.

**The earlier ~$7 figure was wrong by 3-70x.** It was a daily-scale ATR applied to a
5-minute-bar strategy. Since the target move is 1.5·ATR20, that was a 3-70x error in
expected edge, and it propagated into the first cost table.

### The cost gate, derived rather than chosen

`min_atr20_usd` is the ATR at which the capped position stops clearing round-trip cost
by the 5.0 ratio:

| Symbol | qty at cap | Gate | Median ATR | Min ATR | Verdict |
|---|---|---|---|---|---|
| SPY | 20 | 0.4633 | 1.0673 | 0.4651 | clears, barely at quietest |
| QQQ | 23 | 0.4870 | 1.5938 | 0.7120 | clears |
| IWM | 56 | 0.1512 | 0.4259 | 0.2003 | clears |
| XLF | 269 | 0.0579 | 0.0704 | **0.0352** | **gate bites** |
| XLE | 245 | 0.0603 | 0.1173 | **0.0586** | **gate bites** |

XLF and XLE both went below their gate during the sample. Those trades would have been
correctly refused. The gate is live, not decorative.

### Cost basis correction

The $0.175/share from the SPY probe was a **27-second** round trip: ~$0.04 of spread
plus ~$0.135 of adverse drift. Drift is what the strategy is trying to capture, so
charging it as a transaction cost double-counts it. Cost modelling uses quoted spread.

At spread-based costs every symbol passes comfortably — 4.5% to 16.4% of gross edge
against the 20% limit.

### Signal frequency — the question nothing else answered

Bars satisfying all of `close < SMA20 − 1.5·ATR20`, `close > prior close`,
and 10:00-15:00 ET:

| | SPY | QQQ | IWM | XLF | XLE | total |
|---|---|---|---|---|---|---|
| 9-session total | 35 | 29 | 41 | 31 | 21 | **157** |
| per day | 3.9 | 3.2 | 4.6 | 3.4 | 2.3 | **17.4** |

**~78 signals over 4.5 competition sessions against a cap of 27 trades.** The binding
constraint is the frequency budget, not signal scarcity. The strategy is selective
rather than starved — which was not knowable before measuring, and would have been a
fatal discovery on day one.

### The notional cap, not the risk budget, is what binds

On every symbol, `max_position_notional_pct: 15.0` caps size below what
`risk_per_trade_pct: 0.35` would allow:

| | Risk budget allows | Cap allows | Realised risk/trade |
|---|---|---|---|
| SPY | 138 sh | 20 | $50.52 (0.05%) |
| QQQ | 108 | 23 | $73.89 (0.07%) |
| IWM | 411 | 56 | $47.59 (0.05%) |
| XLF | 2,422 | 269 | $38.86 (0.04%) |
| XLE | 2,136 | 245 | $40.13 (0.04%) |

So **0.35% is a ceiling never approached** — realised risk is 0.04-0.07%, 5-9x more
conservative than the stated limit. This is structural: making 0.35% binding on SPY
would require a position of roughly 99% of equity.

Worth stating plainly rather than leaving a number in the config that implies more risk
is being taken than is. The honest version of the trade-off: this caps upside. At ~27
trades risking ~$50 each, total return over the competition is likely well under 1%
whatever the win rate. That is a deliberate consequence of a trust-led entry, not an
oversight — but it is a consequence, and the submission should own it.

### Data quality — two bad ticks, found and excluded

| Symbol | Bar | Range | vs median |
|---|---|---|---|
| SPY | 11-19 15:55 | 15.05 | **16.0x** |
| QQQ | 11-19 11:55 | 22.29 | **16.6x** |

Each inflates ATR20 for the following 20 bars — precisely the input the cost gate
depends on. Excluding them moved SPY's current ATR20 from 2.3523 to 1.6840, a 28%
overstatement. IWM, XLF, XLE were clean, with no bar exceeding 10x median range.

**Consequence for the live agent:** an outlier filter on incoming bars is not optional.
A single bad print silently raises ATR20 for the next 100 minutes, which widens stops,
shrinks position size, and loosens the cost gate — all in the direction of taking worse
trades while reporting a healthy risk profile.

### Feed quirks

- `end` is ignored; results truncate at `num` bars forward from `start`
- `num` caps at 370, so 10 days of 5-minute RTH bars needs paging
- `quote_history_kline` silently omitted 11-19 for some symbols; recoverable via
  `quote_cur_kline`
- A missing trading day is returned as absence, not an error. Bar counts must be
  asserted against expected session count, never assumed

---

## 4. Quotes and microstructure

Measured at 09:37:43 ET, market open.

| Symbol | Last | Bid | Ask | Spread | Spread % of mid |
|---|---|---|---|---|---|
| SPY | 713.83 | 713.83 | 713.87 | 0.04 | 0.0056% |
| QQQ | 637.07 | 637.00 | 637.06 | 0.06 | 0.0094% |
| IWM | 264.37 | 264.45 | 264.46 | 0.01 | 0.0038% |
| XLF | 55.825 | 55.83 | 55.84 | 0.01 | 0.0179% |
| XLE | 60.82 | 60.80 | 60.81 | 0.01 | 0.0164% |

All five sit comfortably under the 0.05% threshold in `risk_limits.yaml`, which is
therefore a live gate rather than a dead one — tight enough to catch a genuinely bad
moment, loose enough not to block normal trading.

### Finding: two different timestamps, and using the wrong one is a real error

- `data_time` on `quote_stock_quote` is the **last trade time** (~9 s old at probe)
- the order book carries its **own, fresher** timestamp (~4 s old)

A staleness check that reads `data_time` and concludes the *book* is 9 seconds old is
measuring the wrong thing. The staleness gate uses the **book timestamp** for
executability and `data_time` only for trade-print recency, and records both in the
ledger.

This is the deck's warning made concrete: a displayed last price is not necessarily an
executable price.

### Finding: order book is not consolidated NBBO

The feed returned **NASDAQ and ARCA only** — two venues, not the full US consolidated
book. Displayed sizes therefore **understate** true available depth.

Consequence: we do **not** use displayed size for liquidity-based sizing, because the
input is known-incomplete. Sizing stays risk-based off ATR. Stated as a limitation
rather than silently relied upon.

---

## 5. Summary of config changes forced by this probe

| Setting | Value | Driver |
|---|---|---|
| `account.competition_acc_id` | `"5832647"` | §1 |
| `account.blocklist_acc_ids` | the other three | §1 |
| `execution.broker_rested_stops` | `false` | §2 |
| `execution.order_type` | `1` (Limit) | §2 |
| `execution.order_trade_time_type` | `1` (intraday only) | §2 |
| `execution.stamp_ledger_hash_in_text` | `true` | §2 |
| `sizing.stop_slippage_buffer_atr_mult` | `0.5` | C4 |
| `kill_switch.max_cycle_gap_seconds` | `180` | C3 |
| `data_quality.staleness_source` | book timestamp | §4 |
| `sizing.use_displayed_depth` | `false` | §4 |

---

## 6. Still open

1. **Competition account starting equity** — step 2 was run against the wrong account.
   Must re-run against `5832647`. Blocking for sizing.
2. **Unfilled order lifetime** without TIF — measure in step 6.
3. **`need_order_confirm`** on simulated orders — does the two-step confirm fire?
4. **Fill vs limit slippage** — first real datapoint comes from step 6.
5. **Fee residual** — compute from the step 6 round trip using the formula in §3.
