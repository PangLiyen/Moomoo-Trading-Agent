# Incident — foreign writer in account 5832647, 2026-11-23

**Status: halted, unresolved. The writer is unidentified.**

Two orders appeared in the competition account between two of our cycles and
one of them filled. Neither is ours. Nothing was placed, cancelled or modified
in response, and no decision cycle has run since. The chain's own account of
the event is `logs/decisions_live.jsonl` seq 59, `stage: incident`,
`reason_code: UNATTRIBUTED_ORDERS`.

---

## The orders

| order | created ET | updated ET | side | symbol | qty | limit | fill | status | `text` | time type |
|---|---|---|---|---|---|---|---|---|---|---|
| 3131737 | 10:52:29 | 10:52:30 | buy | MU | 1 | 705.065 | **704.055** | 4 filled | `""` | **2** |
| 3131738 | 10:52:47 | 10:53:42 | buy | MU | 1 | 703.000 | — | 5 cancelled | `""` | **2** |

Both are outside the universe. MU had never appeared in the account, in the
config, or in any document in this repository before these two orders.

## Timeline, ET

| time | event |
|---|---|
| 10:47:56 | cash, positions and order list read for cycle 5. Seven orders, none MU. Balance 99,990.765, positions flat |
| **10:52:29** | 3131737 created — 15.6 s before our cycle-5 run began |
| **10:52:30** | 3131737 filled: 1 MU @ 704.055 |
| 10:52:32.998 | our order books captured for cycle 5 |
| 10:52:41 | cycle 5 starts |
| 10:52:44.616 | cycle 5 first ledger record (seq 48) |
| 10:52:44.725 | cycle 5 last ledger record (seq 58). All five symbols `no setup`, no intents written |
| **10:52:47** | 3131738 created — 2.3 s after our cycle-5 run ended |
| 10:53:42 | 3131738 cancelled |
| 10:56:58 | fresh snapshot read for the next cycle: both orders and the MU position found. Cycle not run |

The 2026-11-20 episode was described as appearing "2.5 minutes after our last
API write." This one brackets our writes instead: 15.6 seconds before the
first record, 2.3 seconds after the last. That is a tighter relationship to
our activity than the first episode, and no explanation for it is offered
here.

## Cash reconciliation

| quantity | value |
|---|---|
| balance before (10:47:56) | 99,990.765 |
| fill price | 704.055 |
| commission | 0.99 |
| implied debit | 705.045 |
| **balance after** (10:56:58) | **99,285.72** |
| check | 99,990.765 − 99,285.72 = 705.045 = 704.055 + 0.99 |

The fee is the same $0.99 per filled order the API charges us, and it appears
in the balance identically: price plus 0.99, removed at submission.

Equity:

| quantity | value |
|---|---|
| market value, account snapshot | 704.125 |
| total_asset after | 99,989.845 |
| check | 99,285.72 + 704.125 = 99,989.845 |
| delta vs before | −0.92 = −0.99 fee + 0.07 mark above fill |

The account's `mv` (704.125) and the position record's `mv` (704.205) differ
by 0.08. They are separate snapshots taken seconds apart; the difference is
a quote-timing artefact, not a reconciliation failure. `total_asset` uses the
account figure and reconciles exactly.

One side effect worth recording: `max_power_long` reads 199,275.565 while the
position is open. That is 2 × (balance + mv/2), not the 2 × balance recorded
in `docs/API_CAPABILITIES.md` while the account was flat. Sizing never uses
this field, but the recorded claim is now known to be conditional on being
flat.

## Fingerprint

Our orders, without exception across the whole session, carry an `S`-prefixed
tag in `text` and `order_trade_time_type: 1`.

| episode | orders | symbol | `text` | time type |
|---|---|---|---|---|
| ours, 2026-11-20 probes | 3128953, 3128955, 3128956, 3129023, 3129367 | SPY, QQQ | `S00-…` | 1 |
| **foreign, 2026-11-20** | 3128921, 3128965 | ANT | `""` | **2** |
| **foreign, 2026-11-23** | 3131737, 3131738 | MU | `""` | **2** |

Two independent attributes — empty `text`, and `order_trade_time_type: 2` —
match between the two foreign pairs and match nothing of ours. A single
matching attribute would be a coincidence worth doubting. Two, on separate
fields, in two separate episodes, is the basis on which this is called an
identified second actor rather than an API artefact.

What the fingerprint does not establish: who. It distinguishes orders that
are not ours; it cannot distinguish one outside writer from several, and it
cannot say whether the writer is a UI session, a script, or something else
holding credentials for this paper account. No claim beyond "not us" is made.

## What the engine detected, and when

**Nothing, until asked.** Cycle 5 started 11.6 seconds after the fill and
could not have seen it: the order list it reasoned over was captured at
10:47:56, five minutes earlier. The driver does not query the broker during
the decision — capture and decision are separate steps by design, so an order
placed between them is invisible by construction. Another cycle at 10:52
would have produced the same result.

The detection came from the operator, at 10:56:58, on the fresh snapshot taken
for the next cycle. The engine was never run against this state.

Had it been run, it would have halted before doing anything else of
consequence: `gate.assert_no_foreign_activity` (run_cycle.py:306) raises on
any order id that is neither in the ledger nor in
`account_integrity.known_own_order_ids`, and `run_cycle.py:317` returns
before `plan_exits` (run_cycle.py:329) and before any entry is proposed. So
the halt is upstream of both exits and proposals, which is the correct place
for it.

That is a claim about code that was read, not code that was executed against
this state. It is labelled as such deliberately.

## What the engine refused to do

- No order placed, cancelled or modified. Nothing in `outbox/`.
- The 11:05 ET cycle was not run. The cadence stops at an unattributed order;
  it does not continue and note the anomaly in passing.
- The 1-share MU position was left exactly where it is. Flattening it would
  be acting on someone else's trade — a wire into another actor's position
  disguised as risk management. That decision belongs to the operator, and it
  has not been made.

## Open

1. Who is writing to the account.
2. Whether the MU position should be left, exited, or treated as outside the
   strategy entirely.
3. Whether cycles resume while a position we did not open is in the account.
   The engine has no rule for a symbol outside the universe; the guard halts
   before it would ever be asked to act on it.
