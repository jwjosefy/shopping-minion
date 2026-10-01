import argparse
import sys
from pathlib import Path

import yaml


def _cmd_ocr(args: argparse.Namespace) -> int:
    from shopping_minion.intake import IntakeError, transcribe

    try:
        items = transcribe(args.photo)
    except IntakeError as exc:
        print(f"ocr failed: {exc}", file=sys.stderr)
        return 1
    text = yaml.safe_dump(
        {"items": [item.model_dump(mode="json") for item in items]},
        allow_unicode=True,
        sort_keys=False,
    )
    if args.output:
        args.output.write_text(text, encoding="utf-8")
        print(f"{len(items)} items written to {args.output}", file=sys.stderr)
    else:
        print(text, end="")
    return 0


LOGIN_WAIT_SECONDS = 300


def _login() -> int:
    """Open the store, let Johann log in by hand, and save the session in .auth/.

    It doesn't read stdin (it may have none, e.g. when run with `!` from Claude Code): it
    polls the header every 2 s until the logged-out marker is gone, for up to 5 minutes.
    """
    import time

    from shopping_minion.browser import BASE_URL, is_logged_in, open_browser, save_session

    with open_browser() as (_browser, context):
        page = context.new_page()
        page.goto(BASE_URL)
        print("A browser window opened on the store. Log in by hand in that window.")
        print(f"Waiting up to {LOGIN_WAIT_SECONDS // 60} minutes for the login to show...")
        deadline = time.monotonic() + LOGIN_WAIT_SECONDS
        while time.monotonic() < deadline:
            if is_logged_in(page, timeout_ms=2_000) is True:
                page.wait_for_timeout(2_000)  # let the site finish writing its session
                print(f"Logged in. Session saved to {save_session(context)}")
                return 0
            page.wait_for_timeout(2_000)
        print("Still logged out after the wait; nothing was saved.")
        return 1


def _cmd_run(args: argparse.Namespace) -> int:
    from shopping_minion.run import run

    return run(args.list, prefs_path=args.preferences, db_path=args.db, yes=args.yes)


def _cmd_report(args: argparse.Namespace) -> int:
    from shopping_minion.report import report

    return report(args.db, args.run_id)


def _cmd_serve(args: argparse.Namespace) -> int:
    import uvicorn

    from shopping_minion.web.app import create_app, make_access, print_qr
    from shopping_minion.web.statemachine import RunStateMachine

    access = make_access(local=args.local, port=args.port)
    if access is None:
        print("Could not find this machine's LAN address; use --local.", file=sys.stderr)
        return 1
    app = create_app(RunStateMachine(), access=access)
    if args.local:
        host = "127.0.0.1"
        print(f"Listening on http://127.0.0.1:{args.port}/ (this machine only, no token).")
    else:
        host = "0.0.0.0"  # every interface: the desktop on 127.0.0.1 and the phone on the LAN
        print(f"Open on the phone (same Wi-Fi): {access.url}")
        print_qr(access.url)
        print(f"On this machine: http://127.0.0.1:{args.port}/")
    # One worker: the run lives in this process's memory, and so does the browser.
    uvicorn.run(app, host=host, port=args.port, workers=1, proxy_headers=False)
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="shopping-minion",
        description="Turns a photo of a handwritten grocery list into a cart.",
    )
    sub = parser.add_subparsers(dest="command")

    ocr = sub.add_parser("ocr", help="transcribe a photo of a list into YAML")
    ocr.add_argument("photo", type=Path)
    ocr.add_argument("-o", "--output", type=Path, help="write here instead of stdout")
    ocr.set_defaults(func=_cmd_ocr)

    login = sub.add_parser("login", help="log in to the store by hand and save the session")
    login.set_defaults(func=lambda _args: _login())

    run = sub.add_parser("run", help="search, decide and add the items of a list to the cart")
    run.add_argument("list", type=Path, help="the YAML written by `ocr`")
    run.add_argument("--preferences", type=Path, default=Path("data/preferencias.yaml"))
    run.add_argument("--db", type=Path, default=Path("data/shopping-minion.sqlite"))
    run.add_argument("--yes", action="store_true", help="add to the cart without asking")
    run.set_defaults(func=_cmd_run)

    report_parser = sub.add_parser(
        "report", help="times and corrections of a run (the latest by default)"
    )
    report_parser.add_argument("run_id", type=int, nargs="?", help="default: the latest run")
    report_parser.add_argument("--db", type=Path, default=Path("data/shopping-minion.sqlite"))
    report_parser.set_defaults(func=_cmd_report)

    serve = sub.add_parser("serve", help="start the web app (on the LAN, with a token, by default)")
    serve.add_argument("--local", action="store_true", help="127.0.0.1 only: no token, no QR")
    serve.add_argument("--port", type=int, default=8000)
    serve.set_defaults(func=_cmd_serve)
    return parser


def main(argv: list[str] | None = None) -> None:
    parser = build_parser()
    args = parser.parse_args(argv)
    func = getattr(args, "func", None)
    if func is None:
        parser.print_help()
        return
    code = func(args)
    if code:
        raise SystemExit(code)
