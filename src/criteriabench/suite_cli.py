"""Command-line entry point for the structurally offline synthetic suite."""

from __future__ import annotations

import argparse
import asyncio
import os
import tempfile
from pathlib import Path
from typing import cast

from criteriabench.suite.baselines import ALLOWED_BASELINES
from criteriabench.suite.models import BaselineName
from criteriabench.suite.reporting import render_json, render_markdown
from criteriabench.suite.runner import run_suite


def main() -> None:
    parser = _parser()
    args = parser.parse_args()
    try:
        configs = tuple(cast(BaselineName, item) for item in args.configs)
        _validate_configs(configs)
        outputs = (args.json_output, args.markdown_output)
        checks = (args.check_json, args.check_markdown)
        _validate_paths(args.manifest, outputs, checks, args.overwrite)
        report = asyncio.run(run_suite(args.manifest, configs))
        payloads = (render_json(report), render_markdown(report))
        if checks[0] is not None:
            _check_exact(checks[0], payloads[0], "JSON")
        if checks[1] is not None:
            _check_exact(checks[1], payloads[1], "Markdown")
        _write_outputs_atomic(outputs, payloads)
    except (OSError, ValueError) as exc:
        parser.error(str(exc))


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Evaluate allowlisted zero-network baselines on synthetic v0.1"
    )
    parser.add_argument("manifest", type=Path, help="synthetic v0.1 manifest.json")
    parser.add_argument(
        "--configs",
        nargs="+",
        choices=ALLOWED_BASELINES,
        default=list(ALLOWED_BASELINES),
        help="offline baselines to compare (default: both)",
    )
    parser.add_argument("--json-output", type=Path, required=True)
    parser.add_argument("--markdown-output", type=Path, required=True)
    parser.add_argument("--overwrite", action="store_true")
    parser.add_argument("--check-json", type=Path, help="exactly compare generated JSON bytes")
    parser.add_argument(
        "--check-markdown", type=Path, help="exactly compare generated Markdown bytes"
    )
    return parser


def _validate_configs(configs: tuple[BaselineName, ...]) -> None:
    if not configs or len(set(configs)) != len(configs):
        raise ValueError("configs must be a non-empty unique list")
    if any(config not in ALLOWED_BASELINES for config in configs):
        raise ValueError("only allowlisted offline configs are accepted")


def _validate_paths(
    manifest: Path,
    outputs: tuple[Path, Path],
    checks: tuple[Path | None, Path | None],
    overwrite: bool,
) -> None:
    if manifest.name != "manifest.json":
        raise ValueError("input must be the synthetic v0.1 manifest.json")
    paths = (manifest, *outputs, *(path for path in checks if path is not None))
    for path in paths:
        if any(_is_environment_filename(part) for part in path.parts):
            raise ValueError("environment-style paths are not accepted")
    if outputs[0].suffix.casefold() != ".json":
        raise ValueError("JSON output must use a .json suffix")
    if outputs[1].suffix.casefold() not in {".md", ".markdown"}:
        raise ValueError("Markdown output must use a .md or .markdown suffix")
    resolved_outputs = [path.resolve(strict=False) for path in outputs]
    if len(set(resolved_outputs)) != len(resolved_outputs):
        raise ValueError("JSON and Markdown outputs must be different files")
    if manifest.resolve(strict=False) in resolved_outputs:
        raise ValueError("an output must not overwrite the input manifest")
    resolved_checks = {path.resolve(strict=False) for path in checks if path is not None}
    if any(path in resolved_checks for path in resolved_outputs):
        raise ValueError("an output must not overwrite a check file")
    for path in outputs:
        if path.exists() and not overwrite:
            raise ValueError(f"output already exists; pass --overwrite: {path}")


def _write_outputs_atomic(paths: tuple[Path, Path], payloads: tuple[bytes, bytes]) -> None:
    temporary_paths: list[Path] = []
    try:
        for path, payload in zip(paths, payloads, strict=True):
            path.parent.mkdir(parents=True, exist_ok=True)
            with tempfile.NamedTemporaryFile(
                mode="wb",
                dir=path.parent,
                prefix=f".{path.name}.",
                suffix=".tmp",
                delete=False,
            ) as handle:
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
                temporary_paths.append(Path(handle.name))
        for temporary_path, output_path in zip(temporary_paths, paths, strict=True):
            temporary_path.replace(output_path)
    finally:
        for temporary_path in temporary_paths:
            temporary_path.unlink(missing_ok=True)


def _check_exact(path: Path, expected: bytes, label: str) -> None:
    if not path.is_file():
        raise ValueError(f"{label} check file does not exist: {path}")
    if path.read_bytes() != expected:
        raise ValueError(f"{label} check failed: generated bytes differ from {path}")


def _is_environment_filename(name: str) -> bool:
    lowered = name.casefold()
    return lowered == ".env" or lowered.startswith(".env.")


if __name__ == "__main__":
    main()
