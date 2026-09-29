# 2026-09-29 — The private key never landed in `.env.keys`

[ADR-0001](../adr/0001-secrets-with-dotenvx.md) says the dotenvx private key "stays local" in `.env.keys`, which is git-ignored. During setup I went looking for that file to back it up, and it wasn't there.

dotenvx 2.x (2.31.1 here) doesn't write `.env.keys` by default anymore. `dotenvx encrypt` put the private key in the **OS secret store** (the desktop keyring) instead, and printed nothing but `◈ encrypted (.env)`. The `git check-ignore .env.keys` check in the setup still passed, because it only tests the ignore rule, not whether the file exists. Lesson: verify the artifact, not just the rule that would protect it.

How I confirmed where the key lives, without printing any value:

| Command | Result |
|---|---|
| `dotenvx run -- true` | decrypts |
| `dotenvx run --no-armor -- true` | decrypts (so not in dotenvx's cloud "Armor") |
| `dotenvx run --no-native -- true` | `DECRYPTION_FAILED` |

What changed:

- **Backup:** `dotenvx native pull` (keyring → `.env.keys`), then `dotenvx bitwarden up` to store it in my Bitwarden vault. Checked in the vault by hand. No `.env.keys` is left on disk.
- **Runtime:** decryption still comes from the OS keyring. Bitwarden is the backup, not the runtime source.

Does this contradict ADR-0001? Not the decision itself: secrets are still committed encrypted and the private key never enters git. Only the "where the key lives" detail is out of date, so this entry records it instead of rewriting an accepted ADR. If the key storage ever becomes a real choice (e.g., a deployed environment), that's a new ADR.

Two things to carry forward:

1. **Recovery on a new machine** means pulling the key from Bitwarden (`dotenvx bitwarden pull` or by hand), not copying a file.
2. **`dotenvx run` doesn't fail when decryption fails.** With the key missing it printed `DECRYPTION_FAILED`, exited 0, and started the command anyway, leaving the `encrypted:...` strings as the values. Once there's code, startup should refuse to run if a secret still starts with `encrypted:`.
