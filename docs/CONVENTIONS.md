# Conventions

How to extend this codebase without breaking a boundary. Rationale in
[DECISIONS.md](DECISIONS.md); the boundaries themselves in
[ARCHITECTURE.md](ARCHITECTURE.md).

---

## Never do these

| Don't | Why |
|---|---|
| Let any LLM call influence `approved` | ADR-001 — the project's central claim |
| Make a network call inside a rule | ADR-003 — destroys testability |
| Trust a verdict, price, or override flag from a request body | ADR-002 — the check must run where the order is placed |
| Substitute a placeholder for missing data | ADR-004 — confident nonsense is worse than silence |
| Import a third-party SDK outside `services/` | One boundary per dependency |
| Construct a service inside a route | Providers live in `core/dependencies.py` |
| Put business logic in `api/` | Rule checks, prompts, parsing, storage → `services/` |
| Resolve a file path from the working directory | Breaks when uvicorn starts elsewhere |
| Let an exception from Groq or market data fail a request | ADR-007 — degrade, don't fail |

---

## Adding a behavioral rule

**1. Write the class** in `services/guardrail_rules.py`:

```python
class AveragingDownRule(GuardrailRule):
    """Flags adding to a position that's already underwater."""
    name = "averaging_down"
    LOSS_THRESHOLD_PCT = -0.10

    def check(self, ctx: RuleContext) -> RuleFlag:
        if ctx.proposal.side.value != "buy":
            return _clean(self.name)

        for position in ctx.account.positions:
            if position.symbol.upper() != ctx.proposal.symbol.upper():
                continue
            if position.market_value <= 0:
                return _clean(self.name)

            loss_pct = position.unrealized_pl / position.market_value
            if loss_pct <= self.LOSS_THRESHOLD_PCT:
                return RuleFlag(
                    rule_name=self.name,
                    triggered=True,
                    reason=(
                        f"you're already down {abs(loss_pct):.0%} on "
                        f"{position.symbol} — adding here doubles the bet on "
                        f"the same thesis"
                    ),
                )
        return _clean(self.name)
```

**2. Register it** in `ALL_RULES` at the bottom of the same file.

**3. Test it** — add a class to `tests/test_guardrail_rules.py` covering the
trigger case, the non-trigger case, and the missing-data case.

**4. Document it** in [BEHAVIORAL_RULES.md](BEHAVIORAL_RULES.md) with its
threshold and the reasoning behind that number.

Nothing else changes. No route, service, or schema edit is needed — that's
Open/Closed working.

### Rule conventions

- **Thresholds are class constants**, uppercase, on the rule itself. Never
  inline a magic number in `check()`, and never read one from settings — a rule
  should be readable as a single self-contained statement of a behavior.
- **Return `_clean(self.name)` for the not-triggered case**, not a bare
  `RuleFlag(...)`. Every rule returns a flag on every path; `triggered=False` is
  a verdict, not an absence.
- **Guard the direction first.** Most biases are asymmetric between buys and
  sells. Return early rather than nesting.
- **Stand down on missing data.** No price, no positions, empty account → return
  clean. Never guess an input.
- **Never raise.** Malformed upstream data (a bad timestamp, a missing field) is
  skipped for that item. One bad order must not fail an evaluation.

### Writing a `reason`

The reason string is shown to a stressed human, and it's also the fallback
explanation when Groq is unavailable — so it has to stand alone as prose.

- Lead with the observation, not the judgement: *"you've made 6 trades in the
  last hour"* before *"that pace is often when analysis turns into impulse"*.
