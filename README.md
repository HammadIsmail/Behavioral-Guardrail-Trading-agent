# Alpaca Guardrail Agent

A behavioral guardrail that sits between a trader's impulse and Alpaca's order
execution.

Built for the [Alpaca AI Trading Agents Hackathon](https://lablab.ai)
(Aug 28 – Sep 4, 2026). **Paper trading only — no real money.**

---

## The problem

Retail traders don't underperform the market because their analysis is bad.
They underperform because of their own behavior.

DALBAR's research puts a number on it: in 2024 the average equity investor
earned **16.54%** while the S&P 500 returned **25.02%** — an ~850 basis point
shortfall. That gap isn't stock picking. It's panic selling, revenge trading,
overtrading, and oversized positions taken on emotion instead of a plan.

Almost every AI trading tool tries to close that gap by picking better stocks.
This one doesn't pick anything.

## What this does instead

When you propose a trade, the agent:

1. **Pulls your live account state** from Alpaca — positions, recent orders, P&L
2. **Runs the trade through behavioral rules** — oversized position, revenge
   trading, overtrading
3. **If it's clean**, says so briefly and lets it through
4. **If it's flagged**, names the bias it looks like in plain language and asks
   you to confirm or back out. It never silently blocks a trade — it only adds
   friction at the moment friction is worth something
5. **Logs the decision either way**, and shows you your own *behavior gap*:
   what you'd have made holding everything untouched versus what your actual
   buying and selling produced

That last number is the point. It's your personal version of the DALBAR gap,
computed from your own trades.

## The design decision that matters

**The rules decide. The LLM only phrases.**

`GuardrailService.evaluate()` returns `approved: bool` with **zero LLM
involvement** — deterministic, fast, unit-testable. `ExplainerService` receives
an already-final decision and does nothing but put it in friendly words. The
model cannot approve or flag anything.

Two consequences fall out of taking that seriously:

- **An LLM outage can't change an outcome.** If Groq rate-limits or errors, the
  explanation falls back to a deterministic sentence built from the rule
  reasons. The decision was already made; a language failure can't discard it.
  The app runs fine with no `GROQ_API_KEY` at all.
- **The guardrail runs on the request that submits the order.** Both execute
  endpoints re-evaluate before calling Alpaca rather than trusting a verdict
  handed back from the propose step. A check you can skip by posting directly to
  the execute endpoint isn't a guardrail.

This is meant to be uncertainty-aware tooling, not a black box in a chat UI.

## The rules

| Rule | Fires when | Threshold |
|---|---|---|
| `oversized_position` | A **buy** would put too much of the portfolio into one trade | > 15% of portfolio value |
| `revenge_trade` | A buy lands shortly after a sell | any filled sell in the last 30 min |
| `overtrading` | Too many fills in a short window | ≥ 5 in the last hour |

Sizing uses a real price: the position's `current_price` if you already hold the
symbol, otherwise the latest trade from Alpaca's market data API. If neither is
available the rule stands down rather than guessing — flagging a trade against a
made-up number would be worse than staying quiet.

Selling is never flagged as oversizing. Exiting a large position reduces
concentration; that's the behavior we want, not the behavior we're guarding
against.

## Tech stack

- **Backend:** FastAPI (Python 3.10+)
- **Trading:** Alpaca Trading API, paper mode
- **Market data:** Alpaca Data API (IEX feed, free tier)
- **LLM:** Groq (`llama-3.3-70b-versatile`) — explanations only, and optional
- **Frontend:** server-rendered Jinja2 + htmx, same FastAPI app. No build step,
  no Node.

## Setup

Requires Python 3.10+ and an Alpaca paper trading account.

```bash
# 1. Virtual environment
python -m venv .venv
.venv\Scripts\activate          # Windows
# source .venv/bin/activate     # macOS / Linux

# 2. Dependencies
pip install -r requirements.txt

# 3. Credentials
cp .env.example .env            # then fill in your keys
```

Get your keys from:

- **Alpaca** — [paper dashboard](https://app.alpaca.markets/paper/dashboard/overview)
  → API Keys → Generate. The secret is shown once.
- **Groq** — [console.groq.com/keys](https://console.groq.com/keys). Optional;
  leave blank to use deterministic explanations.

## Running it

```bash
python run.py                   # or: uvicorn app.main:app --reload
```

- **Dashboard:** http://127.0.0.1:8000
- **API docs:** http://127.0.0.1:8000/docs

```bash
python -m pytest tests -q       # tests
```

## API

**Trading**

| Method | Path | Purpose |
|---|---|---|
| `POST` | `/trades/propose` | Evaluate a trade, get a decision + explanation, log it |
| `POST` | `/trades/execute` | Re-evaluate, then submit to Alpaca. `?override=true` to proceed past a flag |

**Journal**

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/journal/entries` | Every logged decision |
| `GET` | `/journal/summary` | Counts by outcome |
| `GET` | `/journal/behavior-gap` | Held-everything vs. actually-traded |

**Account**

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/account` | Live account snapshot + positions |
| `GET` | `/health` | Liveness check |
| `GET` | `/` | Dashboard (HTML) |

The dashboard itself is served at `/`. The `/fragments/*` routes mirror the
trading endpoints for htmx and return HTML fragments rather than JSON.

### Example

```bash
curl -X POST http://127.0.0.1:8000/trades/propose \
  -H "Content-Type: application/json" \
  -d '{"symbol": "NVDA", "qty": 200, "side": "buy"}'
```

```json
{
  "journal_entry_id": "3f9a...",
  "proposal": { "symbol": "NVDA", "qty": 200, "side": "buy" },
  "result": {
    "approved": false,
    "flags": [
      {
        "rule_name": "oversized_position",
        "triggered": true,
        "reason": "this trade is about 34% of your portfolio — well above a typical 15% single-position guideline"
      }
    ],
    "explanation": "This would put about a third of your account into one name...",
    "reference_price": 172.4
  }
}
```

## Demo script

1. **Clean trade.** In the dashboard, type `buy 5 shares of AAPL`. Small
   relative to the account → approved, brief explanation, execute it.
2. **Oversized trade.** Type `buy 200 shares of NVDA` — enough to clear 15% of
   the portfolio. It gets flagged, names the bias, and offers *Proceed anyway* /
   *Cancel*. Hit **Cancel** — that decision is logged too.
3. **Revenge trade.** Sell something, then immediately try to buy. Flagged as
   reacting to the exit rather than making a fresh decision. This time hit
   **Proceed anyway** — the journal records it as an override.
4. **The journal.** Now it shows clean, cancelled and overridden trades side by
   side, plus the behavior gap: what holding everything would have earned versus
   what the actual in-and-out trading earned.

## How the behavior gap is computed

Sells are matched against buys FIFO from the journal:

- **Passive** — every buy you made, still held today, valued at the current price
- **Actual** — realized P&L from your sells, plus unrealized on what's still open
- **Gap** — `passive − actual`. Positive means selling cost you money

The useful property: **if you never sell, the gap is exactly zero.** Both sides
of the comparison are identical. So any non-zero gap is attributable to selling
decisions, which is precisely the behavior the agent exists to surface.

Only executed trades with a recorded price count. A trade you were flagged on
and cancelled never moved money, so it never enters the maths — it just shows up
in the journal as a decision you made.

## Architecture

```
backend/
├── run.py                       # Launcher
├── app/
│   ├── main.py                  # FastAPI entrypoint — wiring only
│   ├── core/
│   │   ├── config.py            # Settings from .env
│   │   └── dependencies.py      # DI providers for Depends()
│   ├── schemas/                 # Pydantic models
│   ├── services/
│   │   ├── alpaca_client.py     # ONLY file that talks HTTP to Alpaca
│   │   ├── guardrail_rules.py   # Pure behavioral rules, zero I/O
│   │   ├── guardrail_service.py # Gathers context, runs rules, decides
│   │   ├── explainer.py         # ONLY file that talks to Groq
│   │   ├── trade_parser.py      # "buy 50 shares of NVDA" -> TradeProposal
│   │   ├── behavior_gap.py      # Held-everything vs. actually-traded
│   │   └── journal_service.py   # Trade history + summary stats
│   ├── api/                     # trades.py, journal.py, ui.py
│   ├── templates/               # Jinja2 dashboard + htmx partials
│   └── static/css/theme.css
├── tests/
└── docs/                        # PRD, ADRs, architecture, conventions, status
```

**Why it's split this way**

- **Single Responsibility** — `alpaca_client.py` knows Alpaca's HTTP shape and
  nothing else. `guardrail_rules.py` knows behavioral logic and makes no network
  calls. `explainer.py` knows Groq. One concern, one file.
- **Open/Closed** — a new rule is one new class implementing
  `GuardrailRule.check(ctx) -> RuleFlag`, added to `ALL_RULES`. Nothing else
  changes.
- **Dependency Inversion** — routes never construct services; everything arrives
  through `Depends()`. Swapping the broker, the LLM, or the journal's storage
  touches one file.
- **Rules never do I/O** — account state, order history and prices are resolved
  by `GuardrailService` and passed in on `RuleContext`. That's what keeps rules
  testable against fake data.

## Adding a rule

```python
class PanicSellRule(GuardrailRule):
    name = "panic_sell"

    def check(self, ctx: RuleContext) -> RuleFlag:
        ...
        return RuleFlag(rule_name=self.name, triggered=False, reason="")
```

Add it to `ALL_RULES` in `guardrail_rules.py`. If it needs data from outside,
add the field to `RuleContext` and resolve it in `GuardrailService` — never
inside the rule.

## Documentation

Deeper docs live in [`docs/`](docs/) — start with [`docs/README.md`](docs/README.md),
which gives a reading order.

| Document | Covers |
|---|---|
| [PRD.md](docs/PRD.md) | Problem, goals, requirements, acceptance criteria |
| [DECISIONS.md](docs/DECISIONS.md) | Why the code is shaped this way, and what was rejected |
| [ARCHITECTURE.md](docs/ARCHITECTURE.md) | Layering, request flow, invariants |
| [BEHAVIORAL_RULES.md](docs/BEHAVIORAL_RULES.md) | Each rule's threshold and rationale, behavior gap maths |
| [CONVENTIONS.md](docs/CONVENTIONS.md) | How to extend without breaking a boundary |
| [API.md](docs/API.md) | Endpoint contracts and error behavior |
| [STATUS.md](docs/STATUS.md) | What works, what's unverified, roadmap |
| [GLOSSARY.md](docs/GLOSSARY.md) | Terminology |

## Known limitations

- **Revenge detection is a timing heuristic**, not loss detection. It fires on
  any filled sell in the lookback window, profitable or not, because an Alpaca
  order object carries no realized P&L. Making it loss-aware needs cost-basis
  tracking.
- **The journal is in-memory and process-local.** It resets on restart,
  including uvicorn's `--reload`.
- **The behavior gap only knows trades the journal saw.** Positions opened
  before the journal existed have no recorded cost basis, so their sells
  contribute nothing rather than a fabricated profit.
- **The trade parser is deliberately small** — it handles the phrasings a demo
  gets typed into it, not arbitrary natural language.
- **Thresholds are fixed constants**, not per-user risk profiles.

## Safety

`ALPACA_BASE_URL` defaults to the paper endpoint and the UI is badged **PAPER**.
The app submits real market orders to whatever URL is configured — pointing it at
live trading would place live orders. Don't.

Nothing here is financial advice.
