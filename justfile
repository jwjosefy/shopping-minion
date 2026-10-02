# Shopping Minion — common commands. Run `just` to list them.

# Secrets: .env.local if you made one with `just jev`, otherwise the project's .env.
env_file := if path_exists(".env.local") == "true" { ".env.local" } else { ".env" }

_default:
    @just --list

# Save your TYPESAFE_API_KEY (Jev), encrypted, in .env.local. dotenvx prompts for it, masked.
jev:
    dotenvx set TYPESAFE_API_KEY -f .env.local
    @dotenvx run --strict -f .env.local -- true >/dev/null && echo "TYPESAFE_API_KEY saved, encrypted, in .env.local. Next: just up"

# Start the web app (LAN + QR). Extra flags pass through, e.g. `just up --local`.
up *args:
    dotenvx run --strict -f {{env_file}} -- uv run shopping-minion serve {{args}}

# Log in to the store by hand, once; the session goes to .auth/.
login:
    uv run shopping-minion login

# Time and corrections for a run: the latest by default, or `just report 7`.
report *args:
    uv run shopping-minion report {{args}}
