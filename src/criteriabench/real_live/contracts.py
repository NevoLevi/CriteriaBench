"""Strict, sealed contracts for guarded real-data Luna runs.

The live layer is deliberately generic over an identity-free Structured Outputs
contract.  GraphV2 is one supported product contract; the locked LLF quality
benchmark must use a separate, lossless LLF-semantic contract.
"""

from __future__ import annotations

import hashlib
import importlib.metadata
import json
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path
from typing import Annotated, Literal, cast

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictBool,
    StrictInt,
    StrictStr,
    model_validator,
)

from criteriabench.domain.schemas import StrictModel
from criteriabench.real.graph_v2 import (
    FlatGraphOutputV2,
    flat_graph_strict_json_schema,
)
from criteriabench.real.llf_semantics import (
    LlfAttributeNode,
    LlfBooleanOperationNode,
    LlfCallNode,
    LlfSemanticOutput,
    LlfStringNode,
    LlfSymbolNode,
    LlfTupleNode,
    parse_llf_semantic,
)
from criteriabench.real_eval.integrity import canonical_sha256
from criteriabench.real_eval.models import GenerationDatasetBinding

OPENAI_RESPONSES_ENDPOINT = "https://api.openai.com/v1/responses"
OPENAI_API_BASE_URL = "https://api.openai.com/v1"
LUNA_MODEL = "gpt-5.6-luna"
MAX_INPUT_TOKENS_RESERVED = 16_384
MAX_OUTPUT_TOKENS = 2_048
REQUEST_TIMEOUT_SECONDS = 60.0
MAXIMUM_ATTEMPTS = 1

UNCACHED_INPUT_USD_PER_MILLION = Decimal("0.20")
CACHED_INPUT_USD_PER_MILLION = Decimal("0.02")
CACHE_WRITE_INPUT_USD_PER_MILLION = Decimal("0.25")
OUTPUT_USD_PER_MILLION = Decimal("1.20")
MONEY_QUANTUM = Decimal("0.000000001")
TOKENS_PER_MILLION = Decimal(1_000_000)

RESERVATION_PER_CASE_USD = Decimal("0.006553600")
CANARY_CASE_COUNT = 25
CANARY_BUDGET_CAP_USD = Decimal("0.170000000")
LOCKED_CASE_COUNT = 1_800
LOCKED_BUDGET_CAP_USD = Decimal("11.800000000")
EXPECTED_OPENAI_SDK_VERSION = "2.54.0"
EXPECTED_UV_LOCK_SHA256 = "e4600c744265aaee6781afcbe9f8176bbe025424891dbf685bd258bc7d5dcc0b"
MODULE_ROOT = Path(__file__).resolve().parent
RUNTIME_UV_LOCK_PATH = Path("/app/uv.lock")
EXECUTION_SOURCE_PATHS = {
    "contracts_sha256": MODULE_ROOT / "contracts.py",
    "planning_sha256": MODULE_ROOT / "planning.py",
    "transport_sha256": MODULE_ROOT / "transport.py",
    "runner_sha256": MODULE_ROOT / "runner.py",
    "cli_sha256": MODULE_ROOT / "cli.py",
}

GRAPH_V2_SCHEMA_SHA256 = "2d6ccd6c8fa4092f67c892d854db4a98201367778c365e47f5715e60b6ebd712"
GRAPH_V2_PARSER_ID = "criteriabench.real.graph_v2.FlatGraphOutputV2:model_validate:v1"
GRAPH_V2_PARSER_SHA256 = hashlib.sha256(GRAPH_V2_PARSER_ID.encode("utf-8")).hexdigest()
LLF_WIRE_SCHEMA_SHA256 = "ab7cf39603900057b25d005b9722953987f3212ae6d10ab5cd9d6ef059fd8f15"
LLF_SEMANTIC_PARSER_ID = "criteriabench.real_live.contracts.parse_llf_wire_payload:v1"
LLF_SEMANTIC_PARSER_SHA256 = hashlib.sha256(LLF_SEMANTIC_PARSER_ID.encode("utf-8")).hexdigest()

# These are predeclared engineering/security limits, not statistics learned from
# either benchmark split.  They bound hostile provider output while leaving
# ample room for ordinary LLF expressions under the separately frozen 2,048
# output-token request cap.
LLF_ENGINEERING_LIMITS: dict[str, str | int] = {
    "policy_id": "llf-live-engineering-limits-v1",
    "logical_form_characters": 8_192,
    "logical_form_utf8_bytes": 16_384,
    "semantic_nodes": 256,
    "semantic_depth": 64,
    "call_arguments": 32,
    "collection_items": 32,
    "identifier_characters": 128,
    "string_utf8_bytes": 1_024,
}
LLF_ENGINEERING_LIMITS_SHA256 = canonical_sha256(LLF_ENGINEERING_LIMITS)

LLF_CANARY_ACKNOWLEDGEMENT = (
    "I authorize this exact sealed 25-case LLF semantic paid Luna canary plan."
)
GRAPH_PRODUCT_CANARY_ACKNOWLEDGEMENT = (
    "I authorize this exact sealed 25-case GraphV2 product paid Luna canary plan."
)
LOCKED_ACKNOWLEDGEMENT = "I separately authorize this exact sealed locked LLF paid Luna plan."

HexDigest = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
ImageDigest = Annotated[str, Field(pattern=r"^sha256:[0-9a-f]{64}$")]
Identifier = Annotated[str, Field(pattern=r"^[A-Za-z0-9]+(?:[._-][A-Za-z0-9]+)*$")]
ProviderIdentifier = Annotated[
    str,
    Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9][A-Za-z0-9._:/-]*$"),
]
Money = Annotated[str, Field(pattern=r"^(?:0|[1-9][0-9]*)\.[0-9]{9}$")]
UtcTimestamp = Annotated[
    str,
    Field(pattern=r"^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z$"),
]
OutputTrack = Literal["graph_v2_product", "llf_semantic_ast"]
PlanPurpose = Literal[
    "development_llf_canary_25",
    "development_graph_product_canary_25",
    "locked_llf_test",
]


def canonical_schema_json(schema: Mapping[str, object]) -> str:
    """Return the exact JSON representation used for schema hashing and transport."""

    return json.dumps(
        schema,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    )


@dataclass(frozen=True, slots=True)
class StrictOutputContract[TOutput: BaseModel]:
    """Runtime parser plus frozen identity for one strict, identity-free output."""

    contract_id: str
    track: OutputTrack
    schema_name: str
    schema_json: str
    schema_sha256: str
    parser_id: str
    parser_sha256: str
    parser_code_sha256: str
    instructions: str
    prompt_sha256: str
    parser: Callable[[Mapping[str, object]], TOutput]

    def schema(self) -> dict[str, object]:
        value = json.loads(self.schema_json)
        if not isinstance(value, dict):
            raise ValueError("strict output schema root must be an object")
        return cast(dict[str, object], value)

    def parse(self, value: Mapping[str, object]) -> TOutput:
        return self.parser(value)


