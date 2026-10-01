# Shopping Minion — Low-Level Design (M3: full list, measured)

- **Status:** Approved by Johann on 2026-10-01
- **Date:** 2026-10-01
- **Implements:** [roadmap.md](roadmap.md) §M3. No HLD change: this adds measurement, not a new data source or runtime.

**M3 is done when** one real list of 30+ items goes from photo to checked cart through the web app, and a report states:
- time per step;
- total time against the baseline;
- the three correction counts.

The baseline is Johann's estimate: over 1 h by hand for 30–50+ items.

What's missing today, read from the code:
- **Time:** the history only keeps `runs.created_at`. `set_status` overwrites `runs.status` without a timestamp, so nothing tells how long search, picking or filling took.
- **List corrections:** countable already, since the OCR output and the confirmed list are both saved (`items.ocr_json`, `items.confirmed_json`).
- **Picks:** when you pick, the decision is overwritten with your choice (`statemachine.py`, `status: user_chosen`), and Jev's original pick is lost. So "you confirmed Jev's pick" and "you chose another product" can't be told apart.
- **Cart edits:** quantity changes and removals in the cart review aren't recorded.

## 1. A run log (`storage.py`)

One new table, written as things happen:

```sql
CREATE TABLE IF NOT EXISTS run_log (
    run_id INTEGER NOT NULL REFERENCES runs(id),
    seq    INTEGER NOT NULL,
    at     TEXT    NOT NULL,      -- UTC, ISO 8601 with milliseconds
    kind   TEXT    NOT NULL,      -- state | pick | cart_edit
    data   TEXT    NOT NULL,      -- JSON
    PRIMARY KEY (run_id, seq)
);
```

`Storage.log(run_id, kind, data)` appends a row, and `Storage.read_log(run_id)` reads them back.

| kind | Written by | data |
|---|---|---|
| `state` | `RunStateMachine`, on every state change | `{state}` |
| `pick` | `POST /api/run/picks` | `{index, item, jev_choice, jev_confidence, chosen}` (`chosen` is null for a skip) |
| `cart_edit` | `PUT /api/run/cart-draft` | `{line_id, quantity, remove}` |

- **Timing comes from the `state` rows.** Time in a state is the next row's `at` minus this one's. The run's total is the last row minus the first. Waiting states (`reviewing_list`, `picking`, `reviewing_cart`) count as your time; the others count as the machine's.
- The CLI (`run.py`) writes `state` rows at its own stage boundaries, with the same names where they apply. M3 is measured on the web app, so the CLI only has to not break.
- `Decision` gets no new field. Jev's original pick lives in the `pick` row, so the contract stays the same.

## 2. The report (`report.py`, `shopping-minion report [run_id]`)

Read only, from SQLite; the latest run by default. It prints:

```
Rodada 8 — 2026-10-02 10:14 — 34 itens na lista, 31 no carrinho

Tempo                               máquina    você
  lendo a lista (OCR)                  24 s
  revisando a lista                             3 min 10 s
  buscando                           2 min 40 s
  decidindo (Jev)                       3 s
  escolhendo produtos                           6 min 05 s
  revisando o carrinho                            1 min 20 s
  adicionando ao carrinho           4 min 30 s
  total                              7 min 37 s  10 min 35 s   = 18 min 12 s
  referência (estimativa do Johann): mais de 1 h à mão

Correções
  lista: 3 linhas editadas, 2 apagadas, 0 adicionadas (de 34 lidas pelo OCR)
  produtos: 2 aceitos pelo Jev sozinho; dos 32 que vieram para você:
            20 você confirmou a escolha do Jev, 9 escolheu outro, 3 pulou
  carrinho: 1 quantidade mudada, 0 removidos

Conferência: 31 de 31 itens conferem; 1 produto no carrinho não é desta lista
```

The numbers above are made up, to show the layout.

**How each count is computed:**
- **List:** `difflib.SequenceMatcher` over the OCR items and the confirmed items, each compared on (name, search_term, constraints, brand, quantity). `equal` blocks are unchanged. A `replace` counts edited lines up to the shorter side and added or deleted lines for the rest. `insert` is added, `delete` is deleted.
- **Products:**
  - *accepted by Jev on its own:* decisions with status `accepted`;
  - *you confirmed Jev's pick:* `pick` rows where `chosen == jev_choice`;
  - *you chose another:* `pick` rows where `chosen != jev_choice`, with chosen not null;
  - *you skipped:* `pick` rows where chosen is null.
- **Cart:** `cart_edit` rows, counted as quantity changes and removals.
- **Check:** from the saved cart results and the outcome. It is the same "N de M conferem" the done screen shows.

A run from before this change (runs 1–7) has no log. The report says "sem registro de tempo" and shows only the list corrections and the check.

## 3. Tasks

| Task | What | Who |
|---|---|---|
| **T10** | `run_log` and `Storage.log`/`read_log`; state, pick and cart_edit rows from the state machine and the API; state rows from `run.py`; `report.py` with `shopping-minion report`; tests (a fake run's log gives the expected times and counts, the list diff cases, a run without a log). | Sonnet |
| **T11** | M3 acceptance: Johann runs a real list of 30+ items through the web app. Claude runs `report` and records it in this LLD and in the roadmap. | Johann + Claude |

One task, so no waves. Paid calls: T11 only (Jev, a fraction of a cent).

## 4. Questions resolved in review

| # | Question | Answer (Johann, 2026-10-01) |
|---|---|---|
| 1 | Report in the terminal only, or also on the done screen? | Terminal only for now. Showing it in the app goes to M5 (noted in the roadmap). |
| 2 | Which list for T11? | A new, real list. Johann will scan it. |
