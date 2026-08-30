# Alpaca Guardrail Agent

## What this is

A behavioral guardrail trading agent built for the **Alpaca AI Trading Agents
Hackathon** on lablab.ai (Aug 28 – Sep 4, 2026, $6,000 prize pool).

**The real-world problem:** retail traders don't underperform the market
because of bad analysis — they underperform because of behavior. DALBAR's
research shows the average equity investor earned 16.54% in 2024 while the
S&P 500 returned 25.02%, an ~850 basis-point shortfall driven by panic
selling, revenge trading, overtrading, and oversized positions taken on
emotion rather than a plan.

**What we're building:** not another "AI picks better stocks" bot. This
agent sits between a trader's impulse and Alpaca's order execution. When a
user proposes a trade, it:

1. Pulls their live account state via Alpaca's API (positions, recent
   orders, P&L)
2. Runs the proposed trade through a set of behavioral rules (oversized
   position, revenge trading, overtrading)
3. If clean, explains briefly and lets it through
4. If flagged, explains *which bias it looks like* in plain language and
   asks the user to confirm or cancel — it never silently blocks a trade,
   only adds friction
5. Logs every decision (approved, flagged, overridden, cancelled) to a
   trading journal that shows the user their own behavior gap: what they'd
   have earned holding everything untouched vs. what their actual in-and-out
   trading earned

All trading happens in **Alpaca paper trading mode** — no real money.

## Documentation map

This file is the always-loaded summary and carries the hard invariants. Depth
lives in `docs/`, read on demand — start with `docs/README.md`, which lists a
reading order and says which file owns which fact.

| Read when you need… | File |
|---|---|
| Product requirements, acceptance criteria | [docs/PRD.md](docs/PRD.md) |
| **Why the code is shaped this way** — read before restructuring | [docs/DECISIONS.md](docs/DECISIONS.md) |
| Layering, request flow, the full invariant list | [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) |
| What the rules detect, thresholds, behavior gap maths | [docs/BEHAVIORAL_RULES.md](docs/BEHAVIORAL_RULES.md) |
| How to add a rule, route, service or button | [docs/CONVENTIONS.md](docs/CONVENTIONS.md) |
| Endpoint shapes and error behavior | [docs/API.md](docs/API.md) |
| **What actually works right now** | [docs/STATUS.md](docs/STATUS.md) |
| What a term means here | [docs/GLOSSARY.md](docs/GLOSSARY.md) |

Thresholds and behavior live in code. If a doc disagrees with the code, the
code is right and the doc is a bug — fix it in the same change.

## Tech stack

- **Backend:** FastAPI (Python 3.10+), running on Windows via venv
- **Trading:** Alpaca Trading API (paper mode) — `https://paper-api.alpaca.markets`
- **Market data:** Alpaca Data API (`https://data.alpaca.markets`), IEX feed
  (free tier) for latest trade prices
- **LLM:** Groq free tier (`llama-3.3-70b-versatile`) for plain-language
  explanations only — the LLM never decides approve/deny, it only phrases
  a decision the rules engine already made
- **Frontend:** server-rendered Jinja2 templates + htmx, served by the same
  FastAPI app at `/` (htmx fragments under `/fragments/*`). No separate
  build step, no Node.

## Architecture (SOLID-driven layering)

```
backend/
├── run.py                       # Convenience launcher (python run.py)
├── conftest.py                  # Puts backend/ on sys.path for pytest
├── app/
│   ├── main.py                  # FastAPI entrypoint — wiring only, no logic
│   ├── core/
│   │   ├── config.py            # Settings loaded from .env via pydantic-settings
│   │   └── dependencies.py      # DI providers for FastAPI's Depends()
│   ├── schemas/
│   │   ├── account.py           # AccountSnapshot, Position
│   │   └── trade.py             # TradeProposal, GuardrailResult, RuleFlag,
│   │                            # ExecutedOrder, JournalEntry, BehaviorGap,
│   │                            # OrderSide
│   ├── services/
│   │   ├── alpaca_client.py     # ONLY file that talks HTTP to Alpaca
│   │   ├── guardrail_rules.py   # Pure behavioral rules, zero I/O
│   │   ├── guardrail_service.py # Orchestrates: pulls context, runs rules
│   │   ├── explainer.py         # ONLY file that talks to Groq
│   │   ├── trade_parser.py      # "buy 50 shares of NVDA" -> TradeProposal
│   │   ├── behavior_gap.py      # Held-everything vs. actually-traded maths
│   │   └── journal_service.py   # In-memory trade history + summary stats
│   ├── api/
│   │   ├── trades.py            # POST /trades/propose, POST /trades/execute
│   │   ├── journal.py           # GET /journal/entries, /summary, /behavior-gap
│   │   └── ui.py                # Dashboard at /, htmx fragments at /fragments/*
│   ├── templates/               # Jinja2: dashboard + htmx partials
│   └── static/css/theme.css     # Alpaca-matched dark theme
├── tests/                       # pytest — rules, parser, journal, behavior gap
└── docs/                        # PRD, ADRs, architecture, conventions, status
```

### Why it's split this way

- **Single Responsibility:** `alpaca_client.py` only knows Alpaca's HTTP
  shape. `guardrail_rules.py` only knows behavioral logic — no network
  calls. `explainer.py` only knows how to phrase things via Groq. Change
  one concern, touch one file.
