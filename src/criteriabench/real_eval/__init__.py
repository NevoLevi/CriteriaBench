"""Provider-neutral Real v1 generation and offline evaluation contracts."""

from criteriabench.real_eval.generation import (
    FLAT_GRAPH_OUTPUT_SCHEMA_SHA256,
    BackendFailure,
    BackendOutcome,
    BackendSuccess,
    ProviderRequest,
    RealGenerationBackend,
    generate_bundle,
)
from criteriabench.real_eval.llf_binding import (
    LLF_GENERATION_MANIFEST_SHA256,
    LlfBindingError,
    LlfGenerationSplit,
    load_llf_generation_split,
)
from criteriabench.real_eval.metrics import GraphComparison, compare_graphs
from criteriabench.real_eval.scoring import score_bundle

__all__ = [
    "FLAT_GRAPH_OUTPUT_SCHEMA_SHA256",
    "LLF_GENERATION_MANIFEST_SHA256",
    "BackendFailure",
    "BackendOutcome",
    "BackendSuccess",
    "GraphComparison",
    "LlfBindingError",
    "LlfGenerationSplit",
    "ProviderRequest",
    "RealGenerationBackend",
    "compare_graphs",
    "generate_bundle",
    "load_llf_generation_split",
    "score_bundle",
]
