# Demo script

A run-through for the submission video and for a live walkthrough. Roughly 4
minutes. Every command is real — nothing here is mocked.

Judging criteria this is built to hit: **P&L Performance**, **Technology
Implementation**, **Creativity & Originality**, **Presentation & Execution**.

---

## Before you record

Do these the day before, not an hour before.

```bash
pip install -r requirements.txt
cp .env.example .env          # then fill in your Alpaca paper + Groq keys
python -m pytest tests -q     # expect: 66 passed
python run.py                 # leave running — the agent trades on its own
```

Checklist:

- [ ] Server has been running through **at least one full market session**, so
      the journal holds real trades and the P&L figure isn't zero
- [ ] `python cli.py status` shows a non-zero cycle count
- [ ] `python cli.py impact` shows at least one blocked trade — if the guardrail
      has never fired, use the manual override path in Act III instead
- [ ] `journal.db` exists and has history (`python cli.py journal`)
- [ ] Browser at `http://127.0.0.1:8000`, zoomed to ~125% so text is legible on
      video

**Timing matters.** P&L history needs market hours to accumulate. Start the
agent on the first available trading day and leave it running.

---

## Act I — the problem (30s)

Talk over the dashboard, no clicking.

> Retail traders don't lose to the market because they pick bad stocks. DALBAR's
> 2024 numbers: the average equity investor made 16.5% while the S&P 500
> returned 25%. That 850 basis point gap isn't stock selection — it's panic
> selling, revenge trading, and positions sized on emotion.
>
> Every AI trading tool I looked at attacks stock selection. So we asked a
> different question: **if we're now building autonomous agents to trade, do
> they inherit the same behavioral pathologies?** They do. So we built an agent
> that polices its own.

## Act II — the agent is actually trading (60s)

Point at the **Autonomous agent** card.

> This is running unattended. Every 15 minutes it wakes up, reads daily bars for
> ten large caps, computes a 5-day and a 20-day moving average, and decides.

Point at the four counters — cycles, proposed, executed, **blocked**.

> Note the last one. Of the trades this agent wanted to make, that many were
> stopped — by its own guardrail.

Scroll to **Strategy signals** and expand *What the strategy saw*.

> It shows its work. Every symbol, both averages, and the verdict. Nothing here
> is a black box — no LLM decided any of this.

Read one signal's reasoning aloud:

> *"5-day average is 2.8% above the 20-day — momentum is up, sizing at 2.4x base
> on that separation."*
>
> That last part is the interesting bit. The strategy scales position size on
> conviction. Which is a completely respectable technique — and it's exactly how
> a disciplined system talks itself into a position too big to hold.

Now hit **Run one cycle now** and let it complete on camera.

## Act III — the guardrail intervenes (60s)

Scroll to the journal. Find a `blocked` entry and read both lines — the signal
reason, then the rule reason.

> The strategy wanted this. The guardrail said no, and said why: *this trade is
> about 24% of your portfolio — well above a typical 15% single-position
> guideline.*
>
> When a human is at the keyboard, that's a question — confirm or cancel, we
> never block anyone. When the agent is alone, it's a no. It has no override.

If nothing is blocked yet, force it live in the trade box:

```
buy 400 shares of NVDA
```

> Same guardrail on the manual path. It names the bias, and offers me the choice
> the agent doesn't get.

Click **Proceed anyway**, then show the journal row labelled `overridden`.

> Overriding is always allowed and always recorded. Nothing is silently
> prevented, and nothing is silently forgiven.

## Act IV — the numbers (60s)

Scroll to **What the guardrail bought you**.

> Here's the part I'd point a judge at. Every trade the guardrail stopped is
> priced at today's market to answer: what would it have done?

Read the savings figure.

> Those blocked trades would have lost $X. That's the guardrail's contribution
> to P&L — restraint, in dollars, not as a claim.

Then the **Behavior gap** panel.

> And this is the agent's own DALBAR gap. What it would have earned holding
> everything untouched, versus what its actual in-and-out trading earned.
>
> There's a property worth knowing: if you never sell, this number is exactly
> zero — both sides compute the same thing. So any non-zero gap is caused purely
> by selling decisions. It isolates timing from selection.

Be honest about the horizon:

> Five trading days of P&L is noise, and I'd rather say so than dress it up.
> What this demonstrates isn't that moving-average crossover beats the market.
> It's that the agent trades autonomously, the guardrail intervenes on real
> proposals, and both outcomes are priced and recorded.

## Act V — it's a service, not just an app (45s)

Terminal:

```bash
python cli.py status
python cli.py impact
```

> Full CLI over the same API.

Then the MCP angle — the strongest technology point:

```bash
python mcp_server.py
```

> And the guardrail is exposed as an MCP server. `evaluate_trade`,
> `execute_trade`, `get_behavior_gap`, `get_guardrail_impact`.
>
> Which means the behavioral check isn't locked inside our app. **Any** agent —
> Claude, a custom bot, someone else's hackathon entry — can route its trades
> through a behavioral guardrail before they reach a broker. That's the part I
> think outlives the hackathon.

## Close (15s)

> Deterministic rules decide. The LLM only phrases. It never touches
> approve-or-deny, so a rate limit can't change a trading decision — it just
> makes the explanation stiffer.
>
> Everything runs in Alpaca paper trading. The agent trades, the guardrail
> stops it when it's about to act on a bias, and both halves of that are
> measured.

---

## Commands cheat sheet

```bash
python run.py                          # server + autonomous agent
python cli.py status                   # account, agent, journal
python cli.py signals                  # what the strategy wants now
python cli.py run-once                 # force a cycle
python cli.py propose NVDA 400 buy     # guardrail check, no order
python cli.py execute NVDA 400 buy --override
python cli.py journal --limit 30
python cli.py gap                      # behavior gap
python cli.py impact                   # what the guardrail bought you
python mcp_server.py                   # MCP server over stdio
```

## If something breaks on camera

| Symptom | Cause | Say this / do this |
|---|---|---|
| `market closed` on the agent card | Weekend or outside 09:30–16:00 ET | Expected — show journal history from the last session instead |
| No signals at all | Momentum unchanged across the universe | Show *What the strategy saw* — holds are a decision too |
| Everything blocked, nothing executes | Overtrading rule tripped by the loop's own pace | This is the demo working. Say so. |
| Behavior gap is exactly `$0.00` | Nothing bought has been sold yet | Real property, not a bug — explain the zero |
| Stiff, templated explanations | Groq key missing or rate-limited | Perfect moment to make the point: the decision is unaffected |
| `reference_price: null`, oversized never fires | Market data unavailable | Check `python cli.py signals`; see V-3 in [STATUS.md](STATUS.md) |

## What to say if asked "where's the alpha?"

Don't claim any. The strategy is textbook dual moving-average crossover and
[STRATEGY.md](STRATEGY.md) says so in the first paragraph.

> The strategy is the thing being measured, not the contribution. The
> contribution is the layer that decides which of its trades shouldn't happen —
> and the fact we can put a dollar figure on that decision.
