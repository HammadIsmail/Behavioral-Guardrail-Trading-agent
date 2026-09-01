"""
Command-line interface for the guardrail agent.

    python cli.py status
    python cli.py signals
    python cli.py run-once
    python cli.py propose NVDA 200 buy
    python cli.py execute NVDA 200 buy --override
    python cli.py journal
    python cli.py gap
    python cli.py impact
    python cli.py start | stop

Talks to the running server over HTTP rather than importing the services
directly. That's deliberate: the autonomous loop lives in the server process,
so its status and counters are only truthful if we ask the process that owns
them. Start the server first with `python run.py`.
"""
import argparse
import sys

import httpx

DEFAULT_BASE_URL = "http://127.0.0.1:8000"

GREEN = "\033[32m"
RED = "\033[31m"
YELLOW = "\033[33m"
DIM = "\033[2m"
BOLD = "\033[1m"
RESET = "\033[0m"


def money(value: float) -> str:
    return f"${value:,.2f}"


def signed(value: float) -> str:
    colour = GREEN if value > 0 else RED if value < 0 else DIM
    sign = "+" if value > 0 else ""
    return f"{colour}{sign}{money(value)}{RESET}"


class Client:
    def __init__(self, base_url: str):
        self._base_url = base_url.rstrip("/")

    def request(self, method: str, path: str, **kwargs):
        try:
            response = httpx.request(
                method, f"{self._base_url}{path}", timeout=60.0, **kwargs
            )
        except httpx.ConnectError:
            sys.exit(
                f"{RED}Can't reach {self._base_url}{RESET}\n"
                f"Start the server first:  python run.py"
            )
        if response.status_code >= 400:
            sys.exit(f"{RED}{response.status_code}{RESET} {response.text}")
        return response.json()

    def get(self, path: str):
        return self.request("GET", path)

    def post(self, path: str, **kwargs):
        return self.request("POST", path, **kwargs)


# ---------- commands ----------


def cmd_status(client: Client, _args) -> None:
    account = client.get("/account")
    agent = client.get("/agent/status")
    summary = client.get("/journal/summary")

    print(f"\n{BOLD}Account{RESET}")
    print(f"  Portfolio value   {money(account['portfolio_value'])}")
    print(f"  Buying power      {money(account['buying_power'])}")
    print(f"  Open positions    {len(account['positions'])}")
    for position in account["positions"]:
        print(
            f"    {position['symbol']:<6} {position['qty']:>8.0f} @ "
            f"{money(position['current_price']):>10}   "
            f"{signed(position['unrealized_pl'])}"
        )

    if agent["loop_running"]:
        state = f"{GREEN}running{RESET}" if agent["market_open"] else f"{YELLOW}running (market closed){RESET}"
    elif agent["enabled"]:
        state = f"{YELLOW}enabled, not started{RESET}"
    else:
        state = f"{DIM}disabled{RESET}"

    print(f"\n{BOLD}Agent{RESET}  {state}")
    print(f"  Interval          {agent['interval_seconds'] // 60} min")
    print(f"  Universe          {', '.join(agent['universe'])}")
    print(f"  Cycles run        {agent['cycles_completed']}")
    print(
        f"  Proposed          {agent['total_proposed']}  "
        f"({GREEN}{agent['total_executed']} executed{RESET}, "
        f"{RED}{agent['total_blocked']} blocked{RESET})"
    )
    if agent.get("last_error"):
        print(f"  {RED}Last error{RESET}        {agent['last_error']}")

    print(f"\n{BOLD}Journal{RESET}")
    print(
        f"  {summary['proposals']} proposed · {summary['executed_trades']} executed · "
        f"{summary['blocked_trades']} blocked · {summary['cancelled_trades']} cancelled · "
        f"{summary['overridden_trades']} overridden"
    )
    print()


