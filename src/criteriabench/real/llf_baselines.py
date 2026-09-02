"""Deterministic, zero-network retrieval baselines for the Real v1 LLF track.

This module deliberately accepts already-loaded, typed objects only.  It has no
file, environment, provider, or network entry point, so callers must keep the
source-only generation artifact and the offline scoring references separated at
the process boundary that loads them.
"""

from __future__ import annotations

import hashlib
import json
import math
import unicodedata
from collections import Counter
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Final, Literal

from criteriabench.real.llf_semantics import (
    LlfGenerationCase,
    LlfScoringReference,
    LlfSemanticOutput,
)

BASELINE_ID: Final = "llf-bm25-nearest-development-v1"
BASELINE_IDENTITY_SCHEMA_VERSION: Final = "llf-retrieval-baseline-identity-v1"
TOKENIZER_VERSION: Final = "nfkc-casefold-alnum-marks-v1"

_BM25_K1: Final = 1.2
_BM25_B: Final = 0.75
_CONFIGURATION_ITEMS: Final[tuple[tuple[str, object], ...]] = (
    ("baseline_id", BASELINE_ID),
    ("ranking", "okapi-bm25"),
    ("k1", "1.2"),
    ("b", "0.75"),
    ("idf", "ln(1 + (N - df + 0.5) / (df + 0.5))"),
    ("query_term_frequency", "binary"),
    ("tokenizer", TOKENIZER_VERSION),
    ("candidate_polarity", "exact-match"),
    ("development_exclusion", "entire-target-trial"),
    ("test_training_split", "development-only"),
    ("tie_break", "ascending-development-case-id"),
)
_CODE_CONTRACT: Final[tuple[str, ...]] = (
    "Materialize inputs once and require an exact one-to-one case_id join.",
    "Require every training case and reference to be in the development split.",
    "Require joined case_id, trial_id, and criterion source_sha256 values to match.",
    "Recompute criterion source_sha256 from UTF-8 text and recheck logical-form hash lineage.",
    "Recompute and validate target criterion identity and source_sha256 before ranking.",
    (
        "Normalize text with NFKC, casefold, then NFKC; tokenize Unicode letters, "
        "numbers, and combining marks."
    ),
    "Restrict candidates to the target polarity.",
    (
        "For development targets, exclude the target trial from candidates and all "
        "BM25 corpus statistics."
    ),
    "For test targets, use every joined development example of the target polarity.",
    "Score unique sorted query terms with Okapi BM25 using the frozen configuration.",
    "Break equal-score ties by ascending development case_id.",
    (
        "Return a newly validated identity-free LlfSemanticOutput containing only the "
        "copied LLF node table."
    ),
)


def _canonical_sha256(value: object) -> str:
    payload = json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


BASELINE_CONFIGURATION_SHA256: Final = _canonical_sha256(dict(_CONFIGURATION_ITEMS))
BASELINE_ALGORITHM_CONTRACT_SHA256: Final = _canonical_sha256(
    {
        "algorithm_contract": _CODE_CONTRACT,
        "implementation": "criteriabench.real.llf_baselines:LlfBm25RetrievalBaseline",
    }
)
# SHA-256 of this module's UTF-8 source after CRLF-to-LF normalization and replacing
# the 64 hex characters in this assignment with 64 ASCII zeroes.
BASELINE_CODE_SHA256: Final = "e2626cb7aeb8b7117e4f4aacf5bba583565b787faef2bde84117361300fa7b1d"
BASELINE_IDENTITY_SHA256: Final = _canonical_sha256(
    {
        "schema_version": BASELINE_IDENTITY_SCHEMA_VERSION,
        "baseline_id": BASELINE_ID,
        "configuration_sha256": BASELINE_CONFIGURATION_SHA256,
        "code_sha256": BASELINE_CODE_SHA256,
    }
)


class LlfBaselineDataError(ValueError):
    """Training inputs are incomplete, duplicated, or provenance-inconsistent."""


class LlfBaselineUnavailableError(RuntimeError):
    """The frozen candidate restrictions leave no admissible neighbor."""


@dataclass(frozen=True, slots=True)
class LlfRetrievalBaselineIdentity:
    """Frozen algorithm identity suitable for inclusion in result artifacts."""

    schema_version: Literal["llf-retrieval-baseline-identity-v1"]
    baseline_id: Literal["llf-bm25-nearest-development-v1"]
    configuration_sha256: str
    code_sha256: str
    identity_sha256: str

    def as_dict(self) -> dict[str, str]:
        """Return a fresh JSON-compatible identity mapping."""

        return {
            "schema_version": self.schema_version,
            "baseline_id": self.baseline_id,
            "configuration_sha256": self.configuration_sha256,
            "code_sha256": self.code_sha256,
            "identity_sha256": self.identity_sha256,
        }


FROZEN_BASELINE_IDENTITY: Final = LlfRetrievalBaselineIdentity(
    schema_version="llf-retrieval-baseline-identity-v1",
    baseline_id="llf-bm25-nearest-development-v1",
    configuration_sha256=BASELINE_CONFIGURATION_SHA256,
    code_sha256=BASELINE_CODE_SHA256,
    identity_sha256=BASELINE_IDENTITY_SHA256,
)


