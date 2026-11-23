# CONTEXT.md — where this project stands

Handoff note. `AGENTS.md` holds the operational rules an agent must obey; this
file holds the *state*: what has been decided, what has been measured, what is
still open, and why things are the way they are. Written so that someone — or
some agent — with no access to the planning conversation can continue.

Last updated: 2026-11-20, end of the Session 00 capability probe.

---

## 1. What this is

Team entry in the **2026 Moomoo x Qoder AI Trading Agent Challenge**: an AI
paper-trading agent running on Qoder Cloud Agents against the Moomoo OpenAPI
MCP connector. Final in Singapore.

Competition account: **`5832647`**, $100,000 paper, US market.

## 2. The thesis, in one paragraph

Roughly **75% of the score is not P&L** — it is strategy design, risk control
and evidence. Over ~4.5 trading sessions no one's return is statistically
meaningful, and the judges know it. So the entry does not try to win on
return. It tries to be the one entry that can *prove* what it did: that the
model proposed and code decided, that every limit was enforced rather than
advised, that refusals were recorded as carefully as fills, and that
attacker-controlled text could never reach order execution.

Anyone can get an LLM to place trades. The claim here is narrower and harder:
**the LLM cannot place a trade that breaks the rules**, and there are receipts.

### What that costs us, stated plainly

The notional cap binds before the risk budget on every symbol, so realised
risk per trade is 0.04–0.07% against a stated ceiling of 0.35%. At ~27 trades
risking ~$50 each, total return will likely land well under 1% whatever the
win rate. That is a deliberate trade — conceding most of the P&L quarter to be
unimpeachable on the other three — not an oversight. It is structural, too:
making 0.35% binding on SPY would need a position worth ~99% of equity.

If the team ever wants more P&L exposure, the lever is
`sizing.max_position_notional_pct`, and the price is evidence quality for
variance. Decide it deliberately, not in Singapore.

## 3. The strategy, in one paragraph

Intraday mean reversion on five liquid ETFs (SPY, QQQ, IWM, XLF, XLE), 5-minute
bars, regular hours only, long only. Enter when price dislocates more than
1.5·ATR20 below SMA20 **and** has ticked back up **and** SPY is above its own
SMA60 (no downtrend) **and** the quote is fresh, the spread tight, and budgets
unexhausted. Exit at SMA20, or 1.0·ATR20 against, or 60 minutes, or 15:50 ET —
whichever is first.

The edge is not the contribution. The governance is. `docs/STRATEGY.md` is the
pre-committed spec and was committed **before** the first strategy trade; that
ordering is the whole reason it counts as evidence rather than as a story told
afterwards.

## 4. Session 00 — what was measured, 2026-11-20

Full working in `docs/API_CAPABILITIES.md`. The headlines, because each one
changed a design decision:

| Finding | Consequence |
|---|---|
| **No stop orders exist** — `order_type` is `{1 Limit, 3 Market}` only | Stops are enforced in software by polling. Sizing adds a 0.5·ATR buffer so a late stop still lands inside the risk budget |
| **No `time_in_force` input** | `order_trade_time_type: 1` on every order. Extended-hours fills impossible by construction |
| **Commission $0.99/filled order**, reserved in `hold` as `limit + 0.99` exactly | Pre-trade cost is exact, not estimated. Round trip $1.98 |
| **Aggressive limits fill at market, not at the limit** | Fill-vs-limit is price *improvement*. Logging it as slippage would show the agent beating the market on every trade |
| **Cancel response carries no status** | `ret_code: 0` is not confirmation. Poll until `status == 5` or the executor fails open |
| **`total_asset = balance + mv + hold`** reconciles exactly | Per-cycle invariant. A snapshot that fails it is mid-transition and must not be sized against |
| **Measured ATR20 is $0.096–$2.35**, not the ~$7 assumed early on | The early figure was a *daily* ATR against a 5-minute strategy. Cost gates re-derived |
| **Signal fires 17.4×/day** across the universe | ~78 signals over 4.5 sessions against a cap of 27 trades. Selective, not starved |
| **Two bad ticks in 9 sessions**, 16× median range | An outlier filter is mandatory. One bad print raises ATR20 for 100 minutes, widening stops *and* loosening the cost gate while risk still reports healthy |

### Two near-misses worth remembering

**The wrong account.** On the first probe run an agent chose `5852463`
("US Margin Account [Paper Trading]") unprompted — it is the obvious US paper
account — and reported its $1,000,000 balance as the competition balance. The
real figure is $100,000. Sizing off it would have made every position 10× too
large. This is why the account guard checks id, exact title *and* type, on
every cycle rather than once at startup.

**A second writer.** Two untagged orders appeared in `5832647` during the
probe (`3128921` ANT buy 09:55:36 ET, `3128965` ANT sell 10:04:55 ET), the
second of them 2.5 minutes after our last API write. Source still
unidentified. The `text` tag — added for ledger-hash stamping — is what
detected it. The provenance mechanism earned its place before the strategy
ran.

## 5. Code layout

