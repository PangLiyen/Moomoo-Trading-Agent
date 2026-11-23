# Complete findings register — audit of 2026-11-21

`docs/AUDIT_2026-11-21.md` discusses the high-severity findings and referred
the rest to "the audit transcript." No transcript was in the repository: it
cited 14 findings and left **12 uncited and unrecorded anywhere**. This file
is the register. It exists because a document claiming completeness it does
not have is the same defect as P-c, one level up.

Severity is the auditor's. Disposition is ours, and "accepted" means we read
it, agree, and are not changing it — with the reason.

**This register has itself been audited and corrected.** Seven of its own
claims were wrong on first writing. See "Corrections to this register" at the
end; the errors are listed rather than silently repaired, and git history holds
the original.

---

## Provenance

| # | Sev | Finding | Disposition |
|---|---|---|---|
| P-a | High | Ledger genesis pinned `code_commit 5748462`, a tree containing no engine files | **Fixed.** `engine/provenance.py` derives the commit from git and refuses to start unless every path in `ENGINE_FILES` exists at it — now all nine `engine/` modules plus `run_cycle.py`, the config and the spec, with a test that fails if a module is added and not listed. Eight tests build real repositories, including the exact 5748462 shape. The old genesis record stands: rewriting it is what the chain exists to detect |
| P-b | High | `STRATEGY.md`'s rules hash is the config at 5748462, not the config in force (`f03bc61c`) | **Documented.** Exactly two enforced values differ, both toward conservatism. Header keeps the original hashes; a hash that follows its file is not a hash |
| P-c | High | `PRE_REGISTRATION.md` and others claimed to be committed while untracked | **Fixed** in `223a34d`. No claim is made that any of it predates the measurements it discusses |

## Section 3 — universe and cluster

| # | Sev | Finding | Disposition |
|---|---|---|---|
| N1 | Low | "Fixed… never changed mid-session" is procedural; nothing prevents editing the YAML between cycles, and the genesis hash is written once | **Partly fixed.** `rules_hash`, `code_commit` and `engine_files_dirty` are now written on **every** `account_guard` record, so a mid-run YAML edit is visible in the chain afterwards. Preventing it needs a supervisor the architecture does not have; detection is the honest substitute. *(This disposition was false when first written — the hash was recorded only at genesis. Fixed in code, then re-stated.)* |

## Section 4 — entry conditions

| # | Sev | Finding | Disposition |
|---|---|---|---|
| N2 | Med | Condition 6 says "position and order budgets"; code also enforces 3 concurrent, no-add-to-position, 6 entries/session, 20 orders/session, 300s spacing | **Accepted, doc incomplete.** The code is stricter than the spec, which is the safe direction. Listed here rather than back-fitted into the pre-committed §4 |
| N3 | **High** | Kill switch, drawdown, consecutive-loss, cycle-gap and spacing branches cannot fire — the driver hardcodes zeros. `AGENT_MODE` never read | **Open, blocking the live run.** Goes into the executor with real session state. Not trading until it does |
| N4 | Med | 10:00 entry floor is a function default; `session.earliest_entry_et` is dead config | **Open.** Values agree today, so no behavioural effect, but it violates "config is the single source of truth" |
| N5 | Med | `--quote-age` and `--spread-pct` are operator floats with no cross-check against the book | **Accepted for the supervised phase, open for autonomous.** The executor will read the book itself. While a human runs cycles, the number is the human's claim and the ledger records it as supplied |

## Section 4 — exits and indicators

