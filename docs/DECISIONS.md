# Architecture decisions

Why the code is shaped the way it is. **Read this before restructuring
anything** — most of these look like arbitrary style choices until you know
what they're defending against, and several were bugs before they were
decisions.

Format: context, the decision, what it costs, and what was rejected.

---

## ADR-001 — The rules decide; the LLM only phrases

**Context.** The obvious build is "send account state and the proposed trade to
an LLM, ask if it looks like a behavioral mistake." It demos well and is one
prompt long.

**Decision.** `GuardrailService.evaluate()` produces `approved: bool` and
per-rule flags with **zero LLM involvement**. `ExplainerService` receives an
already-final `GuardrailResult` and returns only prose. Nothing in
`explainer.py` can alter `approved`.

**Consequences.**
- The decision is deterministic, reproducible, and unit-testable with fake data.
- Latency of a decision is bounded by Alpaca, not by a model.
- The user can be shown the actual reason a trade was flagged, not a paraphrase.
- Cost: the rules are dumber than a model would be. They will miss biases a
  model might catch, and the thresholds are blunt instruments.

**Rejected.**
- *LLM decides, rules advise* — unauditable, non-reproducible, and impossible
  to write a meaningful test for. Also makes the product's core claim
  ("we tell you which bias this is") unfalsifiable.
- *LLM as a tie-breaker on borderline cases* — reintroduces non-determinism at
  exactly the moments that matter most, and creates a threshold nobody can
  explain.

**This is the project's central claim.** If a change makes an LLM call able to
influence `approved`, it is wrong regardless of how well it works.

---

## ADR-002 — The guardrail re-runs on the request that submits the order

**Context.** The natural flow is propose → get verdict → execute. It's tempting
to have execute trust the verdict from propose, since it was just computed.

Originally the dashboard's execute route did exactly that — and in fact skipped
the guardrail entirely, submitting straight to Alpaca with a client-supplied
`override` flag. The guardrail was decorative on the path that actually placed
orders.

**Decision.** Both `POST /trades/execute` and `POST /fragments/trades/execute`
call `GuardrailService.evaluate()` themselves before touching Alpaca.
`was_overridden` is derived from that fresh decision. No verdict, price, or
override flag from the request body is trusted.

**Consequences.**
- Every order that reaches Alpaca was checked on the same request that placed it.
- Cost: an extra evaluation per execution — two or three additional Alpaca
  round trips. Accepted; correctness over latency here.
- The check can't be bypassed by posting directly to the execute endpoint.

**Rejected.**
- *Pass a signed verdict token from propose to execute* — more machinery, and
  it would still be checking a stale account state. Re-evaluating is simpler
  and strictly more correct.
- *Trust the client because the UI is ours* — the endpoint is reachable
  independently of the UI. A check you can skip is not a check.

---

## ADR-003 — Rules do no I/O; context is injected

**Context.** Rules need account state, order history and a price. The direct
route is to hand each rule the Alpaca client.

**Decision.** Rules receive a `RuleContext` dataclass and nothing else.
`GuardrailService` gathers everything — including resolving the symbol's price —
and passes it in. No rule imports `AlpacaClient`.

**Consequences.**
- Rules are pure functions. `tests/test_guardrail_rules.py` needs no mocks, no
  network, and no async.
- Adding a rule that needs new data means adding a field to `RuleContext` and
  resolving it once in `GuardrailService`, not N times across rules.
- Cost: `GuardrailService` fetches data some rules don't use.

**Rejected.**
- *Give rules the client and let them fetch what they need* — makes every rule
  async, network-bound, and mock-heavy to test; and N rules would redundantly
  fetch the same account snapshot.

---

## ADR-004 — No price means the rule stands down

**Context.** `OversizedPositionRule` needs a price to size a trade. For a symbol
the user doesn't hold, there's no price in the account payload. The original
code used a hardcoded `$100.0` placeholder.

**Decision.** Resolve a real price: the held position's `current_price`, else
the latest trade from Alpaca's market data API. If neither is available,
`reference_price` is `None` and the rule returns not-triggered.

**Consequences.**
- The rule is meaningful for symbols the user doesn't already hold, which is
  most first purchases — the placeholder made it inert exactly there.
