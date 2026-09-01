"""Structurally offline CLI for validating and scoring frozen prediction bundles."""

from __future__ import annotations

import argparse
import os
import re
import sys
import tempfile
from pathlib import Path

from .integrity import canonical_json_bytes, load_verified_bundle
from .scoring import score_verified_bundle

_SHA256_LINE = re.compile(rb"^([0-9a-f]{64})\n$")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m criteriabench.predictions",
        description="Validate and score a frozen prediction bundle without network access.",
    )
    parser.add_argument("--bundle", required=True, type=Path)
    parser.add_argument("--manifest", required=True, type=Path)
    parser.add_argument(
        "--check",
        type=Path,
        help="Optional file containing the expected lowercase bundle SHA-256 and one LF.",
    )
    parser.add_argument("--output", required=True, type=Path)
    return parser


def run(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        _validate_paths(args.bundle, args.manifest, args.check, args.output)
        bundle, loaded = load_verified_bundle(args.bundle, args.manifest)
        if args.check is not None:
            expected = _read_check(args.check)
            if expected != bundle.bundle_sha256:
                raise ValueError("bundle hash does not match the external check file")
        report = score_verified_bundle(bundle, loaded)
        _write_new_atomic(args.output, canonical_json_bytes(report) + b"\n")
    except (OSError, ValueError) as exc:
        print(f"prediction replay failed: {exc}", file=sys.stderr)
        return 2
    return 0


def _validate_paths(
    bundle: Path,
    manifest: Path,
    check: Path | None,
    output: Path,
) -> None:
    paths = (bundle, manifest, output, *(path for path in (check,) if path is not None))
    for path in paths:
        if any(_is_environment_filename(part) for part in path.parts):
            raise ValueError("environment-style paths are not accepted")
    if output.suffix.casefold() != ".json":
        raise ValueError("score output must use a .json suffix")

    resolved_output = output.resolve(strict=False)
    resolved_inputs = {
        path.resolve(strict=False) for path in (bundle, manifest, check) if path is not None
    }
    if resolved_output in resolved_inputs:
        raise ValueError("score output must not overwrite an input or check file")
    if output.exists():
        raise ValueError("score output already exists")


def _is_environment_filename(name: str) -> bool:
    lowered = name.casefold()
    return lowered == ".env" or lowered.startswith(".env.")


def _read_check(path: Path) -> str:
    if not path.is_file():
        raise ValueError("bundle check file does not exist")
    raw = path.read_bytes()
    match = _SHA256_LINE.fullmatch(raw)
    if match is None:
        raise ValueError("bundle check file must contain one lowercase SHA-256 and one LF")
    return match.group(1).decode("ascii")


def _write_new_atomic(path: Path, raw: bytes) -> None:
    """Durably and exclusively publish a report without clobbering another writer."""

    temporary: Path | None = None
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(
            mode="wb",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            handle.write(raw)
            handle.flush()
            os.fsync(handle.fileno())
            temporary = Path(handle.name)
        os.link(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def main() -> None:
    raise SystemExit(run())
