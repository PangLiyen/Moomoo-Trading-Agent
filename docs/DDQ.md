# Due Diligence Questionnaire

**Entity:** Moomoo x Qoder AI Trading Agent Challenge entry
**Account:** 5832647, paper, USD 100,000
**Date:** 2026-11-23
**Repository:** https://github.com/PangLiyen/Moomoo-Trading-Agent

---

## How to read this

This follows the structure institutional allocators actually use. Manager
evaluation splits into **investment due diligence** — can this generate
returns — and **operational due diligence** — will anyone actually receive
them. ODD covers governance, controls, valuation, technology and
documentation, is usually run by a separate team, and commonly holds a veto.
The practitioner literature is blunt that a fund can pass investment
diligence and be killed by ODD, and that incomplete documentation halts a
process regardless of returns.

We are using that structure because it is the honest one for this entry.

**Our investment case is weak and we say so in §2: the strategy has no
measured edge and has been declared invalidated on three of its own
pre-registered conditions.** Our operational case is where the work is.

Every answer carries a **verification** line: the command or file that
settles it. Nothing here asks to be believed.

---

# Part A — Investment due diligence

## A1. What is the strategy?

Intraday mean reversion on five liquid US ETFs — SPY, QQQ, IWM, XLF, XLE —
on 5-minute regular-hours bars, long only. Enter when price sits more than
1.5·ATR20 below SMA20 *and* has ticked up *and* SPY is above its own SMA60
*and* the quote is fresh, the spread tight and the budgets unexhausted. Exit
at SMA20, or 1.0·ATR20 against, or 60 minutes, or the session flat-by —
whichever comes first.

No shorting, no options, no margin, no leveraged or inverse products.

**Verification:** `docs/STRATEGY.md` §§3–5, committed `2026-11-20T15:33:59Z`
at `5748462`, sha256 `d2295d99…`. Check with
`git show 5748462:docs/STRATEGY.md | shasum -a 256`.

## A2. What is the edge, and what is it worth?

**There is no measured edge.** Across 170 base signals over nine sessions,
gross P&L was **−0.65 on 170 one-share trades** — indistinguishable from zero
*before* costs.

The strategy is declared invalidated on three of its own pre-registered
conditions:

| | Condition | Result |
|---|---|---|
| I1 | win rate < 40%, n ≥ 15 | 0.0% on 22; 9.6% on 166 |
| I2 | mean realised R < 0 | −2.32 per trade, gross also negative |
| I3 | reversion fails in > 55% of setups | **90.9%** failed |

Sample size was adequate for all three, so these are failures rather than
"untestable".

**Verification:** `docs/STRATEGY.md` Addendum 1;
`python3 analysis/measure_signal_rate.py --extend`.

## A3. Did you search for a better configuration?

No, deliberately. We tested **four pre-registered hypotheses** on a declared
train/test split and all four failed. The script prints the hypothesis set,
both split date lists and the seed *before* loading any data.

The result worth reporting is H1, the logical complement of the invalidated
rule:

| | TRAIN | TEST |
|---|---|---|
| signals | 202 | 25 |
| targets hit | **92** | **0** |
| net at engine sizing | **+3.18%** | **−1.21%** |

Combined across both splits H1's gross is **+59.88** and would have read as a
discovery. Without the holdout we would have promoted it, written it up as a
pre-committed continuation strategy, and deployed a rule whose out-of-sample
performance is −1.21%.

A random-entry control hit **5 targets on TEST to the real rule's zero**, so
on the test split our entry signal selected worse than chance.

We did not test a fifth hypothesis. Bailey, Borwein, López de Prado and Zhu
prove the expected maximum Sharpe across N trials grows with √log N *when
every true Sharpe is zero*; the fifth trial is the one most likely to produce
a mirage.

**Verification:** `docs/HYPOTHESIS_TESTS.md`;
`python3 analysis/test_hypotheses.py`.

## A4. Why trade a strategy you have invalidated?

To test our own measurement out of sample, and to demonstrate the risk system
on live data rather than replayed bars. The simulation says this system loses
0.188% with 0.210% max drawdown over nine sessions; the live run is the test
of whether that description holds.

