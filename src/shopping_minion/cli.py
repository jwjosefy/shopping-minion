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
