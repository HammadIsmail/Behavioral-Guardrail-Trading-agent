# Status

Where the build actually is. **Read this before assuming anything works.**

**Last updated:** 2026-08-30

---

## Honest summary

The project is now an **autonomous trading agent with a behavioral guardrail on
itself**, not a human-in-the-loop advisor. That change was made to meet the
hackathon's P&L and technology criteria — see [DECISIONS.md](DECISIONS.md)
ADR-014 through ADR-018 for why, and what it cost.

**Verified: 83 tests pass** — rules, strategy, behavior gap, guardrail impact,
journal persistence, parser, app wiring, MCP server import. Dependencies install
cleanly (`pydantic` at 2.10+, `mcp` installed).

**Live, confirmed 2026-08-30 (Sunday, market closed):**

- `python run.py` starts, lifespan completes, the agent loop launches
- Alpaca account fetch works — $100,000 portfolio, $388,466 buying power
- Dashboard renders; `/account`, `/agent/status`, `/journal/summary`,
  `/journal/entries`, `/fragments/agent` all return 200
- `python cli.py status`, `cli.py journal` and `cli.py signals` all work
- **The strategy produces real signals off live IEX daily bars** — four buys with
  sane moving averages and conviction multiples. V-3 and V-11 closed.
- The agent completed a cycle, correctly detected the market as closed, and
  traded nothing

The first live signal set also exposed a genuine defect, now fixed: four buys
totalling **$69,614 on a $100,000 portfolio** with nothing watching the aggregate.
Added `OverexposureRule` plus a matching strategy ceiling (ADR-021).

It also confirmed ADR-017 empirically. Two of those four signals — NFLX at 23.9%
and TSLA at 20.5% of portfolio — exceed the 15% limit, so conviction scaling
produced oversized positions unprompted. The guardrail has something real to
catch on the first market-open cycle.

**What remains unproven is order submission.** No trade has ever been placed.

## Verify first, in this order

```bash
pip install -r requirements.txt        # ✅ done
python -m pytest tests -q              # ✅ 83 passed; +14 added since, unrun
python run.py                          # ✅ starts, agent loop alive
python cli.py status                   # ✅ real account data
python cli.py signals                  # ✅ real signals off live bars
python cli.py run-once                 # ⬅ needs market hours (Mon 31 Aug)
```

The only paths left unproven need an open market: order submission, the block
path, and a full cycle end to end.

`tests/test_app_wiring.py` is deliberately import-only — a `TestClient` would run
the lifespan and start the agent placing real paper orders from a test run.

Then walk the acceptance criteria in [PRD.md](PRD.md) §9, especially AC-15
through AC-21, which are all new.

---

## Done

| Area | State |
|---|---|
| Env, venv, dependencies | ✅ (except `mcp`, not installed) |
| Alpaca paper account, keys in `.env` | ✅ |
| Alpaca client — account, positions, orders, submit | ✅ |
| Alpaca market data — latest price, daily bars, clock | ⚠️ written, never called |
| Three behavioral rules | ✅ tested |
| Guardrail service, deterministic decision | ✅ |
| Explainer with deterministic fallback | ✅ |
| Momentum strategy, conviction sizing | ✅ tested |
| Autonomous agent loop | ⚠️ written, never run |
| Journal — SQLite, survives restart | ✅ tested |
| Behavior gap | ✅ tested |
| Guardrail impact (blocked-trade counterfactual) | ✅ tested |
| JSON API — trades, journal, agent, account | ⚠️ routes never loaded |
| Dashboard — agent panel, signals, journal, both metrics | ⚠️ never rendered |
| CLI | ⚠️ written, never run |
| MCP server | ⚠️ written, never run, dep not installed |
| Docs | ✅ |

## Not verified

