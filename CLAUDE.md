# Alpaca Guardrail Agent

## What this is

An **autonomous trading agent that polices its own behavioral biases**, built for
the Alpaca AI Trading Agents Hackathon on lablab.ai (Aug 28 – Sep 4, 2026).

**The real-world problem:** retail traders don't underperform because of bad
analysis — they underperform because of behavior. DALBAR: the average equity
investor earned 16.54% in 2024 while the S&P 500 returned 25.02%, an ~850 bp
shortfall from panic selling, revenge trading, overtrading and emotional sizing.

**The turn that makes this a project rather than a lecture:** autonomous trading
agents inherit the same pathologies, for structural rather than emotional
reasons. Conviction-scaled sizing produces oversized positions exactly when a
trend is most extended. Crossover signals whipsaw into overtrading. Rotating
capital out of a decaying name into the next strongest one is structurally
identical to revenge trading.

So: an agent that trades unattended, and a guardrail pointed at the agent.

Every 15 minutes it reads daily bars for ten large caps, computes 5/20-day
moving averages, sizes positions on conviction, and runs every proposal through
the same guardrail a human would face (oversized position, overexposure, revenge
trading, overtrading). It executes what passes and **blocks itself** on what
doesn't. Both outcomes are journalled, and the blocked ones are priced against
the market later: *what would that trade have done?*

A human can also propose trades at the dashboard. Same guardrail, one
difference: **a person always gets an override; the agent never does.**

All trading is **Alpaca paper mode** — no real money.

## Documentation map

This file is the always-loaded summary and carries the hard invariants. Depth
lives in `docs/`, read on demand — start with `docs/README.md`.

| Read when you need… | File |
|---|---|
| Product requirements, acceptance criteria | [docs/PRD.md](docs/PRD.md) |
| **Why the code is shaped this way** — read before restructuring | [docs/DECISIONS.md](docs/DECISIONS.md) |
| What it trades and why, sizing, weaknesses | [docs/STRATEGY.md](docs/STRATEGY.md) |
| Layering, request flow, the full invariant list | [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) |
| Rule thresholds, behavior gap maths | [docs/BEHAVIORAL_RULES.md](docs/BEHAVIORAL_RULES.md) |
| How to add a rule, route, service or button | [docs/CONVENTIONS.md](docs/CONVENTIONS.md) |
| Endpoint shapes and error behavior | [docs/API.md](docs/API.md) |
| Demo script and talk track | [docs/DEMO.md](docs/DEMO.md) |
| **What actually works right now** | [docs/STATUS.md](docs/STATUS.md) |
| What a term means here | [docs/GLOSSARY.md](docs/GLOSSARY.md) |

Thresholds and behavior live in code. If a doc disagrees with the code, the code
is right and the doc is a bug — fix it in the same change.

## Tech stack

- **Backend:** FastAPI (Python 3.10+), Windows via venv
- **Trading:** Alpaca Trading API, paper — `https://paper-api.alpaca.markets`
- **Market data:** Alpaca Data API, IEX feed — latest trades and daily bars
- **Persistence:** SQLite via stdlib `sqlite3` — the agent runs for days
- **MCP:** own MCP server exposing the guardrail (`mcp_server.py`)
- **CLI:** `cli.py`, talks to the running server over HTTP
- **LLM:** Groq (`llama-3.3-70b-versatile`) for phrasing only — never decides
- **Frontend:** server-rendered Jinja2 + htmx at `/`, fragments at `/fragments/*`

## Architecture (SOLID-driven layering)

```
backend/
├── run.py                       # Launcher: server + autonomous agent
├── cli.py                       # Command-line interface
├── mcp_server.py                # MCP server exposing the guardrail
├── conftest.py                  # Puts backend/ on sys.path for pytest
├── app/
│   ├── main.py                  # FastAPI entrypoint — wiring + agent lifespan
│   ├── core/
│   │   ├── config.py            # Settings from .env via pydantic-settings
│   │   └── dependencies.py      # DI providers for Depends()
│   ├── schemas/
│   │   ├── account.py           # AccountSnapshot, Position
│   │   ├── trade.py             # TradeProposal, GuardrailResult, RuleFlag,
│   │   │                        # JournalEntry, BehaviorGap, GuardrailImpact
│   │   └── strategy.py          # StrategySignal, AgentStatus, AgentCycleResult
│   ├── services/
│   │   ├── alpaca_client.py     # ONLY file that talks HTTP to Alpaca
│   │   ├── strategy.py          # Momentum signals — pure logic + fetcher
│   │   ├── agent.py             # The autonomous loop
│   │   ├── guardrail_rules.py   # Pure behavioral rules, zero I/O
│   │   ├── guardrail_service.py # Gathers context, runs rules, decides
│   │   ├── explainer.py         # ONLY file that talks to Groq
│   │   ├── behavior_gap.py      # Behavior gap + blocked-trade counterfactual
│   │   ├── trade_parser.py      # "buy 50 shares of NVDA" -> TradeProposal
│   │   └── journal_service.py   # SQLite trade record
│   ├── api/
│   │   ├── trades.py            # POST /trades/propose, /trades/execute
│   │   ├── journal.py           # /journal/entries, /summary, /behavior-gap,
│   │   │                        # /guardrail-impact
│   │   ├── agent.py             # /agent/status, /run-once, /signals, ...
│   │   └── ui.py                # Dashboard at /, fragments at /fragments/*
│   ├── templates/               # Jinja2: dashboard + htmx partials
│   └── static/css/theme.css     # Alpaca-matched dark theme
├── tests/                       # pytest — rules, strategy, gap, impact, journal
└── docs/                        # PRD, ADRs, strategy, demo, status
```