A strategy known to be edgeless is the better instrument for that purpose:
there is nothing to hope for, so there is nothing to fudge.

**Verification:** `docs/PRE_REGISTRATION.md`, seven numbered predictions
committed before deployment.

## A5. How is position size determined?

By formula, in code. **No model input at any step.**

```
risk_usd  = equity × 0.35%
distance  = 1.5 × ATR20          # 1.0 stop trigger + 0.5 slippage buffer
qty       = min( floor(risk_usd / distance),
                 floor(15% × equity / price) )
```

Then four rejections: ATR below a per-symbol floor, price under $20, notional
under $3,000, and cost/edge above 20%.

The notional cap binds before the risk budget on **all five symbols**, so
realised risk per trade is **0.04–0.07%** against a stated ceiling of 0.35% —
five to nine times more conservative than the limit implies. We state that
rather than leaving a number in the config that suggests more risk is being
taken than is.

**Verification:** `engine/sizing.py`; `pytest tests/test_sizing_determinism.py`.

## A6. What are the known limitations?

1. Nine sessions cannot validate an edge, and 4.5 cannot either.
2. Paper fills are not live fills.
3. No walk-forward harness. We did not build one, so we cite none.
4. Stops are software-enforced: the API has no resting stop orders of any kind.
5. Mean reversion has a fat left tail; a sustained trend day is the known
   failure mode. The regime filter was the mitigation and **it was falsified** —
   trades it permitted were worse than trades it blocked on every measure.
6. **ATR20 is 100 minutes of lookback.** In a volatility regime change it
   rises slowly, so size is largest just as risk increases. What limits the
   damage is the notional cap — the same constraint that costs us upside.

**Verification:** `docs/STRATEGY.md` §7 and Addendum 1;
`docs/RESEARCH_CRISIS_AND_ALLOCATORS.md` §2.

---

# Part B — Operational due diligence

## B1. Who generates ideas, who sizes them, who kills them, and is any of it written down?

| Role | May | May not |
|---|---|---|
| Researcher | read quotes, bars, news; compute indicators | place, modify or cancel any order; invent missing data |
| Strategy engine | produce a proposal from deterministic rules | accept any model-generated number as a sizing input |
| Risk auditor | tighten, shrink, block, escalate | loosen a limit; approve its own escalation; hold credentials |
| Executor | submit approved orders to account 5832647 only | originate a trade; increase size; widen or remove a stop |
| Ledger | append | edit or delete a prior entry |

Written down, committed before trading, and enforced where enforcement is
possible. Where it is **not** possible we say so: see B8, finding N19.

**Verification:** `docs/STRATEGY.md` §8; `pytest tests/test_risk_gate.py`.

## B2. What stops the model placing a trade that breaks the rules?

The model proposes; code decides. `config/risk_limits.yaml` is read and
enforced by code and is never handed to a model to interpret.

The gate's structural invariant: **it may only ever shrink a proposal or
refuse it.** Enforced by assertion inside the gate, not merely intended by
the branches above it, and exercised across a randomised sweep — of 2,000
iterations **1,910 reach the gate**, with 90 refused in sizing first.

The config fails closed. A missing limit raises rather than defaulting:
`allow_shorting` unset does not mean shorting is permitted, it means the
engine will not start.

**Verification:** `pytest tests/test_config_fails_closed.py tests/test_risk_gate.py`.

## B3. What are the position limits?

| Limit | Value |
|---|---|
| Risk per trade | 0.35% ceiling; **0.04–0.07% realised** |
| Max position notional | 15% of equity |
| Max gross exposure | 40% |
| Max concurrent positions | 3 |
| Cluster — SPY/QQQ/IWM | **1** |
| Cluster — all five symbols | **2** |
| Entries per session | 6 |
| Orders per session | 20 |
| Minimum spacing between entries | 300s |
| Daily loss halt | 1.5% |
| Peak drawdown flatten | 3.5% |
| Consecutive losses stop | 5 |

