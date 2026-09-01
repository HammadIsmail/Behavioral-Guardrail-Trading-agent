# Behavioral rules & the behavior gap

The domain knowledge behind the code: what each rule detects, why the threshold
is where it is, and how the behavior gap is computed.

**Thresholds live in code** as class constants on each rule. If this document
and the code disagree, the code is right.

---

## Interface

Every rule implements:

```python
class GuardrailRule(ABC):
    name: str

    @abstractmethod
    def check(self, ctx: RuleContext) -> RuleFlag: ...
```

`RuleContext` carries everything a rule may use:
| Field | Source |
|---|---|
| `proposal` | The trade being evaluated |
| `account` | Live `AccountSnapshot` including positions |
| `recent_orders` | Raw Alpaca order dicts, most recent 20 |
| `reference_price` | Resolved price for the symbol, or `None` |

Rules do no I/O (ADR-003). A trade is approved iff **no** rule triggers.

---

## `oversized_position`

**Bias:** overconfidence / position-sizing on conviction rather than a plan.

A trader who is *sure* about a name puts too much of the account into it. The
conviction is often correct about direction and still ruinous about size — a
single position large enough to matter is a single position large enough to
force a panic exit when it moves against you.

**Triggers when:** a **buy** would exceed **15%** of portfolio value.

```
trade_value = qty × reference_price
trade_value / portfolio_value > 0.15
```

**Why 15%.** Roughly 6–7 equal positions, which is the low end of conventional
single-name concentration guidance for a self-directed equity account. Low
enough to catch genuine over-concentration, high enough that a normally-sized
position in a small account doesn't trip it constantly. Blunt but defensible —
it is not a risk model.

**Stands down when:**
- The trade is a **sell**. Exiting reduces concentration; flagging it would
  punish the behavior we want (ADR-005).
- `reference_price` is `None`. No honest way to size the trade, so no flag
  (ADR-004).
- `portfolio_value <= 0`.

**Price resolution** (in `GuardrailService._resolve_price`):
1. The symbol's `current_price` from the held position, if held — free, already
   in the account payload.
2. Latest trade from Alpaca's market data API, IEX feed.
3. `None`.

**Known limitations.** Compares against *this trade's* notional, not the
resulting total position — buying 14% three times passes three times. Ignores
volatility entirely; 15% of a utility and 15% of a biotech are not the same risk.

**Tests:** `tests/test_guardrail_rules.py::TestOversizedPosition`

---

## `overexposure`

**Bias:** the aggregate version of overconfidence — leverage arrived at by
accumulation rather than decision.

Nobody decides to be 240% invested. They make seven individually sensible
purchases. `oversized_position` caps any *single* trade at 15% of the book and
says nothing about the seventh such trade, which is a real blind spot: an Alpaca
paper account carries roughly **4× buying power**, so a strategy that only checks
affordability will happily stack reasonable positions into a materially levered
one.

**Triggers when:** a **buy** would push total capital at work past **100%** of
portfolio value.

```
current  = Σ |position.market_value|
total_pct = (current + qty × reference_price) / portfolio_value > 1.00
```

**Why 100%.** It's the one threshold that needs no justification: don't invest
more than you have. Everything above it is borrowed money, and leverage amplifies
a bad stretch exactly as much as a good one — which is the whole behavioral
problem restated at the portfolio level.

**Stands down when:** the trade is a **sell** (exiting reduces exposure by
definition), `reference_price` is `None`, or `portfolio_value <= 0`.

Uses `abs(market_value)` so a short position counts as exposure rather than
netting against a long.

**Why this was added.** It wasn't in the original three. It surfaced from live
data: a real paper account showed **$100,000 portfolio value against $388,466
buying power**, and the strategy's first live signal set proposed four buys
totalling **$69,614 — 70% of the book in a single cycle.** Every one of those
trades individually passed or failed on its own merits; nothing was watching the
total. See ADR-021.

**Known limitations.** Uses `market_value`, so a position that has run up
consumes more headroom than it cost — correct for solvency, but it means a
winning book throttles new entries. No notion of correlation: 100% spread across
ten names and 100% in one sector are treated identically.

**Tests:** `tests/test_guardrail_rules.py::TestOverexposure`

