# Hypothesis tests — 2026-11-21

Run after `docs/STRATEGY.md` was declared invalidated and **before any
strategy trade**. Four hypotheses, named in advance, each reported on a
train/test split whatever the result. Script: `analysis/test_hypotheses.py`.
It prints the hypothesis set, both split date lists, the seed and the shared
exit rules *before* it loads any bar data, so the output itself evidences that
nothing was added after a number appeared.

**All four failed.** The detail of how they failed is worth more than the
verdict.

---

## The split

| | sessions |
|---|---|
| TRAIN | 11-09, 11-10, 11-11, 11-12, 11-13 |
| TEST | 11-16, 11-17, 11-18, 11-19, 11-20 (feed ends 11:15, so 4.3) |

## The hypotheses

| | rule | why this one |
|---|---|---|
| **H0** | the invalidated rule: buy 1.5·ATR below SMA20 on an up-tick, regime OK | control |
| **H1** | the logical complement: buy 1.5·ATR **above** SMA20 on an up-tick, target +1.5·ATR | I3 tripped because price failed to revert in 90.9% of setups — it kept going. If that is real, continuation should work where reversion did not |
| **H2** | H1 with the regime condition inverted | regime-permitted trades measured worse than regime-blocked |
| **H3** | random eligible bars, seeded, matched to H0's count, same exits | is the exit structure itself producing the loss? |

Long only throughout. Shorting stayed forbidden.

---

## Results

| TRAIN | n | stop/tgt/time | win% | gross | net | mean R | net @ engine sizing |
|---|---|---|---|---|---|---|---|
| H0 | 14 | 9/2/3 | 0.0 | −5.52 | −33.38 | −0.392 | −0.061% |
| **H1** | 202 | 46/**92**/64 | 12.4 | **+101.38** | −300.60 | **+0.673** | **+3.184%** |
| H2 | 32 | 10/10/12 | 3.1 | +2.79 | −60.89 | +0.242 | +0.061% |
| H3 | 14 | 1/13/0 | 0.0 | +1.11 | −26.75 | +0.073 | −0.009% |

| TEST | n | stop/tgt/time | win% | gross | net | mean R | net @ engine sizing |
|---|---|---|---|---|---|---|---|
| H0 | 8 | 6/0/2 | 0.0 | −1.84 | −17.76 | −1.162 | −0.359% |
| **H1** | 25 | **24**/**0**/1 | **0.0** | **−41.50** | −91.25 | **−1.296** | **−1.213%** |
| H2 | 13 | 9/2/0 | 0.0 | −4.92 | −26.81 | −0.693 | −0.307% |
| H3 | 8 | 3/**5**/0 | 0.0 | −5.22 | −21.14 | −0.412 | −0.145% |

Sequential simulation under the real limits, TEST only:

| | taken | refused | result | max DD |
|---|---|---|---|---|
| H1 | 6 of 25 | 9 holding, 10 cluster | **−$242.09 (−0.242%)** | 0.242% |
| H2 | 6 of 13 | 2 holding, 2 cluster, 2 spacing | −$75.63 (−0.076%) | 0.131% |

---

## H1 is the finding

On TRAIN it looked like a discovery: 92 targets out of 202 signals, mean R
+0.673, **+3.18% at engine sizing**. On TEST the same rule, unchanged,
produced **zero targets in 25 signals**, stopped out 24 times, mean R −1.296.

Across the combined nine sessions H1's gross is **+59.88** and would have read
as a positive result. Had we searched the whole window instead of splitting
it, we would have promoted H1, written it up as a pre-committed continuation
strategy, and deployed a rule whose out-of-sample performance is −1.21%.

Two separate errors would have produced that number, and both are worth
naming because both are common:

1. **No holdout.** Selecting on all available data and reporting the same
   data's performance. The split cost nothing and caught it.
2. **Signal-set totals read as portfolio results.** H1's TRAIN figure sums 202
   overlapping signals with no concurrency, cluster or budget limits applied.
   It is not reachable by any portfolio. Under the real limits the TEST set
   took 6 of 25 and lost $242.

The second error is the subtler one, and it survives a train/test split. A
figure that assumes you can hold every signal simultaneously is not a
strategy result at all, however it is computed.