| # | Sev | Finding | Disposition |
|---|---|---|---|
| N6 | **High** | `evaluate_exit` has zero callers and zero tests. No exit path, no flatten scheduler, no exit record | **Open, disqualifying.** §4 promises five exits; the live system implements none. This alone is why Monday is not a trading day |
| N7 | **High** | Holiday stop unimplemented; `calendar_overrides` never read. 27 Nov 12:45 ET will not happen | **Open.** In the executor. 27 Nov is a half day and the deck warns there are no reset cards |
| N8 | Med | Session stop 15:50 and time stop 60 min are function defaults; the config keys are unread | **Open.** Same fix as N4 — read from config, not from a default that happens to match |
| N9 | Med | `STOP_HIT` compares a 5-minute bar close; `software_stop_check_interval_seconds: 60` implies quote polling. An intra-bar breach that recovers by the close does not exit | **Open, and the config's reading is the safer one.** Measured stop breaches had a 0.249 ATR median, so bar-close checking has not yet cost anything visible — but it is looser than the config claims |
| N10 | Low | `evaluate_exit`'s docstring says "Target, stop, time stop, session stop… in that order"; the code checks SESSION_STOP → STOP_HIT → TARGET_HIT → TIME_STOP | **Fixed.** The docstring now states the real precedence — SESSION_STOP, STOP_HIT, TARGET_HIT, TIME_STOP — and says why the order is the tie-break: the label is what the ledger records and what P7 would be measured from. *(Previously marked "fixed in the executor rewrite", for an executor that did not exist.)* |
| N11 | Low | Target uses the current SMA20, so it drifts after entry; §4 is ambiguous | **Accepted.** "Price returns to the mean" is the stated intent and the mean moves. Noted because a reader could reasonably expect the entry-time value |
| N12 | **High** | RTH-only and 5-minute claims are not enforced anywhere in the live path. `ET_SESSION_OPEN`/`CLOSE` are defined and never used | **Open.** The analysis pipeline filters; the driver accepts any bars at any interval. A pre-market bar would silently enter the indicators |
| N13 | Med | ATR20 is Wilder smoothing, not a plain average true range; the doc says "average true range" | **Accepted, doc imprecise.** Wilder is the intended implementation and the cost gates were derived against it. *Evidence corrected:* the 0.000e+00 agreement was engine vs `analysis/compute_indicators.py`. `canonical.mjs` emits no per-bar series, so it corroborated only the exclusion set and the signal counts — it cannot and did not confirm the ATR values |
| N14 | Med | The outlier filter drops bars before SMA/ATR and §4 never mentions it. `prev_close` is the previous *clean* bar, so condition 2 can compare across a dropped bar | **Accepted and worth stating plainly.** The filter changes every indicator and is undocumented in the pre-committed spec. Comparing across a dropped bar is the correct behaviour — the dropped bar's close is not trustworthy — but it is not what §4's "previous bar close" literally says |

## Section 5 — sizing

| # | Sev | Finding | Disposition |
|---|---|---|---|
| N15 | Med | Four rejection rules are absent from §5: ATR floor, price < $20, notional < $3,000, cost/edge > 20% | **Accepted, doc incomplete.** These change *which trades exist*, which is a larger omission than C1's change to their size. Each is required by config and tested |
| N16 | Low | Cash sufficiency is an extra shrink/block not in §5 | **Accepted.** Consistent with `forbid_sizing_off_buying_power` |
| N17 | Low | The 15% per-position cap lives only in `sizing`; the gate does not independently re-check it | **Accepted, structural note.** One proposal source today. A second source could reach the gate with an oversized proposal and not be caught |
| N18 | Med | The model cannot choose quantity, but it supplies the bars that quantity is derived from. Nothing in code detects fabricated bars | **Accepted, and the most honest limitation in this list.** "The model never chooses a quantity" is literally true and materially weaker than it sounds. §8's "invent missing data" is a prohibition, not a control. Cross-implementation verification is the mitigation, and it is partial |

## Section 8 — permissions

| # | Sev | Finding | Disposition |
|---|---|---|---|
| N19 | Med | "Risk auditor may tighten at runtime" does not exist. `RiskConfig` has no setters; tightening needs a YAML edit and a restart | **Accepted, and the spec overstates.** `auditor_may_loosen_limits`, `auditor_runs_in_separate_session` and `credentials_scoped_to` are declarative. The gate does tighten per-proposal via SHRINK; it cannot tighten a *limit* |
| N20 | Med | "Executor may not increase size" is detectable after the fact, not prevented. Nothing verifies submitted qty equals `decision.qty_out` | **Open.** The executor will write the approved qty to the ledger and reconcile it against the broker fill, which makes a mismatch detectable within one cycle rather than at audit time |
| N21 | Low | `run_cycle` prints the stop as `sizing_distance_usd / 1.5`, hard-coding 1.5 | **Fixed.** `run_cycle.py` now reads `stop_distance_atr_mult` from config instead of dividing by a literal 1.5. *(Previously marked "fixed in the executor rewrite", for an executor that did not exist.)* |
| N22 | — | Ledger exposes append/verify only; edit and delete impossible through the API, tampering detectable; the docstring's qualification is accurate | **Confirmation, not a defect** |