def unicode_tokens(text: str) -> tuple[str, ...]:
    """Tokenize text deterministically without ASCII-only assumptions.

    Combining marks remain attached when they follow a letter or number.  All
    other characters, including underscores and punctuation, are boundaries.
    """

    normalized = unicodedata.normalize(
        "NFKC",
        unicodedata.normalize("NFKC", text).casefold(),
    )
    tokens: list[str] = []
    current: list[str] = []
    for character in normalized:
        category = unicodedata.category(character)
        if character.isalnum() or (current and category.startswith("M")):
            current.append(character)
            continue
        if current:
            tokens.append("".join(current))
            current.clear()
    if current:
        tokens.append("".join(current))
    return tuple(tokens)


@dataclass(frozen=True, slots=True)
class _TrainingExample:
    case: LlfGenerationCase
    reference: LlfScoringReference
    term_frequencies: Mapping[str, int]
    token_count: int


def _join_development_inputs(
    cases: Iterable[LlfGenerationCase],
    references: Iterable[LlfScoringReference],
) -> tuple[_TrainingExample, ...]:
    case_by_id: dict[str, LlfGenerationCase] = {}
    for case in tuple(cases):
        if not isinstance(case, LlfGenerationCase):
            raise TypeError("cases must contain only LlfGenerationCase objects")
        if case.case_id in case_by_id:
            raise LlfBaselineDataError(f"duplicate development case identity: {case.case_id}")
        if case.split != "development":
            raise LlfBaselineDataError("training cases must all be in the development split")
        if not case.case_id.startswith(f"{case.trial_id}_"):
            raise LlfBaselineDataError(f"case and trial identity mismatch for {case.case_id}")
        observed_source_sha256 = hashlib.sha256(case.source_text.encode("utf-8")).hexdigest()
        if case.source_sha256 != observed_source_sha256:
            raise LlfBaselineDataError(f"criterion source bytes are tampered for {case.case_id}")
        case_by_id[case.case_id] = case

    reference_by_id: dict[str, LlfScoringReference] = {}
    for reference in tuple(references):
        if not isinstance(reference, LlfScoringReference):
            raise TypeError("references must contain only LlfScoringReference objects")
        if reference.case_id in reference_by_id:
            raise LlfBaselineDataError(
                f"duplicate development reference identity: {reference.case_id}"
            )
        if reference.split != "development":
            raise LlfBaselineDataError("training references must all be in the development split")
        if not reference.case_id.startswith(f"{reference.trial_id}_"):
            raise LlfBaselineDataError(
                f"reference and trial identity mismatch for {reference.case_id}"
            )
        if reference.reference_sha256 != reference.reference.source_sha256:
            raise LlfBaselineDataError(
                f"logical-form reference lineage is tampered for {reference.case_id}"
            )
        reference_by_id[reference.case_id] = reference

    if not case_by_id or not reference_by_id:
        raise LlfBaselineDataError("development cases and references must both be non-empty")
    if case_by_id.keys() != reference_by_id.keys():
        missing_references = sorted(case_by_id.keys() - reference_by_id.keys())
        missing_cases = sorted(reference_by_id.keys() - case_by_id.keys())
        raise LlfBaselineDataError(
            "development identities do not join one-to-one "
            f"(missing_references={missing_references}, missing_cases={missing_cases})"
        )

    examples: list[_TrainingExample] = []
    for case_id in sorted(case_by_id):
        case = case_by_id[case_id]
        reference = reference_by_id[case_id]
        if case.trial_id != reference.trial_id:
            raise LlfBaselineDataError(f"trial identity mismatch for {case_id}")
        if case.source_sha256 != reference.source_sha256:
            raise LlfBaselineDataError(f"criterion source hash mismatch for {case_id}")
        tokens = unicode_tokens(case.source_text)
        examples.append(
            _TrainingExample(
                case=case,
                reference=reference,
                term_frequencies=MappingProxyType(dict(Counter(tokens))),
                token_count=len(tokens),
            )
        )
    return tuple(examples)


def _training_set_sha256(examples: tuple[_TrainingExample, ...]) -> str:
    return _canonical_sha256(
        [
            {
                "case_id": example.case.case_id,
                "trial_id": example.case.trial_id,
                "split": example.case.split,
                "polarity": example.case.polarity,
                "source_sha256": example.case.source_sha256,
                "reference_sha256": example.reference.reference_sha256,
            }
            for example in examples
        ]
    )


