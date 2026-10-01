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


def _login() -> int:
    """Open the store, let Johann log in by hand, and save the session in .auth/."""
    from shopping_minion.browser import BASE_URL, is_logged_in, open_browser, save_session

    with open_browser() as (_browser, context):
        page = context.new_page()
        page.goto(BASE_URL)
        print("A browser window opened on the store.")
        print("Log in by hand in that window, then come back here and press Enter.")
        input()
        if is_logged_in(page) is not True:
            print("The page still shows the logged-out header; nothing was saved.")
            return 1
        print(f"Session saved to {save_session(context)}")
    return 0


def _cmd_run(args: argparse.Namespace) -> int:
    from shopping_minion.run import run

    return run(args.list, prefs_path=args.preferences, db_path=args.db, yes=args.yes)


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
