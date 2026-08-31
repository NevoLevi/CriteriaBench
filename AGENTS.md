# CriteriaBench agent instructions

## Secrets

- Never read, display, search, diff, copy, summarize, or transmit `.env.local` or `.env`.
- Never run commands that print `OPENAI_API_KEY`, dump the full process environment, or include secret values in command arguments.
- Verify secret configuration only as a redacted boolean such as `configured=true` from application-owned code.
- Keep `.env.local` and `.env` excluded from Git, Docker build contexts, diagnostics, logs, and test artifacts.
- Local and CI tests use the deterministic mock provider unless the user explicitly authorizes a live benchmark.

