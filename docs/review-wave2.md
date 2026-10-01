# Wave 2 review (T2 intake, T3 decide, T4 browser/search/cart)

_Written by Claude on 2026-10-01. The three branches are merged into `wave2-check`, not into `main`. There, `ruff` is clean and 155 tests pass. The 2 live tests passed too. Answer inline with `>`._

## What came back

| Task | Result |
|---|---|
| **T2 intake** | `claude -p` with Haiku works. The structured answer is in `structured_output`. 1 real call on the list-001 photo: 32 items in 161 s. Eval: 26/34 items found, 1 line split wrong, constraints 26/26, brand 26/26, quantity 25/26 (presunto read as 500 g). |
| **T3 decide** | Built and tested offline. The eval was run by me (≈9 calls to Jev). Table below. |
| **T4 browser/search/cart** | Search works live: 15 candidates per item, in 1–2 s. **The cart failed on its first live run and I fixed it.** It now passes live: atum ×3 → "3", frango kg ×3 → "300g", and the cart drawer reads back both lines. `login` is built, but your part of it (log in, press Enter) hasn't been exercised. |

### Jev on the 7 cases

| | batch 5 | batch 1 |
|---|---|---|
| raw pick correct | 6/7 | 5/7 |
| wrong product would be added | 0 | 0 |
| accepted with no question | 1/7 (the "atum among milk" no-match) | 1/7 |
| sent to you | 6/7 | 6/7 |

Confidence stayed between 0.3 and 0.6, even on right picks. **Inference:** with no preferences, several products are equally right for "atum", and Jev spreads its probability across them. alfa0 saw the same with Julia-1. Batch 5 is no worse than batch 1, so it stays at 5.

### What I fixed in T4 (all seen live)

- The buy box the agent chose (`.product-header-summary`) is a hidden sticky summary. The visible one is `.product-renderer-info-box`.
- While the number animates, the stepper briefly holds the old and the new value ("1 2 3"). The reading now waits for a single quantity that stays the same for 300 ms.
- The Peso/Unidade switch and the drawer's "Instruções / Remover" row also have two buttons. Both are excluded now.

### Other findings

- **The page's own search call double-encodes the term.** "papel higienico" goes out as `search=papel%2520higienico`, and the store still answers correctly. T4 matches the parameter as it is or decoded once more. It still matches on path and parameter only.

## Questions

1. **The Jev model can't be pinned.** The API only accepts `jev-latest` and `jev-preview`, and `jev-1.13` returns `400 Unknown model`. That contradicts what you approved (LLD Q2). Proposal: use `jev-latest` and record the version each response reports (`model`, e.g. `jev-1.13.0`) in SQLite and in the eval output, so numbers stay traceable.
> ok


2. **OCR quality and speed.** 26/34 with exact name matching. Some misses are only naming differences: "refrigerante" and "flocão de milho" matched nothing. It also takes 161 s for one photo. alfa0's Gemini got 32/34, but with different matching code, so the numbers aren't directly comparable. Proposal: keep Haiku for M1, since you edit the YAML anyway. Before M3, run the same eval with `--model sonnet` (still on your subscription) and compare.
> compare with sonnet before closing out M1

3. **Almost everything goes to you in decide.** That is fine for M1's 3 items. Proposal: write `data/preferencias.yaml` entries for the M1 items (atum, papel higiênico, filé de frango) with brand and size. Then rerun the eval to see whether confidence goes up before touching the 0.8/0.5 thresholds. Can you give me the brand and size you buy for those three?
> for testing purposes, just pick the first one from search result and assume it's the preference. we'll develop it further later   

4. **The list-001 fixture, "Saco lixo pia e banheiro".** The line is tagged `crossed_out` but still expects two items. The model dropped it, which follows the prompt's "ignore crossed-out words". Is the line crossed out on the paper? If so, the fixture should expect no items. Fixing a wrong label is allowed; fixing the result isn't.
> the item is crossed - haiku did it right.

5. **Merge.** Once you OK it, `wave2-check` goes to `main`. I also add the double-encoding note to `site-notes/andorinha.md` and the Jev model note to the LLD, then start T5 (`run` command and the M1 acceptance with you).
> just test sonnet on the OCR, then do merge