## Cross-cutting

| # | Sev | Finding | Disposition |
|---|---|---|---|
| N23 | Med | Nothing writes `order_id` to the ledger, so every live fill must be hand-added to `known_own_order_ids` or the next cycle halts | **Open.** The executor writes an `execute` record carrying `order_id` at submit time, which is the actual fix. The config comment already flags the workaround's shelf life |
| N24 | Low | `run_cycle` takes `sorted(glob)[0]` per symbol, so a stale bar file can win over today's | **Open.** Combined with N12 this is a real operational hazard: stale bars, silently |
| N25 | Low | `account.max_gross_exposure_pct_of_cash` duplicates `sizing.max_gross_exposure_pct` and is unread | **Open.** Two numbers that must agree and only one that is enforced |
| N26 | Low | `DECISION_LOG_SCHEMA.md` promises `stage: execute/exit`, an `order` block, `account_snapshot` with equity/peak/drawdown, `EXIT` verdicts and `CONSEC_LOSS_THROTTLE` — none written | **Open.** The schema documents the intended design; the engine implements a subset. The three missing record types are exactly what P2, P6 and P7 need |
| N27 | Med | `provenance._git` stripped its output, eating `git status --porcelain`'s leading status column; the fixed-offset parse then cut into the filename, so a **dirty engine reported as clean** | **Fixed.** `strip` is now opt-out, and porcelain is parsed with rename handling. Found by `test_a_dirty_engine_is_recorded_even_when_permitted` — the only defect here caught by the suite rather than by a reader |

---

## One correction the audit made to our own correction

Addendum 2 (C2) states the gate's invariant is "tested across 2,000
randomised states." The audit instrumented the sweep: of 2,000 iterations
**1,910 reach the gate** and 90 raise in `size_position` before it, and SHRINK
is exercised by three of them — targeted tests cover that path properly.

So the accurate figure is **1,910**, not 2,000. Recorded here rather than
edited into Addendum 2, which is itself committed. The same rule applies to a
correction as to the original: append, date, explain.

---

## Count

**29 numbered findings:** three provenance (P-a to P-c) and 26 code findings
(N1 to N27, where N22 is a confirmation rather than a defect).

- 3 high-severity provenance issues: two fixed, one documented
- **4** high-severity implementation gaps — N3, N6, N7, N12 — all open, and
  collectively the reason the live run is not starting on Monday
- the remainder accepted-with-reason or scheduled into the executor

## How these were found

All 29 came from reading code against documents — a cross-implementation
comparison and two independent audits. None was surfaced by running the test
suite, and that is not a coincidence: the recurring defect is a control whose
logic is correct and whose caller starves it, and tests that construct their
own inputs cannot see a production path that constructs them wrongly.

**One exception, and it belongs in the register rather than a commit message.**
N27 below was found by writing the provenance tests. It is the only defect in
this project so far that the test suite caught before a human did, which is
itself the argument for writing tests against the driver rather than the
functions.

---

## Corrections to this register

Seven claims in the first version were wrong. Listed because a register that
quietly repairs itself is not evidence of anything.

| Claim as written | Reality | Now |
|---|---|---|
| "twelve findings were cited and recorded nowhere" | inverted: 14 were cited, 12 were *uncited* | corrected in the header |
| "26 findings" with P-a/b/c inside it | 29 numbered items; 26 is the N-count | stated as 29 = 3 + 26 |
| "Five high-severity implementation gaps" | four: N3, N6, N7, N12 | corrected |
| N1 "config sha256 now recorded per cycle" | recorded only at genesis | made true in code |
| N10 "Fixed in the executor rewrite" | no executor; docstring unchanged | made true in code |
| N21 "Fixed in the executor rewrite" | no executor; literal 1.5 still there | made true in code |
| N13 "matches canonical.mjs at 0.000e+00" | that agreement was vs `compute_indicators.py`; canonical emits no per-bar series | evidence corrected |
| P-a "every engine file" | `ENGINE_FILES` omitted three modules | list made exhaustive, with a test |