```
AGENTS.md                      standing rules for any agent in this repo
CONTEXT.md                     this file
config/risk_limits.yaml        every enforced bound; single source of truth
docs/STRATEGY.md               pre-committed trading rule
docs/API_CAPABILITIES.md       measured API behaviour vs documented
docs/DECISION_LOG_SCHEMA.md    ledger record format
docs/SESSION_00_PROBE.md       the capability-probe runbook
docs/COMPETITION_BRIEF.md      scoring, field, timeline, team structure
analysis/                      5-min bar data + compute_indicators.py
engine/                        deterministic core (see below)
tests/                         310 tests; the evidence for the claim
```

`engine/`, strictest layer first. Nothing lower imports anything higher.

| Module | Role |
|---|---|
| `errors.py` | Vocabulary of refusal. Nothing here is catch-and-continue |
| `ledger.py` | SHA-256 hash chain, fsync'd appends |
| `config.py` | Fail-closed loader. Absence is not permission |
| `sizing.py` | Pure function: inputs → quantity, plus its full arithmetic |
| `risk_gate.py` | The last gate. May only tighten or refuse |

No module in `engine/` performs network I/O, reads free text, or consults a
model. That is what makes the limits enforceable rather than advisory.

## 6. What the tests are for

`python3 -m pytest tests/ -q` → 310 passing.

They are not regression cover. They are the evidence for the submission's
central claim, so each one names a rule and demonstrates enforcement. Two
matter most:

- `test_gate_never_increases_quantity_across_randomised_states` — 2,000
  randomised account and session states asserting the gate only ever shrinks
  or refuses. Tests the property rather than trusting each branch.
- `test_editing_and_rehashing_one_record_is_still_detected` — a careful forger
  recomputes the hash of the record they edit. Still caught, because every
  later record carries the old `prev_hash`.

And one deliberately demonstrates a **weakness**:
`test_tail_truncation_is_NOT_detectable_internally` asserts that dropping
records from the end of the chain *passes* verification. It does: seq runs
1..n, every `prev_hash` matches. That gap is closed externally, by stamping
each order's ledger hash into moomoo's own `text` field where we cannot edit
it. The test stays because a suite that only demonstrates strengths
misrepresents the mechanism — and a judge who finds the hole after we claimed
completeness is the bad outcome.

## 7. Open items

**Blocking the executor:**

1. **Order `3128955`** (SPY limit 709.50, `text: S00-PROBE-002`) was left
   resting deliberately on 20 Nov. Check it before Monday's first cycle.
   Gone → `order_trade_time_type: 1` behaves as a day order and no
   end-of-session cancel sweep is needed. Still there → the sweep is needed.
   Note the confound: if the second actor cancelled it, "gone" proves nothing.
2. **Identify the second writer.** The engine halts on any unattributed order
   (`FOREIGN_ORDER_DETECTED`), which is correct and also means it will refuse
   to trade on day one if this is unresolved. Leading candidates: a teammate
   on the shared Moomoo login, or an already-live Forward Mode schedule.

**Not blocking:**

3. `execution.max_holding_minutes: 60` is still provisional. Measured
   submit-to-fill is ~7s and cancel ~2s, so the figure can now be set from
   measurement rather than guessed.
4. Whether `need_order_confirm` ever fires on simulated orders — it did not on
   any of the four probe orders, so the executor currently assumes no
   two-step confirm path.

## 8. Next build steps

- **Executor** — the only module that talks to moomoo. Needs: the status-poll
  discipline for cancels, the `status == 4 and cum_qty == qty` fill rule, the
  outlier filter on incoming bars, and ledger-hash stamping into `text`.
- **Signal module** — the 7-condition entry and 5 exit conditions from
  `docs/STRATEGY.md`, over `analysis/compute_indicators.py`'s indicators.
- **Injection test suite** — `news_search` and `quote_community_search` return
  attacker-influenceable text. The differentiating claim is that such text can
  never reach order-execution logic, which needs adversarial cases
  demonstrating it, logged with `INJECTION_SUSPECTED` and the payload
  quarantined verbatim.
- **Deploy to Qoder Cloud Agents** — Vault credentials, skill packaging,
  multi-agent split (researcher / executor / risk auditor in a separate
  session), Forward Mode schedules. Mind `max_concurrent_runs: 1`: an
  overrunning run silently skips the next trigger, and with software-only
  stops a skipped cycle is an unmonitored position. `kill_switch.
  max_cycle_gap_seconds` exists for exactly this.
- **Dashboard + submission package.**

## 9. Honesty commitments already made in writing

These are load-bearing. Walking any of them back would cost more than the
thing being claimed.

- The hash chain proves **no post-hoc editing**. It does **not** prove entries
  were truthful when written. Stated in `ledger.py`'s own docstring.
- 4.5 sessions of paper results are **not** evidence the strategy is
  profitable. They are evidence the system does what it says.
- `docs/STRATEGY.md` carries 7 pre-committed invalidation conditions (I1–I7),
  each with a sample-size requirement, and a rule to report **"untestable"**
  rather than "passed" when the sample is too small.
- No backtest has been run. The strategy is not claimed to be validated.
- Realised risk per trade is 0.04–0.07%, not the 0.35% ceiling in the config.
