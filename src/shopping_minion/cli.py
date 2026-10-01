import argparse


def build_parser() -> argparse.ArgumentParser:
    return argparse.ArgumentParser(
        prog="shopping-minion",
        description="Turns a photo of a handwritten grocery list into a cart.",
    )


def main(argv: list[str] | None = None) -> None:
    parser = build_parser()
    parser.parse_args(argv)
    parser.print_help()