class LlfBm25RetrievalBaseline:
    """Frozen nearest-development LLF baseline with split-safe prediction rules."""

    __slots__ = ("_examples", "_training_set_sha256")

    def __init__(self, examples: tuple[_TrainingExample, ...]) -> None:
        self._examples = examples
        self._training_set_sha256 = _training_set_sha256(examples)

    @classmethod
    def from_development(
        cls,
        cases: Iterable[LlfGenerationCase],
        references: Iterable[LlfScoringReference],
    ) -> LlfBm25RetrievalBaseline:
        """Strictly join already-loaded development source cases and references."""

        return cls(_join_development_inputs(cases, references))

    @property
    def identity(self) -> LlfRetrievalBaselineIdentity:
        return FROZEN_BASELINE_IDENTITY

    @property
    def training_case_count(self) -> int:
        return len(self._examples)

    @property
    def training_trial_count(self) -> int:
        return len({example.case.trial_id for example in self._examples})

    @property
    def training_set_sha256(self) -> str:
        """Bind the joined development identities and both source/reference hashes."""

        return self._training_set_sha256

    def _eligible_examples(self, target: LlfGenerationCase) -> tuple[_TrainingExample, ...]:
        if target.split == "development":
            eligible = tuple(
                example
                for example in self._examples
                if example.case.polarity == target.polarity
                and example.case.trial_id != target.trial_id
            )
        else:
            eligible = tuple(
                example for example in self._examples if example.case.polarity == target.polarity
            )
        if not eligible:
            exclusion = " after leave-one-trial-out" if target.split == "development" else ""
            raise LlfBaselineUnavailableError(
                f"no same-polarity development neighbor is available{exclusion}"
            )
        return eligible

    @staticmethod
    def _document_frequencies(examples: tuple[_TrainingExample, ...]) -> Counter[str]:
        frequencies: Counter[str] = Counter()
        for example in examples:
            frequencies.update(example.term_frequencies.keys())
        return frequencies

    @staticmethod
    def _score(
        example: _TrainingExample,
        *,
        query_terms: tuple[str, ...],
        document_frequencies: Mapping[str, int],
        corpus_size: int,
        average_document_length: float,
    ) -> float:
        contributions: list[float] = []
        length_ratio = example.token_count / average_document_length
        length_normalization = _BM25_K1 * (1.0 - _BM25_B + _BM25_B * length_ratio)
        for term in query_terms:
            term_frequency = example.term_frequencies.get(term, 0)
            if term_frequency == 0:
                continue
            document_frequency = document_frequencies[term]
            inverse_document_frequency = math.log1p(
                (corpus_size - document_frequency + 0.5) / (document_frequency + 0.5)
            )
            contributions.append(
                inverse_document_frequency
                * (term_frequency * (_BM25_K1 + 1.0))
                / (term_frequency + length_normalization)
            )
        return math.fsum(contributions)

    def predict(self, target: LlfGenerationCase) -> LlfSemanticOutput:
        """Copy the nearest admissible development AST without output identity fields."""

        if not isinstance(target, LlfGenerationCase):
            raise TypeError("target must be an LlfGenerationCase")
        if not target.case_id.startswith(f"{target.trial_id}_"):
            raise LlfBaselineDataError(
                f"target case and trial identity mismatch for {target.case_id}"
            )
        observed_source_sha256 = hashlib.sha256(target.source_text.encode("utf-8")).hexdigest()
        if target.source_sha256 != observed_source_sha256:
            raise LlfBaselineDataError(
                f"target criterion source bytes are tampered for {target.case_id}"
            )
        eligible = self._eligible_examples(target)
        corpus_size = len(eligible)
        total_document_length = sum(example.token_count for example in eligible)
        average_document_length = total_document_length / corpus_size
        if average_document_length == 0.0:
            average_document_length = 1.0
        document_frequencies = self._document_frequencies(eligible)
        query_terms = tuple(sorted(set(unicode_tokens(target.source_text))))

        ranked = sorted(
            eligible,
            key=lambda example: (
                -self._score(
                    example,
                    query_terms=query_terms,
                    document_frequencies=document_frequencies,
                    corpus_size=corpus_size,
                    average_document_length=average_document_length,
                ),
                example.case.case_id,
            ),
        )
        nearest = ranked[0].reference.reference
        return LlfSemanticOutput(
            root_node_id=nearest.root_node_id,
            nodes=nearest.nodes,
        )

    def predict_many(
        self,
        targets: Iterable[LlfGenerationCase],
    ) -> tuple[LlfSemanticOutput, ...]:
        """Predict in caller order using the same immutable development index."""

        return tuple(self.predict(target) for target in targets)


def build_llf_bm25_baseline(
    development_cases: Iterable[LlfGenerationCase],
    development_references: Iterable[LlfScoringReference],
) -> LlfBm25RetrievalBaseline:
    """Build the frozen BM25 baseline from in-memory typed development inputs."""

    return LlfBm25RetrievalBaseline.from_development(
        development_cases,
        development_references,
    )


__all__ = [
    "BASELINE_ALGORITHM_CONTRACT_SHA256",
    "BASELINE_CODE_SHA256",
    "BASELINE_CONFIGURATION_SHA256",
    "BASELINE_ID",
    "BASELINE_IDENTITY_SHA256",
    "FROZEN_BASELINE_IDENTITY",
    "TOKENIZER_VERSION",
    "LlfBaselineDataError",
    "LlfBaselineUnavailableError",
    "LlfBm25RetrievalBaseline",
    "LlfRetrievalBaselineIdentity",
    "build_llf_bm25_baseline",
    "unicode_tokens",
]
