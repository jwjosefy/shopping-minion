"""The `run` command: search, decide, resolve in the terminal, add to the cart (LLD section 3.9).

Plain functions, no framework. Input and output are injected so the whole flow is testable
offline. The browser only does what search.py and cart.py do; the final purchase is manual.
"""

from collections.abc import Callable
from pathlib import Path
from typing import Any

import yaml

from shopping_minion.browser import NotLoggedInError, ensure_logged_in, open_browser
from shopping_minion.config import DecideConfig, load_decide_config, load_history_config
from shopping_minion.decide import describe_candidate, nothing_fit, on_offer
from shopping_minion.history import ItemHistory, lines_for_item
from shopping_minion.items import Candidate, CartDraft, Decision, Item
from shopping_minion.merge import line_label
from shopping_minion.preferences import load_preferences, read_yaml_preferences
from shopping_minion.reconcile import report_lines
from shopping_minion.storage import Storage
from shopping_minion.workflow import CartOutcome, decide_list, draft_cart, fill_cart, search_list

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
    """Jev's pick first, then the candidates on offer, then the rest.

    Inside each group: Jev's probability, highest first (missing counts as 0); ties keep the
    search order.
    """
    picked = [c for c in decision.candidates if c.product_id == decision.choice]
    rest = [c for c in decision.candidates if c.product_id != decision.choice]

    def by_probability(candidates: list[Candidate]) -> list[Candidate]:
        return sorted(candidates, key=lambda c: -decision.probabilities.get(c.product_id, 0.0))

    offers = by_probability([c for c in rest if on_offer(c)])
    others = by_probability([c for c in rest if not on_offer(c)])
    return picked + offers + others


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


# --- the cart draft on the terminal (LLD 3.5) ---------------------------------------------


def _price(candidate: Candidate) -> str:
    if candidate.price is None:
        return "sem preço"
    return f"R$ {candidate.price:.2f}".replace(".", ",") + (
        "/kg" if candidate.unit_of_sale == "kg" else ""
    )


def print_table(draft: CartDraft, print_fn: PrintFn) -> None:
    for warning in draft.warnings:
        print_fn(f"aviso: {warning}")
    print_fn("")
    print_fn("Resumo do que será adicionado:")
    for line in draft.lines:
        label = line_label(line)
        if len(line.items) > 1:
            label += f" ({len(line.items)} linhas da lista)"
        flags = f"  [{', '.join(line.flags)}]" if line.flags else ""
        quantity = f"{line.quantity.value:g} {line.quantity.unit} -> {line.target.clicks} cliques"
        print_fn(
            f"  {label} | {line.candidate.name} | {_price(line.candidate)} | {quantity}{flags}"
        )
    for decision in draft.skipped:
        print_fn(f"  {decision.item.name} | (pulado)")
    print_fn("")


# --- the run ------------------------------------------------------------------------------


def _yes(answer: str) -> bool:
    return answer.strip().casefold() in YES


