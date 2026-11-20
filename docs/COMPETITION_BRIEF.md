# Moomoo × Qoder AI Trading Agent Challenge — Team Brief

**Compiled:** Fri 20 Nov 2026, 12:20 MYT
**Status:** research complete · direction locked · build starting
**Audience:** the whole team — read this before touching anything

---

## 1. The hard facts

| | |
|---|---|
| **Event** | 2026 Moomoo × Qoder AI Trading Agent Challenge (also "AI Paper Trading Agent Challenge") |
| **Kickoff** | Mon 16 Nov 2026, 6–9 PM SGT, Singapore |
| **Trading window** | 17–28 Nov 2026 |
| **Submission deadline** | **Mon 30 Nov, 23:59 SGT** |
| **Finals** | **Mon 8 Dec** — top 10 shortlisted, in-person presentation, Singapore |
| **Prize pool** | Over **S$8,500** in cash, trading vouchers and Qoder credits |
| **Paper capital** | US$100,000 in a dedicated competition paper account |
| **Participant perk** | 1 month Qoder Pro (US$20) + 1,500 credits (US$20), tied to the Luma registration email |
| **Hosts listed** | Alibaba Cloud SG events, Hannah Wu, Adeline Toh, James Liu (Alibaba Group), Qoder, Lawrence, Wan Qi. Moomoo side: Alwin Aw (options educator, Moomoo MY) presented the strategy deck |
| **Judges** | **Not published.** See §6 for what we can infer |

### Submission must contain

- A Qoder-generated presentation deck
- A runnable trading-agent package: strategy explanation, risk limits, decision log, trade fills and results
- A demo covering **what worked, what failed, and why**
- The exact agent version used, preserved
- *Bonus:* a short screen recording of the dashboard in action

---

## 2. Scoring — and what it actually means

| Weight | Criterion | What it rewards |
|---|---|---|
| **30%** | Strategy & risk design | Clear trading thesis and edge, invalidation conditions, position limits, drawdown response, kill switches |
| **30%** | Qoder & Cloud Agent usage | Multi-agent engineering, orchestration, automation, dashboards, reports, reusable skills |
| **25%** | Risk-adjusted return | Performance relative to risk taken (Sharpe) |
| **15%** | Reusability & documentation | Templates, reusable skills and scripts, slides/report |

The deck groups the first two as **"Trade Strategy Modeling — 55%, more than half the score"** and notes *the published weights may change*.

### The decisive reading

**75% of the score is not P&L.** With only a handful of sessions left, a Sharpe ratio computed on four or five daily observations is statistically meaningless — and the people judging this are engineers who know that. We cannot win the 25%. We can lose it badly with one bad day.

So: **the paper account is an evidence generator, not the product.** We optimise the 75%.

Two scoring details most entrants will miss:

- The handbook states that routing agent output to a chat channel (Slack) **"is what counts towards the automation half of the usage score."** Cheap points, explicitly named.
- The handbook also says **"the judging rubric explicitly rewards dashboards and reports."**

---

## 3. Time remaining

Today is Fri 20 Nov, 12:20 MYT. The US session for Fri 20 has **not opened yet** — pre-market starts 5:00 PM MYT.

**Session clock (SGT/MYT, UTC+8):**

| Session | Local time |
|---|---|
| Pre-market | 5:00 PM – 10:30 PM |
| Regular | 10:30 PM – 5:00 AM (+1d) |
| After-hours | 5:00 AM – 9:00 AM (+1d) |

**Sessions left: 4.5**

| Date | Status | Plan |
|---|---|---|
| **Fri 20 Nov** | Full | Setup + capability probe + 1 supervised trade |
| Sat 21 / Sun 22 | Closed | Build weekend — engine, Cloud Agents deployment |
| **Mon 23** | Full | First automated session (supervised) |
| **Tue 24** | Full | Full automated session |
| **Wed 25** | Full | Full automated session |
| Thu 26 | **Closed** (Thanksgiving) | Audit logs, tune, build deck, record demo |
| **Fri 27** | **Half day** — 13:00 ET close | Flatten by **12:45 ET**. No positions into the weekend |
| Sat 28 | Closed | Window closes. Freeze agent version |
| Sun 29 | — | Deck, recording, rehearsal |
| **Mon 30** | — | **Submit before 23:59 SGT** |
| Mon 8 Dec | — | Finals, Singapore |

