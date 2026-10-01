"""The `run` command: search, decide, resolve in the terminal, add to the cart (LLD section 3.9).

Plain functions, no framework. Input and output are injected so the whole flow is testable
offline. The browser only does what search.py and cart.py do; the final purchase is manual.
"""

from collections.abc import Callable
from pathlib import Path
from typing import Any

import yaml

from shopping_minion.browser import NotLoggedInError, ensure_logged_in, open_browser
from shopping_minion.cart import add_all, read_cart_drawer
from shopping_minion.config import DecideConfig, load_decide_config
from shopping_minion.decide import decide, describe_candidate, nothing_fit
from shopping_minion.items import Candidate, CartResult, CartTarget, Decision, Item
from shopping_minion.preferences import find_preference, load_preferences
from shopping_minion.quantity import target_quantity, to_clicks
from shopping_minion.search import search_all
from shopping_minion.storage import Storage

InputFn = Callable[[str], str]
PrintFn = Callable[..., None]

YES = {"s", "sim", "y", "yes"}
OPEN_MESSAGE = "carrinho aberto no navegador — revise e finalize você mesmo; Enter fecha a janela"


class ListError(ValueError):
    """The list file is not `{"items": [...]}` of valid items."""


def load_items(path: str | Path) -> list[Item]:
    data = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    if not isinstance(data, dict) or not isinstance(data.get("items"), list):
        raise ListError(f"{path}: expected a mapping with an `items` list (the `ocr` output)")
    return [Item.model_validate(entry) for entry in data["items"]]


# --- terminal resolution (LLD 3.4) --------------------------------------------------------


def _ordered_candidates(decision: Decision) -> list[Candidate]:
    """Jev's pick first, then the rest in search order."""
    picked = [c for c in decision.candidates if c.product_id == decision.choice]
    rest = [c for c in decision.candidates if c.product_id != decision.choice]
    return picked + rest


def resolve_in_terminal(
    decision: Decision,
    config: DecideConfig,
    *,
    position: str,
    input_fn: InputFn,
    print_fn: PrintFn,
) -> Decision:
    """Ask the user about one `ask` / `no_match` decision: a number picks, 0 skips."""
    item = decision.item
    print_fn("")
    print_fn(f"{position} {item.name}  (lista: {item.source_line!r})")
    if item.constraints:
        print_fn(f"    restrições: {', '.join(item.constraints)}")
    if not decision.candidates:
        print_fn("    sem resultados na loja; item pulado.")
        return decision.model_copy(update={"choice": None, "status": "skipped"})

    if decision.status == "no_match":
        print_fn("    Jev: nenhum destes parece servir.")
    elif nothing_fit(decision, config):
        print_fn("    Jev: nada parece servir (confiança baixa).")
    else:
        print_fn(f"    Jev não tem certeza (confiança {decision.confidence:.2f}).")

    ordered = _ordered_candidates(decision)
    for number, candidate in enumerate(ordered, start=1):
        mark = "  <- escolha do Jev" if candidate.product_id == decision.choice else ""
        print_fn(f"    {number}. {describe_candidate(candidate)}{mark}")
    print_fn("    0. pular este item")

    while True:
        answer = input_fn(f"    escolha [0-{len(ordered)}]: ").strip()
        if answer.isdigit() and int(answer) <= len(ordered):
            break
        print_fn("    opção inválida.")
    if int(answer) == 0:
        return decision.model_copy(update={"choice": None, "status": "skipped"})
    chosen = ordered[int(answer) - 1]
    return decision.model_copy(
        update={"choice": chosen.product_id, "confidence": None, "status": "user_chosen"}
    )


def resolve_all(
    decisions: list[Decision],
    config: DecideConfig,
    *,
    input_fn: InputFn,
    print_fn: PrintFn,
) -> list[Decision]:
    pending = [d for d in decisions if d.status in ("ask", "no_match")]
    resolved = []
    seen = 0
    for decision in decisions:
        if decision.status in ("ask", "no_match"):
            seen += 1
            decision = resolve_in_terminal(
                decision,
                config,
                position=f"[{seen}/{len(pending)}]",
                input_fn=input_fn,
                print_fn=print_fn,
            )
        resolved.append(decision)
    return resolved


# --- quantity (LLD 3.5) -------------------------------------------------------------------


def build_targets(
    decisions: list[Decision], prefs: dict[str, dict], print_fn: PrintFn
) -> list[tuple[Decision, Candidate, CartTarget, str]]:
    """For each decided item: (decision, candidate, cart target, "what we want" text)."""
    rows = []
    for decision in decisions:
        if decision.status not in ("accepted", "user_chosen") or decision.choice is None:
            continue
        candidate = next(c for c in decision.candidates if c.product_id == decision.choice)
        target, assumed = target_quantity(decision.item, find_preference(prefs, decision.item))
        try:
            clicks, inexact = to_clicks(target, candidate.unit_of_sale, candidate.step_kg)
        except ValueError as exc:  # a kg product with no stepper increment
            print_fn(f"aviso: {decision.item.name}: {exc}; item pulado.")
            continue
        flags = list(dict.fromkeys([*assumed, *inexact]))
        cart_target = CartTarget(product_id=candidate.product_id, clicks=clicks, flags=flags)
        value = f"{target.value:g} {target.unit}"
        rows.append((decision, candidate, cart_target, f"{value} -> {clicks} cliques"))
    return rows


def _price(candidate: Candidate) -> str:
    if candidate.price is None:
        return "sem preço"
    return f"R$ {candidate.price:.2f}".replace(".", ",") + (
        "/kg" if candidate.unit_of_sale == "kg" else ""
    )