def cmd_signals(client: Client, _args) -> None:
    signals = client.get("/agent/signals")
    if not signals:
        print(f"\n{DIM}No signals — momentum is unchanged across the universe.{RESET}\n")
        return
    print(f"\n{BOLD}Current signals{RESET}")
    for signal in signals:
        colour = GREEN if signal["side"] == "buy" else RED
        print(
            f"\n  {colour}{signal['side'].upper():<4}{RESET} "
            f"{BOLD}{signal['symbol']}{RESET}  "
            f"{signal['qty']:.0f} @ ~{money(signal['price'])}  "
            f"({money(signal['qty'] * signal['price'])}, "
            f"{signal['conviction']:.1f}x conviction)"
        )
        print(f"       {DIM}{signal['reason']}{RESET}")
    print()


def cmd_run_once(client: Client, _args) -> None:
    result = client.post("/agent/run-once")
    if not result["market_open"]:
        print(f"\n{YELLOW}Market is closed{RESET} — cycle recorded, nothing traded.\n")
        return
    print(
        f"\n{BOLD}Cycle complete{RESET}\n"
        f"  {result['signals_generated']} signals · "
        f"{GREEN}{result['executed']} executed{RESET} · "
        f"{RED}{result['blocked']} blocked by guardrail{RESET}"
        + (f" · {result['skipped_cap']} held back by cap" if result["skipped_cap"] else "")
    )
    for error in result.get("errors", []):
        print(f"  {RED}error{RESET} {error}")
    print()


def cmd_propose(client: Client, args) -> None:
    result = client.post(
        "/trades/propose",
        json={"symbol": args.symbol.upper(), "qty": args.qty, "side": args.side},
    )
    verdict = result["result"]
    if verdict["approved"]:
        print(f"\n{GREEN}Clean{RESET} — no behavioral flags.")
    else:
        print(f"\n{RED}Flagged{RESET} — {', '.join(verdict['triggered_rules'])}")
        for flag in verdict["flags"]:
            if flag["triggered"]:
                print(f"  · {flag['reason']}")
    if verdict.get("explanation"):
        print(f"\n{DIM}{verdict['explanation']}{RESET}")
    print(f"\n{DIM}journal entry {result['journal_entry_id']}{RESET}\n")


def cmd_execute(client: Client, args) -> None:
    result = client.post(
        f"/trades/execute?override={str(args.override).lower()}",
        json={"symbol": args.symbol.upper(), "qty": args.qty, "side": args.side},
    )
    if not result["executed"]:
        print(f"\n{RED}Not executed{RESET} — {result['reason']}")
        for flag in (result.get("guardrail_result") or {}).get("flags", []):
            if flag["triggered"]:
                print(f"  · {flag['reason']}")
        print(f"\n{DIM}Re-run with --override to proceed anyway.{RESET}\n")
        return
    order = result["order"]
    tag = f" {YELLOW}(overridden){RESET}" if result["guardrail_result"] and not result["guardrail_result"]["approved"] else ""
    print(
        f"\n{GREEN}Submitted{RESET}{tag}  {order['side']} {order['qty']:.0f} "
        f"{order['symbol']} — status {order['status']}\n"
    )


def cmd_journal(client: Client, args) -> None:
    entries = client.get("/journal/entries")
    if not entries:
        print(f"\n{DIM}Journal is empty.{RESET}\n")
        return
    colours = {
        "executed": GREEN,
        "clean": GREEN,
        "blocked": RED,
        "overridden": YELLOW,
        "cancelled": DIM,
        "flagged": RED,
    }
    print(f"\n{BOLD}Journal{RESET}  ({len(entries)} entries)")
    for entry in entries[-args.limit :]:
        colour = colours.get(entry["status"], "")
        price = money(entry["price"]) if entry.get("price") else "—"
        print(
            f"  {entry['timestamp'][11:19]}  "
            f"{DIM}{entry['source']:<5}{RESET} "
            f"{entry['side']:<4} {entry['qty']:>6.0f} {entry['symbol']:<6} "
            f"@ {price:>10}  {colour}{entry['status']}{RESET}"
        )
    print()