| # | What | Risk |
|---|---|---|
| V-5 | Neither metric rendered with real data | Formatting, negative branches |
| V-6 | Groq fallback path never exercised live | Only reachable by breaking the key |
| V-8 | **A market-open cycle never run; no order ever submitted** | Order submission, the block path, a full cycle end to end. A closed-market cycle exits before any of it. |
| V-13 | Overexposure rule and strategy ceiling never exercised live | Only binds once the book approaches 100% deployed, which takes several cycles |

Resolved: **V-1** (test suite), **V-3** and **V-11** (market data and bar
history — real signals confirmed), **V-4** (dashboard renders), **V-7** (app
startup and routes), **V-9** (MCP import path), **V-10** (CLI), **V-12**
(unbounded exposure — fixed, see K-16) — all closed 2026-08-30.

**V-3 and V-11 are the dangerous pair** — both fail quietly. If bars come back
empty, the agent runs forever, reports healthy cycles, and never trades. Check
`python cli.py signals` returns something before trusting a quiet agent.

**V-9 is the cheapest to de-risk:** if `mcp==1.2.0` won't install, loosen the pin
to `mcp>=1.0` and re-check the `from mcp.server.fastmcp import FastMCP` path.

## Known issues

| # | Issue | Where |
|---|---|---|
| K-1 | Alpaca order rejection returns `500` on the JSON route; the fragment route catches it | `api/trades.py` |
| K-3 | Revenge detection is a timing heuristic, not loss detection | `guardrail_rules.py` |
| K-4 | Oversized rule checks per-trade notional, not resulting position size | `guardrail_rules.py` |
| K-5 | Overtrading counts fills, so partial fills inflate the count | `guardrail_rules.py` |
| K-6 | Behavior gap values every buy at the current price, not time-weighted | `behavior_gap.py` |
| K-7 | Pre-journal positions have no cost basis; their sells contribute nothing | `behavior_gap.py` |
| K-8 | Trade parser is deliberately small | `trade_parser.py` |
| K-9 | Thresholds are fixed constants, not per-user | `guardrail_rules.py` |
| K-10 | Not a git repository yet | repo root |
| K-11 | Strategy has no stop loss and no volatility scaling | `strategy.py` |
| K-12 | Universe is nine tech-adjacent names plus one bank — highly correlated | config |
| K-13 | Two processes (server + MCP) write the same SQLite file; relies on SQLite locking | `journal_service.py` |
| K-14 | Agent cycle is sequential, so one slow Alpaca call delays the whole pass | `agent.py` |
| K-15 | `AGENT_ENABLED` defaults true, so `python run.py` starts trading immediately | `config.py` |
| K-16 | ~~No aggregate exposure cap~~ **Fixed** — `OverexposureRule` caps total deployed capital at 100% of portfolio value, and the strategy sizes into remaining headroom (ADR-021). The two thresholds are duplicated and must be kept in step. | `guardrail_rules.py`, `strategy.py` |
| K-17 | ~~Journal can't survive a serverless deploy~~ **Fixed** — Postgres backend selected by `DATABASE_URL`, SQLite locally (ADR-022). Two SQL dialects to keep in step. | `journal_service.py` |
| K-18 | **The autonomous agent will not run reliably on a scale-to-zero serverless host.** The loop is an `asyncio.Task` inside the web process; a frozen or evicted container stops trading silently while the dashboard still serves fine. See "Deploying" below. | `agent.py`, `main.py` |

## Deploying

The Postgres switch (ADR-022) makes the journal survive a deploy. It does **not**
make the agent survive one.

`AgentService.start()` launches a background `asyncio.Task` from the FastAPI
lifespan. That works on any host that keeps a process alive — a VM, a container,
Render/Railway/Fly with a always-on instance, a Droplet. It does **not** work on
request-scoped serverless (Vercel functions, AWS Lambda, Cloud Run with
scale-to-zero): between requests the container is frozen or destroyed, so the
15-minute loop simply doesn't fire. Nothing errors. The dashboard stays up, cycles
stop accruing, and the P&L record quietly stops growing.

