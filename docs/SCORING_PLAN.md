# Where the points actually are — 2026-11-23

Written in response to a falling leaderboard position. The conclusion is that
the leaderboard is measuring something the rubric does not, and that our
effort has been badly allocated against the rubric for three days.

---

## 1. The leaderboard is not the scoring function

| Weight | Criterion |
|---|---|
| 30% | Strategy & risk design |
| 30% | **Qoder & Cloud Agent usage** |
| 25% | **Risk-adjusted return (Sharpe)** — not raw P&L |
| 15% | Reusability & documentation |

The 25% is **risk-adjusted**, so a live P&L leaderboard is not the metric. An
entrant up 5% with an 8% drawdown and an entrant flat with a 0.2% drawdown are
not ordered by the number the leaderboard shows.

That does not make our return position good. It makes the leaderboard a poor
estimate of it, in both directions, and a poor reason to change what we trade.

## 2. Honest self-assessment against the rubric

| Criterion | Weight | Where we are | Estimate |
|---|---|---|---|
| Strategy & risk design | 30% | Pre-committed spec, invalidation on three published conditions, 308 tests, self-commissioned audit publishing 29 defects, train/test overfitting demonstration, crisis-sourced correlation tightening | **strong, 25–28** |
| **Qoder & Cloud Agent usage** | **30%** | **Nothing deployed.** No Cloud Agents runtime, no multi-agent orchestration, no Forward Mode schedule, no Slack delivery, no dashboard, no packaged skills | **weak, 5–10** |
| Risk-adjusted return | 25% | Zero strategy trades. Strategy has no measured edge and is invalidated | **weak, 5–10** |
| Reusability & documentation | 15% | Eleven documents, a runbook, reproducible analysis scripts — **but no slides and no packaged skills**, both named in the criterion | **good, 9–12** |

**The single largest gap is not P&L. It is the 30% for Qoder usage, where we
are close to zero.**

Our own competition brief said so on day one: *"Qoder Use is 30% of the score
and nearly everyone is behind on it. That is the single largest available
gap."* We then spent three days on the 30% we were already winning.

## 3. Why this happened, and why it is worth naming

Every defect the audit found was real, and fixing them was not wasted. But the
work had a bias: each fix produced a visible, checkable improvement to
something already strong, while the untouched 30% produced nothing until it is
finished. That is a gradient that leads reliably away from the largest
available gain, and it is the same failure mode as optimising a metric because
it is the one being displayed.

The leaderboard prompted this review. The leaderboard was also the wrong
signal. Both are true.

## 4. Two explicitly named point sources we have not touched

From the handbook, quoted in our brief:

- routing agent output to a chat channel **"is what counts towards the
  automation half of the usage score"**
- **"the judging rubric explicitly rewards dashboards and reports"**

Named, cheap, and unbuilt.

## 5. Plan, ordered by points per hour

### Tonight, alongside the live session
1. **Qoder Cloud Agents deployment.** Vault credential, repo clone, engine
   running in the managed runtime. Recon proved the engine runs there —
   `91 passed` in the sandbox on a plain clone — so this is packaging, not
   discovery. *30% criterion.*
2. **Slack delivery.** Cycle summary routed to a channel via a Forward Mode
   template. Explicitly named in the handbook as the automation half.
   *30% criterion.*

### Tuesday 24
3. **Multi-agent split.** Researcher, executor and risk auditor as separate
   Cloud Agents sessions, the auditor in its own session holding no
   credentials — which `STRATEGY.md` §8 already specifies and nothing
   currently implements. This is the orchestration the criterion names, and
   it is also audit finding N19. *30% criterion, and closes a known gap.*
4. **Dashboard.** Equity curve, drawdown, every decision with its reason code,
   refusals shown alongside executions, chain verification status. Built from
   `logs/decisions_live.jsonl`, which already carries everything needed.
   *30% and 15%.*

### Wednesday 25
5. **Forward Mode schedule.** Cron with an explicit timezone, `max_attempts`,
   and the `max_concurrent_runs: 1` skip hazard handled — which is what
   `kill_switch.max_cycle_gap_seconds` exists for. Converts the agent from
   manually run to autonomous. *30%.*
6. **Packaged reusable skills.** One zip per skill, single top-level folder
   matching `SKILL.md` name, under 50MB. Candidates: the risk-gate pattern,
   the hash-chained ledger, the injection-resistance harness. *15%, named
   in the criterion.*

### Thursday 26 — market closed, a full build day
7. **Slides.** Qoder-generated, as the submission requires.
8. **Submission package assembly** against the required contents.

### Friday 27 — half day, flat by 12:45 ET
9. Final live session, then freeze.

### Weekend
10. Rehearse the finals demo: plant a payload, show it reach the agent, show
    the order path refuse it and log the attempt. Sixty seconds, and it is the
    only demo in the field that shows a control working rather than a return.

## 6. What does not change

We do not alter the strategy to chase the leaderboard. There is no profitable
configuration of this rule in the data — 170 signals, gross −0.65 — and four
pre-registered hypotheses failed. Changing it now would trade the 30% we are
winning for a 25% we cannot win anyway, and it would make every honesty claim
in the submission retrospectively false.

The trade count will also recover on its own. Today's suppression is a gap
artefact: SPY opened +4.2% above Friday's close, so every symbol sits far
*above* its SMA20 and a long mean-reversion entry is not geometrically
reachable until the 100-minute window clears the discontinuity, around 11:10
ET. Base rate over the measured sample is ~2.4 joint signals per session, so
3.5 remaining sessions should produce roughly 8 signals and perhaps 4 trades
after the cluster rule. That is thin, and it is what the pre-registration
predicted: fewer than 14 entries.

## 7. The reframing that is honest and also stronger

Allocators split evaluation into investment due diligence and **operational**
due diligence, and ODD routinely holds a veto — a fund can pass investment
diligence and be killed by ODD, while incomplete documentation halts a process
regardless of returns.

We are an ODD-strong, IDD-weak entry. `docs/DDQ.md` already presents it that
way. The plan above is almost entirely about making the operational case
*demonstrable in the runtime the judges are scoring*, rather than only in a
repository.
