# Documentation index

These docs exist so an AI agent (or a new human contributor) can pick this
project up cold, in one session, without reverse-engineering intent from the
code.

`CLAUDE.md` in the repo root is loaded automatically every session. It carries
the short version and the invariants. **These files carry the depth.** Read
them on demand.

## Reading order for a new session

| # | If you need to know… | Read |
|---|---|---|
| 1 | What this product is and what "done" means | [PRD.md](PRD.md) |
| 2 | Why the code is shaped the way it is — **read before changing structure** | [DECISIONS.md](DECISIONS.md) |
| 3 | How the pieces fit and what must stay true | [ARCHITECTURE.md](ARCHITECTURE.md) |
| 4 | What the rules actually detect and why those thresholds | [BEHAVIORAL_RULES.md](BEHAVIORAL_RULES.md) |
| 5 | How to add a rule, route, service or button without breaking a boundary | [CONVENTIONS.md](CONVENTIONS.md) |
| 6 | Endpoint shapes and error behavior | [API.md](API.md) |
| 7 | What works, what's unverified, what's next | [STATUS.md](STATUS.md) |
| 8 | What a term means in this codebase | [GLOSSARY.md](GLOSSARY.md) |

**Minimum viable context:** `CLAUDE.md` → `DECISIONS.md` → `STATUS.md`. That's
enough to make a safe change. Add `ARCHITECTURE.md` before touching layering
and `BEHAVIORAL_RULES.md` before touching a rule.

## Which file owns what

Duplicated facts drift and then mislead. Each fact has exactly one home:

| Fact | Authoritative source |
|---|---|
| Product requirements, acceptance criteria | `docs/PRD.md` |
| Why a design choice was made | `docs/DECISIONS.md` |
| Layering, request flow, invariants | `docs/ARCHITECTURE.md` |
| Rule thresholds and rationale | `docs/BEHAVIORAL_RULES.md` |
| Behavior gap maths | `docs/BEHAVIORAL_RULES.md` |
| How to extend the code | `docs/CONVENTIONS.md` |
| Endpoint contracts | `docs/API.md` |
| Progress, limitations, roadmap | `docs/STATUS.md` |
| Terminology | `docs/GLOSSARY.md` |
| Setup and running | `README.md` (root) |
| Always-on summary + hard invariants | `CLAUDE.md` (root) |

The actual thresholds live in code as class constants on each rule. If a doc
and the code disagree, **the code is right and the doc is a bug** — fix the
doc in the same change.

## Keeping these current

When you change behavior, update the one doc that owns that fact in the same
change. In particular:

- New or changed rule → `BEHAVIORAL_RULES.md`
- New endpoint → `API.md`
- A design choice you'd have to explain twice → add an ADR to `DECISIONS.md`
- Anything finished, broken or newly known-broken → `STATUS.md`
