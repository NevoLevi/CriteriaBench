"""Safe, deterministic import of the Leaf Logical Forms (LLF) corpus.

The upstream annotations use ``.js`` as a convenient authoring format.  This
module treats every file as inert text: it decodes three leading JavaScript
string literals with a small, bounded parser and retains the remaining logical
form as a string.  It never evaluates or imports upstream JavaScript.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
from collections import Counter, defaultdict
from collections.abc import Iterable, Mapping, Sequence
from pathlib import Path
from typing import Annotated, Literal

from pydantic import Field, StrictInt, model_validator

from criteriabench.domain.schemas import StrictModel

UPSTREAM_REPOSITORY = "https://github.com/uw-bionlp/leaf-corpora"
UPSTREAM_COMMIT = "461288aeba8b37fabd43bd7c55f0e1cb1bb10b9e"
UPSTREAM_COMMIT_URL = f"{UPSTREAM_REPOSITORY}/commit/{UPSTREAM_COMMIT}"
UPSTREAM_CORPUS_TREE = "f846019a2743b8ac20f5d0e7323347e8bcaedf15"
UPSTREAM_LICENSE = "MIT"
UPSTREAM_LICENSE_SHA256 = "5748126bdf992602f455023784c420638dcd18ed5710e17200491a254d110f60"
UPSTREAM_INVENTORY_SHA256 = "c8ee39eaff21251075d36d8d0e3b506fbbc196d3a6eaa95084dda25a5fe88297"

DATASET_ID: Literal["leaf-logical-forms"] = "leaf-logical-forms"
DATASET_VERSION: Literal["llf-461288a"] = "llf-461288a"
IMPORT_SCHEMA_VERSION = "llf-import-v1"
GENERATION_MANIFEST_SCHEMA_VERSION = "llf-generation-manifest-v1"
SPLIT_ALGORITHM = "sha256-trial-rank-v1"
SPLIT_SEED = "criteriabench-llf-real-v1"
DEVELOPMENT_TARGET_CASES = 200

EXPECTED_SOURCE_FILES = 2_060
EXPECTED_PRIMARY_CASES = 2_000
EXPECTED_AGREEMENT_FILES = 60
EXPECTED_AGREEMENT_CASES = 20
EXPECTED_TRIALS = 885
EXPECTED_AVAILABLE_PRIMARY_REFERENCES = 1_997
EXPECTED_MISSING_UPSTREAM_PRIMARY_REFERENCES = 3
MAX_SOURCE_FILE_BYTES = 16_384

EXPECTED_MISSING_UPSTREAM_CASE_IDS = frozenset({"NCT03868891_6", "NCT03923894_9", "NCT03928561_4"})

ATTRIBUTION_TEXT = """# LLF/LCT data attribution

CriteriaBench redistributes a deterministic subset of the Leaf Logical Forms (LLF)
annotations from the University of Washington BioNLP `leaf-corpora` repository:

- repository: https://github.com/uw-bionlp/leaf-corpora
- pinned commit: https://github.com/uw-bionlp/leaf-corpora/commit/461288aeba8b37fabd43bd7c55f0e1cb1bb10b9e
- annotation license: MIT; the complete upstream notice is in `LICENSE.upstream.txt`

The eligibility-criterion text underlying LLF derives from the LCT corpus. Cite its data
descriptor at https://doi.org/10.1038/s41597-022-01521-0 and comply with the Creative
Commons Attribution 4.0 International license:
https://creativecommons.org/licenses/by/4.0/.

The LLF annotation task and corpus use are described in the LeafAI publication:
https://pmc.ncbi.nlm.nih.gov/articles/PMC10654856/.

CriteriaBench does not correct the upstream criterion text or logical forms. It reserializes
them as inert JSONL, adds source hashes and a deterministic trial-level split, and marks three
source files that contain no logical-form body as `missing_upstream`. NCT identifiers are
retained so each criterion can be traced to its public trial record.
"""

DATASET_README_TEXT = """# Leaf Logical Forms: CriteriaBench Real v1 import

This directory is generated deterministically from the pinned LLF checkout. Do not hand-edit
the artifacts. Regenerate them with `python -m criteriabench.real.llf` and verify
`manifest.sha256` plus every artifact hash recorded in `manifest.json`.

## Contents

- `records.jsonl`: all 2,000 primary cases, including 1,997 available references and three
  explicit `missing_upstream` cases.
- `generation_cases.jsonl`: the physically separate source-only model input. It contains no
  logical forms, reference hashes, augmented text, annotator metadata, or source paths.
- `generation_manifest.json`: a source-only seal for generation. It contains no reference
  availability, missing-reference identities, scorable counts, or reference-artifact hashes.
- `development_references.jsonl` and `test_references.jsonl`: physically split offline-only
  references. A development scorer never opens or deserializes locked-test logical forms.
- `agreement_annotations.jsonl`: three annotations for each of 20 sensitivity cases (60 rows).
- `source_manifest.jsonl`: byte length and SHA-256 lineage for all 2,060 upstream `.js` files.
- `split_assignments.json`: the frozen, trial-disjoint development/test assignment.
- `manifest.json` and `manifest.sha256`: import settings, audited counts, and artifact hashes.
- `ATTRIBUTION.md` and `LICENSE.upstream.txt`: required provenance and license notices.

