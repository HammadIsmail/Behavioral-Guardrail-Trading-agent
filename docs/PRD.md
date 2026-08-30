# Product Requirements Document

**Product:** Alpaca Guardrail Agent
**Context:** Alpaca AI Trading Agents Hackathon, lablab.ai (Aug 28 – Sep 4, 2026)
**Status:** in development
**Last updated:** 2026-08-30

---

## 1. Problem

Retail traders underperform the funds they hold. The gap is not analytical —
it's behavioral.

DALBAR's 2024 figures: the average equity investor earned **16.54%** while the
S&P 500 returned **25.02%**. An ~850 basis point shortfall, produced not by bad
stock selection but by *when* people bought and sold — panic selling into
drawdowns, revenge trading after a loss, overtrading, and position sizes chosen
on conviction-in-the-moment rather than a plan.

The tooling available to retail traders makes this worse rather than better.
Brokerages optimize for execution speed and frictionlessness. AI trading tools
almost universally attack the wrong variable: they try to improve *what* you
buy, when the evidence says the damage is in *when* and *how much*.

Nobody is selling the trader a mirror.

## 2. Product thesis

**Don't pick trades. Interrupt them.**

Sit between the trader's impulse and the broker's order endpoint. At the moment
a trade is proposed — the only moment where intervention is still cheap — check
it against the trader's own recent behavior, and if it looks like a known bias,
say so in plain language and make them confirm.

Three properties make this a product rather than a lecture:

1. **It never blocks.** Every flagged trade can be overridden in one click. The
   product's value is friction at the decision point, not control.
2. **It is not a black box.** The decision comes from deterministic rules the
   user can be shown. The LLM only phrases the result.
3. **It shows the user their own number.** The journal computes what they would
   have earned holding everything untouched versus what their actual in-and-out
   trading earned — their personal version of the DALBAR gap, from their own
   trades.

## 3. Target user

A retail trader who is competent enough to have a thesis and undisciplined
enough not to follow it. Trades their own account, checks it more often than
they should, has at some point sold in a panic and bought back higher.

Explicitly **not** targeting: institutional traders, algorithmic/HFT users, or
people who want signal generation.

## 4. Goals

| ID | Goal |
|---|---|
| G-1 | Detect the common behavioral failure modes on a proposed trade before it executes |
| G-2 | Explain a flag in plain, non-judgmental language naming the likely bias |
| G-3 | Preserve trader autonomy — always offer a one-click override |
| G-4 | Record every decision, including the ones where the user backed off |
| G-5 | Quantify the user's behavior gap from their own journal |
| G-6 | Keep approve/deny fully deterministic and independent of any LLM |

## 5. Non-goals

| ID | Non-goal | Why |
|---|---|---|
| NG-1 | Predicting prices or generating trade ideas | Wrong variable; every competitor is here |
| NG-2 | Hard-blocking trades | Turns a coach into a nanny; users would route around it |
| NG-3 | Letting the LLM decide approve/deny | Non-deterministic, unauditable, untestable |
| NG-4 | Live-money trading | Paper only for the hackathon; the safety story isn't built |
| NG-5 | Portfolio optimization / rebalancing advice | Different product |
| NG-6 | Multi-user accounts, auth, tenancy | Single-user demo scope |

## 6. Core user journey

1. Trader opens the dashboard and sees live account state — buying power,
   portfolio value, open positions.
2. Types an intent in plain language: `buy 50 shares of NVDA`.
3. Agent pulls their live account state and recent order history from Alpaca.
4. Agent evaluates the trade against the behavioral rules.
5. **Clean:** brief confirmation, one button to execute.
6. **Flagged:** names the bias, shows the specific rule reasons, offers
   *Proceed anyway* / *Cancel*.
7. Either choice is written to the journal — including a cancel.
8. Journal shows the running behavior gap.

## 7. Functional requirements

### Trade intake

| ID | Requirement |
|---|---|
| FR-1 | Accept a trade as structured JSON (`symbol`, `qty`, `side`) via the API |
| FR-2 | Accept a trade as a natural-language string in the dashboard (`buy 50 shares of NVDA`) |
| FR-3 | On unparseable input, say what was missing and offer no execute path |

### Account context

| ID | Requirement |
|---|---|
| FR-4 | Fetch live account snapshot (buying power, cash, portfolio value, equity, positions) |
| FR-5 | Fetch recent order history for behavioral pattern detection |
| FR-6 | Price the proposed symbol from the held position, else from market data |
| FR-7 | If a symbol cannot be priced, size-dependent rules stand down rather than guess |

### Evaluation

| ID | Requirement |
|---|---|
| FR-8 | Run the proposal through all rules; approved iff zero rules trigger |
| FR-9 | Return per-rule verdicts, not just an overall boolean |
| FR-10 | Decide with **zero** LLM involvement |
| FR-11 | Re-evaluate on the request that submits the order, not only at proposal |

