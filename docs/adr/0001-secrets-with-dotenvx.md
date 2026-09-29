# ADR-0001: Secrets managed with dotenvx, committed encrypted

- **Status:** Accepted
- **Date:** 2026-09-29

## Context

The repo is public from the first commit. The system needs credentials for the store account and API keys for model providers, and it produces browser session state that is effectively a credential. One leaked secret in git history is expensive to undo, so this has to be settled before any code exists.

## Options considered

1. **Plain `.env` in `.gitignore`.** Simple, but secrets live only on one machine and new environments require manual setup with no record of which variables exist.
2. **Cloud secret manager** (GCP Secret Manager, 1Password, Doppler). Strong, but adds an account, auth flow, and runtime dependency before there is anything deployed.
3. **dotenvx.** `.env` is committed encrypted; only the private key (`.env.keys`) stays local.

## Decision

Option 3. The encrypted `.env` documents which secrets exist and travels with the repo; the key never does. `.env.keys`, browser storage state, and everything under `data/` are git-ignored.

## Consequences

- Cloning the repo shows the shape of the configuration without exposing values.
- Losing `.env.keys` means re-creating the secrets. That is acceptable for a single-user v0.
- When the project gets a deployed component, revisit this in favor of a managed vault for that environment.