### The critical design decisions

**1. The rules decide; the LLM only phrases.**
`GuardrailService.evaluate()` returns `approved: bool` with **zero LLM
involvement** — deterministic, testable, fast. `ExplainerService.explain()` takes
that already-decided result and only adds language. It cannot change `approved`.
Corollary: because the decision is already final, an LLM failure must not fail
the request — `explainer.py` falls back to deterministic prose built from the
rule reasons.

**2. The guardrail runs on the path that submits the order.**
Every execution path — `/trades/execute`, `/fragments/trades/execute`, the MCP
`execute_trade` tool, and the agent loop — re-runs `evaluate()` before calling
Alpaca. `was_overridden` is derived from that fresh decision, never read from a
request field.

**3. Absolute over the agent, advisory over a human.**
A flagged trade from the agent is **not executed** — it has no override. A
flagged trade from a person is a question with a one-click *Proceed anyway*. A
person's autonomy is theirs to keep; an agent has only a strategy. `blocked` and
`cancelled` are distinct journal outcomes for this reason.

**4. Neither rules nor the strategy do I/O.**
Account snapshot, order history, prices, bars — all resolved by a service and
passed in (`RuleContext`, and the closes/prices dicts). That's what keeps both
unit-testable with fixed data.

**5. One proposed trade is one journal entry.**
Created at proposal, then updated via `mark_executed` / `mark_cancelled` /
`mark_blocked`. The journal records decisions, not HTTP calls.

**6. The strategy's conviction ceiling exceeds the guardrail's limit.**
Sizing tops out at 24% of portfolio against a 15% rule. Deliberate: a rule that
can never fire proves nothing. Asserted in `tests/test_strategy.py`.

## Current state

Autonomous agent, SQLite journal, guardrail counterfactual, MCP server and CLI
are all written. **Pure logic is verified — 66 tests pass.**

**Nothing has been run against live Alpaca since the agent was added**, and the
`mcp` dependency is in `requirements.txt` but not yet installed.

First actions in a new session:

```bash
pip install -r requirements.txt
python -c "from app.main import app; print(len(app.routes))"
python run.py
python cli.py signals      # gates everything — a dataless agent looks healthy
```

Progress, unverified areas, known issues and roadmap live in
**[docs/STATUS.md](docs/STATUS.md)**. Update that file, not this one.

## .env keys required

See `.env.example` for the full annotated set. Minimum:

```
ALPACA_API_KEY=
ALPACA_SECRET_KEY=
ALPACA_BASE_URL=https://paper-api.alpaca.markets
ALPACA_DATA_URL=https://data.alpaca.markets
GROQ_API_KEY=                     # optional — falls back to deterministic text
AGENT_ENABLED=true
```

## Running it

```bash
python run.py                      # server + agent; --reload for development
python -m pytest tests -q          # tests
python cli.py status               # CLI
python mcp_server.py               # MCP server
```

Dashboard at `http://127.0.0.1:8000`, API docs at `/docs`.

## Conventions to follow when extending this project

Summarised here because this file is always loaded. Worked examples, the htmx
button pattern, testing and style conventions:
**[docs/CONVENTIONS.md](docs/CONVENTIONS.md)**.

- New behavioral rule → new class in `guardrail_rules.py` implementing
  `GuardrailRule.check(ctx) -> RuleFlag`, added to `ALL_RULES`. Rules take only
  `RuleContext` — never import `AlpacaClient` or make a network call inside a
  rule. If a rule needs new outside data, add it to `RuleContext` and resolve it
  in `GuardrailService`.
- New route → belongs in `api/`, thin orchestration only. Rule checks, prompt
  construction, parsing, signal generation and storage logic belong in
  `services/`.
- Any new external service → its own file in `services/`, plus a provider in
  `core/dependencies.py`. Nothing outside that file imports the third-party SDK.
- Never let an LLM call determine `approved`. That value comes from
  `guardrail_service.py` alone.
- Never let an order reach Alpaca without the guardrail running on that same
  request. Don't trust a verdict, price, or override flag from a request body.
- Never substitute a placeholder for missing data. No price → the rule stands
  down. No bars → no signal.
- Paths to `templates/` and `static/` resolve from `__file__`, not the working
  directory.
- Every action button needs a pending state: wrap the group in a `<fieldset>`,
  use `hx-disabled-elt="closest fieldset"` and
  `hx-sync="closest fieldset:drop"`. `hx-disabled-elt` does nothing on a `div`.

## Next steps

See the roadmap in **[docs/STATUS.md](docs/STATUS.md)**.