The importer decodes only three bounded JavaScript string literals. It never executes
JavaScript; each logical-form body remains inert source text for a separate reviewed parser.
The three absent bodies are retained in the evaluation denominator rather than dropped or
fabricated. These annotations are benchmark references, not clinical advice or patient-level
eligibility decisions.
"""

# These trials are development-only.  The 20 agreement trials were used for
# annotator training and are retained only for sensitivity/ambiguity analysis.
# The final trials were manually inspected while designing or auditing the parser.
AGREEMENT_TRIAL_IDS = frozenset(
    {
        "NCT03860350",
        "NCT03861637",
        "NCT03861962",
        "NCT03862937",
        "NCT03863249",
        "NCT03863314",
        "NCT03863977",
        "NCT03865602",
        "NCT03866135",
        "NCT03867903",
        "NCT03920748",
        "NCT03922295",
        "NCT03926559",
        "NCT03926949",
        "NCT03927456",
        "NCT03927846",
        "NCT03928795",
        "NCT03929237",
        "NCT03929679",
        "NCT03931772",
    }
)
DISCLOSED_EXAMPLE_TRIAL_IDS = frozenset(
    {
        "NCT03860038",
        "NCT03860324",
        "NCT03862937",
        "NCT03865043",
        "NCT03925818",
    }
)
FORCED_DEVELOPMENT_TRIAL_IDS = AGREEMENT_TRIAL_IDS | DISCLOSED_EXAMPLE_TRIAL_IDS

SplitName = Literal["development", "test"]
Polarity = Literal["inclusion", "exclusion"]
AnnotationRole = Literal["primary", "agreement"]
AnnotatorId = Literal["annotator_1", "annotator_2", "annotator_3"]
ReferenceStatus = Literal["available", "missing_upstream"]

_SOURCE_PATH = re.compile(
    r"^leaf_logical_forms/"
    r"(?P<annotator>annotator_[123])/"
    r"(?P<group>batch[1-9][0-9]*|double_annotation)/"
    r"(?P<case_id>(?P<trial_id>NCT[0-9]{8})_(?P<criterion_index>[0-9]+))\.js$"
)
_NCT_ID = re.compile(r"^NCT[0-9]{8}$")
_CASE_ID = re.compile(r"^NCT[0-9]{8}_[0-9]+$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")


class LlfImportError(RuntimeError):
    """Raised when upstream bytes or annotation structure violate the pin."""


class LlfAnnotation(StrictModel):
    """One inert LLF annotation with complete source and reference lineage."""

    dataset_id: Literal["leaf-logical-forms"] = DATASET_ID
    dataset_version: Literal["llf-461288a"] = DATASET_VERSION
    case_id: Annotated[str, Field(pattern=r"^NCT[0-9]{8}_[0-9]+$")]
    trial_id: Annotated[str, Field(pattern=r"^NCT[0-9]{8}$")]
    criterion_index: Annotated[StrictInt, Field(ge=0)]
    split: SplitName
    polarity: Polarity
    raw_text: Annotated[str, Field(min_length=1)]
    augmented_text: Annotated[str, Field(min_length=1)]
    reference_status: ReferenceStatus
    logical_form: Annotated[str, Field(min_length=1)] | None
    annotator_id: AnnotatorId
    annotation_role: AnnotationRole
    source_path: Annotated[
        str,
        Field(
            pattern=(
                r"^leaf_logical_forms/annotator_[123]/"
                r"(?:batch[1-9][0-9]*|double_annotation)/"
                r"NCT[0-9]{8}_[0-9]+\.js$"
            )
        ),
    ]
    source_file_bytes: Annotated[StrictInt, Field(ge=1, le=MAX_SOURCE_FILE_BYTES)]
    source_file_sha256: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
    raw_text_sha256: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
    reference_sha256: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")] | None

    @model_validator(mode="after")
    def identifiers_and_hashes_match_content(self) -> LlfAnnotation:
        expected_case_id = f"{self.trial_id}_{self.criterion_index}"
        if self.case_id != expected_case_id:
            raise ValueError("case_id must equal trial_id plus criterion_index")
        if not self.source_path.endswith(f"/{self.case_id}.js"):
            raise ValueError("source_path filename must match case_id")
        if self.annotation_role == "primary" and "/double_annotation/" in self.source_path:
            raise ValueError("primary annotations must come from a batch directory")
        if self.annotation_role == "agreement" and "/double_annotation/" not in self.source_path:
            raise ValueError("agreement annotations must come from double_annotation")
        if self.raw_text_sha256 != _sha256_text(self.raw_text):
            raise ValueError("raw_text_sha256 does not match raw_text")
        if self.reference_status == "available":
            if self.logical_form is None or self.reference_sha256 is None:
                raise ValueError("available references require logical_form and reference_sha256")
            if self.reference_sha256 != _sha256_text(self.logical_form):
                raise ValueError("reference_sha256 does not match logical_form")
        elif self.logical_form is not None or self.reference_sha256 is not None:
            raise ValueError("missing_upstream references must not invent a logical form or hash")
        return self


class LlfGenerationRecord(StrictModel):
    """One source-only model input that cannot carry an LLF reference label."""

    case_id: Annotated[str, Field(pattern=r"^NCT[0-9]{8}_[0-9]+$")]
    trial_id: Annotated[str, Field(pattern=r"^NCT[0-9]{8}$")]
    split: SplitName
    polarity: Polarity
    source_text: Annotated[str, Field(min_length=1, max_length=1_000_000)]
    source_sha256: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]

    @model_validator(mode="after")
    def identity_and_hash_match_source(self) -> LlfGenerationRecord:
        if not self.case_id.startswith(f"{self.trial_id}_"):
            raise ValueError("case_id must belong to trial_id")
        if self.source_sha256 != _sha256_text(self.source_text):
            raise ValueError("source_sha256 does not match source_text")
        return self


class LlfAudit(StrictModel):
    """Audited source inventory before any output files are created."""

    upstream_commit: Literal["461288aeba8b37fabd43bd7c55f0e1cb1bb10b9e"]
    corpus_git_tree: Literal["f846019a2743b8ac20f5d0e7323347e8bcaedf15"]
    inventory_sha256: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
    license_sha256: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
    source_file_count: Literal[2060]
    primary_case_count: Literal[2000]
    available_primary_reference_count: Literal[1997]
    missing_upstream_primary_reference_count: Literal[3]
    missing_upstream_case_ids: tuple[str, ...]
    agreement_file_count: Literal[60]
    agreement_case_count: Literal[20]
    trial_count: Literal[885]
    duplicate_primary_case_count: Literal[0]
    agreement_annotations_per_case: Literal[3]


def _sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _sha256_text(value: str) -> str:
    return _sha256_bytes(value.encode("utf-8"))


def _skip_whitespace(text: str, cursor: int) -> int:
    while cursor < len(text) and text[cursor].isspace():
        cursor += 1
    return cursor


def _parse_hex_escape(text: str, cursor: int, width: int, source_name: str) -> tuple[str, int]:
    end = cursor + width
    digits = text[cursor:end]
    if len(digits) != width or any(
        character not in "0123456789abcdefABCDEF" for character in digits
    ):
        raise LlfImportError(f"{source_name}: malformed hexadecimal string escape")
    return chr(int(digits, 16)), end


def _parse_js_string_literal(text: str, cursor: int, source_name: str) -> tuple[str, int]:
    """Decode one bounded JavaScript string literal without executing code."""

    cursor = _skip_whitespace(text, cursor)
    if cursor >= len(text) or text[cursor] not in {"'", '"'}:
        raise LlfImportError(f"{source_name}: expected a JavaScript string literal")
    quote = text[cursor]
    cursor += 1
    decoded: list[str] = []
    simple_escapes = {
        "b": "\b",
        "f": "\f",
        "n": "\n",
        "r": "\r",
        "t": "\t",
        "v": "\v",
        "0": "\0",
        "\\": "\\",
        "'": "'",
        '"': '"',
    }

    while cursor < len(text):
        character = text[cursor]
        cursor += 1
        if character == quote:
            if cursor < len(text) and text[cursor] == ";":
                cursor += 1
            return "".join(decoded), cursor
        if character in {"\n", "\r", "\u2028", "\u2029"}:
            raise LlfImportError(f"{source_name}: unescaped line break in string literal")
        if character != "\\":
            decoded.append(character)
            continue
        if cursor >= len(text):
            raise LlfImportError(f"{source_name}: unterminated string escape")

        escaped = text[cursor]
        cursor += 1
        if escaped == "\r":
            if cursor < len(text) and text[cursor] == "\n":
                cursor += 1
            continue
        if escaped == "\n":
            continue
        if escaped == "x":
            value, cursor = _parse_hex_escape(text, cursor, 2, source_name)
            decoded.append(value)
            continue
        if escaped == "u":
            if cursor < len(text) and text[cursor] == "{":
                end = text.find("}", cursor + 1)
                if end < 0:
                    raise LlfImportError(f"{source_name}: unterminated Unicode string escape")
                digits = text[cursor + 1 : end]
                if not 1 <= len(digits) <= 6 or any(
                    character not in "0123456789abcdefABCDEF" for character in digits
                ):
                    raise LlfImportError(f"{source_name}: malformed Unicode string escape")
                codepoint = int(digits, 16)
                if codepoint > 0x10FFFF:
                    raise LlfImportError(f"{source_name}: Unicode escape is out of range")
                decoded.append(chr(codepoint))
                cursor = end + 1
            else:
                value, cursor = _parse_hex_escape(text, cursor, 4, source_name)
                decoded.append(value)
            continue
        if escaped in simple_escapes:
            if escaped == "0" and cursor < len(text) and text[cursor].isdigit():
                raise LlfImportError(f"{source_name}: legacy octal escapes are not supported")
            decoded.append(simple_escapes[escaped])
            continue
        if escaped.isdigit():
            raise LlfImportError(f"{source_name}: legacy numeric escapes are not supported")

        # JavaScript's non-escape characters cook to the character itself.
        decoded.append(escaped)

    raise LlfImportError(f"{source_name}: unterminated JavaScript string literal")


def parse_llf_source(
    payload: bytes,
    *,
    source_name: str = "<memory>",
) -> tuple[Polarity, str, str, str | None]:
    """Parse one LLF source file as inert data.

    Only the three leading string literals are decoded.  The logical form is
    returned as source text so a separate reviewed recursive-descent parser can
    interpret it later.
    """

    if not payload or len(payload) > MAX_SOURCE_FILE_BYTES:
        raise LlfImportError(
            f"{source_name}: source file must contain 1..{MAX_SOURCE_FILE_BYTES} bytes"
        )
    try:
        text = payload.decode("utf-8-sig", errors="strict")
    except UnicodeDecodeError as exc:
        raise LlfImportError(f"{source_name}: source file is not valid UTF-8") from exc

    marker, cursor = _parse_js_string_literal(text, 0, source_name)
    raw_text, cursor = _parse_js_string_literal(text, cursor, source_name)
    augmented_text, cursor = _parse_js_string_literal(text, cursor, source_name)
    logical_form = text[cursor:].strip() or None

    if marker not in {"INC", "EXC"}:
        raise LlfImportError(f"{source_name}: polarity marker must be INC or EXC")
    if not raw_text.strip() or not augmented_text.strip():
        raise LlfImportError(f"{source_name}: criterion text fields must be non-empty")
    polarity: Polarity = "inclusion" if marker == "INC" else "exclusion"
    return polarity, raw_text, augmented_text, logical_form


def _safe_source_files(upstream_root: Path) -> tuple[Path, ...]:
    root = upstream_root.resolve(strict=True)
    corpus_root = (root / "leaf_logical_forms").resolve(strict=True)
    if not corpus_root.is_relative_to(root):
        raise LlfImportError("leaf_logical_forms resolves outside upstream_root")
    files = tuple(
        sorted(
            corpus_root.rglob("*.js"),
            key=lambda path: path.relative_to(root).as_posix(),
        )
    )
    for path in files:
        if path.is_symlink() or not path.is_file():
            raise LlfImportError("LLF inventory must contain only regular non-symlink files")
        resolved = path.resolve(strict=True)
        if not resolved.is_relative_to(corpus_root):
            raise LlfImportError("LLF source path resolves outside the corpus directory")
    return files


def _inventory_sha256(source_snapshots: Iterable[tuple[str, bytes]]) -> str:
    digest = hashlib.sha256()
    for relative_path, payload in source_snapshots:
        digest.update(relative_path.encode("utf-8"))
        digest.update(b"\0")
        digest.update(str(len(payload)).encode("ascii"))
        digest.update(b"\0")
        digest.update(payload)
        digest.update(b"\0")
    return digest.hexdigest()


def _verify_detached_commit(upstream_root: Path) -> None:
    head_path = upstream_root / ".git" / "HEAD"
    try:
        head = head_path.read_text(encoding="ascii").strip()
    except (OSError, UnicodeError) as exc:
        raise LlfImportError(
            "upstream checkout must contain a readable detached .git/HEAD"
        ) from exc
    if head != UPSTREAM_COMMIT:
        raise LlfImportError(f"upstream checkout must be detached at {UPSTREAM_COMMIT}")


def _source_snapshot(path: Path, upstream_root: Path) -> tuple[re.Match[str], str, bytes]:
    """Read one verified source exactly once for both its digest and parser input."""

    relative_path = (
        path.resolve(strict=True).relative_to(upstream_root.resolve(strict=True)).as_posix()
    )
    match = _SOURCE_PATH.fullmatch(relative_path)
    if match is None:
        raise LlfImportError(f"unexpected LLF source path: {relative_path}")
    payload = path.read_bytes()
    return match, relative_path, payload


def _parse_inventory(
    upstream_root: Path,
) -> tuple[list[dict[str, object]], str, str, bytes]:
    root = upstream_root.resolve(strict=True)
    _verify_detached_commit(root)
    source_files = _safe_source_files(root)
    if len(source_files) != EXPECTED_SOURCE_FILES:
        raise LlfImportError(
            f"expected {EXPECTED_SOURCE_FILES} LLF files, found {len(source_files)}"
        )
    source_snapshots = tuple(_source_snapshot(path, root) for path in source_files)
    inventory_sha256 = _inventory_sha256(
        (relative_path, payload) for _match, relative_path, payload in source_snapshots
    )
    if inventory_sha256 != UPSTREAM_INVENTORY_SHA256:
        raise LlfImportError("LLF source inventory does not match the audited pinned commit")

    license_path = root / "LICENSE"
    license_payload = license_path.read_bytes()
    license_sha256 = _sha256_bytes(license_payload)
    if license_sha256 != UPSTREAM_LICENSE_SHA256:
        raise LlfImportError("upstream LICENSE does not match the audited pinned commit")

    annotations: list[dict[str, object]] = []
    for match, relative_path, payload in source_snapshots:
        polarity, raw_text, augmented_text, logical_form = parse_llf_source(
            payload,
            source_name=relative_path,
        )
        annotations.append(
            {
                "case_id": match.group("case_id"),
                "trial_id": match.group("trial_id"),
                "criterion_index": int(match.group("criterion_index")),
                "polarity": polarity,
                "raw_text": raw_text,
                "augmented_text": augmented_text,
                "reference_status": (
                    "available" if logical_form is not None else "missing_upstream"
                ),
                "logical_form": logical_form,
                "annotator_id": match.group("annotator"),
                "annotation_role": (
                    "agreement" if match.group("group") == "double_annotation" else "primary"
                ),
                "source_path": relative_path,
                "source_file_bytes": len(payload),
                "source_file_sha256": _sha256_bytes(payload),
                "raw_text_sha256": _sha256_text(raw_text),
                "reference_sha256": (
                    _sha256_text(logical_form) if logical_form is not None else None
                ),
            }
        )
    return annotations, inventory_sha256, license_sha256, license_payload


def _validate_inventory_structure(annotations: Sequence[Mapping[str, object]]) -> None:
    primary = [row for row in annotations if row["annotation_role"] == "primary"]
    agreement = [row for row in annotations if row["annotation_role"] == "agreement"]
    primary_counts = Counter(str(row["case_id"]) for row in primary)
    agreement_by_case: dict[str, list[Mapping[str, object]]] = defaultdict(list)
    for row in agreement:
        agreement_by_case[str(row["case_id"])].append(row)

    if len(primary) != EXPECTED_PRIMARY_CASES:
        raise LlfImportError("primary LLF case count does not match the audited corpus")
    if any(count != 1 for count in primary_counts.values()):
        raise LlfImportError("each primary LLF case_id must occur exactly once")
    if len(agreement) != EXPECTED_AGREEMENT_FILES:
        raise LlfImportError("agreement file count does not match the audited corpus")
    if len(agreement_by_case) != EXPECTED_AGREEMENT_CASES:
        raise LlfImportError("agreement case count does not match the audited corpus")
    if len({str(row["trial_id"]) for row in primary}) != EXPECTED_TRIALS:
        raise LlfImportError("LLF trial count does not match the audited corpus")

    missing_primary = [row for row in primary if row["reference_status"] == "missing_upstream"]
    available_primary = [row for row in primary if row["reference_status"] == "available"]
    missing_case_ids = {str(row["case_id"]) for row in missing_primary}
    if len(available_primary) != EXPECTED_AVAILABLE_PRIMARY_REFERENCES:
        raise LlfImportError("available primary reference count does not match the audited corpus")
    if len(missing_primary) != EXPECTED_MISSING_UPSTREAM_PRIMARY_REFERENCES:
        raise LlfImportError("missing-upstream primary reference count does not match the audit")
    if missing_case_ids != EXPECTED_MISSING_UPSTREAM_CASE_IDS:
        raise LlfImportError(
            "missing-upstream case identities do not match the audited pinned corpus"
        )
    if any(row["reference_status"] != "available" for row in agreement):
        raise LlfImportError("all audited agreement annotations must have a reference")

    primary_by_case = {str(row["case_id"]): row for row in primary}
    for case_id, rows in agreement_by_case.items():
        if case_id not in primary_by_case:
            raise LlfImportError("agreement annotation has no corresponding primary case")
        if len(rows) != 3 or {str(row["annotator_id"]) for row in rows} != {
            "annotator_1",
            "annotator_2",
            "annotator_3",
        }:
            raise LlfImportError("each agreement case must have exactly three annotators")
        primary_row = primary_by_case[case_id]
        for row in rows:
            for field in ("trial_id", "criterion_index", "polarity", "raw_text"):
                if row[field] != primary_row[field]:
                    raise LlfImportError(
                        f"agreement annotation {case_id} disagrees on source field {field}"
                    )


def _split_assignments(
    primary_rows: Sequence[Mapping[str, object]],
    *,
    target_cases: int = DEVELOPMENT_TARGET_CASES,
    seed: str = SPLIT_SEED,
) -> dict[str, SplitName]:
    if target_cases <= 0:
        raise ValueError("target_cases must be positive")
    if not seed:
        raise ValueError("seed must be non-empty")
    trial_counts = Counter(str(row["trial_id"]) for row in primary_rows)
    missing_forced = FORCED_DEVELOPMENT_TRIAL_IDS - trial_counts.keys()
    if missing_forced:
        raise LlfImportError(f"forced development trials are absent: {sorted(missing_forced)}")

    development_trials = set(FORCED_DEVELOPMENT_TRIAL_IDS)
    development_cases = sum(trial_counts[trial_id] for trial_id in development_trials)
    candidates = sorted(
        trial_counts.keys() - development_trials,
        key=lambda trial_id: (hashlib.sha256(f"{seed}\0{trial_id}".encode()).digest(), trial_id),
    )
    for trial_id in candidates:
        if development_cases >= target_cases:
            break
        development_trials.add(trial_id)
        development_cases += trial_counts[trial_id]

    return {
        trial_id: "development" if trial_id in development_trials else "test"
        for trial_id in sorted(trial_counts)
    }


def validate_trial_disjoint(records: Sequence[LlfAnnotation]) -> None:
    """Prove that each trial appears in one and only one split."""

    split_by_trial: dict[str, SplitName] = {}
    for record in records:
        previous = split_by_trial.setdefault(record.trial_id, record.split)
        if previous != record.split:
            raise LlfImportError(f"trial {record.trial_id} occurs in multiple splits")


def load_llf_records(path: Path) -> tuple[LlfAnnotation, ...]:
    """Load a deterministic JSONL artifact through the strict record contract."""

    return load_llf_records_bytes(path.read_bytes(), source_name=str(path))


def load_llf_records_bytes(
    payload: bytes,
    *,
    source_name: str,
) -> tuple[LlfAnnotation, ...]:
    """Parse one already-verified JSONL byte snapshot without reopening its path."""

    try:
        text = payload.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise LlfImportError(f"{source_name}: JSONL is not valid UTF-8") from exc
    records: list[LlfAnnotation] = []
    for line_number, line in enumerate(text.splitlines(keepends=True), start=1):
        if not line.endswith("\n") or line.endswith("\r\n"):
            raise LlfImportError(f"{source_name}:{line_number}: JSONL line must end with LF")
        try:
            records.append(LlfAnnotation.model_validate_json(line))
        except ValueError as exc:
            raise LlfImportError(f"{source_name}:{line_number}: invalid LLF record") from exc
    validate_trial_disjoint(records)
    return tuple(records)


def load_llf_generation_records(path: Path) -> tuple[LlfGenerationRecord, ...]:
    """Read the physical source-only generation artifact exactly once."""

    return load_llf_generation_records_bytes(path.read_bytes(), source_name=str(path))


def load_llf_generation_records_bytes(
    payload: bytes,
    *,
    source_name: str,
) -> tuple[LlfGenerationRecord, ...]:
    """Parse a verified source-only JSONL byte snapshot without reopening its path."""

    try:
        text = payload.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise LlfImportError(f"{source_name}: JSONL is not valid UTF-8") from exc
    records: list[LlfGenerationRecord] = []
    for line_number, line in enumerate(text.splitlines(keepends=True), start=1):
        if not line.endswith("\n") or line.endswith("\r\n"):
            raise LlfImportError(f"{source_name}:{line_number}: JSONL line must end with LF")
        try:
            records.append(LlfGenerationRecord.model_validate_json(line))
        except ValueError as exc:
            raise LlfImportError(
                f"{source_name}:{line_number}: invalid LLF generation record"
            ) from exc
    case_ids = [record.case_id for record in records]
    if len(case_ids) != len(set(case_ids)):
        raise LlfImportError(f"{source_name}: duplicate generation case IDs")
    if case_ids != sorted(case_ids):
        raise LlfImportError(f"{source_name}: generation cases are not in case-ID order")
    split_by_trial: dict[str, SplitName] = {}
    for record in records:
        previous = split_by_trial.setdefault(record.trial_id, record.split)
        if previous != record.split:
            raise LlfImportError(f"{source_name}: trial occurs in multiple splits")
    return tuple(records)


def audit_llf(upstream_root: Path) -> LlfAudit:
    """Verify all pinned bytes and the complete corpus structure without writing output."""

    annotations, inventory_sha256, license_sha256, _license_payload = _parse_inventory(
        upstream_root
    )
    _validate_inventory_structure(annotations)
    primary = [row for row in annotations if row["annotation_role"] == "primary"]
    agreement = [row for row in annotations if row["annotation_role"] == "agreement"]
    return LlfAudit(
        upstream_commit=UPSTREAM_COMMIT,
        corpus_git_tree=UPSTREAM_CORPUS_TREE,
        inventory_sha256=inventory_sha256,
        license_sha256=license_sha256,
        source_file_count=len(annotations),
        primary_case_count=len(primary),
        available_primary_reference_count=sum(
            row["reference_status"] == "available" for row in primary
        ),
        missing_upstream_primary_reference_count=sum(
            row["reference_status"] == "missing_upstream" for row in primary
        ),
        missing_upstream_case_ids=tuple(
            sorted(
                str(row["case_id"])
                for row in primary
                if row["reference_status"] == "missing_upstream"
            )
        ),
        agreement_file_count=len(agreement),
        agreement_case_count=len({str(row["case_id"]) for row in agreement}),
        trial_count=len({str(row["trial_id"]) for row in primary}),
        duplicate_primary_case_count=(len(primary) - len({str(row["case_id"]) for row in primary})),
        agreement_annotations_per_case=3,
    )


def _canonical_json_bytes(value: object, *, pretty: bool = False) -> bytes:
    if pretty:
        serialized = json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True)
    else:
        serialized = json.dumps(
            value,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )
    return (serialized + "\n").encode("utf-8")


def _jsonl_bytes(records: Sequence[LlfAnnotation]) -> bytes:
    return b"".join(
        _canonical_json_bytes(record.model_dump(mode="json"), pretty=False) for record in records
    )


def _generation_jsonl_bytes(records: Sequence[LlfAnnotation]) -> bytes:
    return b"".join(
        _canonical_json_bytes(
            LlfGenerationRecord(
                case_id=record.case_id,
                trial_id=record.trial_id,
                split=record.split,
                polarity=record.polarity,
                source_text=record.raw_text,
                source_sha256=record.raw_text_sha256,
            ).model_dump(mode="json"),
            pretty=False,
        )
        for record in records
    )


def _source_manifest_bytes(records: Sequence[LlfAnnotation]) -> bytes:
    fields = {
        "dataset_id",
        "dataset_version",
        "case_id",
        "trial_id",
        "criterion_index",
        "split",
        "annotator_id",
        "annotation_role",
        "reference_status",
        "source_path",
        "source_file_bytes",
        "source_file_sha256",
        "raw_text_sha256",
        "reference_sha256",
    }
    ordered = sorted(records, key=lambda record: record.source_path)
    return b"".join(
        _canonical_json_bytes(
            record.model_dump(mode="json", include=fields),
            pretty=False,
        )
        for record in ordered
    )


def _artifact(path: str, payload: bytes, record_count: int | None = None) -> dict[str, object]:
    artifact: dict[str, object] = {
        "path": path,
        "bytes": len(payload),
        "sha256": _sha256_bytes(payload),
    }
    if record_count is not None:
        artifact["record_count"] = record_count
    return artifact


def _source_case_set_sha256(records: Sequence[LlfAnnotation]) -> str:
    identities = [
        {
            "case_id": record.case_id,
            "trial_id": record.trial_id,
            "document_id": record.case_id,
            "criterion_kind": record.polarity,
            "source_sha256": record.raw_text_sha256,
        }
        for record in records
    ]
    payload = json.dumps(
        identities,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return _sha256_bytes(payload)


def _atomic_write(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        temporary.write_bytes(payload)
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def import_llf(upstream_root: Path, output_dir: Path) -> dict[str, object]:
    """Verify pinned upstream data and write deterministic, attributed artifacts."""

    annotations, inventory_sha256, license_sha256, license_payload = _parse_inventory(upstream_root)
    _validate_inventory_structure(annotations)
    primary_rows = [row for row in annotations if row["annotation_role"] == "primary"]
    agreement_rows = [row for row in annotations if row["annotation_role"] == "agreement"]
    split_by_trial = _split_assignments(primary_rows)

    primary = tuple(
        LlfAnnotation.model_validate({**row, "split": split_by_trial[str(row["trial_id"])]})
        for row in sorted(primary_rows, key=lambda row: str(row["case_id"]))
    )
    agreement = tuple(
        LlfAnnotation.model_validate({**row, "split": split_by_trial[str(row["trial_id"])]})
        for row in sorted(
            agreement_rows,
            key=lambda row: (str(row["case_id"]), str(row["annotator_id"])),
        )
    )
    validate_trial_disjoint((*primary, *agreement))

    primary_payload = _jsonl_bytes(primary)
    generation_payload = _generation_jsonl_bytes(primary)
    agreement_payload = _jsonl_bytes(agreement)
    source_manifest_payload = _source_manifest_bytes((*primary, *agreement))
    attribution_payload = ATTRIBUTION_TEXT.encode("utf-8")
    readme_payload = DATASET_README_TEXT.encode("utf-8")
    development = tuple(record for record in primary if record.split == "development")
    test = tuple(record for record in primary if record.split == "test")
    development_reference_payload = _jsonl_bytes(development)
    test_reference_payload = _jsonl_bytes(test)
    assignment_payload = _canonical_json_bytes(
        {
            "schema_version": IMPORT_SCHEMA_VERSION,
            "algorithm": SPLIT_ALGORITHM,
            "seed": SPLIT_SEED,
            "unit": "trial_id",
            "assignments": [
                {"split": split_by_trial[trial_id], "trial_id": trial_id}
                for trial_id in sorted(split_by_trial)
            ],
        },
        pretty=True,
    )

    generation_manifest_payload: dict[str, object] = {
        "schema_version": GENERATION_MANIFEST_SCHEMA_VERSION,
        "dataset_id": DATASET_ID,
        "dataset_version": DATASET_VERSION,
        "upstream": {
            "repository": UPSTREAM_REPOSITORY,
            "commit": UPSTREAM_COMMIT,
            "corpus_git_tree_sha1": UPSTREAM_CORPUS_TREE,
            "inventory_sha256": inventory_sha256,
        },
        "safety": {
            "source_only": True,
            "reference_availability_present": False,
            "missing_reference_identities_present": False,
            "scorable_counts_present": False,
            "reference_artifact_hashes_present": False,
        },
        "artifacts": [
            _artifact("generation_cases.jsonl", generation_payload, len(primary)),
            _artifact("split_assignments.json", assignment_payload, len(split_by_trial)),
        ],
        "split": {
            "algorithm": SPLIT_ALGORITHM,
            "seed": SPLIT_SEED,
            "unit": "trial_id",
            "development": {
                "cases": len(development),
                "trials": len({record.trial_id for record in development}),
                "case_set_sha256": _source_case_set_sha256(development),
            },
            "test": {
                "cases": len(test),
                "trials": len({record.trial_id for record in test}),
                "case_set_sha256": _source_case_set_sha256(test),
            },
        },
    }
    generation_manifest = {
        **generation_manifest_payload,
        "canonical_payload_sha256": _sha256_bytes(
            _canonical_json_bytes(generation_manifest_payload)
        ),
    }
    generation_manifest_bytes = _canonical_json_bytes(generation_manifest, pretty=True)

    manifest_payload: dict[str, object] = {
        "schema_version": IMPORT_SCHEMA_VERSION,
        "dataset_id": DATASET_ID,
        "dataset_version": DATASET_VERSION,
        "upstream": {
            "repository": UPSTREAM_REPOSITORY,
            "commit": UPSTREAM_COMMIT,
            "commit_url": UPSTREAM_COMMIT_URL,
            "corpus_path": "leaf_logical_forms",
            "corpus_git_tree_sha1": UPSTREAM_CORPUS_TREE,
            "inventory_sha256": inventory_sha256,
            "inventory_hash_algorithm": "sha256(path_utf8 + NUL + size_ascii + NUL + bytes + NUL)",
            "license": UPSTREAM_LICENSE,
            "license_path": "LICENSE.upstream.txt",
            "license_sha256": license_sha256,
        },
        "safety": {
            "javascript_executed": False,
            "generation_reference_isolation": "physical_source_only_artifact",
            "parser": "bounded-js-string-header-v1",
            "logical_form_interpretation": "inert_source_text",
            "maximum_source_file_bytes": MAX_SOURCE_FILE_BYTES,
        },
        "counts": {
            "source_files": len(annotations),
            "primary_cases": len(primary),
            "available_primary_references": sum(
                record.reference_status == "available" for record in primary
            ),
            "missing_upstream_primary_references": sum(
                record.reference_status == "missing_upstream" for record in primary
            ),
            "missing_upstream_case_ids": sorted(
                record.case_id
                for record in primary
                if record.reference_status == "missing_upstream"
            ),
            "unique_trials": len({record.trial_id for record in primary}),
            "agreement_files": len(agreement),
            "agreement_cases": len({record.case_id for record in agreement}),
            "duplicate_primary_case_ids": (
                len(primary) - len({record.case_id for record in primary})
            ),
        },
        "split": {
            "algorithm": SPLIT_ALGORITHM,
            "seed": SPLIT_SEED,
            "unit": "trial_id",
            "development_target_cases": DEVELOPMENT_TARGET_CASES,
            "forced_development_trial_ids": sorted(FORCED_DEVELOPMENT_TRIAL_IDS),
            "forced_development_reasons": {
                "annotator_training_and_agreement_analysis": sorted(AGREEMENT_TRIAL_IDS),
                "manually_inspected_disclosed_examples": sorted(DISCLOSED_EXAMPLE_TRIAL_IDS),
            },
            "assignment_sha256": _sha256_bytes(assignment_payload),
            "development": {
                "cases": len(development),
                "trials": len({record.trial_id for record in development}),
            },
            "test": {
                "cases": len(test),
                "trials": len({record.trial_id for record in test}),
            },
            "trial_disjoint": True,
        },
        "agreement": {
            "use": "sensitivity_and_ambiguity_analysis_only",
            "annotations_per_case": 3,
            "cases_also_have_one_primary_annotation": True,
        },
        "artifacts": [
            _artifact("records.jsonl", primary_payload, len(primary)),
            _artifact("generation_cases.jsonl", generation_payload, len(primary)),
            _artifact("generation_manifest.json", generation_manifest_bytes),
            _artifact(
                "development_references.jsonl",
                development_reference_payload,
                len(development),
            ),
            _artifact("test_references.jsonl", test_reference_payload, len(test)),
            _artifact("agreement_annotations.jsonl", agreement_payload, len(agreement)),
            _artifact(
                "source_manifest.jsonl",
                source_manifest_payload,
                len(primary) + len(agreement),
            ),
            _artifact("split_assignments.json", assignment_payload, len(split_by_trial)),
            _artifact("ATTRIBUTION.md", attribution_payload),
            _artifact("LICENSE.upstream.txt", license_payload),
            _artifact("README.md", readme_payload),
        ],
    }
    canonical_payload_sha256 = _sha256_bytes(_canonical_json_bytes(manifest_payload))
    manifest = {**manifest_payload, "canonical_payload_sha256": canonical_payload_sha256}
    manifest_bytes = _canonical_json_bytes(manifest, pretty=True)
    manifest_hash_bytes = f"{_sha256_bytes(manifest_bytes)}  manifest.json\n".encode("ascii")

    output_dir = output_dir.resolve()
    _atomic_write(output_dir / "records.jsonl", primary_payload)
    _atomic_write(output_dir / "generation_cases.jsonl", generation_payload)
    _atomic_write(output_dir / "generation_manifest.json", generation_manifest_bytes)
    _atomic_write(
        output_dir / "development_references.jsonl",
        development_reference_payload,
    )
    _atomic_write(output_dir / "test_references.jsonl", test_reference_payload)
    _atomic_write(output_dir / "agreement_annotations.jsonl", agreement_payload)
    _atomic_write(output_dir / "source_manifest.jsonl", source_manifest_payload)
    _atomic_write(output_dir / "split_assignments.json", assignment_payload)
    _atomic_write(output_dir / "ATTRIBUTION.md", attribution_payload)
    _atomic_write(output_dir / "LICENSE.upstream.txt", license_payload)
    _atomic_write(output_dir / "README.md", readme_payload)
    _atomic_write(output_dir / "manifest.json", manifest_bytes)
    _atomic_write(output_dir / "manifest.sha256", manifest_hash_bytes)
    return manifest


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--upstream-root", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path)
    parser.add_argument(
        "--audit-only",
        action="store_true",
        help="verify and print bounded metadata without writing dataset artifacts",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    if args.audit_only:
        audit = audit_llf(args.upstream_root)
        print(json.dumps(audit.model_dump(mode="json"), indent=2, sort_keys=True))
        return 0
    if args.output_dir is None:
        raise SystemExit("--output-dir is required unless --audit-only is used")
    manifest = import_llf(args.upstream_root, args.output_dir)
    print(json.dumps(manifest["counts"], indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
