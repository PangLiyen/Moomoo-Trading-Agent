# Complete findings register — audit of 2026-11-21

`docs/AUDIT_2026-11-21.md` discusses the high-severity findings and referred
the rest to "the audit transcript." No transcript was in the repository, so
twelve findings were cited and recorded nowhere. This file is the register.
It exists because a document claiming completeness it does not have is the
same defect as P-c, one level up.

Severity is the auditor's. Disposition is ours, and "accepted" means we read
it, agree, and are not changing it — with the reason.

---

## Provenance

| # | Sev | Finding | Disposition |
|---|---|---|---|
| P-a | High | Ledger genesis pinned `code_commit 5748462`, a tree containing no engine files | **Fixed.** `engine/provenance.py` derives the commit from git and refuses to start unless every engine file exists at it. Seven tests build real repositories, including the exact 5748462 shape. The old genesis record stands — rewriting it is what the chain exists to detect |
| P-b | High | `STRATEGY.md`'s rules hash is the config at 5748462, not the config in force (`f03bc61c`) | **Documented.** Exactly two enforced values differ, both toward conservatism. Header keeps the original hashes; a hash that follows its file is not a hash |
| P-c | High | `PRE_REGISTRATION.md` and others claimed to be committed while untracked | **Fixed** in `223a34d`. No claim is made that any of it predates the measurements it discusses |

## Section 3 — universe and cluster

| # | Sev | Finding | Disposition |
|---|---|---|---|
| N1 | Low | "Fixed… never changed mid-session" is procedural; nothing prevents editing the YAML between cycles, and the genesis hash is written once | **Partly fixed.** The config sha256 is now recorded per cycle in the `account_guard` record, so a mid-run edit is visible in the chain afterwards. Preventing it needs a supervisor the architecture does not have; the detection is the honest substitute |

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
| N10 | Low | `evaluate_exit`'s docstring says "Target, stop, time stop, session stop… in that order"; the code checks SESSION_STOP → STOP_HIT → TARGET_HIT → TIME_STOP | **Fixed** in the executor rewrite. The code order is correct; the docstring was wrong |
| N11 | Low | Target uses the current SMA20, so it drifts after entry; §4 is ambiguous | **Accepted.** "Price returns to the mean" is the stated intent and the mean moves. Noted because a reader could reasonably expect the entry-time value |
| N12 | **High** | RTH-only and 5-minute claims are not enforced anywhere in the live path. `ET_SESSION_OPEN`/`CLOSE` are defined and never used | **Open.** The analysis pipeline filters; the driver accepts any bars at any interval. A pre-market bar would silently enter the indicators |
| N13 | Med | ATR20 is Wilder smoothing, not a plain average true range; the doc says "average true range" | **Accepted, doc imprecise.** Wilder is the intended and verified implementation — it matches `canonical.mjs` at 0.000e+00 — and the cost gates were derived against it. The spec wording is loose, not wrong |
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
| N21 | Low | `run_cycle` prints the stop as `sizing_distance_usd / 1.5`, hard-coding 1.5 | **Fixed** in the executor rewrite: read `stop_distance_atr_mult` |
| N22 | — | Ledger exposes append/verify only; edit and delete impossible through the API, tampering detectable; the docstring's qualification is accurate | **Confirmation, not a defect** |

## Cross-cutting

| # | Sev | Finding | Disposition |
|---|---|---|---|
| N23 | Med | Nothing writes `order_id` to the ledger, so every live fill must be hand-added to `known_own_order_ids` or the next cycle halts | **Open.** The executor writes an `execute` record carrying `order_id` at submit time, which is the actual fix. The config comment already flags the workaround's shelf life |
| N24 | Low | `run_cycle` takes `sorted(glob)[0]` per symbol, so a stale bar file can win over today's | **Open.** Combined with N12 this is a real operational hazard: stale bars, silently |
| N25 | Low | `account.max_gross_exposure_pct_of_cash` duplicates `sizing.max_gross_exposure_pct` and is unread | **Open.** Two numbers that must agree and only one that is enforced |
| N26 | Low | `DECISION_LOG_SCHEMA.md` promises `stage: execute/exit`, an `order` block, `account_snapshot` with equity/peak/drawdown, `EXIT` verdicts and `CONSEC_LOSS_THROTTLE` — none written | **Open.** The schema documents the intended design; the engine implements a subset. The three missing record types are exactly what P2, P6 and P7 need |

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

26 findings. Three high-severity provenance issues, of which two are fixed and
one is documented. Five high-severity implementation gaps, all open, and
collectively the reason the live run is not starting on Monday. The remainder
are accepted-with-reason or scheduled into the executor.

Nothing in this register was found by the code's own tests.