## H3 is the uncomfortable one

Random eligible bars, matched to H0's count, lost in both splits — and on TEST
the random control hit **5 targets to H0's zero**. The entry rule did not
merely fail to add value; on the test split it selected worse than chance.

H3 also answers the question it was built for. Random entries lose roughly a
fee-and-noise-sized amount whatever is fed in, so the exit structure is not
manufacturing the loss. It is not rescuing anything either.

## One positive result

H0 reproduces the earlier Part E joint group **exactly** — 22 closed, 15 stop,
2 target, 5 time, 0 wins, net −$51.14 (−33.38 train, −17.76 test) — from a
separately written script. Two independent implementations agree on the rule
being tested. Whatever is uncertain here, the measurement of H0 is not.

## Conclusion, and why we stop

No rule tested has an edge in this window. Gross P&L across H0's full 170
signals was −0.65, indistinguishable from zero before costs; the continuation
complement fails out of sample; inverting the regime fails; random does about
as well as either.

**A negative result across a pre-registered set is a reason to stop testing
variants, not a reason to try a fifth.** With n of 8–25 per hypothesis on the
test split, a positive result would not have established an edge either. The
window is nine sessions of one tape. What we can say is bounded by that, and
we say only that.

Nothing was promoted. No value in `config/` or `engine/` changed as a result
of this run.

---

## Addendum 2026-11-24 — the tick-up condition, measured on TRAIN only

Entry condition 2 (`close > previous_bar_close`) had never been measured
separately; it arrived inside every H0 signal because the analysis defines the
signal set with it (`analysis/measure_signal_rate.py:171` — see
`docs/AUDIT_FINDINGS.md` N28). To measure it, the tick-up test has to be moved
*after* the regime and ATR gates, so that both arms exist:

- bars that are dislocated, in the entry window, regime-OK and above the ATR
  floor — 57 across the window, of which 35 fail tick-up and 22 pass;
- the 22 that pass are H0's signal set; the 35 that fail were, until now,
  never counted as anything.

**TRAIN only (11-09 … 11-13). TEST was not run.** Same exits as H0, 1 share,
$1.99 round trip, entry at the qualifying bar's close.

| group | n | closed | net > 0 | gross > 0 | stop/tgt/time | mean gross | median gross | mean net | total gross | total net |
|---|---|---|---|---|---|---|---|---|---|---|
| ticked up (H0) | 14 | 14 | **0** | 3 | 9/2/3 | **−0.395** | −0.188 | −2.385 | −5.52 | −33.38 |
| did not tick | 23 | 23 | **0** | 4 | 16/1/6 | **−0.215** | −0.165 | −2.205 | −4.93 | −50.70 |

The ticked row reproduces H0's TRAIN line in the table above exactly
(14, 9/2/3, gross −5.52, net −33.38), which is the expected check: H0 *is*
the ticked group.

**Result.** Zero net-positive signals in either group. Gross is negative in
both groups, and the group that passes the condition has the worse mean
(−0.395 vs −0.215) and median (−0.188 vs −0.165). At 1 share with a $1.99
round trip, no trade can clear its costs, so net is a fee count here; gross
is the statistic that describes the entries, and on this sample the tick-up
condition did not separate good entries from bad ones. The gross-positive
rate (3 of 14 vs 4 of 23) is not distinguishable from noise at this size.

**Independence caveat, stated in full.** These are not 37 independent
observations. They are 37 bar-level flags over 2.5 sessions of one tape, and
they cluster hard: 9 of the 14 ticked signals are consecutive bars between
14:10 and 14:50 on 11-11, inside one SPY/QQQ move, and the not-ticked group
has the same shape on 11-09 (11 of 23, XLE, 13:20–14:45). Counting them
separately overstates the evidence by a large factor. They are also
signal-set totals, not portfolio results: no concurrency limit, cluster rule
or entry spacing is applied, which is the same error the H1 section above
names as the subtler of its two. The honest sample size here is closer to a
handful of price moves than to 37.

Nothing was promoted, and nothing in `config/` or `engine/` changed. TEST
remains untouched by this addendum.
