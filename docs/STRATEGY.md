# Trading strategy

What the agent trades, why, and how it sizes. Written to be argued with — every
number here is a choice with a reason and a known weakness.

Rules the strategy is checked against: [BEHAVIORAL_RULES.md](BEHAVIORAL_RULES.md).
Implementation: `app/services/strategy.py`.

---

## The strategy in one paragraph

Dual moving-average momentum, long-only, on a fixed universe of ten liquid US
large caps. When a symbol's 5-day average closing price rises above its 20-day
average, the agent buys. When it falls back below, the agent sells the whole
position. Position size is 8% of portfolio value, scaled up to 3× on the
separation between the two averages — the strategy's own measure of conviction.
Nothing else trades.

## Why this strategy

The honest answer first: **the strategy is not the contribution.** Dual moving
average crossover is one of the oldest, most-published trend-following rules in
existence. We are not claiming edge from it.

It was chosen for four properties that matter more here than alpha:

**1. It is fully explainable.** Every signal reduces to two numbers a judge can
check by eye. There is no fitted model, no opaque feature vector, no "the LLM
thought it looked good." Each signal carries its own reasoning string:

> *5-day average (182.40) is 2.8% above the 20-day (177.43) — momentum is up,
> sizing at 2.4x base on that separation.*

**2. It trades often enough to generate a P&L record in days, not months.** A
mean-reversion or value strategy might not fire once in a five-day window. This
one turns over across ten symbols on daily bars.

**3. Its natural failure modes are exactly the behavioral ones under test.**
This is the important one, and it isn't a coincidence — see below.

**4. It needs only closing prices.** Free-tier IEX daily bars are enough. No
fundamentals, no options chain, no alternative data.

## Why momentum is the right foil for a behavioral guardrail

A behavioral guardrail is only interesting if the thing it guards actually
misbehaves. Momentum strategies misbehave in precisely the three ways the rules
detect, and they do it for structural reasons rather than because we rigged it:

| Failure mode | Why momentum produces it |
|---|---|
| **Oversized position** | Conviction scaling. A strong trend produces a large separation between the averages, which produces a large position — at the exact moment the trend is most extended and most likely to mean-revert. |
| **Overtrading** | Crossover signals whipsaw. A symbol oscillating around its own moving average generates a buy, then a sell, then a buy, each of which looks locally justified. |
| **Revenge trading** | Rotation. The loop exits a decaying trend and immediately deploys the freed capital into the next strongest name, within the same cycle. Structurally identical to a human "making it back". |

So the demo is not a strawman. The agent genuinely wants to do these things, and
the guardrail genuinely stops it. `tests/test_strategy.py::test_conviction_scaling_can_exceed_the_guardrail_ceiling`
asserts that the strategy *can* propose an oversized position — if it couldn't,
the guardrail would have nothing to catch and the whole project would be
theatre.

## Signal generation

For each symbol in the universe, plus anything currently held:

```
short_ma   = mean of the last 5 daily closes
long_ma    = mean of the last 20 daily closes
spread_pct = (short_ma - long_ma) / long_ma
```

| Condition | Action |
|---|---|
| `spread_pct > 0` and no position | **Buy** |
| `spread_pct < 0` and position held | **Sell** the full position |
| anything else | Hold |

Fewer than 20 closes available → no signal for that symbol. The agent records
`no data` rather than computing an average over whatever it happens to have.

**Long-only.** A negative spread with no position does nothing. Shorting is a
different risk profile and would need borrow handling, so it's out of scope.

**Full exits.** No partial scaling out. It keeps the FIFO behavior-gap
calculation clean and the demo legible.

## Position sizing

```
conviction      = min(1 + spread_pct / 0.02, 3.0)
target_notional = portfolio_value × 0.08 × conviction
qty             = floor(target_notional / price)
```

A 2% separation between the averages counts as one full step of extra
conviction. So:

| Spread | Conviction | Target size |
|---|---|---|
| 0.5% | 1.25× | 10% of portfolio |
| 2% | 2.0× | 16% of portfolio |
| 4% | 3.0× | 24% of portfolio |
| 8% | 3.0× (capped) | 24% of portfolio |

**The 24% ceiling deliberately sits above the guardrail's 15% limit.** This is
the single most important design choice in the strategy. Conviction scaling is a
real, widespread, professionally respectable technique — and it is exactly the
mechanism by which a disciplined system talks itself into a position too large
to hold through a drawdown. Leaving the ceiling above the guardrail's limit is
what makes the guardrail load-bearing rather than decorative.

