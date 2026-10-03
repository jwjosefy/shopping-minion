"""`shopping-minion report [run_id]`: times and corrections of a run (LLD-M3 §2)."""

import difflib
from collections.abc import Callable
from datetime import datetime
from pathlib import Path

from shopping_minion.items import Item
from shopping_minion.storage import Storage

# state -> label; the machine's states and then yours
MACHINE = {
    "reading_list": "lendo a lista (OCR)",
    "syncing_history": "lendo os pedidos",
    "searching": "buscando",
    "deciding": "decidindo (Jev)",
    "filling_cart": "adicionando ao carrinho",
}
YOU = {
    "reviewing_list": "revisando a lista",
    "picking": "escolhendo produtos",
    "reviewing_cart": "revisando o carrinho",
}
ORDER = [
    "reading_list",
    "reviewing_list",
    "syncing_history",
    "searching",
    "deciding",
    "picking",
    "reviewing_cart",
    "filling_cart",
]
BASELINE = "referência (estimativa do Johann): mais de 1 h à mão"
LABEL_WIDTH = 34
COLUMN = 12


def format_duration(seconds: float) -> str:
    total = round(seconds)
    if total < 60:
        return f"{total} s"
    minutes, secs = divmod(total, 60)
    if minutes < 60:
        return f"{minutes} min {secs:02d} s"
    hours, minutes = divmod(minutes, 60)
    return f"{hours} h {minutes:02d} min"


def state_durations(log: list[dict]) -> dict[str, float]:
    """Seconds per state: each `state` row lasts until the next one; the last one has no end."""
    states = [row for row in log if row["kind"] == "state"]
    spent: dict[str, float] = {}
    for row, nxt in zip(states, states[1:], strict=False):
        seconds = (
            datetime.fromisoformat(nxt["at"]) - datetime.fromisoformat(row["at"])
        ).total_seconds()
        state = row["data"]["state"]
        spent[state] = spent.get(state, 0.0) + seconds
    return spent


def _key(item: Item) -> tuple:
    quantity = None if item.quantity is None else (item.quantity.value, item.quantity.unit)
    return (item.name, item.search_term, tuple(item.constraints), item.brand, quantity)


def list_corrections(ocr: list[Item], confirmed: list[Item]) -> tuple[int, int, int]:
    """(edited, deleted, added) lines between what the OCR read and what you confirmed."""
    edited = deleted = added = 0
    matcher = difflib.SequenceMatcher(
        None, [_key(i) for i in ocr], [_key(i) for i in confirmed], autojunk=False
    )
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "replace":
            common = min(i2 - i1, j2 - j1)
            edited += common
            deleted += (i2 - i1) - common
            added += (j2 - j1) - common
        elif tag == "delete":
            deleted += i2 - i1
        elif tag == "insert":
            added += j2 - j1
    return edited, deleted, added


def _plural(n: int, one: str, many: str) -> str:
    return f"{n} {one if n == 1 else many}"


def _time_data(log: list[dict]) -> dict:
    """The time per step, in the order of the run, and the sums of the machine's and yours."""
    spent = state_durations(log)
    steps = []
    machine = you = 0.0
    for state in ORDER:
        if state not in spent:
            continue
        mine = state in MACHINE
        if mine:
            machine += spent[state]
        else:
            you += spent[state]
        steps.append(
            {
                "state": state,
                "label": MACHINE[state] if mine else YOU[state],
                "who": "machine" if mine else "you",
                "seconds": spent[state],
                "text": format_duration(spent[state]),
            }
        )

    def total(seconds: float) -> dict:
        return {"seconds": seconds, "text": format_duration(seconds)}

    return {
        "steps": steps,
        "machine": total(machine),
        "you": total(you),
        "total": total(machine + you),
        "baseline": BASELINE,
    }


def _time_lines(time: dict) -> list[str]:
    lines = [f"{'Tempo':<{LABEL_WIDTH + 2}}{'máquina':>{COLUMN}}{'você':>{COLUMN}}"]
    for step in time["steps"]:
        if step["who"] == "machine":
            cols = f"{step['text']:>{COLUMN}}"
        else:
            cols = f"{'':>{COLUMN}}{step['text']:>{COLUMN}}"
        lines.append(f"  {step['label']:<{LABEL_WIDTH}}{cols}")
    total = (
        f"  {'total':<{LABEL_WIDTH}}{time['machine']['text']:>{COLUMN}}"
        f"{time['you']['text']:>{COLUMN}}   = {time['total']['text']}"
    )
    return [*lines, total, f"  {time['baseline']}"]


