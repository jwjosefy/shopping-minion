"""decide pass: one Jev Choice per item, then the confidence policy (LLD section 3.4)."""

from concurrent.futures import ThreadPoolExecutor
from datetime import date
from decimal import Decimal
from typing import Literal

from typesafe_sdk import Choice, TypeSafeClient

from shopping_minion.config import DecideConfig
from shopping_minion.history import ItemHistory, ProductHistory, local_date
from shopping_minion.items import Candidate, Decision, Item, Quantity
from shopping_minion.preferences import find_preference

NONE_KEY = "nenhum"
STATE = "Lista de compras de supermercado."
MAX_WORKERS = 8  # batch_size=1 runs this many calls at once

OFFER_RULE = "Entre produtos equivalentes, prefira o que está em oferta."
HISTORY_OPTIONS_RULE = (
    "Entre os produtos que correspondem, prefira o que já foi comprado antes."  # variant A
)
HISTORY_LIST_RULE = (
    "Considere o `historico` de compras: entre os produtos que correspondem, "
    "prefira um que já foi comprado antes."  # variant B
)
QUESTION = (
    "Qual produto da loja corresponde ao `item` da lista de compras? "
    "Respeite as restrições de `item`. "
    f"{OFFER_RULE} "
    "Se nenhum produto corresponde, escolha `nenhum`."
)
QUESTION_WITH_PREFERENCE = (
    "Qual produto da loja corresponde ao `item` da lista de compras? "
    "Respeite as restrições de `item` e a `preferencia`. "
    f"{OFFER_RULE} "
    "Se nenhum produto corresponde, escolha `nenhum`."
)
NONE_DESCRIPTION = (
    "Nenhum dos produtos listados corresponde ao `item`: todos são de outro tipo de produto "
    "ou contrariam as restrições do `item`. Só vale quando nenhum outro produto serve."
)

ItemWithCandidates = tuple[Item, list[Candidate]]


class DecideError(Exception):
    """Jev's response did not hold the answer we asked for."""


def _reais(price: Decimal) -> str:
    text = f"{price:,.2f}"  # 1,234.56
    return "R$ " + text.replace(",", "_").replace(".", ",").replace("_", ".")


def on_offer(candidate: Candidate) -> bool:
    return (
        candidate.price is not None
        and candidate.list_price is not None
        and candidate.list_price > candidate.price
    )


def describe_candidate(candidate: Candidate) -> str:
    parts = [candidate.name]
    if candidate.brand:
        parts.append(f"marca {candidate.brand}")
    if candidate.price is None:
        parts.append("sem preço")
    elif on_offer(candidate):
        parts.append(f"em oferta, de {_reais(candidate.list_price)} por {_reais(candidate.price)}")
    else:
        parts.append(_reais(candidate.price))
    parts.append("vendido por kg" if candidate.unit_of_sale == "kg" else "vendido por unidade")
    if not candidate.available:
        parts.append("indisponível")
    return ", ".join(parts)


def _number(value: float) -> str:
    """pt-BR decimal comma, no trailing zeros: 2.045 -> `2,045`, 0.5 -> `0,5`, 3.0 -> `3`."""
    return format(Decimal(str(value)).normalize(), "f").replace(".", ",")


def format_quantity(quantity: Quantity) -> str:
    return f"{_number(quantity.value)} {quantity.unit}"


def format_times(count: int) -> str:
    return "1 vez" if count == 1 else f"{count} vezes"


def format_date(day: date) -> str:
    return day.strftime("%d/%m/%Y")


def describe_history(history: ProductHistory) -> str:
    """Variant A: the text that follows the option's description."""
    return (
        f"comprado antes: {format_times(history.orders)}, "
        f"a última em {format_date(history.last_at)}, {format_quantity(history.last_quantity)}"
    )


def history_list(candidates: list[Candidate], history: ItemHistory) -> list[str]:
    """Variant B: `<name> | <quantity> | <dd/mm/aaaa>`, the candidates' last purchases first,
    then the related lines."""
    entries = []
    for candidate in candidates:
        product = history.products.get(candidate.product_id)
        if product is not None:
            entries.append(
                f"{candidate.name} | {format_quantity(product.last_quantity)} | "
                f"{format_date(product.last_at)}"
            )
    for line in history.related:
        quantity = Quantity(value=line.quantity, unit=line.unit)
        entries.append(
            f"{line.name} | {format_quantity(quantity)} | {format_date(local_date(line))}"
        )
    return entries


def _item_fields(item: Item) -> dict:
    fields: dict = {"name": item.name}
    if item.constraints:
        fields["constraints"] = list(item.constraints)
    if item.brand:
        fields["brand"] = item.brand
    fields["source_line"] = item.source_line
    return fields


def _with_rule(question: str, rule: str) -> str:
    """The history sentence goes right before the offer sentence."""
    return question.replace(OFFER_RULE, f"{rule} {OFFER_RULE}", 1)