Guards on top:

- `qty < 1` or notional under $50 → no trade.
- Notional above available buying power → sized down.
- **Notional above remaining exposure headroom → sized down.** Total capital at
  work is capped at 100% of portfolio value, tracked across the whole cycle so
  three buys in one pass can't collectively breach it.

### Why the exposure cap exists separately

Buying power on a paper account is roughly **4× portfolio value** — the live
account showed $388,466 against $100,000. So "can I afford this?" is almost never
the binding question, and a strategy that only asks it will lever itself by
accumulation. The first live signal set proposed four buys totalling $69,614 on a
$100,000 book, with nothing watching the total.

`OverexposureRule` is the authority here and covers manual and MCP-submitted
trades too. The strategy's own ceiling
(`STRATEGY_MAX_TOTAL_EXPOSURE_PCT`) is a courtesy that keeps it from
re-proposing rejected buys every 15 minutes once the book is full, which would
bury the journal in blocks and distort the guardrail-impact metric. **The two
values must be kept in step** — see ADR-021.

## Universe

```
AAPL  MSFT  NVDA  AMZN  GOOGL  META  TSLA  AMD  NFLX  JPM
```

Ten names, configurable via `AGENT_UNIVERSE`. Chosen for liquidity and IEX data
availability, not for any view on the companies. Nine tech-adjacent names plus
one bank is **deliberately concentrated** — it makes correlated signals likely,
which is itself an interesting stress on the guardrail. It is not a diversified
portfolio and isn't presented as one.

Anything currently held is always evaluated even if it has dropped out of the
configured universe — otherwise the agent could never exit a position after a
config change.

## Execution

- **Market orders, day time-in-force.** No limit orders: the strategy trades
  daily signals, so a few basis points of slippage is noise, and unfilled limits
  would leave the position state ambiguous.
- **Cycle interval:** 15 minutes (`AGENT_INTERVAL_SECONDS`).
- **Per-cycle cap:** 3 orders (`AGENT_MAX_TRADES_PER_CYCLE`). Exits are ordered
  before entries so a sell frees buying power for a buy in the same pass, and
  within the entries the highest-conviction signals survive the cap.
- **Market-hours gated.** Every cycle checks Alpaca's clock first and stands
  down when closed. A clock failure counts as closed — the agent will not trade
  blind.

Note that daily bars only change once per session, so a 15-minute interval
mostly re-confirms the same signals. That is intentional: the loop's job is to
act on a signal promptly and to keep the account state fresh, not to find
intraday edge.

## The guardrail's authority over the strategy

When a human is present, a flagged trade is a question. **When the agent is
alone, a flagged trade is a no.** The agent has no override.

That asymmetry is the point. A human's autonomy is theirs to keep — the product
adds friction, never a block (ADR-002 rationale in
[DECISIONS.md](DECISIONS.md)). An autonomous agent has no autonomy worth
protecting, so the guardrail is absolute over it.

Every blocked trade is recorded with its price, and priced later against the
market to answer: *what would that trade have done?* That's the
`GET /journal/guardrail-impact` figure — restraint measured in dollars rather
than asserted.

## Known weaknesses

Stated plainly, because a strategy presented without them isn't credible:

- **Crossover strategies whipsaw in range-bound markets.** In a choppy tape this
  agent will buy high and sell low repeatedly. The overtrading rule limits the
  bleed but does not prevent it.
- **No stop losses.** A position is held until the moving averages cross back,
  which can be a long way down.
- **No volatility adjustment.** 8% of the portfolio in a utility and 8% in a
  high-beta semiconductor are treated as the same bet. Sizing on conviction
  without sizing on volatility is a real gap.
- **No correlation awareness.** Nine correlated tech names can all signal at
  once. The per-cycle cap accidentally mitigates this; nothing addresses it
  deliberately.
- **Daily bars, so slow to react.** A gap-down is acted on the following session
  at the earliest.
- **Five trading days of live P&L is noise, not evidence.** With ~5 sessions of
  history, the P&L figure measures luck. What the run *does* demonstrate is that
  the agent trades autonomously, the guardrail intervenes on real proposals, and
  both outcomes are recorded and priced. That's the claim being made — not that
  momentum crossover beats the market.

## What would improve it

In rough order of expected value: volatility-scaled sizing (replace the flat
8%), a trailing stop, correlation-aware position limits, and a longer-horizon
confirmation filter to suppress whipsaw. None of these are in scope before the
submission deadline; see the roadmap in [STATUS.md](STATUS.md).