Three ways out, in order of preference for this deadline:

| Option | What it means |
|---|---|
| **Always-on host** | Deploy to a platform with a persistent process (Render/Railway/Fly worker, or any small VM). Zero code change — the loop already works. |
| **External scheduler** | Keep serverless, set `AGENT_ENABLED=false`, and have a cron (GitHub Actions, Neon/Vercel cron, cron-job.org) hit `POST /agent/run-once` every 15 minutes. That endpoint is the same code path the loop runs, so this needs no new logic. |
| **Run the agent locally, dashboard remotely** | Both point at the same `DATABASE_URL`. The local process trades; the deployed dashboard reads the shared journal. |

The middle option is the one that fits "serverless" as asked, and it costs one
cron entry. Note that `POST /agent/run-once` is currently unauthenticated — worth
a shared-secret header before it's publicly reachable.

K-11 and K-12 are deliberate and documented in [STRATEGY.md](STRATEGY.md).
K-15 is deliberate — an agent that needs to be told to be an agent isn't one —
but worth knowing before you start the server.

## Not built

Authentication, multi-user, live-money trading, mobile layout, per-user
thresholds, time-weighted behavior gap, shorting, a fourth rule.

---

## Roadmap

**Before the demo — in this order**

1. ~~Run the test suite~~ ✅ 66 passing
2. `pip install -r requirements.txt` for `mcp` (V-9)
3. Confirm the app boots and `/` renders (V-7)
4. `python cli.py signals` — confirm bars and prices actually arrive (V-3, V-11).
   **This gates everything else.** A quiet agent looks healthy.
5. `python cli.py run-once` during market hours — the full agent path (V-8)
6. **Start the agent and leave it running.** P&L needs market hours; every
   session missed is history you can't recover.
7. Confirm blocked trades appear, then check `python cli.py impact` (V-5)
8. Start `python mcp_server.py` and call one tool (V-9)
9. Fix K-1 so the JSON route reports rejections properly
10. Record the demo — [DEMO.md](DEMO.md)

**After the demo**

11. `git init` and a first commit
12. Volatility-scaled position sizing (K-11) — the biggest strategy weakness
13. Loss-aware revenge detection using journal cost basis (K-3)
14. Concurrent Alpaca calls inside a cycle (K-14)
15. Resulting-position-size limits rather than per-trade notional (K-4)

---

## History

**2026-08-30, third pass — autonomous agent.** Assessed against the hackathon
judging criteria and found the project could not score on P&L Performance at
all: it generated no trades of its own, and its PRD explicitly excluded trade
generation. Rebuilt around an autonomous loop with the guardrail pointed at the
agent instead of only at a human. Added: momentum strategy, agent loop with
market-hours gating, SQLite journal, blocked-trade counterfactual, agent API,
dashboard agent/signals panels, MCP server, CLI, strategy and demo docs.

**2026-08-30, second pass — docs.** Added `docs/` and de-duplicated `CLAUDE.md`.

**2026-08-30, first pass — audit.** Reviewed the scaffold against `CLAUDE.md`
and fixed gaps between documented intent and actual behavior:

- The dashboard's execute route **bypassed the guardrail entirely**
- `GET /journal/summary` crashed on any entry with no guardrail result
- The behavior gap did not exist
- Cancel logged nothing while claiming it had
- Propose + execute wrote two journal rows for one trade
- A Groq failure discarded an already-computed decision
- `OversizedPositionRule` used a hardcoded `$100` price and flagged sells
- Parse failures rendered as "flagged" with a working *Proceed anyway* button
- The parser returned `NOW` as the ticker for "I want to buy 50 NVDA now"
- The propose loading indicator never displayed

Then loading states on every action, and the dashboard moved from `/ui` to `/`.

Full rationale for the resulting design: [DECISIONS.md](DECISIONS.md).