def print_table(rows: list, skipped: list[Decision], print_fn: PrintFn) -> None:
    print_fn("")
    print_fn("Resumo do que será adicionado:")
    for decision, candidate, target, quantity_text in rows:
        flags = f"  [{', '.join(target.flags)}]" if target.flags else ""
        print_fn(
            f"  {decision.item.name} | {candidate.name} | {_price(candidate)} | "
            f"{quantity_text}{flags}"
        )
    for decision in skipped:
        print_fn(f"  {decision.item.name} | (pulado)")
    print_fn("")


# --- the run ------------------------------------------------------------------------------


def _yes(answer: str) -> bool:
    return answer.strip().casefold() in YES


def run(
    path: str | Path,
    *,
    prefs_path: str | Path = "data/preferencias.yaml",
    db_path: str | Path = "data/shopping-minion.sqlite",
    yes: bool = False,
    input_fn: InputFn = input,
    print_fn: PrintFn = print,
    open_browser_fn: Callable[[], Any] = open_browser,
    client_factory: Callable[[], Any] | None = None,
    config_path: str | Path = "config/decide.yaml",
) -> int:
    """Run the three passes over the list in `path`. Returns the exit code."""
    try:
        items = load_items(path)
    except (OSError, ValueError, yaml.YAMLError) as exc:
        print_fn(f"lista inválida: {exc}")
        return 1
    if not items:
        print_fn("a lista não tem itens.")
        return 1

    config = load_decide_config(config_path)
    prefs = load_preferences(prefs_path)
    storage = Storage(db_path)
    run_id = storage.new_run(photo=str(path))
    storage.save_items(run_id, items, items)  # M1: the edited YAML is the confirmed list
    storage.set_status(run_id, "items_saved")
    try:
        return _run_stages(
            run_id,
            storage,
            items,
            config,
            prefs,
            yes,
            input_fn,
            print_fn,
            open_browser_fn,
            client_factory,
        )
    except (EOFError, KeyboardInterrupt):
        storage.set_status(run_id, "interrupted")
        print_fn("\ninterrompido.")
        return 130
    except Exception:
        storage.set_status(run_id, "error")
        raise
    finally:
        storage.close()


def _run_stages(
    run_id, storage, items, config, prefs, yes, input_fn, print_fn, open_browser_fn, client_factory
) -> int:
    if client_factory is None:
        from typesafe_sdk import TypeSafeClient

        client_factory = TypeSafeClient

    with open_browser_fn() as (_browser, context):
        page = context.new_page()
        try:
            ensure_logged_in(page)
        except NotLoggedInError as exc:
            storage.set_status(run_id, "not_logged_in")
            print_fn(str(exc))
            return 1

        # 1. search
        def progress(i, total, item, candidates):
            print_fn(f"[{i}/{total}] {item.search_term}: {len(candidates)} resultados")

        candidates_per_item = search_all(page, items, progress)
        storage.set_status(run_id, "searched")

        # 2. decide
        print_fn("decidindo com o Jev...")
        decisions = decide(
            list(zip(items, candidates_per_item, strict=True)), prefs, config, client_factory()
        )
        storage.save_decisions(run_id, decisions)
        storage.set_status(run_id, "decided")

        # 3. the user resolves what Jev wasn't sure about
        decisions = resolve_all(decisions, config, input_fn=input_fn, print_fn=print_fn)
        storage.save_decisions(run_id, decisions)
        storage.set_status(run_id, "resolved")

        # 4. quantity and the final table
        rows = build_targets(decisions, prefs, print_fn)
        in_cart = {id(decision) for decision, *_ in rows}
        skipped = [d for d in decisions if id(d) not in in_cart]
        print_table(rows, skipped, print_fn)
        if not rows:
            storage.set_status(run_id, "nothing_to_add")
            print_fn("nada a adicionar ao carrinho.")
            return 0
        if yes:
            print_fn("--yes: adicionando sem perguntar.")
        elif not _yes(input_fn("adicionar ao carrinho? [s/N] ")):
            storage.set_status(run_id, "declined")
            print_fn("nada foi adicionado.")
            return 0

        # 5. cart, one item at a time
        def cart_progress(i, total, candidate, result):
            detail = f" ({result.message})" if result.message else ""
            print_fn(f"[{i}/{total}] {candidate.name}: {result.status}{detail}")

        storage.set_status(run_id, "adding")
        results = add_all(page, [(c, t) for _, c, t, _ in rows], cart_progress)
        storage.save_cart(run_id, results)
        storage.set_status(run_id, "done")

        print_report(rows, results, page, print_fn)
        print_fn(OPEN_MESSAGE)
        input_fn("")
    return 0


def print_report(rows: list, results: list[CartResult], page, print_fn: PrintFn) -> None:
    print_fn("")
    print_fn("Relatório:")
    for (decision, candidate, _target, _text), result in zip(rows, results, strict=True):
        detail = f" - {result.message}" if result.message else ""
        shown = f" (carrinho mostra: {result.quantity_shown})" if result.quantity_shown else ""
        print_fn(f"  {decision.item.name} | {candidate.name}: {result.status}{detail}{shown}")
    try:
        lines = read_cart_drawer(page)
    except Exception as exc:  # the report must not hide the results already saved
        print_fn(f"não consegui ler o carrinho: {exc}")
        return
    print_fn("No carrinho (recarregado do site):")
    for name, quantity in lines:
        print_fn(f"  {quantity}  {name}")
    in_cart = {" ".join(name.lower().split()) for name, _ in lines}
    for (_decision, candidate, _target, _text), result in zip(rows, results, strict=True):
        if result.status == "added" and " ".join(candidate.name.lower().split()) not in in_cart:
            print_fn(
                f"  ATENÇÃO: {candidate.name} foi dado como adicionado mas não está no carrinho"
            )