---

## `revenge_trade`

**Bias:** loss aversion expressed as revenge trading.

After closing a position, especially at a loss, the urge to "make it back"
produces a fast, unplanned re-entry. The tell is timing: a buy that follows an
exit closely enough that it can't have been reconsidered.

**Triggers when:** the proposal is a **buy** and any order in `recent_orders`
was a **filled sell** within the last **30 minutes**.

**Why 30 minutes.** Long enough to cover the impulsive window after an exit,
short enough not to flag a trader who sold in the morning and is making an
unrelated decision after lunch.

**Stands down when:** the proposal is a sell, or no filled sell falls inside the
window. Orders without a `filled_at` are ignored (an unfilled order isn't a
completed action). A malformed timestamp is skipped rather than allowed to raise.

**Known limitation — this is a timing heuristic, not loss detection.**

It fires on *any* recent sell, profitable or not. An Alpaca order object carries
no realized P&L, so the rule has no way to know whether the exit was a loss.
Consequences:

- False positives: taking a profit and then buying something unrelated.
- The reason text says "you sold within the last 30 minutes", deliberately —
  it does not claim to know you lost money.

Making it loss-aware needs cost-basis tracking. The journal now records a price
per executed trade, so the data is beginning to exist — but rules can't read the
journal (ADR-003), so it would need to arrive on `RuleContext`. Tracked in
[STATUS.md](STATUS.md).

**Tests:** `tests/test_guardrail_rules.py::TestRevengeTrade`

---

## `overtrading`

**Bias:** overtrading / illusion of control.

Activity feels like progress. Beyond a handful of decisions an hour, a trader is
almost certainly reacting to price movement rather than executing a thesis, and
every round trip pays the spread.

**Triggers when:** **5 or more** orders filled within the last **1 hour**.

Applies to buys and sells alike — pace is the signal, not direction.

**Why 5/hour.** A deliberate trader making five separate decisions in an hour is
unusual. It's a pace threshold, not a cost threshold.

**Known limitations.** Counts fills, so one intent split across partial fills
inflates the count. Uses a fixed 1-hour lookback, not a rolling day. Doesn't
distinguish opening from closing.

**Tests:** `tests/test_guardrail_rules.py::TestOvertrading`

---

## Rules deliberately not built

| Rule | Why not yet |
|---|---|
| Panic sell | Needs market context (is this position down? is the market down today?). Flagging large sells on size alone punishes rebalancing — see ADR-005. |
| Averaging down into a loser | Needs cost basis per position; Alpaca gives unrealized P&L per position, so this is the most tractable next rule. |
| Trading outside market hours | Cheap to add, weak behavioral signal on its own. |
| Concentration across correlated names | Needs sector/correlation data we don't fetch. |

---

## The behavior gap

The product's payoff number. `services/behavior_gap.py`, exposed at
`GET /journal/behavior-gap`.

### What it compares

| Side | Definition |
|---|---|
| **Passive** | Every buy you made, still held today, valued at the current price |
| **Actual** | Realized P&L from your sells + unrealized P&L on what's still open |
| **Gap** | `passive_pl − actual_pl`. Positive means selling cost you money |

### How it's computed

Only entries with `executed=True` and a recorded `price` count. Entries are
sorted by timestamp, then per symbol:

- **A buy** adds `qty × price` to `passive_cost`, `qty × current_price` to
  `passive_value`, and pushes a lot `[qty, price]` onto that symbol's FIFO queue.
- **A sell** consumes the oldest lots, realizing `matched_qty × (sell_price −
  lot_price)` against each.
- **Leftover open lots** are valued at the current price for `unrealized_pl`.
- **A sell with no matching lot** (a position opened before the journal existed)
  contributes nothing — no cost basis, so no fabricated profit.
- **A symbol with no current price** is skipped and reported in
  `unpriced_symbols`.

### The property that makes it meaningful

**If you never sell, the gap is exactly zero.** Both sides compute the same
number: `passive_pl` values every buy at the current price, and `unrealized_pl`
values every still-open lot at the same price against the same cost.

