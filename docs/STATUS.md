# Status

Where the build actually is. **Read this before assuming anything works.**

**Last updated:** 2026-08-30

---

## Honest summary

Feature-complete against the PRD. **The pure-logic layer is verified: 39 tests
pass** — rules, behavior gap FIFO maths, journal lifecycle, and the parser.

Everything that crosses a process boundary is still unverified. No run has
confirmed a real propose → flag → override → journal cycle against live paper
Alpaca. Note also that the tests import from `app.schemas` and `app.services`
but **never import `app.main`** — so app startup and route registration are not
covered by that green run.

## Verify first

```bash
python -m pytest tests -q      # ✅ 39 passed (2026-08-30)
python run.py                  # ⬅ next: does it even start? then http://127.0.0.1:8000
```

Then walk the acceptance criteria in [PRD.md](PRD.md) §9.

---

## Done

| Area | State |
|---|---|
| Env, venv, dependencies | ✅ |
| Alpaca paper account, keys in `.env` | ✅ |
| Groq key in `.env` | ✅ (optional at runtime) |
| Settings via pydantic-settings | ✅ |
| Alpaca client — account, positions, orders, order submit | ✅ |
| Alpaca market data — latest trade price, IEX feed | ✅ |
| Three behavioral rules | ✅ |
| Guardrail service, deterministic decision | ✅ |
| Explainer with deterministic fallback | ✅ |
| Natural-language trade parser | ✅ |
| Journal — one entry per trade, lifecycle updates | ✅ |
| Behavior gap — FIFO, exposed via API and UI | ✅ |
| JSON API — trades, journal, account, health | ✅ |
| Dashboard at `/` with htmx fragments | ✅ |
| Loading states, double-submit protection | ✅ |
| Tests — rules, parser, journal, behavior gap | ✅ 39 passing |
| `.gitignore`, `.env.example`, README, docs | ✅ |

## Not verified

| # | What | Risk |
|---|---|---|
| V-2 | **No live Alpaca round trip confirmed** since the fix pass | Order submission, market data shape, order history fields |
| V-3 | Market data response shape assumed | `/v2/stocks/trades/latest` → `{"trades": {SYM: {"p": …}}}`, `feed=iex`. If wrong, every symbol reads as unpriced and the size rule silently stands down. |
| V-4 | htmx loading behavior not seen in a browser | `hx-disabled-elt` / `hx-sync` on htmx 2.0.3, fieldset CSS resets |
| V-5 | Behavior gap never rendered with real data | Formatting, negative-gap branch |
| V-6 | Groq fallback path never exercised live | Only reachable by breaking the key |
| V-7 | **App startup and route registration untested** | The test suite never imports `app.main`, so nothing has confirmed the routers register, `/` resolves, the static mount works, or the lifespan closes cleanly |

Resolved: **V-1** (test suite never run) — closed 2026-08-30, 39 passing.

**V-3 deserves attention.** It fails silently rather than loudly: no crash, no
error — just `reference_price: null` and an oversized rule that never fires.
Confirm a real price comes back before trusting a clean verdict on a large buy.

**V-7 is the cheapest to close:** `python run.py` and load the page. The green
test run says nothing about it.

## Known issues

| # | Issue | Where |
|---|---|---|
| K-1 | Alpaca order rejection returns `500` on the JSON route. The fragment route catches it and shows the message; `api/trades.py` doesn't catch it. | `api/trades.py` `execute_trade` |
| K-2 | Journal is in-memory; resets on restart including `--reload` (ADR-012) | `journal_service.py` |
| K-3 | Revenge detection is a timing heuristic, not loss detection | `guardrail_rules.py` |
| K-4 | Oversized rule checks per-trade notional, not resulting position size — three 14% buys all pass | `guardrail_rules.py` |
| K-5 | Overtrading counts fills, so partial fills inflate the count | `guardrail_rules.py` |
| K-6 | Behavior gap values every buy at the current price, not a time-weighted baseline | `behavior_gap.py` |
| K-7 | Pre-journal positions have no cost basis, so their sells contribute nothing | `behavior_gap.py` |
| K-8 | Trade parser is deliberately small; unusual phrasings will fail | `trade_parser.py` |
| K-9 | Thresholds are fixed constants, not per-user | `guardrail_rules.py` |
| K-10 | Not a git repository yet — `.gitignore` is in place but nothing is tracked | repo root |

K-1 is a small, self-contained fix. K-3 through K-9 are deliberate scope
decisions, documented in [BEHAVIORAL_RULES.md](BEHAVIORAL_RULES.md) and
[DECISIONS.md](DECISIONS.md).

## Not built

- Authentication, multi-user, tenancy
- Persistence of any kind
- Live-money trading
- Mobile layout
- Per-user configurable thresholds
- Historical/time-weighted behavior gap
- A rule beyond the three shipped

---

## Roadmap

**Before the demo**

1. ~~Run the test suite~~ ✅ 39 passing (2026-08-30)
2. Start the app and confirm it boots and `/` renders (V-7) — cheapest gap left
3. Confirm market data returns a real price (V-3) — the quietest failure here
4. Walk all 14 acceptance criteria against live paper Alpaca (V-2)
5. Click through the dashboard in a browser and watch the loading states (V-4)
6. Fix K-1 so the JSON route reports rejections properly
7. Script the demo: clean → flagged → cancel → flagged → override → gap

**After the demo**

8. `git init` and a first commit
9. Make revenge detection loss-aware using journal cost basis (K-3). The journal
   already records a price per executed trade; it would need to arrive on
   `RuleContext`, since rules can't read the journal.
10. SQLite-backed journal so the gap accumulates (K-2) — `journal_service.py`
    internals plus its provider, nothing else
11. Add the averaging-down rule; `Position.unrealized_pl` is already available,
    making it the most tractable next one
12. Track resulting position size, not just per-trade notional (K-4)

---

## History

The scaffold was reviewed against `CLAUDE.md` on 2026-08-30 and had several
gaps between documented intent and actual behavior. Fixed in one pass:

- The dashboard's execute route **bypassed the guardrail entirely**, submitting
  straight to Alpaca with a client-supplied override flag
- `GET /journal/summary` crashed on any entry with no guardrail result
- The behavior gap — the product's headline feature — did not exist
- Cancel logged nothing while telling the user it had been logged
- Propose + execute wrote two journal rows for one trade
- A Groq failure discarded an already-computed decision
- `OversizedPositionRule` used a hardcoded `$100` price and flagged sells
- Parse failures rendered as "flagged" with a working *Proceed anyway* button
- The parser returned `NOW` as the ticker for "I want to buy 50 NVDA now"
- The propose loading indicator never displayed (wrong CSS selector for
  `hx-indicator`)

Then: loading states on every action, and the dashboard moved from `/ui` to `/`
with fragments under `/fragments/*`.

Full rationale for the resulting design: [DECISIONS.md](DECISIONS.md).