- **Open/Closed:** adding a new rule means writing one new class
  implementing `GuardrailRule` and adding it to `ALL_RULES` in
  `guardrail_rules.py`. Nothing else changes.
- **Liskov/Interface Segregation:** every rule implements the same tiny
  interface (`check(ctx) -> RuleFlag`), so callers never need to know
  which rule they're calling.
- **Dependency Inversion:** routes never construct services directly —
  everything comes through `Depends(get_x_service)` in
  `core/dependencies.py`. Swapping Alpaca, Groq, or the journal's storage
  backend later touches one file, not every caller.

### The critical design decisions

**1. The rules decide; the LLM only phrases.**
`GuardrailService.evaluate()` returns a decision (`approved: bool`,
`flags: list[RuleFlag]`) with **zero LLM involvement** — deterministic,
testable, fast. `ExplainerService.explain()` takes that *already-decided*
result and only adds language. The LLM cannot influence whether a trade
is approved or flagged. This split is the whole point of the project:
uncertainty-aware, non-overconfident tooling, not another black-box bot
dressed up in a chat UI.

A corollary: because the decision is already final, an LLM failure must
never fail the request. `explainer.py` falls back to a deterministic
sentence built from the rule reasons if Groq errors, rate-limits, or is
unconfigured.

**2. The guardrail runs on the path that submits the order.**
Both `/trades/execute` and `/fragments/trades/execute` re-run
`GuardrailService.evaluate()` before calling Alpaca, rather than trusting
a verdict passed back from the propose step. Both endpoints are reachable
directly and their inputs are client-supplied, so checking at propose time
only would leave the guardrail advisory. `was_overridden` is likewise
derived from the fresh decision, never read from a request field.

**3. Rules never do I/O.**
Everything a rule needs — account snapshot, order history, the symbol's
market price — is resolved by `GuardrailService` and passed in on
`RuleContext`. That's what keeps rules unit-testable with fake data.

**4. One proposed trade is one journal entry.**
`JournalService` creates an entry at propose time and *updates* it on
execute or cancel (`mark_executed` / `mark_cancelled`). It doesn't append a
second row, so the journal reflects decisions rather than HTTP calls, and
the behavior gap can't double-count a trade.

## Current state

Feature-complete against the PRD. **The pure-logic layer is verified — 39 tests
pass** (rules, behavior gap, journal, parser).

Still unverified: anything crossing a process boundary. No live Alpaca round
trip has been confirmed, and the tests never import `app.main`, so app startup
and route registration aren't covered either.

First actions in a new session: `python run.py` to confirm it boots and `/`
renders, then check market data returns a real price — that one fails silently
(`reference_price: null` and an oversized rule that never fires).

Progress, unverified areas, known issues and roadmap all live in
**[docs/STATUS.md](docs/STATUS.md)**. Update that file, not this one, when
status changes.

## .env keys required

```
ALPACA_API_KEY=
ALPACA_SECRET_KEY=
ALPACA_BASE_URL=https://paper-api.alpaca.markets
ALPACA_DATA_URL=https://data.alpaca.markets
GROQ_API_KEY=
GROQ_MODEL=llama-3.3-70b-versatile
```

## Running it

```bash
python run.py                      # or: uvicorn app.main:app --reload
python -m pytest tests -q          # tests
```

Then open `http://127.0.0.1:8000` for the dashboard, `/docs` for the API.

## Conventions to follow when extending this project

Summarised here because this file is always loaded. Worked examples, the htmx
button pattern, testing and style conventions: **[docs/CONVENTIONS.md](docs/CONVENTIONS.md)**.

- New behavioral rule → new class in `guardrail_rules.py` implementing
  `GuardrailRule.check(ctx) -> RuleFlag`, add to `ALL_RULES` list. Rules
  take only `RuleContext` — never import `AlpacaClient` or make network
  calls inside a rule. If a rule needs new outside data, add it to
  `RuleContext` and resolve it in `GuardrailService`. Rules stay
  independently unit-testable with fake data.
- New route → belongs in `api/`, should read like a thin orchestration
  layer only. If a route file starts containing business logic (rule
  checks, prompt construction, parsing, storage logic), that logic belongs
  in `services/` instead.
- Any new external service (a second LLM provider, a database, a
  different broker) → gets its own file in `services/`, and a provider
  function in `core/dependencies.py`. Nothing outside that one service
  file should import the third-party SDK directly.
- Keep the guardrail decision and its explanation separate. Never let
  `explainer.py` (or any LLM call) determine `approved` — that value
  must always come from `guardrail_service.py` alone.
- Never let an order reach Alpaca without the guardrail running on that
  same request. Don't trust a verdict, a price, or an override flag that
  arrived in the request body.
- Paths to `templates/` and `static/` are resolved from `__file__`, not the
  working directory. Keep it that way so the app starts from anywhere.
- Every action button needs a pending state: wrap the group in a `<fieldset>`
  and use `hx-disabled-elt="closest fieldset"` plus
  `hx-sync="closest fieldset:drop"`. `hx-disabled-elt` does nothing on a `div`.

## Next steps

See the roadmap in **[docs/STATUS.md](docs/STATUS.md)**.