- Cost: a symbol that can't be priced gets no size protection at all. This is
  surfaced (`reference_price: null`) rather than hidden.

**Rejected.**
- *Keep a placeholder price* — silently produces confident nonsense. A $100
  assumption flags a 10-share buy of a $2,000 stock as fine and a 200-share buy
  of a $3 stock as reckless.
- *Fail the request when a price is unavailable* — a market data outage would
  take down the whole guardrail. Degrading one rule is better than failing all
  of them.

**Principle:** when input is missing, say less. Never fabricate an input to keep
a rule firing.

---

## ADR-005 — Oversizing applies to buys only

**Context.** The rule originally checked any trade whose notional exceeded 15%
of the portfolio, buy or sell.

**Decision.** `OversizedPositionRule` returns immediately for sells.

**Consequences.**
- Selling out of a large position — the behavior we *want* — is no longer
  flagged as a bias. The old version flagged responsible de-risking with reason
  text about exceeding a "single-position guideline", which is meaningless for
  an exit.
- Concentration risk on the sell side is covered by nothing. That's correct:
  a sell reduces concentration by definition.

**Rejected.**
- *Flag large sells under a different rule name (panic sell)* — a legitimate
  future rule, but it needs a market-context signal (is the position down? is
  the market down today?) that we don't currently fetch. Flagging on size alone
  would punish ordinary rebalancing.

---

## ADR-006 — One journal entry per proposal, updated in place

**Context.** Propose wrote a journal row; execute wrote another. One trade
produced two rows, inflating every count and double-counting buys in the
behavior gap. Cancel wrote nothing at all, while the UI told the user it had
been "logged to your journal."

**Decision.** `JournalService.add_entry()` creates one entry at proposal time
and returns it with an `id`. `mark_executed()` and `mark_cancelled()` update
that entry. The entry id is threaded through the UI via `hx-vals`.

**Consequences.**
- The journal records decisions, not HTTP calls.
- A cancel is a first-class recorded outcome — which matters, because a user
  backing off is the product working.
- `JournalEntry.status` is one computed field (`clean` / `flagged` / `executed`
  / `overridden` / `cancelled`) so templates and the API can't disagree.
- Cost: entries are mutable, so the journal isn't strictly append-only.

**Rejected.**
- *Append-only event log with a separate projection* — correct for a real
  system, overkill here, and would need a reducer to answer "what happened to
  this trade."

---

## ADR-007 — An LLM failure falls back to deterministic prose

**Context.** `explainer.explain()` had no error handling. Groq's free tier
rate-limits. A 429 mid-demo would 500 the propose endpoint and throw away a
decision that had already been computed correctly.

**Decision.** Any exception from Groq — and an absent API key — falls back to a
sentence assembled from the triggered rules' own `reason` strings. The Groq
client isn't even constructed without a key.

**Consequences.**
- The app runs with no `GROQ_API_KEY` at all.
- An LLM outage degrades wording, never outcomes. This is ADR-001 taken
  seriously: if the LLM genuinely can't affect the decision, its failure can't
  either.
- Cost: fallback prose is stiffer than generated prose.

**Rejected.**
- *Retry with backoff* — adds latency to an interactive path for a cosmetic
  field. The rule reasons are already human-readable.

---

## ADR-008 — Behavior gap uses FIFO lot matching

**Context.** The gap needs "what your trading actually earned" versus "what
holding would have earned." Computing the actual side requires matching sells
against buys somehow.

**Decision.** Per symbol, buys become a FIFO queue of lots. Sells consume the
oldest lots and realize P&L against those cost bases. Remaining open lots are
valued at the current price for unrealized P&L.

`gap = passive_pl - actual_pl`. Positive means selling cost money.

**Consequences.**
- **If you never sell, the gap is exactly zero** — both sides compute the same
  number. So any non-zero gap is attributable purely to selling decisions,
  which is precisely the claim the product makes. This property is the reason
  FIFO was chosen; it's asserted in `tests/test_behavior_gap.py`.
- A sell with no matching buy in the journal (a position opened before the
  journal existed) contributes nothing rather than a fabricated profit.