def build_questions(
    items_with_candidates: list[ItemWithCandidates],
    prefs: dict[str, dict],
    histories: list[ItemHistory] | None = None,
    mode: Literal["none", "options", "list"] = "none",
) -> dict[str, Choice]:
    """One Choice per item, keyed `item_<n>` (n is the position in the list, from 0).

    `histories` has one entry per item, in the same order; with None, or mode `none`, the
    questions have no history (LLD-M4 section 11.2)."""
    questions = {}
    for n, (item, candidates) in enumerate(items_with_candidates):
        preference = find_preference(prefs, item)
        history = histories[n] if histories is not None and mode != "none" else None
        question = QUESTION_WITH_PREFERENCE if preference else QUESTION
        as_options = mode == "options" and history is not None and bool(history.products)
        as_list = (
            mode == "list" and history is not None and bool(history.products or history.related)
        )
        if as_options:
            question = _with_rule(question, HISTORY_OPTIONS_RULE)
        elif as_list:
            question = _with_rule(question, HISTORY_LIST_RULE)
        instructions: dict = {"question": question, "item": _item_fields(item)}
        if preference:
            instructions["preferencia"] = preference
        if as_list:
            instructions["historico"] = history_list(candidates, history)
        criteria: dict = {}
        for c in candidates:
            text = describe_candidate(c)
            if as_options and c.product_id in history.products:
                text += f"; {describe_history(history.products[c.product_id])}"
            criteria[f"p{c.product_id}"] = text
        criteria[NONE_KEY] = NONE_DESCRIPTION
        questions[f"item_{n}"] = Choice(instructions=instructions, criteria=criteria)
    return questions


def apply_policy(choice: str | None, confidence: float, config: DecideConfig) -> str:
    if confidence >= config.accept_at:
        return "accepted" if choice is not None else "no_match"
    return "ask"


def nothing_fit(decision: Decision, config: DecideConfig) -> bool:
    """True when the item goes to the user flagged "nothing fit" (confidence below ask_below)."""
    return (
        decision.status == "ask"
        and decision.confidence is not None
        and decision.confidence < config.ask_below
    )


def _product_id(key: str) -> str:
    """`p123` -> `123`; `nenhum` stays `nenhum`."""
    return key if key == NONE_KEY else key.removeprefix("p")


def _decide_batch(
    batch: list[ItemWithCandidates],
    prefs: dict[str, dict],
    config: DecideConfig,
    client: TypeSafeClient,
    histories: list[ItemHistory] | None = None,
) -> list[Decision]:
    questions = build_questions(batch, prefs, histories, config.history)
    response = client.system_one(state=STATE, questions=questions, model=config.model)
    decisions = []
    for n, (item, candidates) in enumerate(batch):
        answer = response.choices.get(f"item_{n}")
        if answer is None:
            raise DecideError(f"no answer for item_{n} ({item.name!r})")
        label = _product_id(answer.choice)
        choice = None if label == NONE_KEY else label
        decisions.append(
            Decision(
                item=item,
                candidates=candidates,
                choice=choice,
                confidence=answer.confidence,
                probabilities={_product_id(k): p for k, p in answer.probabilities.items()},
                status=apply_policy(choice, answer.confidence, config),
                model=getattr(response, "model", None),
            )
        )
    return decisions


def decide(
    items_with_candidates: list[ItemWithCandidates],
    prefs: dict[str, dict],
    config: DecideConfig,
    client: TypeSafeClient,
    histories: list[ItemHistory] | None = None,
) -> list[Decision]:
    """Decisions in the order of the input. Items without candidates never reach Jev.
    `histories` has one entry per item, in the same order; None is the same as `history: none`."""
    if histories is not None and len(histories) != len(items_with_candidates):
        raise ValueError("histories must have one entry per item")
    decisions: dict[int, Decision] = {}
    to_ask: list[tuple[int, ItemWithCandidates]] = []
    for index, (item, candidates) in enumerate(items_with_candidates):
        if candidates:
            to_ask.append((index, (item, candidates)))
        else:
            decisions[index] = Decision(
                item=item, candidates=[], choice=None, confidence=None, status="no_match"
            )

    size = config.batch_size
    batches = [to_ask[i : i + size] for i in range(0, len(to_ask), size)]

    def run(batch: list[tuple[int, ItemWithCandidates]]) -> list[Decision]:
        batch_histories = None if histories is None else [histories[i] for i, _ in batch]
        return _decide_batch([pair for _, pair in batch], prefs, config, client, batch_histories)

    if size == 1 and len(batches) > 1:
        with ThreadPoolExecutor(max_workers=min(MAX_WORKERS, len(batches))) as pool:
            results = list(pool.map(run, batches))
    else:
        results = [run(batch) for batch in batches]

    for batch, batch_decisions in zip(batches, results, strict=True):
        for (index, _), decision in zip(batch, batch_decisions, strict=True):
            decisions[index] = decision
    return [decisions[i] for i in range(len(items_with_candidates))]