def cmd_gap(client: Client, _args) -> None:
    gap = client.get("/journal/behavior-gap")
    if gap["executed_trades"] == 0:
        print(f"\n{DIM}No executed trades to compare yet.{RESET}\n")
        return
    print(f"\n{BOLD}Behavior gap{RESET}")
    print(f"  If it had held everything   {signed(gap['passive_pl'])}")
    print(f"  What the trading did        {signed(gap['actual_pl'])}")
    print(f"    realized                  {signed(gap['realized_pl'])}")
    print(f"    unrealized                {signed(gap['unrealized_pl'])}")
    print(f"  {BOLD}Gap{RESET}                         {signed(-gap['gap'])}")
    if gap["gap"] > 0:
        print(f"\n  {DIM}Selling cost {money(gap['gap'])} versus sitting still.{RESET}")
    elif gap["gap"] < 0:
        print(f"\n  {DIM}The exits beat holding by {money(abs(gap['gap']))}.{RESET}")
    else:
        print(f"\n  {DIM}Nothing bought has been sold — gap is exactly zero.{RESET}")
    print()


def cmd_impact(client: Client, _args) -> None:
    impact = client.get("/journal/guardrail-impact")
    if impact["blocked_trades"] == 0:
        print(f"\n{DIM}The guardrail hasn't stopped anything yet.{RESET}\n")
        return
    print(f"\n{BOLD}What the guardrail bought you{RESET}")
    print(f"  Trades stopped              {impact['blocked_trades']}")
    print(f"  Capital not deployed        {money(impact['avoided_cost'])}")
    print(f"  {BOLD}Net effect{RESET}                  {signed(impact['savings'])}")
    if impact["by_rule"]:
        rules = ", ".join(f"{name} ({n})" for name, n in impact["by_rule"].items())
        print(f"  Stopped by                  {rules}")
    if impact["savings"] > 0:
        print(
            f"\n  {DIM}Those trades would have lost "
            f"{money(impact['savings'])}.{RESET}"
        )
    elif impact["savings"] < 0:
        print(
            f"\n  {DIM}Those trades would have made "
            f"{money(abs(impact['savings']))} — restraint cost money this time.{RESET}"
        )
    print()


def cmd_start(client: Client, _args) -> None:
    result = client.post("/agent/start")
    print(f"\nloop_running: {result['loop_running']}\n")


def cmd_stop(client: Client, _args) -> None:
    result = client.post("/agent/stop")
    print(f"\nloop_running: {result['loop_running']}\n")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="cli.py", description="Alpaca Guardrail Agent CLI"
    )
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL, help=argparse.SUPPRESS)
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("status", help="account, agent and journal overview").set_defaults(
        func=cmd_status
    )
    sub.add_parser("signals", help="what the strategy wants right now").set_defaults(
        func=cmd_signals
    )
    sub.add_parser("run-once", help="run one autonomous cycle now").set_defaults(
        func=cmd_run_once
    )
    sub.add_parser("gap", help="behavior gap").set_defaults(func=cmd_gap)
    sub.add_parser("impact", help="what the guardrail bought you").set_defaults(
        func=cmd_impact
    )
    sub.add_parser("start", help="start the autonomous loop").set_defaults(
        func=cmd_start
    )
    sub.add_parser("stop", help="stop the autonomous loop").set_defaults(func=cmd_stop)

    journal = sub.add_parser("journal", help="recent journal entries")
    journal.add_argument("--limit", type=int, default=20)
    journal.set_defaults(func=cmd_journal)

    propose = sub.add_parser("propose", help="guardrail-check a trade")
    propose.add_argument("symbol")
    propose.add_argument("qty", type=float)
    propose.add_argument("side", choices=["buy", "sell"])
    propose.set_defaults(func=cmd_propose)

    execute = sub.add_parser("execute", help="place a trade (guardrail-gated)")
    execute.add_argument("symbol")
    execute.add_argument("qty", type=float)
    execute.add_argument("side", choices=["buy", "sell"])
    execute.add_argument(
        "--override", action="store_true", help="proceed even if flagged"
    )
    execute.set_defaults(func=cmd_execute)

    return parser


def main() -> None:
    args = build_parser().parse_args()
    args.func(Client(args.base_url), args)


if __name__ == "__main__":
    main()