The all-five cluster was added 2026-11-23 from crisis research. XLF and XLE
previously sat outside the index cluster, so three simultaneous
long-equity-beta positions were permitted. In 1997 investors treated a whole
region as one asset class; in 2008 the Gaussian copula's correlation
parameter was calibrated on 2003–2006 and never updated for stress. Under a
correlation of 1 these five instruments are one trade.

That change is **tightening only**, justified by evidence outside our own
window, and cannot improve any result we report.

**Verification:** `config/risk_limits.yaml`;
`docs/RESEARCH_CRISIS_AND_ALLOCATORS.md` §1.

## B4. How is the account verified before each order?

Three independent checks, all of which must agree: account id, exact title,
and account type. On the first probe run an agent selected `5852463` —
"US Margin Account [Paper Trading]", the obvious US paper account — unprompted
and reported its $1,000,000 balance as the competition balance. The real
figure is $100,000. Every position size derives from it.

`AccountState` can only be built from the **broker's own response**. An earlier
driver built it from config and then asked the guard to validate it against
config: every check passed and could only have failed if the YAML contradicted
itself. The guard now records both sides of the comparison, so a vacuous
check is visible in the evidence.

**Verification:** `pytest tests/test_account_guard_is_not_vacuous.py`.

## B5. Books and records — what does the ledger prove?

`logs/decisions.jsonl` is append-only and SHA-256 hash-chained. Each entry's
hash covers its content plus the previous hash, and every append is fsync'd.

**It proves no record was altered or removed after it was written. It does not
prove the records were truthful when written.** That distinction is stated in
the docstring of the mechanism itself, because it is the easiest thing here to
overclaim.

One limitation we publish rather than hide: **truncating the chain's tail is
undetectable from inside it.** Dropping the last records leaves seq running
1..n with every prev_hash matching. A test asserts this *passes* verification.
What closes the hole is external: each order's ledger hash is stamped into
moomoo's own 100-byte `text` field, where we cannot edit it, so the broker
holds hashes for orders a truncated ledger no longer mentions.

Refusals are recorded with the same detail as executions. A system that logs
only what it did cannot show its limits were ever load-bearing.

**Verification:** `pytest tests/test_ledger_integrity.py`;
`Ledger("logs/decisions.jsonl").verify_report()`.

## B6. What technology runs this, and what can it reach?

Nine modules in `engine/`, layered strictest-first, nothing lower importing
anything higher. **No module in the decision path performs network I/O, reads
free text, or consults a model.**

`run_cycle.py` decides and records. It cannot execute: it writes an intents
file, and something with credentials submits it. A test fails if a network
import appears in `engine/executor.py`.

The commit a ledger claims to come from is **derived from git and verified** to
contain every engine file, and per-file dirty state is recorded — because a
correct commit still does not describe running code when the tree is modified.

**Verification:** `pytest tests/ -q` → 261 passed;
`pytest tests/test_provenance_is_verified.py`.

## B7. How are untrusted inputs handled?

`news_search` and `quote_community_search` return attacker-influenceable text.
We measured the surface rather than assuming it:

- `quote_community_search.title` is **unmoderated retail post text**
- `news_search.content` is ~3,000 characters of HTML with anchors and
  `futunn://` deep links — 30× the length of `title`, so the cheapest place to
  hide a payload
- **keyword matching is naive substring:** `keyword="SPY"` returned
  *SPY×FAMILY* anime licensing notices. An attacker does not need to write
  about SPY to enter our SPY feed; the token merely has to appear

Both are declared data sources in config and may never influence order
submission, sizing, stop levels, risk values, account selection or universe
membership.

### What actually enforces the boundary

Not a filter. **No function in the decision path accepts text from a feed at
all.** `size_position` takes floats; `evaluate_entry` takes an Indicators
object and booleans; the gate reads counts, prices and timestamps;
`build_order` emits enums and strings we construct ourselves. A payload that
cannot be an argument cannot be an exploit, and that is a stronger property
than any amount of sanitising.

**92 adversarial tests** fire a corpus at every engine entry point. The
corpus is modelled on the measured field shapes rather than invented ones —
a ~47-character unmoderated community title, a ~3,000-character HTML news
body with anchors and `futunn://` deep links, and an `abstract` truncated at
exactly 200 characters mid-token so a payload can straddle the boundary and
look harmless in one field.