Four of those were **intent written as fact** — the same error as P-a itself,
committed three times in a row inside documents whose only purpose is accuracy.
Writing "fixed" about work not yet done is the most natural dishonesty
available to someone keeping their own scorecard, and noticing the pattern took
an outside reader each time.

---

## Commit message corrections

**2026-11-23 — commit `2153c64` describes work that is not in it.**

Its body claims to add the executor, the four rehearsal fixes and the second
correlation cluster. All three landed one commit earlier, in `689e5c5`. What
`2153c64` actually contains is `.gitignore`, `docs/DDQ.md` and a rehearsal
ledger. The message was reused verbatim from the intended commit and the two
were not squashed.

Recorded rather than rebased because the commit is already pushed and rewriting
published history is the act the ledger's chain exists to make detectable. The
commit that matters is checkable directly: `git show --stat 689e5c5` contains
every engine file, and `git show --stat 2153c64` contains none.

Corrected here by appending, the same rule that governs the register above: a
document that quietly repairs itself is evidence of nothing.

---

## Additions — 2026-11-24

Four findings from the 2026-11-23 live session. Appended rather than merged
into the tables above so the register's own history stays visible. With these
the register stands at **33** numbered findings; the Count section above says
29 and is left as written for that reason.

| # | Sev | Finding | Disposition |
|---|---|---|---|
| N28 | **High** | `analysis/measure_signal_rate.py:171` builds the signal set with the very condition it is later used to evaluate: `if not (dislocated and b["c"] > b["prev_close"]): continue`. Every signal in `base`, and therefore in `joint`, ticked up by construction, so "how many of the 22 would the tick-up filter block" must return 0 of 22 and can return nothing else. Measured correctly — on bars that pass regime and the ATR floor *before* the tick-up test — 57 dislocated bars survive the gates and tick-up removes **35 (61.4%)**, passing 22. Of all 614 dislocated bars in the window, 182 (29.6%) ticked up | **Open, documentation only.** The analysis code is not changed; the counts it produces are still correct as counts of *this strategy's* signals. What it cannot support is any inference about the tick-up condition itself. See "Which reports were affected" below |
| N29 | Med | The entry gate never re-checks the live price. `evaluate_entry` scores the last **closed bar** close against the threshold; `quotes.json`'s bid/ask feed the spread check only. Cycle C20261123T1052 evaluated XLE at 60.35 against a 60.3688 threshold while the captured book was 60.41 / 60.42 — the market was above the trigger and the rule still read "dislocated". Between the qualifying bar and any order lies up to five minutes of price action | **Open.** The pre-committed rule is defined on bar closes, so this is a fidelity gap between the rule and the order that follows it, not a bug in the rule. Closing it means an explicit live-price re-check before submission, which is a strategy change and not taken (see N5, the same operator-supplied-numbers family) |
| N30 | Low | `inputs/quotes.json` stores `bid`, `ask` and a precomputed `book_age_seconds` — no absolute book timestamp. The freshness the engine enforces at run_cycle.py:387 is a number the operator calculated at capture time; nothing in the file or the ledger can be re-derived afterwards, and nothing can check it against the book | **Open.** Unlike N5 this survives the executor: reading the book directly does not help unless the book's own timestamp is recorded. `quote_age_seconds` in the ledger is currently an assertion, not evidence |
| N31 | **High** | The foreign-order guard has no freshness bound on its input. Cycle C20261123T1052 decided on a broker snapshot captured at 10:47:56 ET; order 3131737 was created at 10:52:29 — five minutes after the capture, 15.6 s before the run — so the order list the guard checked could not contain it. `gate.assert_no_foreign_activity` (run_cycle.py:306) correctly rejects any id it is shown; it is shown whatever the operator captured. Same class as FIX 1 (commit 3e4ce37: a negative quote age now halts instead of passing) and as the bar-lookahead defect fixed in 689e5c5 (bars that had not closed were used, producing negative staleness and lookahead): a safety decision taken on an input of unbounded age | **Open.** The detection on 2026-11-23 at 10:56:58 was **operator-initiated** — a fresh snapshot read by hand for the next cycle — and not engine-detected; no cycle ran against the post-order state. The fix is for the decision path to read the order list and account state itself and record the capture time, refusing to proceed past a configured age, as it already does for quotes. No line in this register or in `docs/INCIDENT_2026-11-23_FOREIGN_WRITER.md` claimed engine detection — the incident doc's section "What the engine detected, and when" opens "Nothing, until asked." and names the operator and the 10:56:58 snapshot; the run_cycle.py:306 halt it describes is explicitly labelled as read, not executed |

