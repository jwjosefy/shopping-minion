"""decide pass: one Jev Choice per item, then the confidence policy (LLD section 3.4)."""

from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal

from typesafe_sdk import Choice, TypeSafeClient

from shopping_minion.config import DecideConfig
from shopping_minion.items import Candidate, Decision, Item
from shopping_minion.preferences import find_preference

NONE_KEY = "nenhum"
STATE = "Lista de compras de supermercado."
MAX_WORKERS = 8  # batch_size=1 runs this many calls at once

QUESTION = (
    "Qual produto da loja corresponde ao `item` da lista de compras? "
    "Respeite as restrições de `item`; se nenhum produto corresponde, escolha `nenhum`."
)
QUESTION_WITH_PREFERENCE = (
    "Qual produto da loja corresponde ao `item` da lista de compras? "
    "Respeite as restrições de `item` e a `preferencia`; "
    "se nenhum produto corresponde, escolha `nenhum`."
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


def describe_candidate(candidate: Candidate) -> str:
    parts = [candidate.name]
    if candidate.brand:
        parts.append(f"marca {candidate.brand}")
    parts.append(_reais(candidate.price) if candidate.price is not None else "sem preço")
    parts.append("vendido por kg" if candidate.unit_of_sale == "kg" else "vendido por unidade")
    if not candidate.available:
        parts.append("indisponível")
    return ", ".join(parts)


def _item_fields(item: Item) -> dict:
    fields: dict = {"name": item.name}
    if item.constraints:
        fields["constraints"] = list(item.constraints)
    if item.brand:
        fields["brand"] = item.brand
    fields["source_line"] = item.source_line
    return fields


def build_questions(
    items_with_candidates: list[ItemWithCandidates], prefs: dict[str, dict]
) -> dict[str, Choice]:
    """One Choice per item, keyed `item_<n>` (n is the position in the list, from 0)."""
    questions = {}
    for n, (item, candidates) in enumerate(items_with_candidates):
        preference = find_preference(prefs, item)
        instructions: dict = {
            "question": QUESTION_WITH_PREFERENCE if preference else QUESTION,
            "item": _item_fields(item),
        }
        if preference:
            instructions["preferencia"] = preference
        criteria: dict = {f"p{c.product_id}": describe_candidate(c) for c in candidates}
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
) -> list[Decision]:
    questions = build_questions(batch, prefs)
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
            )
        )
    return decisions


def decide(
    items_with_candidates: list[ItemWithCandidates],
    prefs: dict[str, dict],
    config: DecideConfig,
    client: TypeSafeClient,
) -> list[Decision]:
    """Decisions in the order of the input. Items without candidates never reach Jev."""
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
        return _decide_batch([pair for _, pair in batch], prefs, config, client)

    if size == 1 and len(batches) > 1:
        with ThreadPoolExecutor(max_workers=min(MAX_WORKERS, len(batches))) as pool:
            results = list(pool.map(run, batches))
    else:
        results = [run(batch) for batch in batches]

    for batch, batch_decisions in zip(batches, results, strict=True):
        for (index, _), decision in zip(batch, batch_decisions, strict=True):
            decisions[index] = decision
    return [decisions[i] for i in range(len(items_with_candidates))]
