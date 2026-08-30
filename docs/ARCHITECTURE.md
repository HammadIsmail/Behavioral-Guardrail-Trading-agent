# Architecture

How the pieces fit, what happens on each request, and what must stay true.

Rationale for these choices lives in [DECISIONS.md](DECISIONS.md).

---

## Layers

Dependencies point downward only. Nothing in a lower layer imports from a
higher one.

```
        api/            trades.py · journal.py · ui.py
         │              thin orchestration, no business logic
         ▼
     services/          guardrail_service · guardrail_rules · explainer
         │              alpaca_client · behavior_gap · journal_service
         │              trade_parser
         ▼
     schemas/           Pydantic models — the shared vocabulary
         ▲
       core/            config (settings) · dependencies (DI providers)
```

`core/dependencies.py` is the only module that knows how to *construct* a
service. `api/` receives them already built via `Depends()`.

## File responsibilities

| File | Owns | Must not |
|---|---|---|
| `services/alpaca_client.py` | Every HTTP call to Alpaca (trading + market data) | Interpret behavior |
| `services/guardrail_rules.py` | Behavioral logic | Do any I/O, import `AlpacaClient` |
| `services/guardrail_service.py` | Gathering context, running rules, deciding | Call an LLM |
| `services/explainer.py` | Every call to Groq | Influence `approved` |
| `services/trade_parser.py` | Natural language → `TradeProposal` | Know about Alpaca |
| `services/behavior_gap.py` | Passive-vs-actual maths | Fetch prices (the service class does; the function doesn't) |
| `services/journal_service.py` | Trade history storage and counts | Compute the gap |
| `api/*.py` | Wiring request → service → response | Contain rule checks, prompts, parsing, or storage logic |
| `core/config.py` | Reading `.env` | — |
| `core/dependencies.py` | Constructing services | Contain logic |
| `main.py` | Router registration, static mount, lifespan | Anything else |

If exactly one file in the project imports a given third-party SDK, that
boundary is intact. Today: `httpx` → `alpaca_client.py`, `groq` →
`explainer.py`.

## Request flow: proposing a trade

`POST /trades/propose` (JSON) and `POST /fragments/trades/propose` (htmx) follow
the same path.

```
1. api/ui.py or api/trades.py receives the request
       │
2. trade_parser.parse_trade_message()          ← htmx path only
       │   ValueError → parse_error.html, no execute path offered
       ▼
3. GuardrailService.evaluate(proposal)
       ├── alpaca.get_account_snapshot()       → AccountSnapshot + positions
       ├── alpaca.get_recent_orders(limit=20)  → raw order dicts
       ├── _resolve_price(symbol, account)
       │       held position's current_price, else
       │       alpaca.get_latest_prices([symbol])  (IEX feed)
       │       else None
       ├── build RuleContext
       ├── [rule.check(ctx) for rule in ALL_RULES]
       └── approved = no flag triggered        ← DECISION IS FINAL HERE
       ▼
4. ExplainerService.explain(result)
       Groq call, or deterministic fallback on any failure
       Writes only result.explanation
       ▼
5. JournalService.add_entry(...)  → entry.id
       ▼
6. Response: verdict + explanation + entry id
```

Step 3 is the whole product. Step 4 cannot reach back into step 3.

## Request flow: executing a trade

```
1. api receives symbol, qty, side, override, journal_entry_id
       │   all client-supplied — none of it trusted as a decision
       ▼
2. GuardrailService.evaluate(proposal)         ← RE-RUN, not reused
       ▼
3. if not approved and not override:
       └── render the flagged card again, do not touch Alpaca
       ▼
4. alpaca.submit_order(proposal)               → market order, paper
       │   4xx → surface Alpaca's own message
       ▼
5. was_overridden = (not approved) and override    ← derived, never posted
       ▼
6. JournalService.mark_executed(entry_id, price, was_overridden)
       │   falls back to add_entry() if the id is unknown
       ▼
7. Response + HX-Trigger: journalUpdated       → journal panel refreshes
```

## Invariants

These are load-bearing. A change that breaks one is a bug even if tests pass.

| # | Invariant | Enforced in |
|---|---|---|
| I-1 | `approved` is set only by `GuardrailService` | `guardrail_service.py` |
| I-2 | No LLM call can alter `approved` | `explainer.py` writes only `explanation` |
| I-3 | An LLM failure never fails a request | `explainer.py` try/except → `_fallback()` |
| I-4 | Rules make no network calls | `guardrail_rules.py` imports only schemas + stdlib |
| I-5 | The guardrail runs on the request that submits the order | both execute routes |
| I-6 | `was_overridden` is derived from a fresh decision | both execute routes |
| I-7 | A missing price disables size rules; it never becomes a fake number | `_resolve_price()` returns `None`; rule stands down |
| I-8 | One proposed trade is one journal entry | `mark_executed` / `mark_cancelled` |
| I-9 | Every outcome reaches the journal, including cancels | `fragment_cancel_trade` |
| I-10 | Only `alpaca_client.py` imports `httpx`; only `explainer.py` imports `groq` | grep |
| I-11 | Template and static paths resolve from `__file__`, not cwd | `main.py`, `api/ui.py` |
| I-12 | No route constructs a service | `core/dependencies.py` |

## State and lifecycle

**Settings** — `get_settings()` is `@lru_cache`'d, so `.env` is parsed once.

**Services** — every provider in `core/dependencies.py` is `@lru_cache`'d, so
each service is a process singleton. Consequences:

- `JournalService` holds the journal in a list. It survives across requests and
  dies with the process (including on `--reload`).
- `AlpacaClient` holds one `httpx.AsyncClient` and its connection pool, closed
  by the FastAPI `lifespan` in `main.py`.

**No database.** See ADR-012.

## Data model

`schemas/trade.py`:

- `TradeProposal` — symbol, qty (> 0), side
- `RuleFlag` — one rule's verdict: name, triggered, reason
- `GuardrailResult` — approved, flags, explanation, reference_price
- `ExecutedOrder` — what Alpaca returned
- `JournalEntry` — one trade's whole life; `status` is a computed field
- `BehaviorGap` — passive vs. actual, plus the gap

`schemas/account.py`: `AccountSnapshot`, `Position`.

`RuleContext` (in `guardrail_rules.py`, a dataclass not a schema) — proposal,
account, recent_orders, reference_price. It's internal to the rules layer and
deliberately not part of any API surface.

## Extension points

| To add… | Do this |
|---|---|
| A behavioral rule | New class in `guardrail_rules.py`, add to `ALL_RULES` |
| Data a rule needs | New field on `RuleContext`, resolved in `GuardrailService` |
| An endpoint | New route in `api/`, logic in `services/` |
| An external service | New file in `services/`, provider in `core/dependencies.py` |
| Persistence | Rewrite `JournalService` internals; its provider is the only other touch point |

Step-by-step with code: [CONVENTIONS.md](CONVENTIONS.md).

## Frontend

Server-rendered Jinja2 with htmx. No build step.

```
templates/
├── base.html                       shell, htmx script, PAPER badge
├── dashboard.html                  the page at GET /
└── partials/
    ├── trade_result.html           verdict card + action buttons
    ├── execution_result.html       order submitted / cancelled
    ├── parse_error.html            couldn't read the message
    └── journal_list.html           behavior gap panel + entry list
```

Fragment routes return these partials directly. `HX-Trigger: journalUpdated` on
a response causes `#journal-list` to re-fetch `/fragments/journal`.

Loading state is CSS-driven off htmx's `.htmx-request` class, with button groups
as `<fieldset>` so disabling one disables every control inside. See ADR-011 and
the htmx section of [CONVENTIONS.md](CONVENTIONS.md).

## Testing

`tests/` — pure-logic tests, no network, no mocks:

| File | Covers |
|---|---|
| `test_guardrail_rules.py` | Each rule's trigger conditions and edge cases |
| `test_behavior_gap.py` | FIFO matching, the zero-gap property, unpriced symbols |
| `test_journal_service.py` | Lifecycle, status labels, the `None` guardrail_result case |
| `test_trade_parser.py` | Phrasings that must parse and inputs that must fail |

`conftest.py` at the repo root puts `backend/` on `sys.path`.

Nothing mocks Alpaca or Groq — the layering makes that unnecessary for the
logic worth testing. End-to-end coverage against live paper Alpaca is manual;
see the acceptance criteria in [PRD.md](PRD.md).