- Second person, lowercase start (it's interpolated mid-sentence).
- Include the actual number. `f"{pct:.0%} of your portfolio"` beats "a large
  share".
- Name the pattern, don't diagnose the person. *"this could be reacting to
  that"* not *"you're revenge trading"*.
- No scolding, no risk disclaimers.

### Needing new data

Add a field to `RuleContext` and resolve it once in `GuardrailService`:

```python
@dataclass
class RuleContext:
    proposal: TradeProposal
    account: AccountSnapshot
    recent_orders: list[dict] = field(default_factory=list)
    reference_price: float | None = None
    market_is_open: bool | None = None      # new
```

```python
# guardrail_service.py
ctx = RuleContext(
    ...,
    market_is_open=await self._alpaca.get_clock(),
)
```

Optional with a safe default, so existing tests keep constructing `RuleContext`
without it.

---

## Adding an endpoint

Routes are wiring. If a route body starts making decisions, that logic belongs
in `services/`.

```python
@router.get("/positions/{symbol}")
async def get_position(
    symbol: str,
    alpaca: AlpacaClient = Depends(get_alpaca_client),
):
    snapshot = await alpaca.get_account_snapshot()
    return next(
        (p for p in snapshot.positions if p.symbol.upper() == symbol.upper()),
        None,
    )
```

- JSON endpoints → `api/trades.py` or `api/journal.py`, with a `response_model`.
- HTML fragments → `api/ui.py`, under `/fragments/*`, returning a partial.
- Services arrive via `Depends()`. Never `AlpacaClient(get_settings())`.
- Register new routers in `main.py`.

---

## Adding an external service

1. New file in `services/`. It is the **only** module allowed to import that
   SDK.
2. Provider function in `core/dependencies.py`, `@lru_cache`'d.
3. If it holds a connection pool, close it in `main.py`'s `lifespan`.
4. Never let its failure fail a request if the feature can degrade — return an
   empty result or a fallback (ADR-007).

```python
# core/dependencies.py
@lru_cache
def get_sentiment_service() -> SentimentService:
    return SentimentService(get_settings())
```

New settings go in `core/config.py` as fields with defaults, and into
`.env.example` with a comment saying where to get the value.

---

## Templates and htmx

### Every action button needs pending state

Copy this shape (ADR-011):

```html
<fieldset class="confirm-row">
  <button
    class="btn-primary"
    hx-post="/fragments/trades/execute"
    hx-vals='{"symbol": "{{ proposal.symbol }}", "journal_entry_id": "{{ journal_entry_id }}"}'
    hx-target="#trade-result"
    hx-swap="innerHTML"
    hx-disabled-elt="closest fieldset"
    hx-sync="closest fieldset:drop"
  >
    <span class="btn-spinner" aria-hidden="true"></span>
    <span class="btn-label">Execute (paper)</span>
  </button>
  <span class="pending-note">Submitting to Alpaca…</span>
</fieldset>
```

Non-negotiable parts:

- **`<fieldset>`, not `<div>`.** `hx-disabled-elt` sets the `disabled`
  attribute; that does nothing on a div, but on a fieldset the browser disables
  every control inside. This is what stops *Cancel* being clicked while
  *Proceed anyway* is in flight.
- **`hx-disabled-elt="closest fieldset"`** on every button in the group.
- **`hx-sync="closest fieldset:drop"`** — covers keyboard and race paths a
  disabled button doesn't.
- **Spinner span before the label span.** Styling is entirely CSS off htmx's
  `.htmx-request` class; no JS.
- New fieldsets need `border: 0; padding: 0; min-width: 0` in the stylesheet.

### Enum values in templates

Always `{{ entry.side.value }}`, never `{{ entry.side }}`. `OrderSide` is a
`str, Enum`, and on Python 3.11+ Jinja renders the bare enum as
`OrderSide.buy`.

### Refreshing the journal

Set `HX-Trigger: journalUpdated` on any response that changes journal state.
`#journal-list` listens for it and re-fetches `/fragments/journal`.

```python
response.headers["HX-Trigger"] = "journalUpdated"
```

### Never trust a Jinja context shape

A partial is rendered from more than one route. `trade_result.html` is rendered
by both propose and execute. If you pass a dict from one and a Pydantic model
from another, `.value` and `.attribute` access will silently differ — which is
exactly how the old parse-error path produced `side=""`. Render a different
partial instead.

---

## Testing

```bash
python -m pytest tests -q
```

- **Test services, not routes.** The layering means everything worth testing is
  reachable without FastAPI, HTTP, or mocks.
- **No mocking Alpaca or Groq.** If you find yourself needing to, the logic is
  probably in the wrong layer — push it down into a pure function.
- **Rules and `compute_behavior_gap` are pure**: build fake `RuleContext` /
  `JournalEntry` objects and assert on the result. Helper constructors at the
  top of each test file (`account()`, `proposal()`, `entry()`) keep cases to a
  few lines.
- **Cover the missing-data path** for anything that touches external data. That
  is where the real bugs have been.
- **Name the behavior, not the method:**
  `test_selling_out_of_a_big_position_is_not_oversized`, not
  `test_check_returns_false`.
- When a test encodes a load-bearing property, say so in a docstring — see
  `test_never_selling_means_no_gap`.

---

## Code style

Match what's there.

- **Type hints on everything public.** Modern syntax: `float | None`,
  `list[dict]`.
- **Async all the way down** for anything touching the network.
- **Docstrings explain *why*.** The signature already says what. A rule's
  docstring names the bias; a service's names the boundary it owns.
- **Comments earn their place** by recording a decision or a trap — *"feed=iex
  keeps this inside Alpaca's free data tier"*, *"fieldset, not div: disabling it
  disables every button inside"*. Don't narrate the code.
- **Private helpers get a leading underscore**, module-level ones too
  (`_parse_filled_at`, `_fallback`, `_clean`).
- Prose in comments and docstrings uses sentence case and em dashes; keep it
  readable rather than telegraphic.

---

## Secrets

- Real values in `.env` only. It's gitignored.
- Every new setting goes in `.env.example` with a placeholder and a comment.
- Never log a key, never put one in an error message, never commit one.
- `ALPACA_BASE_URL` must stay pointed at the paper endpoint.