The deck is blunt about Nov 27: **no reset cards are allowed**, and unmanaged weekend or holiday gaps can trigger forced liquidation.

---

## 4. The competitive field

Four public repos are building for this exact challenge:

| Repo | Approach |
|---|---|
| `aljabrialam/timon` | "Git for trading" — regime-filtered opening-range breakout on US ETFs, deterministic engine, hash-chained ledger, staged autonomy, Vercel dashboard |
| `aloisohzh/tyche` | Strategy-agnostic platform, volatility-targeted ETF rotation, deterministic risk core, LLM on the perimeter, hash-chained journal |
| `specter165/specter-opt` | Options agent — binomial pricing, EWMA vol, CVaR sizing, screener-driven universe, exposure clusters, 7 pre-committed invalidation conditions |
| `timothylee58/stocksense-ai` | Risk-gated execution loop over an ensemble signal |

### The critical observation

Three of these independently built **the same architecture** — deterministic risk core, LLM kept off the trigger, researcher/executor/risk-auditor trio, hash-chained decision ledger, static dashboard.

That is not coincidence. **It is exactly what the organiser's own handbook tells everyone to build.** The handbook prescribes the researcher/executor/auditor split, tells you to run the auditor in a separate session, to pin skill versions, and to log rejected decisions.

**Conclusion: that architecture is table stakes, not differentiation.** Building it well gets you to par. It does not get you to the top 10.

### Where the field is weak

As of their READMEs: `timón` had Cloud Agents deployment, Slack approvals and schedules all pending, and had **not traded**. `tyche` had Cloud Agent deployment unchecked and no backtest harness. `specter-opt` had not passed its own readiness gates.

**Qoder Use is 30% of the score and nearly everyone is behind on it.** That is the single largest available gap.

---

## 5. Ecosystem research

### Qoder stack

- **Qoder Desktop** — local IDE. Install the `moomoo OpenAPI` connector, the official `moomoo-skill`, and the **Qoder Cloud Agents plugin** (`qca` connector + `qoder-cloud-agents` skill).
- **Qoder Cloud Agents** — launched May 2026, Alibaba Cloud's managed agent runtime. Runs in its own container with **no browser session and no local OpenD**, so it cannot reuse your Desktop login. Moomoo auth goes through a **Vault** holding an MCP OAuth credential.
- **Multi-agent orchestration** — a coordinator delegates to child agents, each with its own model, prompt, tools, MCP servers and skills, each in its own Session Thread. Limits: up to 20 distinct child agents plus **one Advisor**; 25 unarchived threads per session; **depth 1** — children cannot create children. Vaults are bound at session creation. Child versions can be pinned to an integer or left as `latest`.
- **Advisor** — an independent model the coordinator consults for a second opinion. **It advises only; it cannot run tools.** Nobody in the public field is using this.
- **Skills** — uploaded as `.zip`, **one zip per skill**, max 50 MB, exactly one top-level folder matching the `name` in `SKILL.md` frontmatter. Agents bound to a skill pick up the latest version unless pinned.
- **Forward Mode schedules** — `cron`, `interval` or `once`, via `POST /api/v1/forward/schedules`. Cron is 5 fields, no seconds, minimum 1 minute. **The API requires an explicit IANA timezone for cron.** Default concurrency per schedule is 1 — an overrunning run causes the next trigger to be skipped with `concurrency_limit_reached`.
- **Dashboard** — needs a Qoder **PAT** from `qoder.com/cloud/pat-keys`, used server-side only against `https://api.qoder.com/api/v1/cloud`.

**Plan limitation:** Projects and Discussion are Teams/Enterprise features and **do not exist on individual plans**. Cloud Agents gives individual users a **personal space only**; enterprise spaces require Teams/Enterprise admin. See §9 for how we work around this.

### Moomoo MCP