- Cost: not tax-lot accurate, and every buy is valued at the *current* price
  rather than a time-weighted baseline.

**Rejected.**
- *Average cost basis* — loses the zero-gap property's crispness and is harder
  to explain in a demo.
- *Compare against a market index instead of the user's own holdings* — that
  measures stock selection, which is explicitly a non-goal (NG-1). The point is
  to isolate *timing*.

---

## ADR-009 — Server-rendered Jinja2 + htmx, not Next.js

**Context.** The original plan was a separate Next.js frontend.

**Decision.** Templates and htmx served by the same FastAPI app.

**Consequences.**
- One process, one language, no build step, no CORS, no API-client layer, no
  second deploy target. For a one-week hackathon this is most of a day saved.
- The dashboard can reuse the Pydantic models directly.
- Cost: no rich client-side interactivity. Fine — the UI is a form, a verdict
  card, and a list.

**Rejected.**
- *Next.js* — better ceiling, worse time-to-demo, and nothing in the product
  needs it.

---

## ADR-010 — Dashboard at `/`, htmx fragments under `/fragments/*`

**Context.** The dashboard was at `/ui`, with fragment routes at
`/ui/trades/propose` etc. — confusingly adjacent to the JSON API's
`/trades/propose`.

**Decision.** Dashboard is `GET /`. Fragment routes are `/fragments/*`. Nothing
is served at `/ui`.

**Consequences.**
- The root URL is the product.
- `/fragments/` reads as internal in `/docs`, and can't be mistaken for the
  JSON API.

---

## ADR-011 — Loading state via fieldset disabling

**Context.** No button gave feedback while its request was open, and nothing
prevented a second click. On an endpoint that places market orders, a
double-click is a duplicate order.

**Decision.** Button groups are `<fieldset>` elements. Buttons carry
`hx-disabled-elt="closest fieldset"` and `hx-sync="closest fieldset:drop"`, plus
a spinner shown via `.htmx-request .btn-spinner`.

**Consequences.**
- `hx-disabled-elt` sets the `disabled` attribute, which does nothing on a
  `div` but disables every control inside a `fieldset` — standard HTML doing
  the work instead of JavaScript.
- On the propose form the fieldset wraps the text input too, so Enter can't
  resubmit either.
- `hx-sync` covers request paths a disabled button doesn't (keyboard, races).
- Cost: fieldsets need their default border/padding/`min-width` reset in CSS.

**Rejected.**
- *Custom JS click handlers* — reimplements what htmx already provides.
- *Disable only the clicked button* — leaves *Cancel* clickable while
  *Proceed anyway* is in flight, which is the actively dangerous case.

---

## ADR-012 — In-memory journal