def build_report(storage: Storage, run_id: int) -> dict:
    """The numbers of a run as plain data (JSON-ready); `format_report` prints them."""
    run = next((r for r in storage.list_runs() if r["id"] == run_id), None)
    if run is None:
        raise LookupError(f"rodada {run_id} não existe")
    log = storage.read_log(run_id)
    ocr, confirmed = storage.read_items(run_id)
    decisions = storage.read_decisions(run_id)
    cart = storage.read_cart(run_id)

    edited, deleted, added = list_corrections(ocr, confirmed)
    products = cart_edits = None
    if log:  # products and cart edits exist only in the log
        # One answer per item: an item reopened and picked again counts once, as it ended.
        answers: dict = {}
        for n, row in enumerate(r for r in log if r["kind"] == "pick"):
            answers[row["data"].get("index", f"row-{n}")] = row["data"]
        picks = list(answers.values())
        skipped = sum(p["chosen"] is None for p in picks)
        same = sum(p["chosen"] is not None and p["chosen"] == p["jev_choice"] for p in picks)
        products = {
            "accepted": sum(d.status == "accepted" for d in decisions),
            "asked": len(picks),
            "same": same,
            "other": len(picks) - skipped - same,
            "skipped": skipped,
        }
        edits = [row["data"] for row in log if row["kind"] == "cart_edit"]
        cart_edits = {
            "changed": sum(not e["remove"] and e["quantity"] is not None for e in edits),
            "removed": sum(bool(e["remove"]) for e in edits),
        }
    check = next((row["data"] for row in reversed(log) if row["kind"] == "check"), None)
    return {
        "run_id": run_id,
        # Stored in UTC; shown in this machine's local time.
        "when": datetime.fromisoformat(run["created_at"]).astimezone().strftime("%Y-%m-%d %H:%M"),
        "items": len(confirmed),
        "in_cart": sum(r.status in ("added", "untouched") for r in cart) if cart else None,
        "cart_size": len(cart),
        "time": _time_data(log) if any(row["kind"] == "state" for row in log) else None,
        "corrections": {
            "list": {"edited": edited, "deleted": deleted, "added": added, "ocr": len(ocr)},
            "products": products,
            "cart": cart_edits,
        },
        "check": None if check is None else {k: check[k] for k in ("ok_count", "total", "extras")},
    }


def format_report(data: dict) -> list[str]:
    """The text `shopping-minion report` prints for the data of `build_report`."""
    head = f"Rodada {data['run_id']} — {data['when']} — {data['items']} itens na lista"
    if data["in_cart"] is not None:
        head += f", {data['in_cart']} no carrinho"
    out = [head, ""]

    if data["time"] is not None:
        out += [*_time_lines(data["time"]), ""]
    else:
        out += ["Tempo: sem registro de tempo", ""]

    lst = data["corrections"]["list"]
    out.append("Correções")
    out.append(
        f"  lista: {_plural(lst['edited'], 'linha editada', 'linhas editadas')},"
        f" {_plural(lst['deleted'], 'apagada', 'apagadas')},"
        f" {_plural(lst['added'], 'adicionada', 'adicionadas')}"
        f" (de {lst['ocr']} lidas pelo OCR)"
    )
    products, cart_edits = data["corrections"]["products"], data["corrections"]["cart"]
    if products is not None and cart_edits is not None:
        out.append(
            f"  produtos: {_plural(products['accepted'], 'aceito', 'aceitos')} pelo Jev sozinho;"
            f" dos {products['asked']} que vieram para"
            f" você:\n            {products['same']} você confirmou a escolha do Jev,"
            f" {products['other']} escolheu outro, {products['skipped']} pulou"
        )
        changed = _plural(cart_edits["changed"], "quantidade mudada", "quantidades mudadas")
        out.append(
            f"  carrinho: {changed}, {_plural(cart_edits['removed'], 'removido', 'removidos')}"
        )
    out.append("")

    check = data["check"]
    if check is not None:
        line = f"Conferência: {check['ok_count']} de {check['total']} itens conferem"
        extras = check["extras"]
        if extras:
            verb = "não é" if extras == 1 else "não são"
            line += f"; {_plural(extras, 'produto', 'produtos')} no carrinho {verb} desta lista"
        out.append(line)
    elif data["in_cart"] is not None:
        out.append(
            f"Conferência: não registrada ({data['in_cart']} de {data['cart_size']}"
            " adições deram certo)"
        )
    else:
        out.append("Conferência: sem dados")
    return out


def report(
    db_path: str | Path, run_id: int | None = None, print_fn: Callable[[str], None] = print
) -> int:
    storage = Storage(db_path)
    try:
        if run_id is None:
            runs = storage.list_runs()
            if not runs:
                print_fn("nenhuma rodada registrada.")
                return 1
            run_id = runs[-1]["id"]
        try:
            lines = format_report(build_report(storage, run_id))
        except LookupError as exc:
            print_fn(str(exc))
            return 1
    finally:
        storage.close()
    for line in lines:
        print_fn(line)
    return 0
