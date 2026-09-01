# Alpaca Guardrail Agent

An autonomous trading agent that polices its own behavioral biases — and puts a
dollar figure on the trades it talked itself out of.

Built for the [Alpaca AI Trading Agents Hackathon](https://lablab.ai)
(Aug 28 – Sep 4, 2026). **Paper trading only — no real money.**

---

## The problem

Retail traders don't underperform the market because their analysis is bad. They
underperform because of their own behavior.

DALBAR's research puts a number on it: in 2024 the average equity investor
earned **16.54%** while the S&P 500 returned **25.02%** — an ~850 basis point
shortfall. That gap isn't stock picking. It's panic selling, revenge trading,
overtrading, and oversized positions taken on emotion instead of a plan.

Almost every AI trading tool tries to close that gap by picking better stocks.

So we asked a different question:

> **We're all building autonomous agents to trade now. Do they inherit the same
> behavioral pathologies as the retail traders they're replacing?**

They do — and not because they get emotional. Because of how they're built.
Conviction-scaled position sizing produces oversized positions at exactly the
moment a trend is most extended. Crossover signals whipsaw into overtrading.
Rotating capital out of a decaying position into the next strongest name is
structurally identical to revenge trading.

So we built an agent that trades on its own, and a guardrail that stops it when
it's about to act on a bias.

## What it does

```
                 ┌──────────────┐
   market data → │   strategy   │ → proposed trades
                 └──────────────┘         │
                                          ▼
                 ┌──────────────────────────────────┐
                 │   behavioral guardrail            │
                 │   oversized · revenge · overtrade │
                 └──────────────────────────────────┘
                     │                        │
                  approved                 flagged
                     │                        │
                     ▼                        ▼
                 Alpaca order          blocked + priced later:
                                       "what would it have done?"
```

Every 15 minutes, unattended, the agent:

1. Reads daily bars for ten liquid large caps
2. Computes 5-day and 20-day moving averages, and decides what it wants to trade
3. Sizes each position at 8% of the portfolio, scaled up to 3× on conviction
4. Runs every proposal through the **same guardrail a human would face**
5. Executes what passes — and **blocks itself** on what doesn't
6. Records both outcomes, then prices the blocked ones against the market

A human can also propose trades at the dashboard. Same guardrail, one difference:
**a person always gets an override. The agent never does.** Autonomy is a
person's to keep; an agent has only a strategy.

## The two numbers

**Guardrail impact** — every trade the guardrail stopped, priced at today's
market. What would it have done?

> *Those blocked trades would have lost $412. The guardrail earned its keep.*

Restraint as a measurement, not a claim. And when restraint *costs* money, it
says that too.

**Behavior gap** — the agent's own DALBAR gap. What holding every buy untouched
would have earned versus what the actual in-and-out trading earned.

There's a property worth knowing: **if nothing is ever sold, the gap is exactly
zero** — both sides compute the same number. So any non-zero gap is caused purely
by selling decisions. It isolates *timing* from *selection*.

## The design decision that matters

**The rules decide. The LLM only phrases.**

`GuardrailService.evaluate()` returns `approved: bool` with **zero LLM
involvement** — deterministic, fast, unit-testable. `ExplainerService` receives
an already-final decision and does nothing but put it in friendly words. The
model cannot approve or flag anything.

Three consequences fall out of taking that seriously:

- **An LLM outage can't change a trading decision.** If Groq rate-limits, the
  explanation falls back to deterministic text built from the rule reasons. The
  app runs fine with no `GROQ_API_KEY` at all.
- **The guardrail runs on the request that submits the order.** Every execution
  path re-evaluates before calling Alpaca. A check you can skip by posting
  directly to the endpoint isn't a guardrail.
- **Nothing is a black box.** The strategy shows its moving averages, the
  guardrail shows which rule fired and why.

## The rules

| Rule | Fires when | Threshold |
|---|---|---|
| `oversized_position` | A **buy** would put too much of the portfolio into one trade | > 15% of portfolio value |
| `overexposure` | A **buy** would put more capital to work than the portfolio is worth | > 100% deployed |
| `revenge_trade` | A buy lands shortly after a sell | any filled sell in the last 30 min |
| `overtrading` | Too many fills in a short window | ≥ 5 in the last hour |

Sizing uses a real price — the held position's `current_price`, else the latest
IEX trade. If neither is available the rule stands down rather than guessing.

Selling is never flagged by either size rule: exiting reduces concentration and
exposure, which is the behavior we want.

`overexposure` exists because of something the live account revealed: **$100,000
portfolio value against $388,466 buying power.** Capping each trade at 15% says
nothing about the seventh such trade, and at ~4× margin affordability is barely a
constraint. The agent's very first signal set proposed four buys totalling
$69,614 — 70% of the book in one cycle.

The strategy's conviction ceiling (24% of portfolio) **deliberately sits above
the 15% limit.** A rule that can never fire proves nothing — and on the first
live run it fired twice, unprompted.

## Tech stack

- **Backend:** FastAPI (Python 3.10+)
- **Trading:** Alpaca Trading API, paper mode — orders, positions, account, clock
- **Market data:** Alpaca Data API (IEX feed) — latest trades and daily bars
- **Persistence:** SQLite (stdlib) — the agent runs for days, so the journal is
  the P&L record
- **MCP:** an MCP server exposing the guardrail to any other agent
- **CLI:** full command-line interface over the same API
- **LLM:** Groq (`llama-3.3-70b-versatile`) — explanations only, and optional
- **Frontend:** server-rendered Jinja2 + htmx. No build step, no Node.

## Setup

Requires Python 3.10+ and an Alpaca paper trading account.

```bash
python -m venv .venv
.venv\Scripts\activate          # Windows
# source .venv/bin/activate     # macOS / Linux

pip install -r requirements.txt
cp .env.example .env            # then fill in your keys
```

Keys from:

- **Alpaca** — [paper dashboard](https://app.alpaca.markets/paper/dashboard/overview)
  → API Keys → Generate. The secret is shown once.
- **Groq** — [console.groq.com/keys](https://console.groq.com/keys). Optional.

## Running it

```bash
python run.py            # server + autonomous agent
```

- **Dashboard:** http://127.0.0.1:8000
- **API docs:** http://127.0.0.1:8000/docs

The agent starts trading on its own, gated on market hours. Set
`AGENT_ENABLED=false` to run the dashboard without it.

```bash
python -m pytest tests -q       # 66 tests
```

### CLI

```bash
python cli.py status                     # account, agent, journal
python cli.py signals                    # what the strategy wants right now
python cli.py run-once                   # force one cycle
python cli.py propose NVDA 400 buy       # guardrail check, no order
python cli.py execute NVDA 400 buy --override
python cli.py journal --limit 30
python cli.py gap                        # behavior gap
python cli.py impact                     # what the guardrail bought you
```

### MCP server

```bash
python mcp_server.py
```

Register it with any MCP client:

```json
{
  "mcpServers": {
    "alpaca-guardrail": {
      "command": "python",
      "args": ["C:/path/to/backend/mcp_server.py"]
    }
  }
}
```

Tools: `evaluate_trade`, `execute_trade` (guardrail-gated), `get_account`,
`get_strategy_signals`, `run_agent_cycle`, `get_agent_status`,
`get_journal_summary`, `get_behavior_gap`, `get_guardrail_impact`.

The point of exposing this over MCP: the behavioral check isn't locked inside
our app. **Any** agent can route its trades through a guardrail before they reach
a broker.

## API

**Agent**

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/agent/status` | Loop state, cycles, proposed / executed / blocked |
| `POST` | `/agent/run-once` | Run one cycle now |
| `POST` | `/agent/start` · `/agent/stop` | Control the loop |
| `GET` | `/agent/signals` | What the strategy wants (read-only) |
| `GET` | `/agent/diagnostics` | Every symbol examined and the verdict |

**Trading**

| Method | Path | Purpose |
|---|---|---|
| `POST` | `/trades/propose` | Evaluate, explain, log — no order placed |
| `POST` | `/trades/execute` | Re-evaluate, then submit. `?override=true` to pass a flag |

**Journal**

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/journal/entries` | Every logged decision |
| `GET` | `/journal/summary` | Counts by outcome |
| `GET` | `/journal/behavior-gap` | Held-everything vs. actually-traded |
| `GET` | `/journal/guardrail-impact` | What the blocked trades would have done |

**Account**

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/account` | Live snapshot + positions |
| `GET` | `/health` | Liveness |
| `GET` | `/` | Dashboard (HTML) |

`/fragments/*` returns HTML partials for htmx. Full contracts in
[docs/API.md](docs/API.md).

## Architecture

```
backend/
├── run.py                       # Launcher (server + agent)
├── cli.py                       # Command-line interface
├── mcp_server.py                # MCP server exposing the guardrail
├── app/
│   ├── main.py                  # FastAPI entrypoint — wiring only
│   ├── core/                    # config, DI providers
│   ├── schemas/                 # Pydantic models
│   ├── services/
│   │   ├── alpaca_client.py     # ONLY file that talks HTTP to Alpaca
│   │   ├── strategy.py          # Momentum signals, pure + a fetching service
│   │   ├── agent.py             # The autonomous loop
│   │   ├── guardrail_rules.py   # Pure behavioral rules, zero I/O
│   │   ├── guardrail_service.py # Gathers context, runs rules, decides
│   │   ├── explainer.py         # ONLY file that talks to Groq
│   │   ├── behavior_gap.py      # Behavior gap + guardrail impact
│   │   ├── trade_parser.py      # "buy 50 shares of NVDA" -> TradeProposal
│   │   └── journal_service.py   # SQLite-backed trade record
│   ├── api/                     # trades, journal, agent, ui
│   ├── templates/               # Jinja2 dashboard + htmx partials
│   └── static/css/theme.css
├── tests/
└── docs/
```

**Why it's split this way**

- **Single Responsibility** — `alpaca_client.py` knows Alpaca's HTTP shape and
  nothing else. `guardrail_rules.py` knows behavioral logic and makes no network
  calls. `strategy.py` knows momentum. One concern, one file.
- **Open/Closed** — a new rule is one class implementing
  `GuardrailRule.check(ctx) -> RuleFlag`, added to `ALL_RULES`.
- **Dependency Inversion** — routes never construct services; everything arrives
  through `Depends()`. Swapping the broker, LLM, or storage touches one file —
  which is how the journal went from in-memory to SQLite in two files.
- **Neither rules nor the strategy do I/O** — all state is resolved by a service
  and passed in. That's what makes both testable against fixed data.

## Documentation

Start with [`docs/README.md`](docs/README.md) for a reading order.

| Document | Covers |
|---|---|
| [PRD.md](docs/PRD.md) | Problem, goals, requirements, acceptance criteria |
| [STRATEGY.md](docs/STRATEGY.md) | What it trades, why, sizing, and honest weaknesses |
| [DECISIONS.md](docs/DECISIONS.md) | 20 ADRs — why the code is shaped this way, and what was rejected |
| [ARCHITECTURE.md](docs/ARCHITECTURE.md) | Layering, request flow, invariants |
| [BEHAVIORAL_RULES.md](docs/BEHAVIORAL_RULES.md) | Thresholds and rationale, behavior gap maths |
| [CONVENTIONS.md](docs/CONVENTIONS.md) | How to extend without breaking a boundary |
| [API.md](docs/API.md) | Endpoint contracts and error behavior |
| [DEMO.md](docs/DEMO.md) | Demo script and talk track |
| [STATUS.md](docs/STATUS.md) | What works, what's unverified, roadmap |
| [GLOSSARY.md](docs/GLOSSARY.md) | Terminology |

## Deployment

The journal picks its backend from the environment:

| `DATABASE_URL` | Storage |
|---|---|
| unset (local) | SQLite at `JOURNAL_DB_PATH` — zero setup |
| set (deployed) | Postgres |

Nothing else changes; no caller knows which database it's talking to. Set
`DATABASE_URL` to your Neon connection string and prefer the **pooled** host —
the one with `-pooler` — since serverless cold starts produce many short-lived
connections:

```
DATABASE_URL=postgresql://user:pass@ep-xxxx-pooler.region.aws.neon.tech/db?sslmode=require
```

Connections are lazy (a cold start pays nothing until a read or write) and
Postgres operations retry once on a dropped socket, because serverless containers
get frozen between invocations.

### One caveat: the agent needs a live process

The autonomous loop is a background task inside the web process. It runs fine on
any always-on host, and **not at all** on request-scoped serverless — between
requests the container is frozen, so the 15-minute cycle never fires. Nothing
errors; the dashboard keeps serving while trading silently stops.

If you deploy to true serverless, set `AGENT_ENABLED=false` and drive it with an
external cron hitting `POST /agent/run-once` every 15 minutes. That endpoint is
the same code path the loop uses, so it needs no extra logic. Details and
alternatives in [docs/STATUS.md](docs/STATUS.md) under *Deploying*.

## Known limitations

Stated plainly, because a project presented without them isn't credible.

- **No edge is claimed.** The strategy is textbook moving-average crossover. It
  exists so the guardrail has something real to police. Five trading days of P&L
  is noise, and we say so in the demo.
- **Revenge detection is a timing heuristic**, not loss detection. It fires on
  any recent filled sell, profitable or not, because an Alpaca order object
  carries no realized P&L.
- **No stop losses, no volatility-scaled sizing.** 8% of the portfolio in a
  utility and 8% in a semiconductor are treated as the same bet.
- **The universe is highly correlated** — nine tech-adjacent names plus one bank.
  Not a diversified portfolio and not presented as one.
- **The behavior gap values every buy at the current price**, not a time-weighted
  baseline.
- **Blocked sells carry no P&L figure**, deliberately — the position stayed on,
  and its outcome is already in the account's real P&L.

Full list, plus what has and hasn't been verified against live Alpaca:
[docs/STATUS.md](docs/STATUS.md).

## Safety

`ALPACA_BASE_URL` defaults to the paper endpoint and the UI is badged **PAPER**.
The agent places real market orders against whatever URL is configured —
pointing it at live trading would place live orders. Don't.

Nothing here is financial advice.