Endpoint `https://mcp.moomoo.com/mcp`, OAuth 2.1. Scopes: `quote:read`, `trade:read`, `trade:write`.

**The endpoint publishes 107 tools.** The competitor repos use roughly five.

Families in use by everyone: `quote_stock_quote`, `quote_history_kline`, `quote_cur_kline`, `sim_trade_account_list`, `quote_option_screen`.

**Largely untouched and high-signal:**

| Group | Tools |
|---|---|
| Unusual activity | `quote_unusual_capital`, `quote_unusual_derivatives`, `quote_unusual_technical` |
| Positioning | `quote_insider_trade_list`, `quote_short_interest`, `quote_daily_short_volume`, `quote_shareholders_institutional`, `quote_top_ten_brokers` |
| Money flow | `quote_capital_flow`, `quote_capital_flow_history`, `quote_capital_distribution` |
| Earnings reaction | `quote_financials_earnings_price_move`, `quote_financials_earnings_price_history` |
| Options analytics | `quote_option_exercise_probability`, `quote_option_volatility`, `quote_option_strategy_spread`, `quote_combo_option_quote` |
| Research | `quote_research_analyst_consensus`, `quote_research_rating_summary`, `quote_research_morningstar_report` |
| Macro | `quote_economic_calendar_hot`, `quote_economic_calendar_search` |
| Microstructure | `quote_order_book`, `quote_rt_ticker`, `quote_rt_data` |
| **Cost modelling** | **`account_order_fees`, `trading_margin_ratio`** |
| Text (untrusted) | `news_search`, `quote_community_search` |
| Paper trading | `sim_trade_input_order`, `sim_trade_cancel_order`, `sim_trade_modify_order`, `sim_trade_position_list`, `sim_trade_cash_info`, `sim_trade_max_buy_sell`, `sim_trade_history_order_list`, `sim_trade_account_list` |

We are **not** building a signal stack from these — see §7. We use `account_order_fees` for honest net P&L, and the two text tools become the subject of our security work.

### Moomoo Skills Hub

`moomoo.com/skillhub` — live skills include News Search, Sentiment Meter, Stock Digest, Technical Analysis, Capital Analysis, Derivatives Analysis. The official `moomoo-skill` bundles quotes, news, sentiment, unusual activity, simulated trading and a grid strategy.

### Market context

Moomoo launched **API Skills** in April 2026, positioning agentic investing with a "safety first" architecture and paper-trading defaults. The sector moved fast — eToro shipped an agent App Store and MCP server in April 2026, Webull shipped Vega. Industry commentary flagged **explainability and regulatory readiness as the hardest open problems** in agent-driven retail trading. That is precisely the gap our entry addresses.

---

## 6. Judge intelligence

Judges are not named for this event. But Alibaba Cloud × Qoder ran a comparable agentic hackathon in Singapore in 2026 and published their judging post-mortem. The first-round panel drew from **Alibaba Cloud Qoder, Alibaba Cloud Solution Architecture** and partners — likely the same pool.

What they said, paraphrased:

- They **watched demos, read code, and checked evidence** before shortlisting. Claims were not enough.
- **"Honesty was rarer than perfection."** Teams that openly labelled which parts were simulated versus built were often the strongest.
- **Polished demos hurt.** When everything looked finished, judges could not tell what actually worked.
- They rewarded **agents that acted**, not agents that advised.
- They rewarded teams that **wrote security and compliance requirements into their own rules and held the AI to them.**
- Boundary handling — when to pause, warn, or hand a decision back to a human — was "a long way from solved" across the field.
- Their working principle: *AI for consistency, humans for judgment.*

Scale reference from that event: **461 applications → 150 shortlisted → 46 completed submissions.** Attrition is brutal. **Completeness is the main filter for a top-10 slot.**

Corroborating signals from this competition's own materials:

- The handbook: *judges replay runs and ask what failed and why*, and *a rejection log answers that better than any slide.*
- The handbook: an unpinned `latest` skill binding *is a real risk when judges replay your runs.*
- The deck's capabilities slide: **"several agents agreeing is not independent proof that a trade is good."**
- The deck's "cannot do" slide: an alert is not a stop order; an order ID means submission, not execution; a displayed last price is not necessarily executable; simulated matching can differ from live.

