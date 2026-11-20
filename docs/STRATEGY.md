# Trading Strategy — Pre-Committed Specification

**Status:** v0.1 — committed BEFORE any strategy trade is placed
**Committed at (UTC):** _fill in on first commit_
**Rules hash:** _computed from config/risk_limits.yaml + this file_

> This document is written before trading begins and is **not** edited to match results.
> If the rules change, a new version is committed with a reason and a timestamp, and the
> old version stays in git history. That is the point.

---

## 1. What this is, and what it is not

This is a **baseline** strategy, chosen deliberately for auditability over expected
return. We have 4.5 trading sessions. No strategy can be validated in 4.5 sessions, and
we will not claim otherwise.

The contribution of this entry is not the edge. It is that every decision the system
makes — including the ones it refuses to make — is bounded, recorded, attributable to a
rule, and reproducible. The strategy exists so that there is something real to govern.

**We state plainly:** 4.5 sessions of paper results are not evidence that this strategy
is profitable. They are evidence that the system does what it says it does.

---

## 2. Thesis

Highly liquid broad-market and sector ETFs mean-revert intraday over short horizons when
price dislocates from its short-run average **in the absence of a directional regime**.
The dislocation is usually order-flow noise rather than information, because these
instruments are baskets — single-stock news is diluted across dozens of holdings.

**Why this instrument class:**
- Tightest spreads available → slippage assumptions are more likely to hold
- No single-name earnings or headline risk → no overnight gap landmines
- No options, no assignment, no exercise mechanics
- No shorting, no borrow, no leveraged/inverse products

**Why this is the honest choice for us:** the team's markets knowledge is limited. A
strategy we can fully specify in one page and defend line by line is worth more than a
sophisticated one we cannot explain under questioning.

---

## 3. Universe

Fixed, declared in config, never changed mid-session.

| Symbol | Role |
|---|---|
| `US.SPY` | Broad market |
| `US.QQQ` | Large-cap tech |
| `US.IWM` | Small cap |
| `US.XLF` | Financials |
| `US.XLE` | Energy |

**Correlation cluster rule:** `SPY`, `QQQ`, `IWM` form one cluster. **At most one open
position across the whole cluster at any time.** Without this, three "independent"
positions are one leveraged bet on the index.

---

## 4. Signal

Computed on **5-minute bars, regular trading hours only**. Pre-market and after-hours
bars are excluded from all calculations — thin extended-hours volume produces wide
spreads and unreliable prints, which the deck warns about directly.

**Indicators**
- `SMA20` = 20-period simple moving average of close
- `ATR20` = 20-period average true range
- `REGIME` = SPY close vs its own `SMA60`

**Long entry — all conditions must hold:**
1. `close < SMA20 − (1.5 × ATR20)` — the dislocation
2. `close > previous_bar_close` — one up-tick, i.e. stabilising rather than still falling
3. `REGIME == OK`, meaning SPY `close > SMA60` — not an intraday downtrend
4. Quote age ≤ 120 seconds
5. Bid-ask spread ≤ 0.05% of mid
6. Cluster rule satisfied, position and order budgets not exhausted
7. Current ET time is between 10:00 and 15:00

**There are no short entries.** Long-only is a deliberate constraint, not an oversight.

**Exits — whichever comes first:**
- **Target:** price returns to `SMA20` (≈ 1.5 × initial risk)
- **Stop:** `entry − (1.0 × ATR20)`
- **Time stop:** 60 minutes in position
- **Session stop:** flat by 15:50 ET, every day, no exceptions
- **Holiday stop:** Fri 27 Nov is a half day with a 13:00 ET close. Flat by **12:45 ET**.

**No position is ever held overnight.** This removes gap risk entirely and satisfies the
deck's warning that unmanaged weekend and holiday gaps can trigger forced liquidation,
with no reset cards available.

---

## 5. Position sizing

```
risk_usd    = equity × risk_per_trade_pct        # 0.35%
stop_dist   = 1.0 × ATR20
qty         = floor(risk_usd / stop_dist)
notional    = qty × entry_price
```

Then clamp:
- `notional ≤ max_position_notional_pct × equity` (15%)
- reject if `qty < 1`
- reject if `notional` would push gross exposure past `max_gross_exposure_pct` (40%)

Sizing is computed in code. **The model never chooses a quantity.** The deck is explicit
that position-sizing calculations returned by an agent are not automatically
broker-enforced limits — so they are enforced by our own gate, before submission.

---

## 6. Pre-committed invalidation conditions

If any of these trips, the strategy is **declared invalidated** and we say so in the
submission rather than quietly re-tuning. Each is measurable directly from the decision
log.

| # | Condition | Measured over |
|---|---|---|
| I1 | Win rate < 40% | ≥ 15 closed trades |
| I2 | Mean realised R < 0 | ≥ 15 closed trades |
| I3 | Price fails to revert to `SMA20` within the 60-min window in > 55% of triggered setups | all triggers |
| I4 | Median realised slippage > 0.05% of notional | all fills |
| I5 | > 60% of losing trades occurred while `REGIME == OK` (the regime filter is not filtering) | all losses |
| I6 | Any single session loss exceeds 1.5% of equity (the risk model failed, not the strategy) | per session |
| I7 | Two simultaneous positions show realised co-movement > 0.9 (cluster rule failed) | all overlaps |

With 4.5 sessions we may not reach the sample size for I1–I3. **If we don't, we report
that the condition was untestable, not that it passed.** Claiming validation from an
inadequate sample is the failure mode we are explicitly guarding against.

---

## 7. Known limitations (stated up front)

1. **Sample size.** 4.5 sessions cannot validate an edge. Any Sharpe figure we report is
   descriptive, not inferential, and is labelled as such.
2. **Simulated fills.** Paper matching, liquidity and slippage can differ from live
   execution. Paper results are not live-trading proof.
3. **No walk-forward validation.** There was no time to build a backtest harness with
   realistic costs. We did not run one, so we do not cite one.
4. **Stop enforcement** depends on the outcome of the Session 00 probe. If the simulated
   API has no resting stop orders, stops are software-enforced and therefore only as
   reliable as the agent's cycle cadence. This is documented, not hidden.
5. **Mean reversion has a fat left tail.** A sustained trend day is the known failure
   mode. The regime filter is the mitigation; I5 is the test of whether it worked.

---

## 8. What the agent may and may not do

| | Allowed | Forbidden |
|---|---|---|
| **Researcher** | Read quotes, bars, news, compute indicators | Place, modify or cancel any order. Invent missing data. |
| **Strategy engine** | Produce a proposal from deterministic rules | Accept any LLM-generated number as an input to sizing |
| **Risk auditor** | Tighten, shrink, block, escalate | Loosen a limit. Approve its own escalation. Hold credentials. |
| **Executor** | Submit approved orders to the competition account only | Originate a trade. Increase size. Widen or remove a stop. |
| **Ledger** | Append | Edit or delete a prior entry |

The deck's warning is adopted as a design rule: **several agents agreeing is not
independent proof that a trade is good.** Agreement between agents is therefore never
used as a reason to increase size or override a limit.