Each payload must either be **rejected by type** or leave the numeric output
**bit-for-bit identical**. Separately, no `engine/` module imports any HTTP
client, so the decision path has no inbound channel to defend.

`engine/untrusted.py` handles the narrow case where untrusted text *is* read,
for regime commentary or human research context. It classifies, quarantines
the payload **verbatim**, and emits an `INJECTION_SUSPECTED` record stating
that nothing was reached and why. It deliberately does not sanitise text for
reuse: a sanitiser invites the belief that cleaned text is then safe to act
on. It also reports any **new** text field a feed starts returning, because
the attack surface is not static.

**Still open:** no adversarial payload has yet been run through a live
`news_search` call. The corpus is built from measured shapes, not from a
live injection, and we say so rather than claiming the boundary has been
tested end to end in production.

**Verification:** `pytest tests/test_injection_resistance.py` → 92 passed;
`config/risk_limits.yaml` `untrusted_input`; `docs/API_CAPABILITIES.md`.

## B8. What is currently broken or unproven?

We commissioned an audit of our own work by an agent with no stake in the
result, told to find what the author had missed. It returned **29 findings**,
all published with dispositions — including three where documents in this
repository made claims that did not hold.

| Finding | Status |
|---|---|
| Ledger genesis pinned a commit containing no engine | fixed; derived and verified now |
| Rules hash in the spec header is not the config in force | documented; two values differ, both toward conservatism |
| Pre-registration claimed to be committed while untracked | fixed |
| Adversarial injection corpus | **open** |
| Realised correlation measured per cycle | **open** — the static cluster cap is the blunt version |
| Risk auditor tightening a limit at runtime | **not implemented**; the spec overstates |
| Model cannot choose quantity, but supplies the bars it is derived from | **accepted limitation.** Literally true, materially weaker than it sounds; nothing in code detects fabricated bars |

Four of the register's own dispositions were wrong when first written — three
said "fixed" about work not yet done. Those are listed in a corrections table
inside the register, because a register that quietly repairs itself is not
evidence of anything.

**Verification:** `docs/AUDIT_FINDINGS.md`, `docs/AUDIT_2026-11-21.md`.

## B9. Counterparties, and an unresolved incident

Broker: moomoo paper trading via its OpenAPI MCP connector. Agent runtime:
Qoder.

**On 2026-11-20 two orders appeared in account 5832647 that we did not place.**
Order 3128921 (ANT buy, 09:55:36 ET) and 3128965 (ANT sell, 10:04:55 ET), both
untagged, the second arriving 2.5 minutes after our last API write — so a
concurrent actor, not an artefact of our own calls. **The source remains
unidentified.**

It was detected because every order we place carries an `S`-prefixed tag in
`text`. That field was added for ledger-hash stamping; it earned its place by
finding an unknown writer on day one.

The engine now reconciles the account's full order list against the ledger
every cycle and **halts** on anything it cannot attribute. That is correct and
it is also operationally expensive: an unresolved second writer stops trading.

**Verification:** full order list, 7 orders, all accounted for;
`pytest tests/test_account_guard_is_not_vacuous.py`.

---

# Part C — Performance reporting

## C1. What can this competition's results establish?

Over 4.5 sessions, the t-statistic of an annualised Sharpe is SR × √(4.5/252)
= SR × 0.134. So:

| Target | Annualised Sharpe required |
|---|---|
| t = 1.0 | **7.5** |
| t = 2.0 | **15.0** |

**No entrant's P&L in this competition is statistically distinguishable from
luck** — not ours, and not the winner's. That is arithmetic.

For context on what a real record requires: Kenneth French calculates that a
manager with 5% alpha and 20% volatility needs **64 years** for a t-statistic
of 2. López de Prado's Minimum Track Record Length puts **2.73 years** on
showing a Sharpe of 2 exceeds 1 at 95% confidence.

## C2. Is selecting on short-run returns a reasonable thing to do?

