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
- *SQLite now* — real value, but it's post-demo work. Recorded in
  [STATUS.md](STATUS.md) as roadmap.

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