Rule specifications: [BEHAVIORAL_RULES.md](BEHAVIORAL_RULES.md).

### Explanation

| ID | Requirement |
|---|---|
| FR-12 | Phrase the already-final decision in plain language via LLM |
| FR-13 | Name the likely bias; calm and non-judgmental, no lecturing |
| FR-14 | On LLM failure or absent API key, fall back to deterministic text built from rule reasons |
| FR-15 | An LLM failure must never change or discard a decision |

### Execution

| ID | Requirement |
|---|---|
| FR-16 | Submit approved trades to Alpaca as market orders, paper mode |
| FR-17 | Execute a flagged trade only on explicit override |
| FR-18 | Derive override status from the fresh decision, never from a client-supplied field |
| FR-19 | Surface Alpaca's own rejection reason rather than a bare HTTP status |

### Journal

| ID | Requirement |
|---|---|
| FR-20 | One journal entry per proposed trade, updated through its lifecycle |
| FR-21 | Record outcome: clean, flagged, executed, overridden, cancelled |
| FR-22 | Record the price the trade was evaluated at |
| FR-23 | Expose counts by outcome |
| FR-24 | Compute and expose the behavior gap |

### Dashboard

| ID | Requirement |
|---|---|
| FR-25 | Show live account state |
| FR-26 | Chat-style trade proposal input |
| FR-27 | Show verdict, explanation, and the triggered rule reasons |
| FR-28 | Journal list with outcome per entry, auto-refreshing after any action |
| FR-29 | Behavior gap panel |
| FR-30 | Every action shows pending state and cannot be double-submitted |

## 8. Non-functional requirements

| ID | Requirement |
|---|---|
| NFR-1 | Guardrail decision is deterministic and reproducible for identical inputs |
| NFR-2 | Rules are pure functions of injected context — unit-testable with no network |
| NFR-3 | App runs with no `GROQ_API_KEY` configured |
| NFR-4 | Market data or LLM outage degrades gracefully; it never fails a decision |
| NFR-5 | Paper trading only; the UI states this visibly |
| NFR-6 | Adding a rule touches one file plus one list entry |
| NFR-7 | No secrets in the repo; `.env` gitignored, `.env.example` committed |

## 9. Acceptance criteria

The build is demo-ready when all of these pass against live paper Alpaca:

| # | Scenario | Expected |
|---|---|---|
| AC-1 | Small buy, well under 15% of portfolio | Approved, brief explanation, executes, journal shows `executed` |
| AC-2 | Buy exceeding 15% of portfolio value | Flagged as `oversized_position`, reason states the actual percentage |
| AC-3 | Sell that is a large share of the portfolio | **Not** flagged — exiting is not oversizing |
| AC-4 | Buy within 30 min of a filled sell | Flagged as `revenge_trade` |
| AC-5 | Sixth fill within an hour | Flagged as `overtrading` |
| AC-6 | Flagged trade, user clicks Cancel | No order placed, journal shows `cancelled` |
| AC-7 | Flagged trade, user clicks Proceed anyway | Order placed, journal shows `overridden` |
| AC-8 | `POST /trades/execute` directly on a flagged trade without override | Refused, `flagged_awaiting_confirmation` |
| AC-9 | `GROQ_API_KEY` blank or Groq erroring | Decision unchanged, deterministic explanation returned |
| AC-10 | Symbol with no available market data | No crash; size rule stands down |
| AC-11 | `GET /journal/summary` after any execution | 200, correct counts |
| AC-12 | Buy, then sell at a loss, then price recovers | Behavior gap positive and equal to the foregone gain |
| AC-13 | Buys only, nothing sold | Behavior gap exactly zero |
| AC-14 | Any button clicked | Spinner shows, group disables, no double-submit |

Current pass/fail state: [STATUS.md](STATUS.md).

## 10. Out of scope for the hackathon

Auth and multi-user; persistent storage; live trading; options/crypto/fractional
handling beyond what Alpaca accepts by default; mobile layout; per-user
configurable thresholds; cost-basis-aware loss detection; historical backtest of
the behavior gap.

## 11. Success metrics

Hackathon judging is the real metric. Proxies we can control:

- A demo that runs end-to-end without a crash or an unexplained flag
- The behavior gap shows a non-trivial, correct number in the demo
- The rules-decide/LLM-phrases split is legible to a judge in under a minute
- The override and cancel paths both visibly reach the journal

## 12. Open questions

| Q | Notes |
|---|---|
| Should thresholds be user-configurable? | Fixed constants today. Configurable is more honest to real risk tolerance but adds surface area and weakens the demo's clarity. |
| Should revenge detection require an actual realized loss? | Currently any recent sell. True loss detection needs cost-basis tracking — see BEHAVIORAL_RULES.md. |
| Should the journal persist? | In-memory today; resets on reload. SQLite would make the behavior gap accumulate across sessions. |
| Does the behavior gap need historical prices? | Today it values every buy at the current price. A time-weighted version would be more rigorous. |