def run(
    path: str | Path,
    *,
    prefs_path: str | Path | None = None,  # None: the preferences table; a file wins
    db_path: str | Path = "data/shopping-minion.sqlite",
    yes: bool = False,
    input_fn: InputFn = input,
    print_fn: PrintFn = print,
    open_browser_fn: Callable[[], Any] = open_browser,
    client_factory: Callable[[], Any] | None = None,
    config_path: str | Path = "config/decide.yaml",
    history_config_path: str | Path = "config/history.yaml",
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
    history_config = load_history_config(history_config_path)
    storage = Storage(db_path)
    prefs = load_preferences(storage) if prefs_path is None else read_yaml_preferences(prefs_path)
    run_id = storage.new_run(photo=str(path))
    storage.save_items(run_id, items, items)  # M1: the edited YAML is the confirmed list
    storage.set_status(run_id, "items_saved")
    try:
        return _run_stages(
            run_id,
            storage,
            items,
            config,
            history_config,
            prefs,
            yes,
            input_fn,
            print_fn,
            open_browser_fn,
            client_factory,
        )
    except (EOFError, KeyboardInterrupt):
        storage.set_status(run_id, "interrupted")
        _state(storage, run_id, "cancelled")
        print_fn("\ninterrompido.")
        return 130
    except Exception:
        storage.set_status(run_id, "error")
        _state(storage, run_id, "failed")
        raise
    finally:
        storage.close()


def decide_config_row(config: DecideConfig) -> dict:
    """What the `decide` log row records: the config this run decided with."""
    return {
        "model": config.model,
        "history": config.history,
        "accept_at": config.accept_at,
        "ask_below": config.ask_below,
        "batch_size": config.batch_size,
    }


def histories_for(
    storage: Storage, items: list[Item], candidates: list[list[Candidate]], k: int
) -> list[ItemHistory]:
    """The stored orders' lines for each item; the table is read once."""
    lines = storage.order_lines()
    return [
        lines_for_item(item, found, lines, k=k)
        for item, found in zip(items, candidates, strict=True)
    ]


def _state(storage: Storage, run_id: int, state: str) -> None:
    storage.log(run_id, "state", {"state": state})


def _log_picks(storage: Storage, run_id: int, before: list[Decision], after: list[Decision]):
    """A `pick` row per question the user answered, with Jev's own pick as it was."""
    for index, (old, new) in enumerate(zip(before, after, strict=True)):
        if old.status in ("ask", "no_match") and old.candidates:
            storage.log(
                run_id,
                "pick",
                {
                    "index": index,
                    "item": old.item.name,
                    "jev_choice": old.choice,
                    "jev_confidence": old.confidence,
                    "chosen": new.choice,
                },
            )


def _run_stages(
    run_id,
    storage,
    items,
    config,
    history_config,
    prefs,
    yes,
    input_fn,
    print_fn,
    open_browser_fn,
    client_factory,
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
            _state(storage, run_id, "failed")
            print_fn(str(exc))
            return 1

        # 1. search
        _state(storage, run_id, "searching")

        def progress(i, total, item, candidates):
            print_fn(f"[{i}/{total}] {item.search_term}: {len(candidates)} resultados")

        candidates_per_item = search_list(page, items, progress)
        storage.set_status(run_id, "searched")

        # 2. decide
        _state(storage, run_id, "deciding")
        print_fn("decidindo com o Jev...")
        histories = histories_for(storage, items, candidates_per_item, history_config.related_lines)
        storage.log(run_id, "decide", decide_config_row(config))
        decisions = decide_list(
            items, candidates_per_item, prefs, config, client_factory(), histories=histories
        )
        storage.save_decisions(run_id, decisions)
        storage.set_status(run_id, "decided")

        # 3. the user resolves what Jev wasn't sure about
        asked = decisions
        if any(d.status in ("ask", "no_match") for d in decisions):
            _state(storage, run_id, "picking")
        decisions = resolve_all(decisions, config, input_fn=input_fn, print_fn=print_fn)
        _log_picks(storage, run_id, asked, decisions)
        storage.save_decisions(run_id, decisions)
        storage.set_status(run_id, "resolved")

        # 4. quantity, duplicates merged, and the final table
        _state(storage, run_id, "reviewing_cart")
        draft = draft_cart(decisions, prefs, histories)
        print_table(draft, print_fn)
        if not draft.lines:
            storage.set_status(run_id, "nothing_to_add")
            _state(storage, run_id, "done")
            print_fn("nada a adicionar ao carrinho.")
            return 0
        if yes:
            print_fn("--yes: adicionando sem perguntar.")
        elif not _yes(input_fn("adicionar ao carrinho? [s/N] ")):
            storage.set_status(run_id, "declined")
            _state(storage, run_id, "cancelled")
            print_fn("nada foi adicionado.")
            return 0

        # 5. cart, one product at a time, then the check
        def cart_progress(i, total, candidate, result):
            detail = f" ({result.message})" if result.message else ""
            print_fn(f"[{i}/{total}] {candidate.name}: {result.status}{detail}")

        storage.set_status(run_id, "adding")
        _state(storage, run_id, "filling_cart")
        outcome = fill_cart(page, draft, cart_progress)
        storage.save_cart(run_id, outcome.results)
        if outcome.after is not None:
            storage.log(
                run_id,
                "check",
                {
                    "ok_count": sum(c.ok for c in outcome.checks),
                    "total": len(outcome.checks),
                    "extras": len(outcome.extras),
                },
            )
        storage.set_status(run_id, "done")
        _state(storage, run_id, "done")

        print_report(draft, outcome, print_fn)
        print_fn(OPEN_MESSAGE)
        input_fn("")
    return 0


def print_report(draft: CartDraft, outcome: CartOutcome, print_fn: PrintFn) -> None:
    if outcome.before_error:
        print_fn(f"não consegui ler o carrinho antes: {outcome.before_error}")
    print_fn("")
    print_fn("O que cada adição informou:")
    for line, result in zip(draft.lines, outcome.results, strict=True):
        detail = f" - {result.message}" if result.message else ""
        print_fn(f"  {line_label(line)} | {line.candidate.name}: {result.status}{detail}")
    if outcome.after is None:
        print_fn(f"não consegui ler o carrinho no fim: {outcome.after_error}")
        return
    print_fn("")
    for text in report_lines(outcome.checks, outcome.extras, outcome.before):
        print_fn(text)
