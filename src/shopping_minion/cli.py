import argparse


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


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="shopping-minion",
        description="Turns a photo of a handwritten grocery list into a cart.",
    )
    commands = parser.add_subparsers(dest="command")
    commands.add_parser("login", help="log in to the store by hand and save the session")
    return parser


def main(argv: list[str] | None = None) -> None:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command == "login":
        raise SystemExit(_login())
    parser.print_help()