---

## 7. Our thesis

> **A simple, fully-specified trading strategy, run by an agent whose every decision is bounded, logged, and reproducible — and which has been tested against adversarial input rather than assumed safe.**

Pitch framing is **trust-led**, not security-led: trading thesis opens, security and replay are the evidence section. Same build either way; this reads on-topic to a trader-heavy panel and an engineer-heavy one alike.

### Why not a market edge

The team's markets knowledge is limited and we are honest about that. A sophisticated strategy we cannot defend under live questioning on 8 Dec is a liability against a 30% "clear trading thesis and edge" criterion. Options and leveraged/inverse ETFs are out for that reason.

### Why security is the real differentiator

`news_search` and `quote_community_search` return **attacker-influenceable text** — news copy and community/KOL posts. Every entrant is piping that into an LLM that influences orders. **That is an untreated indirect prompt-injection surface inside a live trading loop, and nobody in this ecosystem is addressing it.**

It is genuinely emergent, it maps onto both 30% criteria, it is defensible by a team that studies cybersecurity, and it produces a memorable 60-second stage demo: plant a crafted payload, show it reach the agent, show the order path refuse it and log the attempt.

### What we will not claim

- That 4.5 sessions validate a strategy
- That the hash chain proves entries were *truthful* — it proves they were not *edited afterwards*
- That paper fills predict live execution
- That we ran a backtest, because we did not

Every one of those restraints is a scoring asset given §6.

---

## 8. Decisions locked

| Decision | Choice | Rationale |
|---|---|---|
| Asset class | Liquid US ETFs, long only | Defensible without deep markets knowledge; no options/assignment/borrow |
| Strategy | Intraday mean reversion to SMA20, regime-filtered | Deliberately different from `timón`'s ORB; generates ~390 decisions/session |
| Trade posture | Evidence-first, low risk | Cannot win 25%; can lose it. Asymmetric downside is narrative, not numeric |
| Overnight risk | None, ever | Removes gap risk; satisfies the holiday-cutoff warning |
| Pitch framing | Trust-led | Lower risk of reading off-topic |
| Finals attendance | Yes, Singapore | Build for a live demo |
| Claude's role | Full build partner | Specs, prompts, risk config, skills, dashboard, submission docs |

**Risk parameters:** 0.35% risk per trade · 1.5% daily loss halt · 3.5% drawdown flatten · 6 entries and 20 orders per session · max 3 concurrent positions · SPY/QQQ/IWM capped at 1 position between them · flat 15:50 ET daily, 12:45 ET on 27 Nov.

Full detail in `docs/STRATEGY.md` and `config/risk_limits.yaml`.

---

## 9. Team structure — the operator model

Qoder Pro is an individual plan, so there is **no shared workspace to add teammates to**, and Cloud Agents resources live in one person's personal space. The Moomoo competition account and the Vault OAuth credential are bound to one Moomoo ID anyway.

**Designate one operator.** Everything account-bound lives with them:
- Moomoo competition paper account and Moomoo ID
- Cloud Agents: agents, Vault, uploaded skills, schedules
- Slack app tokens
- The final submission

**Everyone else works in their own Qoder Desktop against the shared GitHub repo.** The repo is the collaboration layer. The operator is the only person who pushes config to the cloud, following the handbook's `AGENTS.md` pattern — system prompt, model, tool permissions, MCP connections, skill bindings and sub-agent versions, each with a timestamped comment recording agent ID and the cloud version QCA returned.

**Never share one Qoder login.** It destroys the version-attribution story the entry depends on.

**Upside:** each teammate who registered on Luma has their own Pro month and their own 1,500 credits, and individual-plan credits do not pool. That is roughly 3× the build budget, with the live agent insulated from everyone else's experimentation.

⚠️ Teammates who did **not** register on Luma get no Pro and no credits. Registration is closed, so that is an email to the organisers — send it today.

### Three lanes