def make_output_contract[TOutput: BaseModel](
    *,
    contract_id: str,
    track: OutputTrack,
    schema_name: str,
    strict_schema: Mapping[str, object],
    expected_schema_sha256: str,
    parser_id: str,
    expected_parser_sha256: str,
    parser_source_path: Path,
    instructions: str,
    parser: Callable[[Mapping[str, object]], TOutput],
) -> StrictOutputContract[TOutput]:
    """Create a contract only when its schema/parser identities exactly match."""

    if not contract_id or not schema_name or not instructions.strip():
        raise ValueError("output contract identity and instructions cannot be blank")
    if len(schema_name) > 64 or any(
        not (character.isalnum() or character in "_-") for character in schema_name
    ):
        raise ValueError("schema_name must use at most 64 letters, digits, underscores, or hyphens")
    schema_json = canonical_schema_json(strict_schema)
    actual_schema_sha256 = hashlib.sha256(schema_json.encode("utf-8")).hexdigest()
    if actual_schema_sha256 != expected_schema_sha256:
        raise ValueError("strict output schema hash differs from its frozen identity")
    actual_parser_sha256 = hashlib.sha256(parser_id.encode("utf-8")).hexdigest()
    if actual_parser_sha256 != expected_parser_sha256:
        raise ValueError("output parser hash differs from its frozen identity")
    parser_code_sha256 = hashlib.sha256(parser_source_path.read_bytes()).hexdigest()
    _lint_identity_free_schema(strict_schema)
    prompt_sha256 = canonical_sha256(
        {
            "contract_id": contract_id,
            "instructions": instructions,
            "request_template": "criterion-kind-and-text-only-v1",
            "schema_sha256": actual_schema_sha256,
        }
    )
    return StrictOutputContract(
        contract_id=contract_id,
        track=track,
        schema_name=schema_name,
        schema_json=schema_json,
        schema_sha256=actual_schema_sha256,
        parser_id=parser_id,
        parser_sha256=actual_parser_sha256,
        parser_code_sha256=parser_code_sha256,
        instructions=instructions,
        prompt_sha256=prompt_sha256,
        parser=parser,
    )


GRAPH_V2_INSTRUCTIONS = """You convert exactly one clinical-trial eligibility criterion into
the supplied identity-free GraphV2 schema. Use zero-based Unicode code-point offsets with an
exclusive end offset. Every evidence quote must equal the exact source substring at its offsets.
Preserve all AND, OR, NOT, threshold, numeric, temporal, unit, and modifier semantics; do not
flatten nested logic. Never invent facts, terminology identifiers, identity fields, or source
metadata. Use null for optional normalization fields you cannot support from the source. If the
criterion cannot be represented safely, return an empty graph with not_machine_executable=true,
review_required=true, and a concise review reason. Return only JSON matching the schema."""


def graph_v2_output_contract() -> StrictOutputContract[FlatGraphOutputV2]:
    """Return the frozen GraphV2 product/evidence contract."""

    return make_output_contract(
        contract_id="graph-v2-product-v1",
        track="graph_v2_product",
        schema_name="criteriabench_graph_v2",
        strict_schema=flat_graph_strict_json_schema(),
        expected_schema_sha256=GRAPH_V2_SCHEMA_SHA256,
        parser_id=GRAPH_V2_PARSER_ID,
        expected_parser_sha256=GRAPH_V2_PARSER_SHA256,
        parser_source_path=Path(__file__).resolve().parents[1] / "real" / "graph_v2.py",
        instructions=GRAPH_V2_INSTRUCTIONS,
        parser=FlatGraphOutputV2.model_validate,
    )


