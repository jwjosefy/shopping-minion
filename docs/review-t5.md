# T5 review and the M1 acceptance run

_Written by Claude on 2026-10-01. Answer inline with `>`._

## T5 (the `run` command)

Built by Sonnet in a worktree, not merged yet. 173 tests pass (18 new), with fakes for the browser, search, cart and Jev; it hasn't been run for real. The flow:

1. search, then decide;
2. a terminal prompt for each item Jev isn't sure of: the candidates are numbered with Jev's pick first, and `0` skips;
3. a summary table, then `adicionar ao carrinho? [s/N]`;
4. the cart pass, then a report with what the drawer shows;
5. "Enter fecha a janela".

Choices the agent made that the LLD didn't specify:
- An item with no search results is skipped without a prompt.
- Ctrl-C or EOF at a prompt stops the run with status `interrupted`, and nothing more is added.
- If everything is skipped, the run ends without asking.

One known wart: the cart rows in SQLite are numbered by position among the added items, not by item index. `product_id` is the join key. It's fine for M1; it gets fixed when M2 reads the history.

## Login worked

`login` saved the session. Reopening it: the header no longer shows "Entre | Cadastre-se", so `is_logged_in` is True. That is the first real check of the inferred marker.

## Before the acceptance run

1. **Your real cart already holds 55 products** (the header count). The run adds atum, papel higiênico and filé de frango to that cart. If any of the three is already there, that item fails with "já está no carrinho" and is left alone. Proposal: empty the cart (or note what's there) before the run, so the result is easy to read and to undo.
2. **Who runs it.** The run asks questions in the terminal and waits for Enter at the end. Commands run with `!` here have no stdin, so it has to run in your own terminal:

   ```
   cd ~/dev/agents-portfolio/shopping-minion
   dotenvx run -- uv run shopping-minion run examples/lista-m1.yaml
   ```

   It calls Jev once (3 questions, a fraction of a cent) and opens the browser with your session. With the test preferences, the three items should be accepted without a prompt (0.97–1.00 in the eval). The frango gets 1 kg = 10 clicks. Nothing goes near checkout.
3. **Merge T5 into `main` first?** Proposal: yes, so the run uses `main`.
