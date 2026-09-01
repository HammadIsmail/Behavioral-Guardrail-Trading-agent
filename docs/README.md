# Documentation index

These docs exist so an AI agent (or a new human contributor) can pick this
project up cold, in one session, without reverse-engineering intent from the
code.

`CLAUDE.md` in the repo root is loaded automatically every session. It carries
the short version and the invariants. **These files carry the depth.** Read them
on demand.

## Reading order for a new session

| # | If you need to know… | Read |
|---|---|---|
| 1 | What this product is and what "done" means | [PRD.md](PRD.md) |
| 2 | Why the code is shaped as it is — **read before changing structure** | [DECISIONS.md](DECISIONS.md) |
| 3 | What the agent trades, why, and how it sizes | [STRATEGY.md](STRATEGY.md) |
| 4 | How the pieces fit and what must stay true | [ARCHITECTURE.md](ARCHITECTURE.md) |
| 5 | What the rules detect and why those thresholds | [BEHAVIORAL_RULES.md](BEHAVIORAL_RULES.md) |
| 6 | How to add a rule, route, service or button safely | [CONVENTIONS.md](CONVENTIONS.md) |
| 7 | Endpoint shapes and error behavior | [API.md](API.md) |
| 8 | How to demo it | [DEMO.md](DEMO.md) |
| 9 | What works, what's unverified, what's next | [STATUS.md](STATUS.md) |
| 10 | What a term means here | [GLOSSARY.md](GLOSSARY.md) |

**Minimum viable context:** `CLAUDE.md` → `DECISIONS.md` → `STATUS.md`. Enough to
make a safe change. Add `ARCHITECTURE.md` before touching layering,
`BEHAVIORAL_RULES.md` before touching a rule, `STRATEGY.md` before touching the
strategy or its sizing.

**If you only read one thing:** `DECISIONS.md`. Several of its ADRs record
decisions that were *bugs first* — the guardrail bypass, the placeholder price,
the conviction ceiling. Without the rationale, a change optimising for
simplicity would plausibly reintroduce them.

## Which file owns what

Duplicated facts drift and then mislead. Each fact has exactly one home:

| Fact | Authoritative source |
|---|---|
| Product requirements, acceptance criteria | `docs/PRD.md` |
| Why a design choice was made | `docs/DECISIONS.md` |
| Strategy logic, sizing, universe, weaknesses | `docs/STRATEGY.md` |
| Layering, request flow, invariants | `docs/ARCHITECTURE.md` |
| Rule thresholds and rationale | `docs/BEHAVIORAL_RULES.md` |
| Behavior gap and guardrail impact maths | `docs/BEHAVIORAL_RULES.md` |
| How to extend the code | `docs/CONVENTIONS.md` |
| Endpoint contracts | `docs/API.md` |
| Demo script and talk track | `docs/DEMO.md` |
| Progress, limitations, roadmap | `docs/STATUS.md` |
| Terminology | `docs/GLOSSARY.md` |
| Setup, running, the pitch | `README.md` (root) |
| Always-on summary + hard invariants | `CLAUDE.md` (root) |

Thresholds and behavior live in code as class constants and settings. If a doc
and the code disagree, **the code is right and the doc is a bug** — fix the doc
in the same change.

## Keeping these current

When you change behavior, update the one doc that owns that fact in the same
change. In particular:

- New or changed rule → `BEHAVIORAL_RULES.md`
- Strategy or sizing change → `STRATEGY.md`
- New endpoint or MCP tool → `API.md`
- A design choice you'd have to explain twice → add an ADR to `DECISIONS.md`
- Anything finished, broken, or newly known-broken → `STATUS.md`

Superseded ADRs are kept rather than deleted, marked at the top and left
readable — ADR-012 is the worked example. The reasoning behind a decision that
stopped being true is still the fastest way to understand the one that replaced
it.
