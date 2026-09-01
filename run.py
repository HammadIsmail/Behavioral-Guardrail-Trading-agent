"""
Convenience launcher.

    python run.py              # stable — what you want for a multi-day run
    python run.py --reload     # auto-restart on file changes, for development

Reload is off by default on purpose: the autonomous agent runs inside this
process, and an accidental file save shouldn't interrupt a trading session.
"""
import argparse

import uvicorn

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--reload", action="store_true")
    args = parser.parse_args()

    uvicorn.run(
        "app.main:app", host=args.host, port=args.port, reload=args.reload
    )
