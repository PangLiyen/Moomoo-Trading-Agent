# What crises and allocator research say about this system

**Written 2026-11-23, before the Monday session.** Research into three bodies
of work, read for what they change about our design rather than for colour.
One produced a config change; one produced a reframing of the submission; one
produced a number that bounds what this competition can establish at all.

> A note on framing: there was no financial crisis in 2017–2018. There were two
> significant volatility events — February 2018 and Q4 2018 — and the Global
> Financial Crisis was 2007–2008. Both are covered below because they bear on
> different parts of the design.

---

## 1. The single finding that matters: our cluster rule is calibrated to calm

Every crisis below is, mechanically, the same failure: **a dependence
structure measured in benign conditions, relied on in stressed ones.**

**1997, Asian financial crisis.** Investors had treated the region as one
asset class. When Thailand floated the baht, capital left countries that were
fundamentally sound alongside those that were not — a shock in one place
changed how creditors judged everyone who looked similar. Assets that appeared
uncorrelated in calm markets moved in lockstep. The BIS noted institutions
with no direct exposure to Asia, Russia or LTCM taking losses anyway, having
held diversified-looking positions "on the theory that the usual correlation
among returns in that asset class" would hold.

**2007–2008, GFC.** The Gaussian copula used to price CDO tranches assumed a
single, roughly stable correlation parameter with near-zero tail dependence.
It was calibrated on 2003–2006 — historically low defaults, tight spreads —
and never updated for stress. Parametric VaR assumed normal daily P&L and
short lookbacks. All the assumptions broke at once: normality, stable
correlation, instant liquidity. The standing lesson from the model-risk
literature is explicit: *use crisis-calibrated correlations, not average-period
estimates, and test how portfolio risk changes when correlations approach 1.0.*

### What this says about `config/risk_limits.yaml`

Our `index_broad` cluster caps SPY, QQQ and IWM at **one** concurrent
position, on the stated reasoning that "three independent positions are one
leveraged bet on the index." That reasoning is correct and it stops one bar
short.

**XLF and XLE sit outside that cluster.** So the permitted worst case is one
index ETF plus XLF plus XLE — three positions, up to the 40% gross cap, every
one of them long US equity beta. In calm conditions sector dispersion makes
that look like three bets. In a 1997- or 2008-style event it is one bet at
three times the size, which is precisely the error the cluster rule exists to
prevent, applied one level up.

This is a **risk-tightening change justified by evidence outside our own
window**, and it is being made before the first strategy trade. It cannot
improve any measured result — it can only reduce the number of positions we
are allowed to hold. That is the one category of change the risk auditor is
permitted to make under `STRATEGY.md` §8: *"Tighten, shrink, block, escalate…
may never loosen a limit."*

## 2. February 2018: a mechanism we partly share

On 5 February 2018 the S&P 500 fell **4.1%** — significant, not extreme — and
the VIX rose from 17.31 to 37.32, its largest one-day move on record. XIV, an
inverse-VIX ETN, lost roughly 96% and was terminated under an acceleration
clause its own prospectus disclosed. The BIS documented the mechanism: *"Due to
the mechanical nature of the rebalancing, a higher VIX futures price
necessitated even greater VIX futures purchases by the ETPs, creating a
feedback loop."* The collapse of indicative value happened **after hours**,
where the other side of the trade was empty.

Three parts of that are ours, and one is not:

| 2018 mechanism | Us |
|---|---|
| A **mechanical process** whose own latency amplifies losses | Software-only stops on a 60-second check, plus Forward Mode's `max_concurrent_runs: 1` skipping the next trigger when a run overruns. A skipped cycle is an unmonitored position, and it is skipped exactly when cycles run long — under load |
| **Crowded, correlated positioning** | §1 above |
| **After-hours illiquidity** amplifying the move | **Structurally excluded.** `order_trade_time_type: 1` makes an extended-hours fill impossible by construction, not by convention. XIV died after hours; we cannot trade there |
| Leverage and convexity in the instrument | Not ours: long-only, no options, no leveraged or inverse products, `allow_margin: false` |

The after-hours exclusion was chosen on the strategy deck's warning about thin
extended-hours volume. It turns out to have a far better justification than
the one we gave it.

### The ATR lookback is our version of "short lookbacks"

ATR20 on 5-minute bars is **100 minutes** of history, smoothed by Wilder's
recursion. In a volatility spike it rises slowly, so for roughly 20 bars after
a regime change the risk distance is too small and the computed position size
too large — the 2008 procyclicality failure in miniature, and in the dangerous
direction.

What protects us is the constraint we spent two days complaining about.
`max_position_notional_pct: 15.0` binds before the risk budget on all five
symbols, so position size is set by notional, not by a lagging ATR. The cap
costs us upside and it is also the thing that makes an ATR-lag failure
survivable. Both statements are true and the submission should say so.

---

## 3. Allocator research: the submission is being judged on the wrong axis

Institutional allocators split manager evaluation into two workstreams:
**investment due diligence** (can they generate returns) and **operational due
diligence** (will investors actually receive them). ODD covers governance,
controls, valuation, technology, documentation — everything that is not the
strategy. It is usually run by a separate team, **often with a veto**, and the
practitioner literature is blunt that a fund can pass investment diligence
with flying colours and still be killed by ODD. Incomplete or disorganised
documentation halts the process *regardless of strong returns*.

The standard instrument is the AIMA Due Diligence Questionnaire. On the
investment side it asks, in substance: **who generates ideas, who sizes them,
who kills them, and is any of that written down?**