It is a documented, value-destroying error made consistently by professionals
with far better data than four and a half sessions.

Goyal and Wahal (2008) examined manager selection and termination by **3,400
plan sponsors over 1994–2003**. Sponsors hired after large positive excess
returns; **post-hiring excess returns were indistinguishable from zero.**
Managers fired for underperformance frequently went on to outperform their
replacements. CFA Institute's survey of the literature: institutional
investors lose money on hire-and-fire decisions over one- and three-year
periods and do not recover it even after five.

We are not asking to be excused our P&L. We are reporting it, and reporting
what it can and cannot establish, with the arithmetic shown.

## C3. What do you predict, and how will you be judged wrong?

Seven predictions, committed before deployment. P&L is deliberately not among
the failure criteria — losing money is the expected outcome.

| | Prediction |
|---|---|
| P1 | realised P&L between −0.6% and +0.1% |
| P2 | max drawdown below 0.6% |
| P3 | fewer than 14 entries taken |
| P4 | win rate below 40% |
| P5 | **zero limit breaches** — the only failure we would call serious |
| P6 | commission settles at $0.99 buy / $1.00 sell |
| P7 | median stop breach under 0.5 ATR |

At the time of writing, **P2, P6 and P7 were not measurable** from the records
the engine produced — a prediction that cannot be evaluated looks like a
commitment and settles nothing. Three record types were added to fix that:
per-cycle equity snapshots, execution records carrying order ids and fee
residuals, and exit records carrying the rule that fired.

If any remains unmeasurable at the close, it will be reported as **unmeasurable
by our own instrumentation** — not quietly dropped, and not retrofitted with a
looser definition.

**Verification:** `docs/PRE_REGISTRATION.md`; `docs/AUDIT_2026-11-21.md` §3.

---

# Part D — Verification index

| Claim | Command |
|---|---|
| Spec predates trading | `git show 5748462:docs/STRATEGY.md \| shasum -a 256` → `d2295d99…` |
| Config predates trading | `git show 5748462:config/risk_limits.yaml \| shasum -a 256` → `c0ca0cf8…` |
| Whole suite | `pytest tests/ -q` → 261 passed |
| Limits enforced, not advised | `pytest tests/test_risk_gate.py` |
| Sizing deterministic | `pytest tests/test_sizing_determinism.py` |
| Config fails closed | `pytest tests/test_config_fails_closed.py` |
| Ledger tamper-evident | `pytest tests/test_ledger_integrity.py` |
| Account guard not vacuous | `pytest tests/test_account_guard_is_not_vacuous.py` |
| Indicators causal | `pytest tests/test_outlier_filter_is_causal.py` |
| Provenance verified | `pytest tests/test_provenance_is_verified.py` |
| Driver wiring | `pytest tests/test_driver_integration.py` |
| Injection resistance | `pytest tests/test_injection_resistance.py` → 92 passed |
| Chain intact | `python3 -c "import sys;sys.path.insert(0,'.');from engine import Ledger;print(Ledger('logs/decisions.jsonl').verify_report())"` |
| Measurements reproducible | `python3 analysis/measure_signal_rate.py --extend` |
| Hypothesis tests reproducible | `python3 analysis/test_hypotheses.py` |
| Three implementations agree | `python3 analysis/verify_implementations.py` |

---

## Sources

- Goyal, A. and Wahal, S. (2008), *The Selection and Termination of Investment Management Firms by Plan Sponsors*, Journal of Finance
- Stewart, S. (2013), *Manager Selection*, CFA Institute Research Foundation
- Bailey, Borwein, López de Prado and Zhu (2014), *Pseudo-Mathematics and Financial Charlatanism*, Notices of the AMS 61(5)
- Bailey and López de Prado (2014), *The Deflated Sharpe Ratio*, Journal of Portfolio Management 40(5)
- Lo, A. (2002), *The Statistics of Sharpe Ratios*, Financial Analysts Journal 58(4)
- BIS Quarterly Review, March 2018, on the February 2018 ETP feedback loop
- Federal Reserve History, *Asian Financial Crisis*
- AIMA Illustrative Due Diligence Questionnaire
