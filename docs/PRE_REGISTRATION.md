# Pre-registration — live run, 23–27 Nov 2026

Written **2026-11-21, before the first strategy trade**, and committed before
the agent was deployed. The point of writing it now is that a prediction made
in advance can be wrong in public. One written afterwards cannot.

---

## 1. Why we are trading a strategy we have declared invalidated

`docs/STRATEGY.md` declares the entry rule invalidated on I1, I2 and I3. The
obvious reading is that we should not trade it. We are going to, deliberately,
and the reason is that it converts the remaining sessions from a hope into a
test.

We have a measurement: nine sessions, 170 signals, no edge, and a sequential
simulation under the full limit set giving −0.188% with 0.210% max drawdown.
That measurement is either a good description of how this system behaves or it
is not. The only way to find out is to run the same rule, unchanged, on data
it has never seen, and compare.

So the live run is not an attempt to make money. It is an out-of-sample test
of our own simulation, and a demonstration of the risk system on live data
rather than on replayed bars. A strategy known to be edgeless is, for that
purpose, a better instrument than a promising one: there is nothing to hope
for, so there is nothing to fudge.

The alternative — not trading — leaves the submission with no fills, no live
ledger, and no evidence that the governance works anywhere but in a backtest.

## 2. The predictions

Stated now, judged later. Measured from `logs/decisions.jsonl` and the broker
record over the sessions of 23, 24, 25 and 27 November.

| # | Prediction | Basis |
|---|---|---|
| P1 | Realised P&L lands in **−0.6% to +0.1%** of starting equity | simulation gave −0.188% over 9 sessions; 3.5 sessions is ~0.4x the exposure, widened for small-sample variance |
| P2 | Max drawdown stays **below 0.6%** | simulation 0.210%; the kill switch halts new entries at 1.5% and flattens at 3.5%, so a breach of either means the risk model failed, not the strategy |
| P3 | **Fewer than 14 entries** are taken in total | simulation took 9 of 22 signals over 9 sessions; the cluster rule and spacing refuse roughly 60% |
| P4 | Win rate stays **below 40%** | this is I1 restated; if it exceeds 40% live, our invalidation was premature and we will say so |
| P5 | **Zero** limit breaches in the ledger: no order outside the universe, no size above the notional cap, no entry past 15:00 ET, no position held past 15:50 ET | the limits are enforced in code, so a breach is a defect, not variance |
| P6 | Measured commission settles at **$0.99 per buy and $1.00 per sell** | n=2 buys and n=1 sell so far; `fees.commission_model_is_settled` is false until a second sell agrees |
| P7 | Median stop breach stays **under 0.5 ATR** | measured 0.249 median, 0.333 mean on 15 simulated stops; 0.5 is the buffer `sizing` assumes |

## 3. What would count as a failure of the system, not the strategy

P&L is not in this list. Losing money is the expected outcome and is not a
system failure.

- **P5 breached.** Any limit violation in the ledger means a control that was
  tested, documented and claimed did not hold in production. This is the only
  prediction whose failure we would consider serious.
- **P2 breached past 1.5%.** The kill switch did not fire when it should have.
- **A ledger gap.** A fill in the broker record with no corresponding decision
  record, or a hash-chain break. Either makes the evidence unusable.
- **A foreign order going undetected.** The reconciliation runs every cycle;
  missing a second writer means the attribution claim fails.

## 4. What we will report either way

Every prediction above, with its outcome, in the submission. Including the
ones we get wrong — particularly P4, because if the live win rate exceeds 40%
then our nine-session invalidation was based on an unrepresentative window and
the honest conclusion is that we invalidated too early on too little data.

No prediction in this file will be edited after 23 November. If we want to
revise one, the revision goes in a dated addendum below, the way the
invalidation report was appended to `STRATEGY.md` rather than merged into it.

## 5. Scope

The rule being run is exactly the specification in `docs/STRATEGY.md` sections
3–5, with `config/risk_limits.yaml` at the commit recorded in the ledger's
genesis record. The regime filter stays in, despite the measurement in the
invalidation addendum showing it selected worse trades, because removing it
would mean running a different rule than the one we pre-committed and
measured. The filter's falsification is reported as a finding; it is not acted
on mid-experiment.