class LlfLogicalFormWireOutput(BaseModel):
    """Compact, identity-free paid wire payload; parsed without eval or execution."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    logical_form: Annotated[
        StrictStr,
        Field(
            min_length=1,
            max_length=cast(int, LLF_ENGINEERING_LIMITS["logical_form_characters"]),
        ),
    ]


LLF_PROVIDER_STRICT_SCHEMA: dict[str, object] = {
    "type": "object",
    "properties": {"logical_form": {"type": "string"}},
    "required": ["logical_form"],
    "additionalProperties": False,
}


LLF_DEVELOPMENT_DIRECT_CALLS = (
    "adult",
    "after",
    "age",
    "allergy",
    "and",
    "before",
    "child",
    "clin_score",
    "cond",
    "contraindication",
    "criteria",
    "death",
    "drug",
    "during",
    "enc",
    "eq",
    "ethnic",
    "female",
    "hosp",
    "if_then",
    "immune",
    "indication",
    "intersect",
    "lab",
    "lang",
    "male",
    "measurement",
    "mother",
    "neg",
    "obs",
    "op",
    "or",
    "org",
    "per",
    "proc",
    "res",
    "risk",
    "seq",
    "social_habit",
    "spec",
    "temporal_per",
    "temporal_rec",
    "temporal_unit",
    "union",
    "unit",
    "val",
    "vital",
)
LLF_DEVELOPMENT_METHOD_ATTRIBUTES = (
    "acute",
    "caused_by",
    "change",
    "chronic",
    "duration",
    "eq",
    "equiv",
    "except",
    "for",
    "found_by",
    "loc",
    "min_count",
    "mod",
    "num_filter",
    "pol",
    "possible",
    "severity",
    "stable",
    "temporality",
)
LLF_DEVELOPMENT_BARE_SYMBOLS = (
    "BETWEEN",
    "DAY",
    "EMERGENCY",
    "EQ",
    "FIRST_TIME",
    "FUTURE",
    "GT",
    "GTEQ",
    "HIGH",
    "HOUR",
    "INPATIENT",
    "LT",
    "LTEQ",
    "MILD",
    "MINUTE",
    "MONTH",
    "PAST",
    "POSITIVE",
    "PRESENT",
    "RECENT",
    "REF_RANGE_NORMAL",
    "SEVERE",
    "TRANSFER",
    "WEEK",
    "YEAR",
)


@dataclass(frozen=True, slots=True)
class LlfPromptExample:
    case_id: str
    trial_id: str
    criterion_kind: Literal["inclusion", "exclusion"]
    source_text: str
    source_sha256: str
    logical_form: str

    def __post_init__(self) -> None:
        if hashlib.sha256(self.source_text.encode()).hexdigest() != self.source_sha256:
            raise ValueError("LLF prompt example source hash mismatch")


LLF_PROMPT_EXAMPLES = (
    LlfPromptExample(
        case_id="NCT03865043_6",
        trial_id="NCT03865043",
        criterion_kind="exclusion",
        source_text="-  Minor patient",
        source_sha256="7127df7bf0174019413fec9fa7c34a738ff4851622067716d769c68108e2ae5a",
        logical_form="child()",
    ),
    LlfPromptExample(
        case_id="NCT03860038_5",
        trial_id="NCT03860038",
        criterion_kind="inclusion",
        source_text=(
            "Subject must have an ECOG ( Eastern Cooperative Oncology Group ) "
            "performance status score of 0 - 2 ;"
        ),
        source_sha256="c446de4dab40a2b7f9f27b9ba5cb2f5a4158627d0c964f182e927982f6d5a1f1",
        logical_form=(
            'clin_score("ECOG")\r\n'
            "    .equiv(\r\n"
            '        clin_score("Eastern Cooperative Oncology Group")\r\n'
            "    )\r\n"
            '    .mod("performance status score")\r\n'
            "    .num_filter(\r\n"
            '        eq(op(BETWEEN), val("0"), val("2"))\r\n'
            "    )"
        ),
    ),
    LlfPromptExample(
        case_id="NCT03860324_8",
        trial_id="NCT03860324",
        criterion_kind="exclusion",
        source_text="-  Chronic kidney disease with a Glomerular Filtration Rate < 50 ml / min",
        source_sha256="0267c3d8e1ae0fc88d20d7a7b246e26831361b408ecb13890088186f361f8b6f",
        logical_form=(
            "intersect(\r\n"
            '    cond("kidney disease")\r\n'
            "        .chronic(), \r\n"
            '    lab("Glomerular Filtration Rate")\r\n'
            "        .num_filter(\r\n"
            '            eq(op(LT), val("50"), unit("ml"), per(MINUTE))\r\n'
            "        )\r\n"
            ")"
        ),
    ),
    LlfPromptExample(
        case_id="NCT03860038_10",
        trial_id="NCT03860038",
        criterion_kind="exclusion",
        source_text=(
            "3.  Subject has previously received allogenic stem cell transplant , or subject "
            "has received autologous stem cell transplant within 3 months before administration "
            "of the study agent;"
        ),
        source_sha256="0a3b375637d3ee71bc699db4437c888c014e7a7cffbf25c4a14fc21fa25470b4",
        logical_form=(
            "seq(\r\n"
            "    union(\r\n"
            '        proc("stem cell transplant")\r\n'
            "            .temporality(\r\n"
            "                eq(temporal_per(PAST))\r\n"
            "            )\r\n"
            '            .mod("allogenic"),\r\n'
            '        proc("stem cell transplant")\r\n'
            '            .mod("autologous")\r\n'
            "    ),\r\n"
            "    before(\r\n"
            "        drug()\r\n"
            "            .temporality(\r\n"
            '                eq(op(LTEQ), val("3"), temporal_unit(MONTH))\r\n'
            "            )\r\n"
            "    )\r\n"
            ")"
        ),
    ),
    LlfPromptExample(
        case_id="NCT03862937_1",
        trial_id="NCT03862937",
        criterion_kind="inclusion",
        source_text=(
            "-  Be 60 or older , and in the case of women , they must be postmenopausal "
            "( interruption of menstruation for more than one year ) ."
        ),
        source_sha256="207db65201c683709005f122d84dccb4691e1a1fa3fbba7f18aa8410a875c2b9",
        logical_form=(
            "intersect(\r\n"
            "    age()\r\n"
            '        .eq(val("60"), op(GTEQ)),\r\n'
            "    if_then(\r\n"
            "        female(),\r\n"
            '        cond("postmenopausal")\r\n'
            "            .equiv(\r\n"
            "                neg(\r\n"
            '                    cond("menstruation")\r\n'
            "                )\r\n"
            "                    .duration(\r\n"
            '                        eq(op(GT), val("one"), temporal_unit(YEAR))\r\n'
            "                    )\r\n"
            "            )\r\n"
            "    )\r\n"
            ")"
        ),
    ),
)
LLF_PROMPT_EXAMPLE_TRIAL_IDS = frozenset(example.trial_id for example in LLF_PROMPT_EXAMPLES)
LLF_PROMPT_EXAMPLES_SHA256 = canonical_sha256(
    [
        {
            "case_id": example.case_id,
            "trial_id": example.trial_id,
            "criterion_kind": example.criterion_kind,
            "source_text": example.source_text,
            "source_sha256": example.source_sha256,
            "logical_form": example.logical_form,
        }
        for example in LLF_PROMPT_EXAMPLES
    ]
)


def _render_llf_prompt_examples() -> str:
    rendered: list[str] = []
    for index, example in enumerate(LLF_PROMPT_EXAMPLES, start=1):
        provider_input = {
            "criterion_kind": example.criterion_kind,
            "criterion_text": example.source_text,
        }
        provider_output = {"logical_form": example.logical_form}
        rendered.append(
            f"Example {index} input: "
            + json.dumps(provider_input, ensure_ascii=False, separators=(",", ":"))
            + "\n"
            + f"Example {index} output: "
            + json.dumps(provider_output, ensure_ascii=False, separators=(",", ":"))
        )
    return "\n\n".join(rendered)


LLF_SEMANTIC_INSTRUCTIONS = f"""Translate exactly one clinical-trial eligibility criterion into
one lossless Leaf Logical Forms (LLF) expression string in the logical_form field. This is an LLF
syntax prediction, not GraphV2. Return no identity, explanation, markdown, or source metadata.

Use the complete frozen development-observed direct-call vocabulary where applicable:
{", ".join(LLF_DEVELOPMENT_DIRECT_CALLS)}. The complete development-observed method-attribute
vocabulary is {", ".join(LLF_DEVELOPMENT_METHOD_ATTRIBUTES)}. The complete development-observed
bare-symbol vocabulary is {", ".join(LLF_DEVELOPMENT_BARE_SYMBOLS)}. Literal clinical
phrases remain exact strings. Do not invent identity fields, source metadata, reference labels, or
facts absent from the criterion. Return only JSON matching the schema.