### N28 — which reports were affected

The circular count reached print. Every one of these is qualified or
withdrawn to the extent it bears on tick-up:

- **This session's answer of 2026-11-23, "0 of 22 (0.0%)".** Forced by the
  sample definition. The counterfactual numbers produced alongside it
  (57 pre-tick-up bars, 35 removed, 614 dislocated, 182 ticked) are not
  affected and are the usable ones.
- **Cycle reports for C20261123T1045 and C20261123T1052**, and the summary
  phrasing "22 post-gate signals". Imprecise in a way that matters: the
  pipeline applies tick-up **before** the regime and ATR gates
  (measure_signal_rate.py:171 precedes :173-181), so the largest single
  removal at the gated stage — 35 of 57 — belongs to tick-up and had been
  attributed to nothing. Read "post-gate" in those reports as "post-everything".
- **`docs/PRE_REGISTRATION.md` P3** ("simulation took 9 of 22 signals") and
  **`docs/STRATEGY.md`** §"sequential simulation" and I7 ("cluster rule
  blocked 7 of 22"). These are correct as counts of the pre-committed
  strategy's own signal set, which legitimately includes the tick-up
  condition. They cannot be cited as evidence about the tick-up filter.
- **`docs/DDQ.md`** ("170 base signals … gross −0.65") and
  **`docs/SCORING_PLAN.md:110`**, same category. 170 is *ticked* dislocations
  in the baseline window, not all dislocations there.
- **`analysis/measure_signal_rate.py` Part E** frames its blocked group as
  "the counterfactual for the regime filter". The comparison is valid for the
  regime filter, but both arms are tick-up-selected: 432 of 614 dislocated
  bars never enter either arm.
- **The stored project note** "170 base / 22 joint signals" inherits the same
  qualification.

This is the account-guard tautology again (`docs/AUDIT_2026-11-21.md` §5,
`docs/DDQ.md` B4): there, the guard was handed config and asked to check it
against config, so it could only fail if the YAML contradicted itself; here,
the signal set is handed the tick-up condition and asked how it performs with
and without it. Both are logic that is correct in isolation and unfalsifiable
where it is wired. The earlier instance was found by an outside reader, and
so was this one.

---

## Dispositions closed — 2026-11-24

Two of the four findings above were closed by operator decision the same day,
after this register recorded them. The rows above are left as written, per the
rule this document follows.

| # | Earlier disposition | Now |
|---|---|---|
| N29 | "Open. Closing it means an explicit live-price re-check before submission, which is a strategy change and not taken" | **Closed.** The operator's reading is the one that stands: the registered rule is "price dislocated below SMA20 by 1.5 ATR", and checking a bar close alone let a crossing limit fill above the threshold — an implementation shortfall, not a strategy change. `evaluate_entry` now requires `0 < live_ask < threshold` as an additional AND condition. It can only refuse more entries. The reasoning and the evidence are in `docs/STRATEGY.md` Addendum 3, dated 2026-11-24 |
| N31 | "Open. The fix is for the decision path to read the order list and account state itself and record the capture time, refusing to proceed past a configured age" | **Closed, as specified.** The order-list input now carries `captured_at_et`; `assert_order_list_fresh` refuses an age outside `[0, max_order_list_age_seconds]` (120) with `STALE_ORDER_LIST`, and the age and bound are recorded on the guard record. A bare list is refused as `GUARD_INPUT_INVALID`. The decision path still takes the operator's capture rather than reading the broker itself; what changed is that the capture time is now data the engine can refuse, not an assumption it inherits. Cycle 5's 288-second capture is a regression test, and a 244-second one runs through the real driver |

N28 and N30 remain open, unchanged.