That is an unusually exact description of what this project has built. §8's
permission table answers the first three; the hash-chained ledger answers the
fourth.

### Performance-chasing is a documented failure of professional judgement

Goyal and Wahal (2008) examined manager selection and termination by **3,400
plan sponsors, 1994–2003**. Sponsors hired after large positive excess returns;
post-hiring excess returns were **indistinguishable from zero**. Managers fired
for underperformance frequently went on to produce positive excess returns, and
across matched round-trip decisions the fired managers outperformed their
replacements. The authors' conclusion: plan sponsors have no timing ability.
CFA Institute's survey of the literature puts it plainly — institutional
investors lose money on hire-and-fire decisions over one- and three-year
periods and do not recover it even after five.

So: selecting on recent returns is not merely noisy. It is a documented,
value-destroying error made consistently by professionals with far better data
and far longer windows than four and a half sessions.

### How long a record would actually be needed

| source | requirement |
|---|---|
| Kenneth French | a manager with 5% alpha and 20% volatility needs **64 years** for a t-statistic of 2 |
| López de Prado, Minimum Track Record Length | **2.73 years** for an annualised Sharpe of 2 to be shown greater than 1 at 95% confidence |
| Andrew Lo, *The Statistics of Sharpe Ratios* (FAJ 2002) | the Sharpe ratio is an estimate with a standard error; period sensitivity alone dominates a three-year record |

And for this competition, computed directly:

| target | annualised Sharpe required over 4.5 sessions |
|---|---|
| t = 1.0 | **7.5** |
| t = 1.96 | **14.7** |
| t = 2.0 | **15.0** |

**No entrant's P&L in this competition is statistically distinguishable from
luck.** Not ours, and not the winner's. That is arithmetic, not modesty.

### The theorem our H1 result demonstrates

Bailey, Borwein, López de Prado and Zhu (2014) prove that the expected maximum
Sharpe ratio across N independently tested strategies grows with **√log N even
when every strategy's true Sharpe is zero**, derive a minimum backtest length
as a function of N, and propose the Deflated Sharpe Ratio as the correction.

We tested N = 4 pre-registered hypotheses. H1 — the logical complement of the
invalidated rule — returned **+3.18% in-sample and −1.21% out-of-sample**, with
92 targets in TRAIN and zero in 25 TEST signals. Across the combined window its
gross was +59.88 and would have read as a discovery.

That is the theorem, observed in our own data rather than cited from a paper.
It is also why `docs/HYPOTHESIS_TESTS.md` refuses a fifth hypothesis: with the
in-sample maximum rising in N by construction, the fifth trial is the one most
likely to produce a mirage.

---

## 4. What changes as a result

**Config, before the first trade:** a second correlation cluster covering all
five symbols. Tightening only, justified by 1997 and 2008 rather than by our
own returns.

**Submission structure:** reorganised into the allocator's two workstreams.
Our ODD position is strong — pre-committed spec, enforced limits, hash-chained
ledger, reproducible analysis, a self-commissioned audit publishing 29 defects
in our own work. Our IDD position is weak and documented: no measured edge,
invalidated on three pre-registered conditions. Presenting both in the
structure allocators actually use is more honest *and* more favourable than
leading with a return number.

**One line we can now defend with citations rather than assertion:** judging
this competition on 4.5 sessions of P&L reproduces the exact error Goyal and
Wahal measured across 3,400 plan sponsors. We are not asking to be excused the
P&L. We are saying what it can and cannot establish, and showing the
arithmetic.

---

## Sources

- Goyal, A. and Wahal, S. (2008), *The Selection and Termination of Investment Management Firms by Plan Sponsors*, Journal of Finance — https://www.semanticscholar.org/paper/44371eba809ab1805817bf528c420d2a4c80baeb
- Stewart, S. (2013), *Manager Selection*, CFA Institute Research Foundation — https://rpc.cfainstitute.org/sites/default/files/-/media/documents/book/rf-publication/2013/rf-v2013-n4-1-pdf.pdf
- Bailey, Borwein, López de Prado and Zhu (2014), *Pseudo-Mathematics and Financial Charlatanism*, Notices of the AMS 61(5) — https://www.ams.org/notices/201405/rnoti-p458.pdf
- Bailey and López de Prado (2014), *The Deflated Sharpe Ratio*, Journal of Portfolio Management 40(5)
- Bailey and López de Prado, *The Sharpe Ratio Efficient Frontier* / Minimum Track Record Length — https://www.davidhbailey.com/dhbpapers/sharpe-frontier.pdf
- Lo, A. (2002), *The Statistics of Sharpe Ratios*, Financial Analysts Journal 58(4)
- French, K., on track-record length — https://www.priceactionlab.com/Blog/2024/09/track-record/
- BIS Quarterly Review, March 2018, on the February 2018 ETP feedback loop
- Federal Reserve History, *Asian Financial Crisis* — https://www.federalreservehistory.org/essays/asian-financial-crisis
- FRBSF (2011), *Could We Have Learned from the Asian Financial Crisis of 1997–98?* — https://www.frbsf.org/research-and-insights/publications/economic-letter/2011/02/asian-financial-crisis-1997-1998/
- IMF Finance & Development (1999), *The Asian Financial Crisis: What Have We Learned?* — https://www.imf.org/external/pubs/ft/fandd/1999/09/lane.htm
- AIMA Illustrative Due Diligence Questionnaire (industry standard DDQ)