| Lane | Owner | Scope |
|---|---|---|
| **A — Operator** | 1 person | Paper account, Cloud Agents, Vault, skill packaging and upload, Forward Mode schedules, Slack bot, running live sessions, final submission |
| **B — Engine & security** | 1 person | Indicators, signal, sizing, risk gate, hash-chained ledger, injection payload corpus, replay harness |
| **C — Evidence** | 1 person | Dashboard on Vercel, deck, written submission, screen recording, eval scenario suite |

With two people, merge B and C and keep A separate — A must stay clean because it holds the credentials.

---

## 10. Day-by-day plan

**Fri 20 (today)**
- Verify Qoder Pro is attached to the Luma email; chase organisers if not
- Create own Slack workspace, create app, get `xapp-`/`xoxb-` tokens
- Moomoo app → Discover → Paper Trade → Competition → join, confirm account name
- Qoder Desktop + moomoo connector + moomoo-skill + Cloud Agents plugin
- **Commit `docs/STRATEGY.md` before any strategy trade** — the timestamp is the argument
- Run `SESSION_00_PROBE.md` steps 1–5 now, 6–8 after 10:30 PM

**Sat 21 — build day (markets closed)**
- Engine: indicators, signal, deterministic sizing, risk gate, hash-chained JSONL ledger
- Account guard fail-closed, unit tests
- `account_order_fees` wired into P&L

**Sun 22 — cloud day**
- Vault with Moomoo MCP OAuth credential
- Package skills (one zip each, ≤50 MB, top-level folder matching `SKILL.md` name), upload
- Coordinator + pinned child agents (researcher / executor / risk auditor) + Advisor
- Forward Mode schedules with explicit `America/New_York` timezone
- Slack bot delivery; dry run end to end with the executor disarmed

**Mon 23** — first automated session, supervised throughout. Expect breakage; log everything.
**Tue 24** — full automated session. Dashboard v1 live on Vercel.
**Wed 25** — full automated session. Injection test suite built and run.
**Thu 26** — market closed. Audit logs, tune prompts, re-run fixed eval prompts and diff, build deck, record the demo.
**Fri 27** — half day. Flatten by 12:45 ET. Dashboard final.
**Sat 28** — window closed. **Freeze and tag the exact agent version.** Write the submission.
**Sun 29** — deck, screen recording, rehearse the 8 Dec pitch.
**Mon 30** — final verification, submit well before 23:59 SGT.

---

## 11. Open questions

1. **Does `sim_trade_input_order` support `STOP` / `STOP_LIMIT`?** Blocking the executor design. `timón` reported the paper API has no resting stop orders. If confirmed, stops are software-only and `max_holding_minutes` becomes a hard risk control.
2. **Are simulated fills charged fees?** Determines whether our P&L is gross or net, and we must state which.
3. **Does `need_order_confirm` fire on paper orders?** If so the executor needs a two-step confirm path.
4. **Is `trade:write` scope granted to the competition account?** `timón` hit permission errors on trading-scope reads.
5. **Published weights may change** — recheck the handbook before submission.

---

## 12. Sources

- Moomoo strategy deck (Alwin Aw): `https://moomoo-strategy-briefing-j692pppjg7l.qoder.website/`
- Participant handbook: `https://moomoo-qoder-challenge-rgkelnyuzbm.qoder.website/`
- Luma event: `https://luma.com/oezyv35j`
- Moomoo MCP docs: `https://open.moomoo.com/mcp-docs/available-tools`
- Moomoo agent hub: `https://github.com/MoomooOpen/moomoo-agent-hub`
- Qoder multi-agent docs: `https://docs.qoder.com/cloud-agents/multi-agents`
- Qoder skills API: `https://docs.qoder.com/cloud-agents/skills`
- Qoder Cloud Agents release notes: `https://docs.qoder.com/release-notes/cloud-agents`
- Judging post-mortem, Alibaba Cloud × Atlas Agentic AI Hackathon: `https://www.webintravel.com/?p=74747`
- Competitor repos: `aljabrialam/timon`, `aloisohzh/tyche`, `specter165/specter-opt`, `timothylee58/stocksense-ai`
