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
