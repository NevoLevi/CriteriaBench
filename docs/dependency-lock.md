# Reproducible Python dependencies

CriteriaBench commits `uv.lock` and pins the resolver to uv 0.12.8 through
`[tool.uv]` in `pyproject.toml`. Routine installs must consume that lock:

```console
uv lock --check
uv sync --frozen --extra dev
uv run --frozen --no-env-file pytest -m "not live and not integration"
```

`--frozen` prevents uv from changing the resolution. `--no-env-file` and the
CI-level `UV_NO_ENV_FILE=1` guard prevent uv-run commands from discovering a
dotenv file. Application settings also read the normal process environment
only; dependency installation never needs an API key.

The container build uses the same lock but installs only runtime dependencies.
Its Python and uv base images are pinned by digest, and uv is forbidden from
downloading another Python interpreter.

To update dependencies intentionally, install the pinned uv version, run
`uv lock --upgrade`, review the `uv.lock` diff, then rerun the static, unit,
integration, and container checks. Never hand-edit the generated lock.