> **Superseded by [ADR-018](#adr-018--sqlite-journal).** Kept because the
> reasoning explains what changed and why.

**Context.** The journal needs storage. The behavior gap is more compelling the
more history it has.

**Decision.** A list in `JournalService`, process-local, no persistence.

**Consequences.**
- Zero setup, zero migrations, and the demo starts from a clean slate every
  time — which is actually desirable for a scripted demo.
- Cost: the journal resets on restart, including uvicorn's `--reload`. The
  behavior gap can't accumulate across sessions.
- Contained by design: swapping in SQLite touches `journal_service.py` and its
  provider in `core/dependencies.py`, nothing else (ADR-013).

**Rejected.**
- *SQLite now* — real value, but post-demo work.

**What actually happened.** Adding the autonomous agent (ADR-014) turned this
from a scoping choice into a defect: a loop running for days across restarts
*is* the P&L record. Superseded within hours of the agent landing. The
containment held, though — the swap touched exactly the two files this ADR
predicted.

---

## ADR-013 — Dependency injection through one providers module

**Context.** Routes need services. Services need settings and each other.

**Decision.** Every service is constructed by an `@lru_cache`'d provider in
`core/dependencies.py` and reaches routes via `Depends()`. No route constructs
a service. No module outside `services/` imports a third-party SDK.

**Consequences.**
- Swapping the broker, the LLM provider, or the journal's storage touches one
  provider function.
- `@lru_cache` makes each service a process singleton, which is what lets the
  in-memory journal work at all and lets `AlpacaClient` hold one connection
  pool.
- Cost: singletons hold state across requests. Fine for this scope, would need
  revisiting under multi-user.

---

## ADR-014 — Point the guardrail at an autonomous agent, not only at a human

**Context.** The project began as a human-in-the-loop advisor: a person proposes
a trade, the guardrail checks it, the person confirms. Assessed against the
hackathon's judging criteria, that shape had two structural problems.

**P&L Performance** was unscoreable — the agent generated no trades of its own,
so there was no trading activity to evaluate. Worse, the PRD's original NG-1
explicitly excluded trade generation: the product was designed away from a
judged criterion. **Technology Implementation** asks for an *autonomous* trading
agent; requiring a human to propose every trade and click confirm is the
opposite.

The tempting fix was to bolt a stock-picker on the side and keep the guardrail as
a separate feature. That would have made the project two half-products.

**Decision.** Put an autonomous strategy *behind* the existing guardrail. The
agent generates its own signals, and every one goes through the identical
`GuardrailService.evaluate()` a human's trade goes through. The guardrail stops
being human friction and becomes **the agent's own impulse control**.

**Consequences.**
- The agent trades unattended, so there is a real P&L record.
- It is genuinely autonomous — no human in the loop at all.
- The thesis got *stronger* rather than diluted. "Retail traders have behavioral
  biases" is a known result. "Autonomous trading agents inherit the same
  pathologies for structural reasons, and here's one policing itself" is a
  sharper claim, and it's the one the code now demonstrates.
- The guardrail's value became measurable: blocked trades can be priced
  (ADR-020), which is what connects a behavioral thesis to a P&L number.
- Cost: substantially more surface area — strategy, loop, scheduler, persistence,
  agent API, dashboard panels. All of it unverified against live Alpaca at the
  time of writing.
- Cost: the honest framing now has to admit the strategy is not the contribution
  (ADR-016), which is a harder story to tell than "our AI picks better stocks."

**Rejected.**
- *Keep it human-only and accept a zero on P&L* — a quarter of the rubric.
- *Bolt on an unrelated stock picker* — two half-products, and it would have
  left the guardrail still decorative.
- *Have an LLM generate the trades* — would violate ADR-001's spirit, make the
  strategy unexplainable, and put a non-deterministic component on the one path
  that spends money.

---

## ADR-015 — The guardrail is absolute over the agent, advisory over a human

**Context.** The guardrail's whole premise is that it never blocks — it adds
friction and asks. With no human present, there is nobody to ask.

**Decision.** A flagged trade proposed by the agent is **not executed**. The
agent has no override. A flagged trade proposed by a human is still a question
with a one-click *Proceed anyway*.

**Consequences.**
- The asymmetry has a principled basis, not just convenience: a person's
  autonomy is theirs to keep, and taking it is what turns a coach into a nanny.
  An autonomous agent has no autonomy worth protecting — it has a strategy, and
  the guardrail's job is to be the thing the strategy cannot argue with.
- Blocked trades become a clean counterfactual: nothing was executed, so
  "what would it have done?" is answerable (ADR-020).
- `blocked` is a distinct journal outcome from `cancelled`. Both are declined
  trades, but one is a machine restrained and the other is a human reconsidering,
  and conflating them would muddy both metrics.
- Cost: a persistently flagged signal is never acted on. If the overtrading rule
  latches, the agent goes quiet. That's the intended behavior, and it does mean
  the guardrail can suppress P&L — which the impact metric will show honestly.

**Rejected.**
- *Downsize instead of block* — attractive for the oversized rule, and
  meaningless for overtrading or revenge trading. A single policy that works for
  all three is easier to explain and to defend.
- *Defer and retry next cycle* — becomes "block" for a persistent signal and
  "execute a bit later" for a transient one, which is the worst of both.
- *Let the agent override after N consecutive flags* — an override the agent
  grants itself is not a guardrail.

---

## ADR-016 — Dual moving-average momentum, chosen as a foil rather than for alpha

**Context.** The agent needs a strategy. The obvious instinct is to pick
something clever enough to make money and impress on P&L.

**Decision.** Textbook 5/20-day moving-average crossover, long-only, ten liquid
large caps. And say plainly in [STRATEGY.md](STRATEGY.md) that no edge is
claimed.

**Consequences.**
- **Fully explainable.** Every signal reduces to two numbers a judge can verify
  by eye, and each carries its own reasoning string. No fitted model, nothing
  opaque, no LLM judgement.
- **Its natural failure modes are exactly the three the rules detect** —
  conviction scaling produces oversized positions, crossovers whipsaw into
  overtrading, capital rotation looks like revenge trading. So the demo isn't a
  strawman: the agent genuinely wants to misbehave.
- Trades often enough to build a P&L record in days rather than months.
- Needs only closing prices, which the free IEX feed provides.
- Cost: it will whipsaw in range-bound markets and has no stop loss. Over five
  trading days the P&L is noise and we say so.

**Rejected.**
- *Something with a plausible edge* — would invite the question we can't answer
  honestly in five days ("does it work?") instead of the one we can ("does the
  guardrail change what it does, and by how much?").
- *An LLM-driven strategy* — unexplainable and non-deterministic on the path that
  spends money.

---

## ADR-017 — The conviction ceiling deliberately exceeds the guardrail's limit

**Context.** Position sizing scales with signal strength: base 8% of portfolio,
up to 3× on the separation between the moving averages. That tops out at 24%,
against a guardrail ceiling of 15%.

The safe-looking choice is to cap sizing below 15% so the agent never trips its
own rule.

**Decision.** Leave the ceiling at 24%, above the limit.

**Consequences.**
- The guardrail is load-bearing rather than decorative. A rule that can never
  fire proves nothing, and a demo where nothing is ever blocked has no subject.
- It is not contrived. Conviction scaling is a real, widespread, professionally
  respectable technique, and it is precisely the mechanism by which a
  disciplined system talks itself into a position too large to hold through a
  drawdown. The agent isn't sabotaged — it's doing something defensible that
  happens to be a known trap.
- Asserted in
  `tests/test_strategy.py::test_conviction_scaling_can_exceed_the_guardrail_ceiling`,
  so a future tuning change can't quietly make the guardrail inert.
- Cost: the agent's strongest signals are the ones most likely to be blocked,
  which will suppress P&L on exactly its highest-conviction ideas. That tension
  is real and the impact metric reports it either way.

**Rejected.**
- *Cap sizing under the limit* — the agent never trips the rule, the guardrail
  never fires, and the project's central claim goes untested.
- *Lower the guardrail's 15% threshold to force flags* — dishonest; the threshold
  has its own rationale in [BEHAVIORAL_RULES.md](BEHAVIORAL_RULES.md) and
  shouldn't be reverse-engineered to produce a demo.

---

## ADR-018 — SQLite journal

**Supersedes [ADR-012](#adr-012--in-memory-journal).**

**Context.** The autonomous agent runs across days and restarts. An in-memory
journal loses the trade record, the behavior gap and the guardrail impact on any
restart — including an accidental file save under `--reload`.

**Decision.** SQLite via stdlib `sqlite3`. `JournalService` keeps its exact
interface; the storage swap is invisible to every caller. Default `db_path` is
`:memory:` so tests get a clean journal without touching disk, and the provider
passes the real path from settings.

**Consequences.**
- The P&L record survives restarts, which is a precondition for the agent being
  worth running at all.
- No new dependency, no migrations, no server.
- ADR-013's containment claim was tested for real: the change touched
  `journal_service.py` and its provider, and nothing else.
- `run.py` now defaults `--reload` **off**, so an accidental save can't interrupt
  a trading session.
- Cost: two processes (web app and MCP server) write the same file, relying on
  SQLite's locking. Fine at this write volume; noted as K-13.

**Rejected.**
- *Postgres* — a server to run and configure for a single-user demo.
- *JSON file* — no concurrent-write story at all, and a partial write on crash
  corrupts the whole record.

---

## ADR-019 — Expose our own MCP server rather than only consume Alpaca's

**Context.** The judging criteria name Alpaca's MCP server among the expected
technologies. The obvious reading is "call Alpaca through MCP."

**Decision.** Ship an MCP server that exposes **the guardrail** — `evaluate_trade`,
`execute_trade`, `get_behavior_gap`, `get_guardrail_impact`,
`get_strategy_signals`, `run_agent_cycle`.

**Consequences.**
- The interesting thing this project has is not access to Alpaca — a REST client
  is a solved problem. It's the behavioral check in front of the broker.
  Exposing that over MCP means **any** agent, not just ours, can route its trades
  through a behavioral guardrail before they reach a broker. That's the part
  that outlives the hackathon.
- `execute_trade` is guardrail-gated for the same reason the HTTP route is
  (ADR-002): a check an MCP client can skip is not a check.
- Tools run in-process against the same services and the same SQLite journal, so
  a trade placed over MCP appears on the dashboard.
- Cost: it's an addition to, not a use of, Alpaca's own MCP server. If the rules
  require *consuming* theirs specifically, this doesn't satisfy that reading —
  flagged in [STATUS.md](STATUS.md).

**Rejected.**
- *Only wrap Alpaca's MCP server* — would demonstrate integration and nothing
  original, and would sit awkwardly beside our own `AlpacaClient`.

---

## ADR-020 — Counterfactual on blocked buys only

**Context.** To put a number on the guardrail's value, price the trades it
stopped and ask what they would have done. Blocked buys are unambiguous: no
capital was deployed, so `qty × (current_price − blocked_price)` is exactly the
P&L not taken.

Blocked sells are not. Declining to sell means the position stayed on — and that
position's outcome is *already inside* the account's real P&L.

**Decision.** Compute the counterfactual for blocked buys. Count blocked sells,
but attribute no P&L to them, and say so in the UI.

**Consequences.**
- The `savings` figure can't double count. Attributing a blocked sell's upside
  here as well as in the account's unrealized P&L would inflate the guardrail's
  apparent contribution — precisely the kind of flattering arithmetic that would
  discredit the whole metric under scrutiny.
- Sign convention is explicit: `savings = -avoided_pl`. Positive means the
  blocked trades would have lost money. **A negative figure is displayed as
  such** — restraint sometimes costs money, and hiding that would make the number
  marketing rather than measurement.
- Cost: the metric understates the guardrail when blocking an exit was the right
  call.

**Rejected.**
- *Count blocked sells symmetrically* — double counting.
- *Report only when the guardrail looks good* — indefensible.

---

## ADR-021 — Aggregate exposure is a guardrail rule, not just a strategy setting

**Context.** The first live signal run exposed a hole. A real paper account
reported **$100,000 portfolio value against $388,466 buying power** (~3.9×
margin), and the strategy's opening signal set proposed four buys totalling
**$69,614 — 70% of the book in one cycle**, with the per-cycle cap being the only
thing that stopped all four going out at once.

Every one of those trades was checked correctly. `OversizedPositionRule` caps a
*single* trade at 15%; it has nothing to say about the seventh such trade. And
the strategy sized against *buying power*, which at 4× portfolio value is barely
a constraint at all. Ten symbols × up to 24% each is 240% invested, fully
affordable, and individually compliant at every step.

**Decision.** Add `OverexposureRule` — a fourth guardrail rule flagging any buy
that would push total capital at work past 100% of portfolio value. Separately,
teach `MomentumStrategy` the same ceiling so it sizes down into remaining
headroom instead of re-proposing doomed buys.

**Consequences.**
- **The rule is the authority**, which means the limit also covers manual trades
  and MCP-submitted trades — not just the agent's. Putting it only in the
  strategy would have left every other path unprotected, and it's a behavioral
  failure mode, so it belongs with the other behavioral rules.
- The strategy's copy of the ceiling is a courtesy, not the enforcement. Without
  it, a fully-invested agent would propose the same rejected buys every 15
  minutes — burying the journal in blocks and inflating `avoided_cost` by
  counting one intent dozens of times a day, which would quietly corrupt the
  guardrail-impact metric (ADR-020).
- The duplication is real and deliberate. `OverexposureRule.MAX_TOTAL_EXPOSURE_PCT`
  and `strategy_max_total_exposure_pct` must be kept in step; both carry a comment
  saying so. The alternative — having the strategy import the rule — would put a
  dependency from strategy to guardrail that doesn't otherwise exist.
- Sells are exempt everywhere. An over-invested book must always be able to get
  out.
- Cost: `market_value` rather than cost basis means a winning position consumes
  more headroom than it cost, so a book that has run up throttles new entries.
  Correct for solvency, mildly conservative for opportunity.
- Cost: it caps upside. A levered momentum run in a good week would have posted a
  bigger P&L number.

**Rejected.**
- *Strategy-only limit* — leaves manual and MCP paths unprotected, and a limit
  the strategy can simply be reconfigured past isn't a guardrail.
- *Cap sizing so the total can never approach the limit* — same mistake ADR-017
  argues against. The rule should be reachable.
- *Leave it and accept the variance* — considered seriously, since leverage cuts
  both ways and a five-day window is noise either way. Rejected because an agent
  whose headline pitch is behavioral discipline cannot ship with unbounded
  leverage as an accident of not looking. The failure mode would have been
  discovered by a judge, not by us.

---

## ADR-022 — Postgres when `DATABASE_URL` is set, SQLite otherwise

**Context.** The project is being deployed to a serverless host. Serverless
filesystems are ephemeral: a SQLite file written during one invocation is gone on
the next cold start. Since the journal *is* the P&L record — the behavior gap and
the guardrail counterfactual are both derived from it — a wiped file means the
agent trades all week and can prove nothing.

**Decision.** `JournalService` carries two backends behind one unchanged
interface. Postgres is selected whenever `DATABASE_URL` is non-empty; SQLite
otherwise.

**Why key off the URL rather than `APP_ENV`.** Every serverless platform and
managed Postgres provider — Neon, Vercel, Render, Fly, Heroku — injects
`DATABASE_URL` automatically. Keying off its presence means a deploy needs no
extra configuration and a local run needs none at all. Keying off `APP_ENV`
would add a second thing to remember to set, and getting it wrong fails in the
worst direction: a "production" run quietly writing to a disposable file.

**Consequences.**
- ADR-013's containment claim held a second time: the switch touched
  `journal_service.py` and its provider, and nothing else. No caller knows which
  database it is talking to.
- **Connections are lazy.** A cold start pays no database round trip until
  something actually reads or writes, and an unreachable host doesn't break
  wiring or import.
- **Postgres operations retry once on a dropped connection.**
  `OperationalError` and `InterfaceError` trigger a reconnect. Serverless
  containers get frozen and their sockets closed between invocations, so a
  long-lived connection *will* go stale — and a trade decision shouldn't fail
  over it. SQLite deliberately never retries: reconnecting an in-memory database
  would silently discard everything in it.
- Postgres needs an explicit `seq BIGSERIAL` because it has no implicit `rowid`.
  Insertion order matters — the agent logs several trades inside one cycle and
  they can share a timestamp — so ordering can't fall back to `timestamp`.
- Timestamps are adapted per dialect: ISO text on SQLite (its implicit datetime
  adapter is deprecated), native `TIMESTAMPTZ` on Postgres. Reads accept both.
- `guardrail_result` stays `TEXT` rather than `JSONB`. Nothing queries inside it,
  and TEXT keeps one read path. Worth revisiting if the journal ever needs
  queries over rule flags.
- Cost: two SQL dialects to keep in step, mitigated by sharing one column list
  and one row-mapper.
- Cost: `psycopg[binary]` is now a dependency even for local SQLite runs. It's
  imported lazily, so a failed wheel wouldn't stop a local run, but it is in
  `requirements.txt`.

**Rejected.**
- *SQLAlchemy* — would remove the dialect duplication, and costs a large
  dependency plus a rewrite of a working storage layer four days from a deadline.
  Right answer for a longer-lived project.
- *`asyncpg` and an async journal* — `JournalService` is sync and called from
  both async routes and the sync-ish agent loop. Making it async means touching
  every caller for no benefit at this write volume.
- *Postgres everywhere, including locally* — forces every contributor to run a
  database to execute the test suite. SQLite in-memory is why the journal tests
  need no fixtures.