So a non-zero gap is attributable *purely to selling decisions* — which is
precisely the claim the product makes. This is why FIFO was chosen over average
cost (ADR-008), and it's asserted in
`tests/test_behavior_gap.py::test_never_selling_means_no_gap`.

### Worked example

```
Buy  10 NVDA @ $100      →  cost basis 10 @ $100
Sell 10 NVDA @  $90      →  realized = 10 × (90 − 100) = −$100
Price now $120

passive_cost   = 10 × 100 = $1,000
passive_value  = 10 × 120 = $1,200
passive_pl                =   $200     ← had they simply held

realized_pl               =  −$100
unrealized_pl             =     $0     ← nothing left open
actual_pl                 =  −$100     ← what actually happened

gap = 200 − (−100)        =   $300     ← the cost of that exit
```

The dashboard renders this as: *"Selling cost you $300 versus sitting still."*

A negative gap is possible and honest — the exit beat holding. The UI says so
rather than hiding it.

### Known limitations

- **Every buy is valued at the current price**, not a time-weighted baseline. A
  buy from an hour ago and one from last week are treated identically.
- **Only journal-visible trades count.** Pre-existing positions have no recorded
  basis.
- **Not tax-lot accurate.** FIFO here is a modelling choice for the zero-gap
  property, not an accounting one.
- **Resets with the process** — the journal is in-memory (ADR-012).

---

## The guardrail's impact

The behavior gap measures the cost of *selling*. This measures the value of
*not buying* — the trades the guardrail stopped.

`services/behavior_gap.py::compute_guardrail_impact`, exposed at
`GET /journal/guardrail-impact`.

Only the autonomous agent produces blocked trades: it has no override, so a
flagged signal simply doesn't execute (ADR-015). A human's declined trade is
`cancelled` instead, and isn't counted here — a person reconsidering and a
machine being restrained are different events.

### How it's computed

For each blocked **buy** with a recorded price:

```
avoided_cost += qty × blocked_price          # capital never deployed
avoided_pl   += qty × (current_price − blocked_price)
savings       = −avoided_pl
```

`savings` positive means the blocked trades would have lost money, so standing
down was worth something.

Blocked **sells** are counted but given no P&L figure. Declining to sell left the
position on, and that position's outcome is *already* inside the account's real
P&L — attributing it here as well would double count and inflate the guardrail's
apparent contribution (ADR-020).

Blocks are also attributed to the rules that caused them, so you can see which
behavior the agent is actually prone to.

### Worked example

```
Agent proposed: buy 100 NVDA @ $180  →  blocked (oversized_position, 24% of book)
Price now $165

avoided_cost = 100 × 180        = $18,000    capital not deployed
avoided_pl   = 100 × (165 − 180) = −$1,500   what the trade would have produced
savings                          =  $1,500   the value of standing down
```

Dashboard: *"Those blocked trades would have lost $1,500. The guardrail earned
its keep."*

### When restraint costs money

If the price had gone to $200, `savings` would be `−$2,000` and the UI says so:
*"Those blocked trades would have made $2,000. Restraint cost money this time."*

Reporting only the flattering direction would make this marketing rather than
measurement. Over a five-day window the figure is dominated by noise either way.

### Known limitations

- **Blocked sells carry no figure**, so the metric understates the guardrail when
  blocking an exit was the right call.
- **Values every block at today's price**, so a block from four days ago and one
  from this morning are treated identically.
- **A blocked trade changes what happens next.** Capital not deployed into a
  blocked buy stays available for the next signal, which may itself have been
  executed. The counterfactual prices the blocked trade in isolation and doesn't
  model that knock-on — a limitation shared with essentially every simple
  counterfactual of this shape.

---

## Adding a rule

Full walkthrough in [CONVENTIONS.md](CONVENTIONS.md). The short version:

1. New class in `guardrail_rules.py` implementing `check(ctx) -> RuleFlag`.
2. Add it to `ALL_RULES`.
3. If it needs data not on `RuleContext`, add the field and resolve it in
   `GuardrailService` — **never fetch inside the rule**.
4. Write the `reason` as something a stressed human can read: name the pattern,
   don't scold.
5. Add a test class in `tests/test_guardrail_rules.py` covering trigger,
   non-trigger, and the missing-data case.
6. Document it here with its threshold and rationale.