Frozen development examples:
{_render_llf_prompt_examples()}"""


def llf_semantic_output_contract() -> StrictOutputContract[LlfSemanticOutput]:
    """Return the compact LLF wire contract, parsed to the canonical internal AST."""

    return make_output_contract(
        contract_id="llf-semantic-ast-v1",
        track="llf_semantic_ast",
        schema_name="criteriabench_llf_logical_form",
        strict_schema=LLF_PROVIDER_STRICT_SCHEMA,
        expected_schema_sha256=LLF_WIRE_SCHEMA_SHA256,
        parser_id=LLF_SEMANTIC_PARSER_ID,
        expected_parser_sha256=LLF_SEMANTIC_PARSER_SHA256,
        parser_source_path=Path(__file__),
        instructions=LLF_SEMANTIC_INSTRUCTIONS,
        parser=parse_llf_wire_payload,
    )


def parse_llf_wire_payload(value: Mapping[str, object]) -> LlfSemanticOutput:
    """Validate compact JSON, parse inert LLF syntax, and enforce fixed safety limits."""

    wire = LlfLogicalFormWireOutput.model_validate(value)
    if len(wire.logical_form.encode("utf-8")) > cast(
        int,
        LLF_ENGINEERING_LIMITS["logical_form_utf8_bytes"],
    ):
        raise ValueError("LLF output exceeds the predeclared UTF-8 byte limit")
    parsed = parse_llf_semantic(wire.logical_form, source_name="provider-output")
    output = LlfSemanticOutput(root_node_id=parsed.root_node_id, nodes=parsed.nodes)
    _enforce_llf_engineering_limits(output)
    return output


class FrozenOutputContract(StrictModel):
    contract_id: Identifier
    track: OutputTrack
    schema_name: Annotated[str, Field(min_length=1, max_length=64)]
    schema_sha256: HexDigest
    parser_id: Annotated[str, Field(min_length=1, max_length=500)]
    parser_sha256: HexDigest
    parser_code_sha256: HexDigest
    prompt_sha256: HexDigest


class FrozenLunaConfiguration(StrictModel):
    endpoint: Literal["https://api.openai.com/v1/responses"]
    model: Literal["gpt-5.6-luna"]
    store: Literal[False]
    reasoning_effort: Literal["none"]
    max_output_tokens: Literal[2048]
    service_tier: Literal["default"]
    tools: Annotated[tuple[object, ...], Field(max_length=0)]
    sdk_max_retries: Literal[0]
    app_max_retries: Literal[0]
    request_timeout_seconds: Literal[60]
    execution: Literal["sequential"]
    http_trust_env: Literal[False]
    follow_redirects: Literal[False]


class FrozenExecutionImplementationPayload(StrictModel):
    schema_version: Literal["real-live-execution-implementation-v1"]
    contracts_sha256: HexDigest
    planning_sha256: HexDigest
    transport_sha256: HexDigest
    runner_sha256: HexDigest
    cli_sha256: HexDigest
    package_python_inventory_sha256: HexDigest
    uv_lock_sha256: HexDigest
    openai_sdk_version: Literal["2.54.0"]


class FrozenExecutionImplementation(FrozenExecutionImplementationPayload):
    implementation_sha256: HexDigest

    @model_validator(mode="after")
    def hash_matches_payload(self) -> FrozenExecutionImplementation:
        payload = self.model_dump(mode="json", exclude={"implementation_sha256"})
        if self.implementation_sha256 != canonical_sha256(payload):
            raise ValueError("execution implementation hash does not match its payload")
        return self


class FrozenPricing(StrictModel):
    currency: Literal["USD"]
    pricing_id: Literal["openai-gpt-5.6-luna-2026-09-02"]
    uncached_input_usd_per_million: Literal["0.200000000"]
    cached_input_usd_per_million: Literal["0.020000000"]
    cache_write_input_usd_per_million: Literal["0.250000000"]
    output_usd_per_million: Literal["1.200000000"]
    rounding: Literal["usd_9dp_half_up"]
    reviewed_at_utc: Literal["2026-09-02T00:00:00Z"]
    valid_through_utc: Literal["2026-09-02T23:59:59Z"]
    pricing_sha256: HexDigest

    @model_validator(mode="after")
    def hash_matches_snapshot(self) -> FrozenPricing:
        payload = self.model_dump(mode="json", exclude={"pricing_sha256"})
        if self.pricing_sha256 != canonical_sha256(payload):
            raise ValueError("pricing_sha256 does not match the frozen pricing payload")
        return self


class PlannedCase(StrictModel):
    ordinal: Annotated[StrictInt, Field(gt=0)]
    case_id: Identifier
    trial_id: Annotated[str, Field(min_length=1, max_length=2_000)]
    document_id: Annotated[str, Field(min_length=1, max_length=2_000)]
    criterion_kind: Literal["inclusion", "exclusion", "unknown"]
    source_sha256: HexDigest


class LivePlanPayload(StrictModel):
    schema_version: Literal["real-live-plan-v1"]
    plan_id: Identifier
    created_at_utc: UtcTimestamp
    expires_at_utc: UtcTimestamp
    purpose: PlanPurpose
    runtime_image_id: ImageDigest
    source_dataset: GenerationDatasetBinding
    selected_case_set_sha256: HexDigest
    selection_algorithm: Identifier
    output_contract: FrozenOutputContract
    luna: FrozenLunaConfiguration
    execution_implementation: FrozenExecutionImplementation
    pricing: FrozenPricing
    reservation_input_tokens: Literal[16384]
    reservation_output_tokens: Literal[2048]
    reservation_per_case_usd: Literal["0.006553600"]
    budget_cap_usd: Money
    reserved_total_usd: Money
    requires_separate_locked_authorization: StrictBool
    cases: Annotated[tuple[PlannedCase, ...], Field(min_length=1)]

    @model_validator(mode="after")
    def plan_shape_is_safe(self) -> LivePlanPayload:
        created = parse_utc_timestamp(self.created_at_utc)
        expires = parse_utc_timestamp(self.expires_at_utc)
        price_review = parse_utc_timestamp(self.pricing.reviewed_at_utc)
        price_expiry = parse_utc_timestamp(self.pricing.valid_through_utc)
        if not (price_review <= created < expires <= price_expiry):
            raise ValueError("plan lifetime must fall inside the frozen pricing review window")
        if [case.ordinal for case in self.cases] != list(range(1, len(self.cases) + 1)):
            raise ValueError("planned case ordinals must be contiguous and one-based")
        case_ids = [case.case_id for case in self.cases]
        if len(case_ids) != len(set(case_ids)):
            raise ValueError("live plan contains duplicate case IDs")
        reserved = money(RESERVATION_PER_CASE_USD * len(self.cases))
        if self.reserved_total_usd != reserved:
            raise ValueError("reserved_total_usd does not match planned case reservations")
        if Decimal(self.reserved_total_usd) > Decimal(self.budget_cap_usd):
            raise ValueError("planned reservations exceed the budget cap")
        if self.purpose in {
            "development_llf_canary_25",
            "development_graph_product_canary_25",
        }:
            if len(self.cases) != CANARY_CASE_COUNT:
                raise ValueError("development canary must contain exactly 25 cases")
            if self.source_dataset.split != "development":
                raise ValueError("development canary must bind the development split")
            expected_track = (
                "llf_semantic_ast"
                if self.purpose == "development_llf_canary_25"
                else "graph_v2_product"
            )
            if self.output_contract.track != expected_track:
                raise ValueError("development canary output track does not match its purpose")
            if self.budget_cap_usd != money(CANARY_BUDGET_CAP_USD):
                raise ValueError("development canary budget must be exactly $0.17")
            if self.requires_separate_locked_authorization:
                raise ValueError("canary cannot claim locked-run authorization semantics")
        else:
            if len(self.cases) != LOCKED_CASE_COUNT:
                raise ValueError("locked LLF plan must contain exactly 1800 cases")
            if self.source_dataset.split != "test":
                raise ValueError("locked LLF plan must bind the test split")
            if (
                self.source_dataset.dataset_id != "leaf-logical-forms"
                or self.source_dataset.dataset_version != "llf-461288a"
            ):
                raise ValueError("locked plan must bind the frozen LLF Real v1 dataset")
            if self.output_contract.track != "llf_semantic_ast":
                raise ValueError("locked LLF quality run requires lossless LLF semantic output")
            if self.budget_cap_usd != money(LOCKED_BUDGET_CAP_USD):
                raise ValueError("locked LLF budget must be exactly $11.80")
            if not self.requires_separate_locked_authorization:
                raise ValueError("locked plan requires a separate authorization artifact")
        return self


class LivePlan(LivePlanPayload):
    plan_sha256: HexDigest

    @model_validator(mode="after")
    def hash_matches_payload(self) -> LivePlan:
        payload = self.model_dump(mode="json", exclude={"plan_sha256"})
        if self.plan_sha256 != canonical_sha256(payload):
            raise ValueError("plan_sha256 does not match the canonical plan payload")
        return self


class CanaryExecutionBindingPayload(StrictModel):
    schema_version: Literal["llf-canary-execution-binding-v1"]
    preregistration_sha256: HexDigest
    preregistration_artifact_sha256: HexDigest
    plan_sha256: HexDigest
    plan_artifact_sha256: HexDigest
    runtime_image_id: ImageDigest
    runtime_output_directory: Literal["/run/artifacts/output"]
    runtime_output_directory_sha256: HexDigest
    host_output_directory_sha256: HexDigest
    authorization_state_directory_sha256: HexDigest
    authorization_claim_filename_template: Literal["claim-{authorization_sha256}.json"]
    intended_run_id: Identifier
    intended_authorization_id: Identifier
    purpose: Literal["development_llf_canary_25"]
    case_count: Literal[25]
    selected_case_set_sha256: HexDigest
    source_dataset: GenerationDatasetBinding
    selection_algorithm: Literal["polarity-length-tertile-trial-stratified-sha256-v1"]
    output_contract: FrozenOutputContract
    luna: FrozenLunaConfiguration
    execution: FrozenExecutionImplementation
    pricing: FrozenPricing
    reservation_input_tokens: Literal[16384]
    reservation_output_tokens: Literal[2048]
    reservation_per_case_usd: Literal["0.006553600"]
    reserved_total_usd: Literal["0.163840000"]
    budget_cap_usd: Literal["0.170000000"]
    advancement_gates_sha256: HexDigest
    requires_separate_locked_authorization: Literal[False]
    maximum_execution_count: Literal[1]
    optional_stopping_prohibited: Literal[True]
    quality_failure_policy: Literal["new_versioned_configuration_and_new_preregistration_required"]
    operational_rerun_policy: Literal[
        "new_public_execution_binding_fresh_authorization_and_all_attempts_disclosed"
    ]


class CanaryExecutionBinding(CanaryExecutionBindingPayload):
    execution_binding_sha256: HexDigest

    @model_validator(mode="after")
    def seal_matches_payload(self) -> CanaryExecutionBinding:
        payload = self.model_dump(mode="json", exclude={"execution_binding_sha256"})
        if self.execution_binding_sha256 != canonical_sha256(payload):
            raise ValueError("execution-binding seal does not match its canonical payload")
        return self


class PaidAuthorizationPayload(StrictModel):
    schema_version: Literal["real-live-authorization-v1"]
    authorization_id: Identifier
    authorized_at_utc: UtcTimestamp
    expires_at_utc: UtcTimestamp
    plan_sha256: HexDigest
    preregistration_sha256: HexDigest
    preregistration_artifact_sha256: HexDigest
    execution_binding_sha256: HexDigest
    execution_binding_artifact_sha256: HexDigest
    purpose: PlanPurpose
    authorized_case_count: Annotated[StrictInt, Field(gt=0)]
    authorized_budget_cap_usd: Money
    run_directory_sha256: HexDigest
    host_run_directory_sha256: HexDigest
    authorization_state_directory_sha256: HexDigest
    run_id: Identifier
    acknowledgement: Annotated[str, Field(min_length=1, max_length=200)]


class PaidAuthorization(PaidAuthorizationPayload):
    authorization_sha256: HexDigest

    @model_validator(mode="after")
    def hash_matches_payload(self) -> PaidAuthorization:
        payload = self.model_dump(mode="json", exclude={"authorization_sha256"})
        if self.authorization_sha256 != canonical_sha256(payload):
            raise ValueError("authorization hash does not match its canonical payload")
        return self


class AuthorizationClaimPayload(StrictModel):
    schema_version: Literal["real-live-authorization-claim-v1"]
    plan_sha256: HexDigest
    preregistration_sha256: HexDigest
    preregistration_artifact_sha256: HexDigest
    execution_binding_sha256: HexDigest
    execution_binding_artifact_sha256: HexDigest
    authorization_sha256: HexDigest
    run_directory_sha256: HexDigest
    host_run_directory_sha256: HexDigest
    authorization_state_directory_sha256: HexDigest
    authorization_claim_filename: Annotated[
        str,
        Field(pattern=r"^claim-[0-9a-f]{64}\.json$"),
    ]
    run_id: Identifier


class AuthorizationClaim(AuthorizationClaimPayload):
    claim_sha256: HexDigest

    @model_validator(mode="after")
    def hash_matches_payload(self) -> AuthorizationClaim:
        payload = self.model_dump(mode="json", exclude={"claim_sha256"})
        if self.claim_sha256 != canonical_sha256(payload):
            raise ValueError("authorization claim hash does not match its payload")
        return self


class UsageBreakdown(StrictModel):
    availability: Literal["complete", "unavailable"]
    input_tokens: Annotated[StrictInt, Field(ge=0)]
    uncached_input_tokens: Annotated[StrictInt, Field(ge=0)]
    cached_input_tokens: Annotated[StrictInt, Field(ge=0)]
    cache_write_input_tokens: Annotated[StrictInt, Field(ge=0)]
    output_tokens: Annotated[StrictInt, Field(ge=0)]
    uncached_input_cost_usd: Money
    cached_input_cost_usd: Money
    cache_write_input_cost_usd: Money
    output_cost_usd: Money
    total_cost_usd: Money

    @model_validator(mode="after")
    def categories_and_cost_are_exact(self) -> UsageBreakdown:
        if self.availability == "unavailable":
            numeric = (
                self.input_tokens,
                self.uncached_input_tokens,
                self.cached_input_tokens,
                self.cache_write_input_tokens,
                self.output_tokens,
            )
            costs = (
                self.uncached_input_cost_usd,
                self.cached_input_cost_usd,
                self.cache_write_input_cost_usd,
                self.output_cost_usd,
                self.total_cost_usd,
            )
            if any(numeric) or any(Decimal(value) for value in costs):
                raise ValueError("unavailable usage requires zero placeholders")
            return self
        if self.input_tokens != (
            self.uncached_input_tokens + self.cached_input_tokens + self.cache_write_input_tokens
        ):
            raise ValueError("input token categories must exactly equal input_tokens")
        expected = price_usage(
            uncached_input_tokens=self.uncached_input_tokens,
            cached_input_tokens=self.cached_input_tokens,
            cache_write_input_tokens=self.cache_write_input_tokens,
            output_tokens=self.output_tokens,
        )
        if self.model_dump(mode="json", include=set(expected)) != expected:
            raise ValueError("usage costs do not match frozen Luna pricing")
        return self


FailureKind = Literal[
    "authentication",
    "authorization",
    "budget_breach",
    "content_filter",
    "interrupted_unknown",
    "invalid_json",
    "model_mismatch",
    "model_not_found",
    "network",
    "provider_error",
    "rate_limit",
    "refusal",
    "request_configuration",
    "response_contract",
    "schema_validation",
    "timeout",
    "truncated_output",
]


class SanitizedFailure(StrictModel):
    kind: FailureKind
    retryable: StrictBool
    fingerprint_sha256: HexDigest


class PendingAttemptPayload(StrictModel):
    schema_version: Literal["real-live-pending-v1"]
    plan_sha256: HexDigest
    ordinal: Annotated[StrictInt, Field(gt=0)]
    case_id: Identifier
    source_sha256: HexDigest
    request_sha256: HexDigest
    attempt_started_at_utc: UtcTimestamp
    reservation_usd: Literal["0.006553600"]


class PendingAttempt(PendingAttemptPayload):
    pending_sha256: HexDigest

    @model_validator(mode="after")
    def hash_matches_payload(self) -> PendingAttempt:
        payload = self.model_dump(mode="json", exclude={"pending_sha256"})
        if self.pending_sha256 != canonical_sha256(payload):
            raise ValueError("pending attempt hash does not match its payload")
        return self


class ExternalAttemptClaimPayload(StrictModel):
    schema_version: Literal["real-live-external-attempt-claim-v1"]
    plan_sha256: HexDigest
    authorization_sha256: HexDigest
    authorization_claim_sha256: HexDigest
    preregistration_sha256: HexDigest
    execution_binding_sha256: HexDigest
    run_id: Identifier
    host_run_directory_sha256: HexDigest
    authorization_state_directory_sha256: HexDigest
    ordinal: Annotated[StrictInt, Field(gt=0)]
    pending: PendingAttempt

    @model_validator(mode="after")
    def ordinal_matches_pending(self) -> ExternalAttemptClaimPayload:
        if self.ordinal != self.pending.ordinal:
            raise ValueError("external attempt ordinal differs from pending artifact")
        return self


class ExternalAttemptClaim(ExternalAttemptClaimPayload):
    external_attempt_claim_sha256: HexDigest

    @model_validator(mode="after")
    def hash_matches_payload(self) -> ExternalAttemptClaim:
        payload = self.model_dump(
            mode="json",
            exclude={"external_attempt_claim_sha256"},
        )
        if self.external_attempt_claim_sha256 != canonical_sha256(payload):
            raise ValueError("external attempt claim hash does not match its payload")
        return self


class AuthorizationConsumptionPayload(StrictModel):
    schema_version: Literal["real-live-authorization-consumption-v1"]
    plan_sha256: HexDigest
    authorization_sha256: HexDigest
    authorization_claim_sha256: HexDigest
    run_directory_sha256: HexDigest
    run_id: Identifier


class AuthorizationConsumption(AuthorizationConsumptionPayload):
    consumption_sha256: HexDigest

    @model_validator(mode="after")
    def hash_matches_payload(self) -> AuthorizationConsumption:
        payload = self.model_dump(mode="json", exclude={"consumption_sha256"})
        if self.consumption_sha256 != canonical_sha256(payload):
            raise ValueError("authorization consumption hash does not match its payload")
        return self


class CaseOutcomePayload(StrictModel):
    schema_version: Literal["real-live-case-outcome-v1"]
    plan_sha256: HexDigest
    ordinal: Annotated[StrictInt, Field(gt=0)]
    case_id: Identifier
    trial_id: Annotated[str, Field(min_length=1, max_length=2_000)]
    document_id: Annotated[str, Field(min_length=1, max_length=2_000)]
    source_sha256: HexDigest
    request_sha256: HexDigest
    attempt_sha256: HexDigest
    external_attempt_claim_sha256: HexDigest
    outcome_finished_at_utc: UtcTimestamp
    total_latency_ms: Annotated[StrictInt, Field(ge=0)] | None
    status: Literal["completed", "failed"]
    usage: UsageBreakdown
    charged_cost_usd: Money
    response_id_sha256: HexDigest | None
    provider_model: ProviderIdentifier | None
    provider_model_sha256: HexDigest | None
    provider_response_object: ProviderIdentifier | None
    provider_response_object_sha256: HexDigest | None
    provider_service_tier: ProviderIdentifier | None
    provider_service_tier_sha256: HexDigest | None
    normalized_output_sha256: HexDigest | None
    normalized_output: dict[str, object] | None
    failure: SanitizedFailure | None

    @model_validator(mode="after")
    def completed_and_failed_shapes_are_disjoint(self) -> CaseOutcomePayload:
        for label, digest in (
            (self.provider_model, self.provider_model_sha256),
            (self.provider_response_object, self.provider_response_object_sha256),
            (self.provider_service_tier, self.provider_service_tier_sha256),
        ):
            if (label is None) != (digest is None):
                raise ValueError("provider provenance label and hash must both exist or be absent")
            if label is not None and digest != hashlib.sha256(label.encode()).hexdigest():
                raise ValueError("provider provenance label hash mismatch")
        if self.usage.availability == "unavailable":
            if self.charged_cost_usd != money(RESERVATION_PER_CASE_USD):
                raise ValueError("unknown usage must consume the full reservation")
        elif self.charged_cost_usd != self.usage.total_cost_usd:
            raise ValueError("known usage charge must equal exact priced usage")
        if Decimal(self.charged_cost_usd) > RESERVATION_PER_CASE_USD and (
            self.failure is None or self.failure.kind != "budget_breach"
        ):
            raise ValueError("known reservation overage must be an explicit budget breach")
        if self.status == "completed":
            if (
                self.normalized_output is None
                or self.normalized_output_sha256 is None
                or self.failure is not None
                or self.response_id_sha256 is None
                or self.provider_model != LUNA_MODEL
                or self.provider_model_sha256 != hashlib.sha256(LUNA_MODEL.encode()).hexdigest()
                or self.provider_response_object != "response"
                or self.provider_response_object_sha256 != hashlib.sha256(b"response").hexdigest()
                or self.provider_service_tier != "default"
                or self.provider_service_tier_sha256 != hashlib.sha256(b"default").hexdigest()
            ):
                raise ValueError(
                    "completed outcome requires normalized output and verified Responses provenance"
                )
            if canonical_sha256(self.normalized_output) != self.normalized_output_sha256:
                raise ValueError("normalized output hash mismatch")
        elif (
            self.failure is None
            or self.normalized_output is not None
            or self.normalized_output_sha256 is not None
        ):
            raise ValueError("failed outcome requires only a sanitized failure")
        return self


class CaseOutcome(CaseOutcomePayload):
    outcome_sha256: HexDigest

    @model_validator(mode="after")
    def hash_matches_payload(self) -> CaseOutcome:
        payload = self.model_dump(mode="json", exclude={"outcome_sha256"})
        if self.outcome_sha256 != canonical_sha256(payload):
            raise ValueError("case outcome hash does not match its payload")
        return self


class RunSummaryPayload(StrictModel):
    schema_version: Literal["real-live-summary-v1"]
    plan_sha256: HexDigest
    authorization_sha256: HexDigest
    authorization_claim_sha256: HexDigest
    preregistration_sha256: HexDigest
    preregistration_artifact_sha256: HexDigest
    execution_binding_sha256: HexDigest
    execution_binding_artifact_sha256: HexDigest
    execution_implementation_sha256: HexDigest
    terminal_state: Literal["completed", "aborted"]
    abort_reason: FailureKind | None
    case_count: Annotated[StrictInt, Field(gt=0)]
    attempted_count: Annotated[StrictInt, Field(ge=0)]
    not_attempted_count: Annotated[StrictInt, Field(ge=0)]
    completed_count: Annotated[StrictInt, Field(ge=0)]
    failed_count: Annotated[StrictInt, Field(ge=0)]
    usage_unknown_count: Annotated[StrictInt, Field(ge=0)]
    observed_latency_case_count: Annotated[StrictInt, Field(ge=0)]
    total_latency_ms: Annotated[StrictInt, Field(ge=0)]
    budget_cap_usd: Money
    charged_total_usd: Money
    budget_breached: StrictBool
    external_attempt_claim_count: Annotated[StrictInt, Field(ge=1)]
    external_attempt_claim_inventory_sha256: HexDigest
    outcome_hashes: Annotated[tuple[HexDigest, ...], Field(min_length=1)]

    @model_validator(mode="after")
    def counts_are_exact(self) -> RunSummaryPayload:
        if self.attempted_count + self.not_attempted_count != self.case_count:
            raise ValueError("summary attempted and not-attempted counts must equal case_count")
        if self.completed_count + self.failed_count != self.attempted_count:
            raise ValueError("summary completed and failed counts must equal attempted_count")
        if len(self.outcome_hashes) != self.attempted_count:
            raise ValueError("summary must bind every attempted case outcome")
        if self.external_attempt_claim_count != self.attempted_count:
            raise ValueError("summary must bind one external attempt claim per outcome")
        if self.usage_unknown_count > self.attempted_count:
            raise ValueError("unknown usage count cannot exceed attempted_count")
        if self.observed_latency_case_count > self.attempted_count:
            raise ValueError("observed latency count cannot exceed attempted_count")
        if self.terminal_state == "completed":
            if self.not_attempted_count != 0 or self.abort_reason is not None:
                raise ValueError("completed summary cannot contain an abort")
            if self.budget_breached:
                raise ValueError("completed summary cannot claim a budget breach")
            if Decimal(self.charged_total_usd) > Decimal(self.budget_cap_usd):
                raise ValueError("completed summary charge exceeds its plan cap")
        else:
            if self.abort_reason is None:
                raise ValueError("aborted summary requires a sanitized abort reason")
            if self.budget_breached != (self.abort_reason == "budget_breach"):
                raise ValueError("budget-breach flag must match the terminal abort reason")
        return self


class RunSummary(RunSummaryPayload):
    summary_sha256: HexDigest

    @model_validator(mode="after")
    def hash_matches_payload(self) -> RunSummary:
        payload = self.model_dump(mode="json", exclude={"summary_sha256"})
        if self.summary_sha256 != canonical_sha256(payload):
            raise ValueError("run summary hash does not match its payload")
        return self


def money(value: Decimal) -> str:
    return format(value.quantize(MONEY_QUANTUM, rounding=ROUND_HALF_UP), ".9f")


def token_cost(token_count: int, rate: Decimal) -> Decimal:
    if token_count < 0:
        raise ValueError("token count cannot be negative")
    return (Decimal(token_count) * rate / TOKENS_PER_MILLION).quantize(
        MONEY_QUANTUM,
        rounding=ROUND_HALF_UP,
    )


def price_usage(
    *,
    uncached_input_tokens: int,
    cached_input_tokens: int,
    cache_write_input_tokens: int,
    output_tokens: int,
) -> dict[str, str]:
    uncached = token_cost(uncached_input_tokens, UNCACHED_INPUT_USD_PER_MILLION)
    cached = token_cost(cached_input_tokens, CACHED_INPUT_USD_PER_MILLION)
    cache_write = token_cost(cache_write_input_tokens, CACHE_WRITE_INPUT_USD_PER_MILLION)
    output = token_cost(output_tokens, OUTPUT_USD_PER_MILLION)
    return {
        "uncached_input_cost_usd": money(uncached),
        "cached_input_cost_usd": money(cached),
        "cache_write_input_cost_usd": money(cache_write),
        "output_cost_usd": money(output),
        "total_cost_usd": money(uncached + cached + cache_write + output),
    }


def unavailable_usage() -> UsageBreakdown:
    return UsageBreakdown(
        availability="unavailable",
        input_tokens=0,
        uncached_input_tokens=0,
        cached_input_tokens=0,
        cache_write_input_tokens=0,
        output_tokens=0,
        uncached_input_cost_usd="0.000000000",
        cached_input_cost_usd="0.000000000",
        cache_write_input_cost_usd="0.000000000",
        output_cost_usd="0.000000000",
        total_cost_usd="0.000000000",
    )


def frozen_luna_configuration() -> FrozenLunaConfiguration:
    return FrozenLunaConfiguration(
        endpoint=OPENAI_RESPONSES_ENDPOINT,
        model=LUNA_MODEL,
        store=False,
        reasoning_effort="none",
        max_output_tokens=MAX_OUTPUT_TOKENS,
        service_tier="default",
        tools=(),
        sdk_max_retries=0,
        app_max_retries=0,
        request_timeout_seconds=60,
        execution="sequential",
        http_trust_env=False,
        follow_redirects=False,
    )


def frozen_execution_implementation() -> FrozenExecutionImplementation:
    """Hash the exact live implementation, lockfile, and locked OpenAI SDK version."""

    lock_path = _execution_lock_path()
    lock_bytes = lock_path.read_bytes()
    if hashlib.sha256(lock_bytes).hexdigest() != EXPECTED_UV_LOCK_SHA256:
        raise ValueError("runtime uv.lock differs from the audited lock digest")
    locked_version = _locked_openai_version(lock_bytes.decode("utf-8"))
    if locked_version != EXPECTED_OPENAI_SDK_VERSION:
        raise ValueError("uv.lock does not contain the audited OpenAI SDK version")
    payload = FrozenExecutionImplementationPayload(
        schema_version="real-live-execution-implementation-v1",
        **{
            name: hashlib.sha256(path.read_bytes()).hexdigest()
            for name, path in EXECUTION_SOURCE_PATHS.items()
        },
        package_python_inventory_sha256=_package_python_inventory_sha256(MODULE_ROOT.parent),
        uv_lock_sha256=EXPECTED_UV_LOCK_SHA256,
        openai_sdk_version=EXPECTED_OPENAI_SDK_VERSION,
    )
    body = payload.model_dump(mode="json")
    return FrozenExecutionImplementation.model_validate(
        {**body, "implementation_sha256": canonical_sha256(body)}
    )


def verify_execution_implementation(
    expected: FrozenExecutionImplementation,
    *,
    verify_installed_sdk: bool = True,
) -> None:
    """Fail closed if live code, lockfile, or the installed SDK changed after planning."""

    if frozen_execution_implementation() != expected:
        raise ValueError("live execution implementation differs from the sealed plan")
    if verify_installed_sdk:
        try:
            installed = importlib.metadata.version("openai")
        except importlib.metadata.PackageNotFoundError as error:
            raise ValueError("the sealed OpenAI SDK is not installed") from error
        if installed != expected.openai_sdk_version:
            raise ValueError("installed OpenAI SDK version differs from the sealed plan")


def caller_execution_identity_sha256(
    luna: FrozenLunaConfiguration,
    implementation: FrozenExecutionImplementation,
) -> str:
    return canonical_sha256(
        {
            "provider": "openai",
            "luna": luna.model_dump(mode="json"),
            "execution_implementation_sha256": implementation.implementation_sha256,
            "openai_sdk_version": implementation.openai_sdk_version,
        }
    )


def _locked_openai_version(lock_text: str) -> str:
    lines = lock_text.splitlines()
    for index, line in enumerate(lines[:-1]):
        if line == 'name = "openai"' and lines[index + 1].startswith('version = "'):
            return lines[index + 1].removeprefix('version = "').removesuffix('"')
    raise ValueError("uv.lock does not contain an OpenAI package entry")


def _execution_lock_path() -> Path:
    candidates = (MODULE_ROOT.parents[2] / "uv.lock", RUNTIME_UV_LOCK_PATH)
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    raise ValueError("audited uv.lock is unavailable in source tree and /app runtime")


def _package_python_inventory_sha256(package_root: Path) -> str:
    """Hash every installed criteriabench Python source by relative path and bytes."""

    digest = hashlib.sha256()
    paths = sorted(
        package_root.rglob("*.py"),
        key=lambda path: path.relative_to(package_root).as_posix(),
    )
    if not paths:
        raise ValueError("criteriabench Python source inventory is empty")
    for path in paths:
        relative = path.relative_to(package_root).as_posix().encode("utf-8")
        digest.update(relative)
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


def frozen_pricing() -> FrozenPricing:
    payload = {
        "currency": "USD",
        "pricing_id": "openai-gpt-5.6-luna-2026-09-02",
        "uncached_input_usd_per_million": "0.200000000",
        "cached_input_usd_per_million": "0.020000000",
        "cache_write_input_usd_per_million": "0.250000000",
        "output_usd_per_million": "1.200000000",
        "rounding": "usd_9dp_half_up",
        "reviewed_at_utc": "2026-09-02T00:00:00Z",
        "valid_through_utc": "2026-09-02T23:59:59Z",
    }
    return FrozenPricing.model_validate({**payload, "pricing_sha256": canonical_sha256(payload)})


def freeze_output_contract(
    contract: StrictOutputContract[BaseModel],
) -> FrozenOutputContract:
    return FrozenOutputContract(
        contract_id=contract.contract_id,
        track=contract.track,
        schema_name=contract.schema_name,
        schema_sha256=contract.schema_sha256,
        parser_id=contract.parser_id,
        parser_sha256=contract.parser_sha256,
        parser_code_sha256=contract.parser_code_sha256,
        prompt_sha256=contract.prompt_sha256,
    )


def _lint_identity_free_schema(schema: Mapping[str, object]) -> None:
    forbidden = {
        "case_id",
        "trial_id",
        "document_id",
        "source_sha256",
        "reference",
        "reference_sha256",
        "annotation_role",
        "source_path",
    }
    stack: list[object] = [schema]
    while stack:
        value = stack.pop()
        if isinstance(value, Mapping):
            properties = value.get("properties")
            if isinstance(properties, Mapping):
                present = forbidden.intersection(str(name) for name in properties)
                if present:
                    raise ValueError("strict output schema contains trusted identity fields")
            stack.extend(value.values())
        elif isinstance(value, list | tuple):
            stack.extend(value)


def _enforce_llf_engineering_limits(output: LlfSemanticOutput) -> None:
    """Apply split-independent, predeclared limits to the normalized LLF tree."""

    if len(output.nodes) > cast(int, LLF_ENGINEERING_LIMITS["semantic_nodes"]):
        raise ValueError("LLF output exceeds the predeclared semantic-node limit")
    depths: dict[str, int] = {}
    for node in output.nodes:
        children: tuple[str, ...]
        if isinstance(node, LlfAttributeNode):
            children = (node.target_node_id,)
            if len(node.attribute) > cast(
                int,
                LLF_ENGINEERING_LIMITS["identifier_characters"],
            ):
                raise ValueError("LLF output exceeds the predeclared identifier limit")
        elif isinstance(node, LlfCallNode):
            children = (node.callee_node_id, *node.argument_node_ids)
            if len(node.argument_node_ids) > cast(
                int,
                LLF_ENGINEERING_LIMITS["call_arguments"],
            ):
                raise ValueError("LLF output exceeds the predeclared call-argument limit")
        elif isinstance(node, LlfTupleNode):
            children = node.item_node_ids
            if len(children) > cast(
                int,
                LLF_ENGINEERING_LIMITS["collection_items"],
            ):
                raise ValueError("LLF output exceeds the predeclared collection limit")
        elif isinstance(node, LlfBooleanOperationNode):
            children = node.operand_node_ids
            if len(children) > cast(
                int,
                LLF_ENGINEERING_LIMITS["collection_items"],
            ):
                raise ValueError("LLF output exceeds the predeclared collection limit")
        else:
            children = ()
        if isinstance(node, LlfSymbolNode) and len(node.name) > cast(
            int,
            LLF_ENGINEERING_LIMITS["identifier_characters"],
        ):
            raise ValueError("LLF output exceeds the predeclared identifier limit")
        if isinstance(node, LlfStringNode) and len(node.value.encode("utf-8")) > cast(
            int,
            LLF_ENGINEERING_LIMITS["string_utf8_bytes"],
        ):
            raise ValueError("LLF output exceeds the predeclared string limit")
        depths[node.node_id] = 1 + max((depths[child] for child in children), default=0)
    if depths[output.root_node_id] > cast(
        int,
        LLF_ENGINEERING_LIMITS["semantic_depth"],
    ):
        raise ValueError("LLF output exceeds the predeclared semantic-depth limit")


def parse_utc_timestamp(value: str) -> datetime:
    parsed = datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC)
    return parsed
