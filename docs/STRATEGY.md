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

---

# ADDENDUM — Invalidation report, 2026-11-21

Added after the measurement described in `analysis/measure_signal_rate.py`,
**before any strategy trade was placed.** Everything above this line is the
original pre-committed specification and has not been edited. This addendum is
appended rather than merged so the order of events stays legible: the rule was
specified, then measured, then reported on.

## What was measured

Nine sessions of 5-minute RTH bars, 11-09 to 11-20, 713 bars per symbol.
170 bars satisfied the base entry conditions. Exits simulated per section 4 at
bar closes, fees charged at the measured $1.99 round trip.

## Conditions tripped

| # | Condition | Result | Verdict |
|---|---|---|---|
| **I1** | Win rate < 40%, n ≥ 15 | 0.0% on 22 regime-OK trades; 9.6% on all 166 | **TRIPPED** |
| **I2** | Mean realised R < 0, n ≥ 15 | mean net −2.32/trade; gross also negative | **TRIPPED** |
| **I3** | Reversion to SMA20 fails in > 55% of setups | 90.9% failed (regime-OK); 65.1% (all) | **TRIPPED** |
| I4 | Median slippage > 0.05% of notional | measured 0.0244% on the live probe | not tripped |
| I5 | > 60% of losses while REGIME == OK | 14.7% — **but the condition is mis-specified** | see below |
| I6 | Session loss > 1.5% of equity | max simulated session loss 0.21% | not tripped |
| I7 | Simultaneous positions co-move > 0.9 | cluster rule blocked 7 of 22 entries; no overlap occurred | untestable |

**The strategy is declared invalidated on I1, I2 and I3.** Sample size was
adequate for all three (22 and 166 closed trades against a threshold of 15),
so these are not "untestable" — they are failures.

## I5 was a badly written test, and that is a finding too

I5 asked whether more than 60% of *losing trades* happened while the regime
filter was open. It counts losses. The regime-OK group is small by
construction — 22 trades against 147 blocked — so even if every regime-OK
trade lost, the count could never approach 60% of all losses. **The condition
could not detect the thing it was written to detect.**

Comparing rates rather than counts, which is what it should have done:

| group | n | loss rate | gross P&L | reverted to SMA20 |
|---|---|---|---|---|
| REGIME == OK | 22 | **100.0%** | −7.36 | 9.1% |
| REGIME == blocked | 144 | 88.9% | **+6.71** | ~35% |

The filter did not merely fail to help. **Trades it permitted were worse than
trades it blocked, on every measure.** Its stated premise in section 5 — that
mean reversion is more reliable absent a directional regime — is falsified for
these instruments at this horizon in this window. The plausible mechanism:
requiring SPY above its own 5-hour average selects dips inside an already
extended move, which continue; dips while SPY is below that average are
closer to genuinely oversold, and bounce more often.

I5 is restated for any future work as a rate comparison, not a count:

> **I5′** — the loss rate of REGIME-permitted trades is not lower than the
> loss rate of REGIME-blocked trades, measured over ≥ 20 trades in each group.

Restating a test after it fails to fire is a change to our instrumentation,
not to the strategy. No entry, exit, sizing or risk parameter has been altered
as a result of this measurement, and `config/risk_limits.yaml` is unchanged
except for recording measured facts.

## What we are not doing

We are not searching for a configuration that performs better on these nine
sessions. Zero wins in 22 trades cannot indicate which parameter to change,
and any improvement found by trying stop multiples or filter periods against
this sample would be noise fitted to one window and then presented as a
pre-committed rule. That is the specific dishonesty this entire entry is built
to avoid, and it would be worth less than an honest negative result.

Nor did inverting the regime filter produce an edge: gross P&L across all 170
base signals is **−0.65 on 170 one-share trades**, which is indistinguishable
from zero before costs. There is no profitable version of this rule in this
sample. The strategy has no measured edge, and we say so.

## What the measurement does establish

The risk system contained an edgeless strategy to a small, bounded loss.
Sequential simulation honouring every real limit — 3 concurrent positions, the
SPY/QQQ/IWM cluster cap, 6 entries per session, the 300-second spacing, and
`engine.sizing` for every quantity — took **9 of 22 signals** and produced:

| | |
|---|---|
| Realised P&L | **−$187.57 (−0.188% of equity)** |
| Max drawdown | **$210.23 (0.210%)** |
| Entries refused by the cluster rule | 7 |
| Entries refused as already holding | 5 |
| Entries refused on spacing | 1 |

A strategy with no edge, run for nine sessions, cost 0.19% and never drew down
more than 0.21%. Stop-depth measurement supports this: the median breach past
the stop level was 0.249 ATR and the mean 0.333, both inside the 0.5 ATR
slippage buffer `sizing` already assumes, with two decisive exceptions at
0.775 and 1.054 ATR. The buffer was conservative in the direction that
matters, and the measurement vindicates it rather than prompting a change.

That containment is what this entry actually demonstrates. It was always the
claim — section 1 says the contribution is that every decision is bounded,
recorded and attributable, and that "the strategy exists so that there is
something real to govern." The strategy turned out to be a poor one. The
governance held anyway, and now there are numbers for it rather than
assertions.
