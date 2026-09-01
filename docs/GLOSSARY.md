# Glossary

Terms as this codebase uses them. Several are ordinary words with a specific
local meaning — worth checking before assuming.

---

## Product & domain

**Autonomous agent** — The background loop in `services/agent.py` that generates
its own signals and trades unattended. Distinct from the *guardrail*, which
polices it, and from a human at the dashboard.

**Guardrail impact** — What the guardrail bought you, in dollars: every trade it
stopped, priced at today's market. `savings` positive means those trades would
have lost money. The metric that connects a behavioral thesis to P&L.

**Blocked** — The agent proposed a trade, the guardrail flagged it, and the agent
stood down. **Not the same as cancelled**: blocked is a machine restrained with no
option to override, cancelled is a human reconsidering. Only blocked trades enter
the guardrail-impact counterfactual.

**Conviction** — The strategy's own measure of signal strength: how far the
5-day moving average sits from the 20-day. Multiplies position size up to 3×.
The mechanism by which a disciplined strategy talks itself into an oversized
position — and therefore the reason the guardrail has anything to catch.

**Signal** — One trade the strategy wants, carrying the moving averages,
conviction and a plain-language reason that produced it.

**Cycle** — One pass of the autonomous loop: check the clock, read the account,
generate signals, guardrail each, trade or block.

**Universe** — The fixed list of symbols the strategy considers, plus anything
currently held (so a position can always be exited even after a config change).

**Behavior gap** — The difference between what would have been earned holding
every buy untouched and what the actual buying and selling earned.
`passive_pl − actual_pl`. Positive means selling cost money. Named after the
DALBAR investor-return shortfall this product exists to make visible. Maths:
[BEHAVIORAL_RULES.md](BEHAVIORAL_RULES.md).

**DALBAR gap** — The industry-level version of the same idea: DALBAR's annual
finding that the average investor's return trails the funds they hold. 2024:
16.54% vs. the S&P 500's 25.02%.

**Friction** — The product's actual mechanism. A flagged trade is slowed by one
confirmation step, not prevented. Distinguished from *blocking*, which this
product deliberately never does.

**Bias** — A named behavioral failure mode (overconfidence, loss aversion,
overtrading). Each rule detects the *signature* of one; none of them diagnose a
person.

**Revenge trade** — Buying soon after closing a position, driven by the urge to
recover a loss. Here detected by timing alone — the code cannot see whether the
exit was actually a loss.

**Overtrading** — Trading pace high enough that decisions are reactions to price
movement rather than executions of a thesis.

**Oversized position** — A single buy large enough relative to the account that
an adverse move forces a panicked exit. 15% of portfolio value here.

**Paper trading** — Alpaca's simulated-money mode. All trading in this project
runs against `paper-api.alpaca.markets`. No real money is ever at risk.

---

## Decision vocabulary

**Proposal** (`TradeProposal`) — A trade the user wants to make: symbol, qty,
side. Not an order. Nothing has been sent to a broker.

**Evaluation** — Running a proposal through every rule. Deterministic, no LLM.
Produces a `GuardrailResult`.

**Flag** (`RuleFlag`) — One rule's verdict on one proposal. **Every rule returns
a flag on every evaluation**, including `triggered: false`. A flag is not
inherently a problem; `triggered` is what matters.

**Triggered** — The rule fired. `approved` is `true` iff *no* flag is triggered.

**Approved** — The guardrail found nothing. Set **only** by `GuardrailService`.
Never by an LLM, never from a request field. This is the load-bearing field of
the whole project.

**Explanation** — Plain-language phrasing of an already-final decision. The only
LLM-produced field. Falls back to deterministic prose built from rule reasons.

**Reason** — A rule's own human-readable sentence fragment explaining why it
triggered. Written to stand alone as prose, because it *is* the fallback
explanation when Groq is unavailable.

**Reference price** — The price the rules sized a trade against: the held
position's `current_price`, else the latest market trade, else `None`. `None`
means size rules stood down rather than guess.

**Override** — Executing a flagged trade anyway. One click, always available.
Recorded in the journal. `was_overridden` is derived server-side from a fresh
decision — never read from the client.

**Cancel** — Backing off a flagged trade. **A recorded outcome, not an absence
of one** — it's the product working, so it reaches the journal.

**Stand down** — A rule returning not-triggered because it lacks the data to
judge honestly, as opposed to having judged the trade acceptable. Both look like
`triggered: false`; `reference_price: null` distinguishes them.

---

## Code objects

**`RuleContext`** — Everything a rule may use: proposal, account snapshot,
recent orders, reference price. Assembled by `GuardrailService`. The mechanism
that keeps rules I/O-free — a rule receives this and nothing else.

**`GuardrailRule`** — The one-method interface every rule implements:
`check(ctx) -> RuleFlag`.

**`ALL_RULES`** — The registry list at the bottom of `guardrail_rules.py`.
Adding a rule means adding a class and an entry here; nothing else changes.

**`GuardrailResult`** — Outcome of an evaluation: `approved`, all `flags`,
`explanation`, `reference_price`.

**`JournalEntry`** — One proposed trade's entire life. Created at proposal,
updated on execute or cancel. **One trade is one entry**, never two.

**`status`** — Computed label on a journal entry: `clean`, `flagged`,
`executed`, `overridden`, `cancelled`. Single source so templates and the API
can't disagree.

**Passive / actual** — The two sides of the behavior gap. *Passive* = every buy
still held. *Actual* = realized + unrealized from what really happened.

**Lot** — A `[qty, price]` buy parcel in the behavior gap's FIFO queue. Sells
consume the oldest lots first.

**Fragment** — An HTML partial returned by a `/fragments/*` route for htmx to
swap in. Not JSON, not a public API.

**Partial** — A template under `templates/partials/` rendered on its own rather
than extending `base.html`.

---

## Infrastructure

**Provider** — An `@lru_cache`'d factory in `core/dependencies.py` that
constructs a service. The only place services are built. `@lru_cache` makes each
a process singleton, which is what lets the in-memory journal persist across
requests.

**`HX-Trigger`** — Response header htmx reads to fire a client-side event. Set
to `journalUpdated` on anything that changes journal state, prompting
`#journal-list` to refresh.

**`.htmx-request`** — Class htmx puts on the element that triggered an in-flight
request (or on the element named by `hx-indicator`). All loading state in this
app is CSS off this class.

**`hx-disabled-elt`** — htmx attribute that sets `disabled` on matched elements
during a request. Points at `closest fieldset` here, because `disabled` on a
`div` does nothing but on a `fieldset` it disables every control inside.

**`hx-sync`** — htmx request-coordination attribute. `:drop` discards a new
request while one is already in flight — double-submit protection that also
covers keyboard paths.

**IEX feed** — The free market-data feed on Alpaca's data API. Requests pass
`feed=iex` to stay inside the free tier.

**Invariant** — A property that must hold for the design to mean anything, as
opposed to a test that happens to pass. Enumerated in
[ARCHITECTURE.md](ARCHITECTURE.md).

**ADR** — Architecture Decision Record. One entry in
[DECISIONS.md](DECISIONS.md): context, decision, consequences, rejected
alternatives.
