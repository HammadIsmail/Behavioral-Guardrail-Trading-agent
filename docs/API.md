# API reference

Interactive docs at `/docs` when the server is running — that's generated from
the code and is authoritative for exact field types. This file covers contracts,
semantics, and error behavior that OpenAPI doesn't express.

Base URL in development: `http://127.0.0.1:8000`

---

## JSON API

### `POST /trades/propose`

Evaluate a trade, get a decision and explanation, and log it to the journal. **Does
not place an order.**

**Body**

```json
{ "symbol": "NVDA", "qty": 200, "side": "buy" }
```

`side` is `"buy"` or `"sell"`. `qty` must be > 0.

**Response `200`**

```json
{
  "journal_entry_id": "3f9a2c...",
  "proposal": { "symbol": "NVDA", "qty": 200, "side": "buy" },
  "result": {
    "approved": false,
    "flags": [
      {
        "rule_name": "oversized_position",
        "triggered": true,
        "reason": "this trade is about 34% of your portfolio — well above a typical 15% single-position guideline"
      },
      { "rule_name": "revenge_trade", "triggered": false, "reason": "" },
      { "rule_name": "overtrading", "triggered": false, "reason": "" }
    ],
    "explanation": "This would put about a third of your account into one name…",
    "reference_price": 172.4
  }
}
```

Notes:

- `flags` contains **every** rule's verdict, not only the triggered ones. Filter
  on `triggered` for display.
- `approved` is `true` iff no flag triggered.
- `reference_price` is `null` when the symbol couldn't be priced — size rules
  stood down.
- `explanation` is the only LLM-generated field. It never affects `approved`.
- Keep `journal_entry_id` and pass it to execute so the same journal entry is
  updated rather than a second row created.

### `POST /trades/execute`

Re-evaluate, then submit to Alpaca as a market order.

**Body:** same `TradeProposal` shape.

**Query parameters** — note these are query params, not body fields:

| Param | Type | Default | Meaning |
|---|---|---|---|
| `override` | bool | `false` | Proceed even if flagged |
| `journal_entry_id` | string | — | Update this entry instead of creating one |

```bash
curl -X POST "http://127.0.0.1:8000/trades/execute?override=true&journal_entry_id=3f9a2c" \
  -H "Content-Type: application/json" \
  -d '{"symbol": "NVDA", "qty": 200, "side": "buy"}'
```

**Response `200` — refused**

```json
{
  "executed": false,
  "reason": "flagged_awaiting_confirmation",
  "journal_entry_id": "3f9a2c...",
  "guardrail_result": { "approved": false, "flags": [ ... ] }
}
```

**Response `200` — executed**

```json
{
  "executed": true,
  "journal_entry_id": "3f9a2c...",
  "guardrail_result": { "approved": true, "flags": [ ... ] },
  "order": {
    "order_id": "b1c4…",
    "symbol": "NVDA",
    "qty": 200.0,
    "side": "buy",
    "status": "accepted",
    "submitted_at": "2026-08-30T14:02:11.123456+00:00"
  }
}
```

Semantics that matter:

- **The guardrail re-runs here** against fresh account state. A verdict from
  propose is never reused (ADR-002).
- A refusal is `200` with `executed: false`, not a `4xx` — needing confirmation
  is a normal outcome, not a client error.
- `was_overridden` in the journal is derived as
  `(not approved) and override`. Passing `override=true` on a clean trade
  records nothing special.
- `status` is Alpaca's order status (`accepted`, `filled`, …), not our verdict.

### `GET /journal/entries`

Every logged decision, oldest first.

```json
[
  {
    "id": "3f9a2c...",
    "timestamp": "2026-08-30T14:01:55.000000+00:00",
    "symbol": "NVDA",
    "qty": 200.0,
    "side": "buy",
    "guardrail_result": { "approved": false, "flags": [ ... ] },
    "was_overridden": true,
    "executed": true,
    "cancelled": false,
    "price": 172.4,
    "status": "overridden"
  }
]
```

`status` is computed, not stored — one label so clients don't re-derive it:

| `status` | Means |
|---|---|
| `clean` | Proposed, passed, not yet acted on |
| `flagged` | Proposed, flagged, not yet acted on |
| `executed` | Passed and submitted |
| `overridden` | Flagged and submitted anyway |
| `cancelled` | Flagged and the user backed off |

`guardrail_result` is nullable — an entry created outside the normal flow may
have no recorded decision. Clients must handle `null`.

### `GET /journal/summary`

```json
{
  "proposals": 5,
  "executed_trades": 3,
  "cancelled_trades": 1,
  "flagged_trades": 2,
  "overridden_trades": 1,
  "clean_trades": 3
}
```

`clean_trades + flagged_trades` counts only entries that carry a decision, so it
can be less than `proposals`.

### `GET /journal/behavior-gap`

```json
{
  "passive_cost": 1000.0,
  "passive_value": 1200.0,
  "passive_pl": 200.0,
  "realized_pl": -100.0,
  "unrealized_pl": 0.0,
  "actual_pl": -100.0,
  "gap": 300.0,
  "executed_trades": 2,
  "unpriced_symbols": []
}
```

- `gap = passive_pl − actual_pl`. **Positive means selling cost money.**
- Zero when nothing has been sold — that's a real property, not an empty state.
- `executed_trades` counts only trades that could be priced and included.
- `unpriced_symbols` lists symbols excluded for lack of market data. A
  non-empty list means the numbers are partial.

Maths and worked example: [BEHAVIORAL_RULES.md](BEHAVIORAL_RULES.md).

### `GET /account`

```json
{
  "account_id": "a1b2…",
  "buying_power": 200000.0,
  "cash": 100000.0,
  "portfolio_value": 100000.0,
  "equity": 100000.0,
  "positions": [
    {
      "symbol": "AAPL",
      "qty": 10.0,
      "market_value": 2200.0,
      "unrealized_pl": 150.0,
      "current_price": 220.0
    }
  ]
}
```

### `GET /health`

```json
{ "status": "ok" }
```

Liveness only — it does not check Alpaca or Groq connectivity.

---

## HTML routes

### `GET /`

The dashboard. Renders account state, the proposal box, the journal and the
behavior gap panel.

### `/fragments/*`

htmx fragment endpoints. They return HTML partials, not JSON, and are not
intended as a public API — they exist to serve the dashboard.

| Route | Form fields | Returns |
|---|---|---|
| `POST /fragments/trades/propose` | `message` | verdict card, or parse-error card |
| `POST /fragments/trades/execute` | `symbol`, `qty`, `side`, `override`, `journal_entry_id` | execution result, or the flagged card if refused |
| `POST /fragments/trades/cancel` | `journal_entry_id` | cancellation card |
| `GET /fragments/journal` | — | behavior gap panel + journal list |

`override` is a string here (`"true"` / `"false"`) because it arrives from
`hx-vals`. It's parsed against `{"true", "1", "yes", "on"}`.

Responses that change journal state set `HX-Trigger: journalUpdated`, which
makes `#journal-list` re-fetch.

The same guardrail rules apply on `/fragments/trades/execute` as on the JSON
endpoint — it is not a privileged path.

---

## Error behavior

| Condition | Behavior |
|---|---|
| Invalid body (`qty <= 0`, bad `side`) | `422` from Pydantic |
| Unparseable natural-language message | Fragment route returns the parse-error card with no execute button. The JSON API doesn't parse text. |
| Flagged trade without override | `200`, `executed: false`, `reason: flagged_awaiting_confirmation` |
| Groq unavailable / rate-limited / no key | `200`. Deterministic fallback text in `explanation`. **Decision unaffected.** |
| Market data unavailable | `200`. `reference_price: null`, size rules stand down. |
| Alpaca rejects the order | Fragment route shows the message. **The JSON route currently surfaces this as a `500`** — see [STATUS.md](STATUS.md). |
| Alpaca account/orders call fails | `httpx` raises → `500`. The guardrail can't run without account state, so this is a genuine failure. |

Design principle: an optional dependency failing degrades the response
(ADR-007); a required one failing is an honest error.
