"""Safe, lossless semantic parsing for inert Leaf Logical Form references.

The LLF import deliberately stores each logical form as source text.  This
module performs the next, still non-executing, step: it tokenizes a bounded
expression, parses it with Python's parser, accepts only the tiny syntax used by
the pinned corpus, and serializes that syntax into a flat node table.

No parsed node is compiled, evaluated, imported, or invoked.  The result is a
syntax-preserving reference representation, not a claim that the LLF operators
have already been mapped to CriteriaBench's executable GraphV2 semantics.
"""

from __future__ import annotations

import argparse
import ast
import hashlib
import io
import json
import keyword
import sys
import tokenize
from collections import Counter
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated, Any, Literal, cast

from pydantic import (
    AfterValidator,
    ConfigDict,
    Field,
    StrictBool,
    StrictInt,
    StrictStr,
    model_validator,
)

from criteriabench.domain.schemas import StrictModel
from criteriabench.real.graph_v2 import strict_output_schema
from criteriabench.real.llf import (
    LlfAnnotation,
    load_llf_generation_records_bytes,
    load_llf_records_bytes,
)

SEMANTIC_SCHEMA_VERSION: Literal["llf-semantics-v1"] = "llf-semantics-v1"
OUTPUT_SCHEMA_VERSION: Literal["llf-semantic-output-v1"] = "llf-semantic-output-v1"
REFERENCE_CORPUS_SCHEMA_VERSION: Literal["llf-reference-corpus-v1"] = "llf-reference-corpus-v1"
COVERAGE_SCHEMA_VERSION = "llf-semantic-coverage-v1"
SPLIT_COVERAGE_SCHEMA_VERSION = "llf-semantic-split-coverage-v1"
PARSER_VERSION = "bounded-python-ast-allowlist-v1"
PINNED_LLF_GENERATION_MANIFEST_SHA256 = (
    "c67911011a906afe5e81c4f39310a765d899244a3c831f180111b3260ac9ce58"
)
PINNED_LLF_GENERATION_CASES_SHA256 = (
    "ac7d9c0cf01158afb8b1ea6f8d320dc632b9211742296225d16308aa60884f84"
)
PINNED_LLF_RECORDS_SHA256 = "43be72b6aef84c963adee017601665ba38f3ea79abf3e7f527b23ecf6cd74f50"
PINNED_LLF_SEMANTIC_COVERAGE_SHA256 = (
    "df4fef8669aa1fd5fba23ae7dc858dff8f506d015b971ddfd9351d23538e5ae9"
)
PINNED_LLF_SPLIT_REFERENCE_SHA256 = {
    "development": "3fda6daba1368826b02317e0f7eec82baeb9bb1526c87877fe1364a674dfea12",
    "test": "0f440dc69c5117f74b3470f9f94d06d424dc456611ada536ec9f03f663242c5e",
}
PINNED_LLF_SPLIT_COVERAGE_SHA256 = {
    "development": "bda3d2a2144931213032cc3ffb368e59640dee63176573e96af35017756cd8ee",
    "test": "023ac5b3bf0ff6e50735c3c60b1e2046626a273b7f2f0ffa6b2034a3da3eedd9",
}

MAX_LLF_SOURCE_BYTES = 16_384
MAX_SEMANTIC_NODES = 2_048
MAX_SEMANTIC_DEPTH = 128
MAX_CALL_ARGUMENTS = 256
MAX_COLLECTION_ITEMS = 256
MAX_IDENTIFIER_LENGTH = 128
MAX_STRING_BYTES = 16_384

_NODE_ID_PATTERN = r"^n[0-9]{4}$"
_IDENTIFIER_PATTERN = r"^[A-Za-z][A-Za-z0-9_]*$"
_HASH_PATTERN = r"^[0-9a-f]{64}$"
_KEYWORD_SENTINELS = {
    "and": "llfkw_and",
    "or": "llfkw_or",
    "for": "llfkw_for",
    "except": "llfkw_except",
}
_SENTINEL_KEYWORDS = {value: key for key, value in _KEYWORD_SENTINELS.items()}
_UNSAFE_IDENTIFIERS = frozenset(
    {
        "__import__",
        "compile",
        "delattr",
        "eval",
        "exec",
        "getattr",
        "globals",
        "importlib",
        "locals",
        "open",
        "os",
        "pathlib",
        "popen",
        "read",
        "setattr",
        "subprocess",
        "sys",
        "system",
        "vars",
        "write",
    }
)
_IGNORED_TOKEN_TYPES = {
    tokenize.NL,
    tokenize.NEWLINE,
    tokenize.INDENT,
    tokenize.DEDENT,
    tokenize.ENDMARKER,
}
_ALLOWED_OPERATOR_TOKENS = {"(", ")", ",", "."}
_COMMUTATIVE_CALL_NAMES = frozenset({"and", "intersect", "or", "union"})
_EXPECTED_SPLIT_COUNTS = {
    "all": (2_000, 1_997, 3),
    "development": (200, 200, 0),
    "test": (1_800, 1_797, 3),
}


class LlfSemanticParseError(ValueError):
    """A stable, non-executing LLF parse failure safe to include in reports."""

    def __init__(self, code: str, message: str, *, source_name: str) -> None:
        super().__init__(f"{source_name}: {message}")
        self.code = code
        self.source_name = source_name


def _safe_identifier(value: str) -> str:
    if value.startswith("_") or value in _SENTINEL_KEYWORDS:
        raise ValueError("reserved identifiers are not permitted")
    if value in _UNSAFE_IDENTIFIERS:
        raise ValueError("unsafe identifier is not permitted")
    return value


NodeId = Annotated[StrictStr, Field(pattern=_NODE_ID_PATTERN)]
SafeIdentifier = Annotated[
    StrictStr,
    Field(min_length=1, max_length=MAX_IDENTIFIER_LENGTH, pattern=_IDENTIFIER_PATTERN),
    AfterValidator(_safe_identifier),
]
Sha256 = Annotated[StrictStr, Field(pattern=_HASH_PATTERN)]


class _SemanticModel(StrictModel):
    """Strict immutable model that does not alter source string whitespace."""

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        strict=True,
        str_strip_whitespace=False,
    )


class LlfSymbolNode(_SemanticModel):
    node_id: NodeId
    kind: Literal["symbol"] = "symbol"
    name: SafeIdentifier


class LlfStringNode(_SemanticModel):
    node_id: NodeId
    kind: Literal["string"] = "string"
    value: Annotated[StrictStr, Field(max_length=MAX_STRING_BYTES)]


class LlfBooleanNode(_SemanticModel):
    node_id: NodeId
    kind: Literal["boolean"] = "boolean"
    value: StrictBool


class LlfAttributeNode(_SemanticModel):
    node_id: NodeId
    kind: Literal["attribute"] = "attribute"
    target_node_id: NodeId
    attribute: SafeIdentifier


class LlfCallNode(_SemanticModel):
    node_id: NodeId
    kind: Literal["call"] = "call"
    callee_node_id: NodeId
    argument_node_ids: Annotated[tuple[NodeId, ...], Field(max_length=MAX_CALL_ARGUMENTS)]


class LlfTupleNode(_SemanticModel):
    node_id: NodeId
    kind: Literal["tuple"] = "tuple"
    item_node_ids: Annotated[tuple[NodeId, ...], Field(max_length=MAX_COLLECTION_ITEMS)]


class LlfBooleanOperationNode(_SemanticModel):
    node_id: NodeId
    kind: Literal["boolean_operation"] = "boolean_operation"
    operator: Literal["and", "or"]
    operand_node_ids: Annotated[
        tuple[NodeId, ...],
        Field(min_length=2, max_length=MAX_COLLECTION_ITEMS),
    ]


type LlfSemanticNode = Annotated[
    LlfSymbolNode
    | LlfStringNode
    | LlfBooleanNode
    | LlfAttributeNode
    | LlfCallNode
    | LlfTupleNode
    | LlfBooleanOperationNode,
    Field(discriminator="kind"),
]


def _child_node_ids(node: LlfSemanticNode) -> tuple[str, ...]:
    if isinstance(node, LlfAttributeNode):
        return (node.target_node_id,)
    if isinstance(node, LlfCallNode):
        return (node.callee_node_id, *node.argument_node_ids)
    if isinstance(node, LlfTupleNode):
        return node.item_node_ids
    if isinstance(node, LlfBooleanOperationNode):
        return node.operand_node_ids
    return ()


def _validate_semantic_node_table(
    root_node_id: str,
    nodes: tuple[LlfSemanticNode, ...],
) -> None:
    by_id: dict[str, LlfSemanticNode] = {}
    incoming: Counter[str] = Counter()
    for index, node in enumerate(nodes):
        expected_id = f"n{index:04d}"
        if node.node_id != expected_id:
            raise ValueError("node IDs must be contiguous canonical postorder IDs")
        for child_id in _child_node_ids(node):
            if child_id not in by_id:
                raise ValueError("node references must point to an earlier node")
            incoming[child_id] += 1
        by_id[node.node_id] = node

    if root_node_id not in by_id:
        raise ValueError("root_node_id must identify a node")
    reachable: set[str] = set()
    pending = [root_node_id]
    while pending:
        node_id = pending.pop()
        if node_id in reachable:
            continue
        reachable.add(node_id)
        pending.extend(_child_node_ids(by_id[node_id]))
    if reachable != set(by_id):
        raise ValueError("every semantic node must be reachable from the root")
    if incoming[root_node_id] != 0:
        raise ValueError("semantic root cannot be referenced by another node")
    if any(incoming[node_id] != 1 for node_id in by_id if node_id != root_node_id):
        raise ValueError("semantic syntax must be a tree without shared child nodes")


class LlfSemanticOutput(_SemanticModel):
    """Identity-free provider output: a strict, nonrecursive LLF node table."""

    schema_version: Literal["llf-semantic-output-v1"] = OUTPUT_SCHEMA_VERSION
    root_node_id: NodeId
    nodes: Annotated[
        tuple[LlfSemanticNode, ...],
        Field(min_length=1, max_length=MAX_SEMANTIC_NODES),
    ]

    @model_validator(mode="after")
    def node_table_is_canonical_and_connected(self) -> LlfSemanticOutput:
        _validate_semantic_node_table(self.root_node_id, self.nodes)
        return self


class LlfSemanticReference(_SemanticModel):
    """One bounded LLF expression as a canonical, acyclic flat node table."""

    schema_version: Literal["llf-semantics-v1"] = SEMANTIC_SCHEMA_VERSION
    source_sha256: Sha256
    root_node_id: NodeId
    nodes: Annotated[
        tuple[LlfSemanticNode, ...],
        Field(min_length=1, max_length=MAX_SEMANTIC_NODES),
    ]

    @model_validator(mode="after")
    def node_table_is_canonical_and_connected(self) -> LlfSemanticReference:
        _validate_semantic_node_table(self.root_node_id, self.nodes)
        return self


def llf_semantic_strict_json_schema() -> dict[str, Any]:
    """Build the deterministic strict provider schema for LLF-native output."""

    return strict_output_schema(LlfSemanticOutput.model_json_schema())


def inflate_llf_semantic_output(
    output: LlfSemanticOutput,
    *,
    trusted_source_sha256: str,
) -> LlfSemanticReference:
    """Attach application-trusted reference lineage outside the model boundary."""

    return LlfSemanticReference(
        source_sha256=trusted_source_sha256,
        root_node_id=output.root_node_id,
        nodes=output.nodes,
    )


def _sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


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


def _parse_error(code: str, message: str, source_name: str) -> LlfSemanticParseError:
    return LlfSemanticParseError(code, message, source_name=source_name)


def _normalize_keyword_identifiers(source: str, *, source_name: str) -> str:
    """Normalize only LLF's keyword-shaped identifiers, never string contents."""

    wrapped = f"(\n{source}\n)"
    try:
        tokens = list(tokenize.generate_tokens(io.StringIO(wrapped).readline))
    except (IndentationError, tokenize.TokenError) as exc:
        raise _parse_error(
            "tokenization_error",
            "logical form cannot be tokenized",
            source_name,
        ) from exc

    significant_indices: list[int] = []
    previous_significant_type: int | None = None
    for index, token in enumerate(tokens):
        if token.type in _IGNORED_TOKEN_TYPES:
            continue
        if token.type == getattr(tokenize, "FSTRING_START", -1):
            raise _parse_error(
                "prefixed_string_not_allowed",
                "prefixed string literals are not LLF syntax",
                source_name,
            )
        if token.type == tokenize.COMMENT:
            raise _parse_error("comments_not_allowed", "comments are not LLF syntax", source_name)
        if token.type == tokenize.OP:
            if token.string not in _ALLOWED_OPERATOR_TOKENS:
                raise _parse_error(
                    "disallowed_token",
                    f"operator token {token.string!r} is not LLF syntax",
                    source_name,
                )
        elif token.type == tokenize.STRING:
            if not token.string.startswith(("'", '"')):
                raise _parse_error(
                    "prefixed_string_not_allowed",
                    "prefixed string literals are not LLF syntax",
                    source_name,
                )
            if previous_significant_type == tokenize.STRING:
                raise _parse_error(
                    "implicit_string_concatenation",
                    "adjacent string literals are not losslessly supported",
                    source_name,
                )
        elif token.type == tokenize.NAME:
            if token.string in _SENTINEL_KEYWORDS:
                raise _parse_error(
                    "reserved_identifier",
                    "internal normalization identifier is reserved",
                    source_name,
                )
        else:
            raise _parse_error(
                "disallowed_token",
                f"token category {tokenize.tok_name[token.type]} is not LLF syntax",
                source_name,
            )
        significant_indices.append(index)
        previous_significant_type = token.type

    replacements: dict[int, str] = {}
    for position, index in enumerate(significant_indices):
        token = tokens[index]
        if token.type != tokenize.NAME:
            continue
        previous = tokens[significant_indices[position - 1]] if position else None
        following = (
            tokens[significant_indices[position + 1]]
            if position + 1 < len(significant_indices)
            else None
        )
        if token.string in {"and", "or"}:
            is_call = following is not None and following.string == "("
            valid_call_position = previous is None or previous.string in {"(", ","}
            if is_call and valid_call_position:
                replacements[index] = _KEYWORD_SENTINELS[token.string]
        elif token.string in {"for", "except"}:
            if previous is not None and previous.string == ".":
                replacements[index] = _KEYWORD_SENTINELS[token.string]
            else:
                raise _parse_error(
                    "keyword_context",
                    f"{token.string!r} is valid only as an LLF method name",
                    source_name,
                )
        elif keyword.iskeyword(token.string):
            if token.string not in {"True", "False"}:
                raise _parse_error(
                    "keyword_not_allowed",
                    f"Python keyword {token.string!r} is not LLF syntax",
                    source_name,
                )

    normalized = [
        (token.type, replacements.get(index, token.string)) for index, token in enumerate(tokens)
    ]
    return tokenize.untokenize(normalized)


class _FlatAstBuilder:
    def __init__(self, *, source_name: str) -> None:
        self.source_name = source_name
        self.nodes: list[LlfSemanticNode] = []

    def _node_id(self) -> str:
        if len(self.nodes) >= MAX_SEMANTIC_NODES:
            raise _parse_error(
                "node_limit_exceeded",
                "logical form exceeds the semantic node limit",
                self.source_name,
            )
        return f"n{len(self.nodes):04d}"

    def _append(self, node: LlfSemanticNode) -> str:
        self.nodes.append(node)
        return node.node_id

    def build(self, node: ast.AST, *, depth: int = 1) -> str:
        if depth > MAX_SEMANTIC_DEPTH:
            raise _parse_error(
                "depth_limit_exceeded",
                "logical form exceeds the semantic depth limit",
                self.source_name,
            )
        if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load):
            name = _SENTINEL_KEYWORDS.get(node.id, node.id)
            try:
                symbol = LlfSymbolNode(node_id=self._node_id(), name=name)
            except ValueError as exc:
                raise _parse_error(
                    "unsafe_identifier",
                    f"identifier {name!r} is not permitted",
                    self.source_name,
                ) from exc
            return self._append(symbol)
        if isinstance(node, ast.Constant):
            if type(node.value) is str:
                if len(node.value.encode("utf-8")) > MAX_STRING_BYTES:
                    raise _parse_error(
                        "string_limit_exceeded",
                        "string literal exceeds the byte limit",
                        self.source_name,
                    )
                return self._append(LlfStringNode(node_id=self._node_id(), value=node.value))
            if type(node.value) is bool:
                return self._append(LlfBooleanNode(node_id=self._node_id(), value=node.value))
            raise _parse_error(
                "constant_not_allowed",
                "only string and boolean literals are LLF syntax",
                self.source_name,
            )
        if isinstance(node, ast.Attribute) and isinstance(node.ctx, ast.Load):
            target_id = self.build(node.value, depth=depth + 1)
            attribute = _SENTINEL_KEYWORDS.get(node.attr, node.attr)
            try:
                attribute_node = LlfAttributeNode(
                    node_id=self._node_id(),
                    target_node_id=target_id,
                    attribute=attribute,
                )
            except ValueError as exc:
                raise _parse_error(
                    "unsafe_identifier",
                    f"attribute {attribute!r} is not permitted",
                    self.source_name,
                ) from exc
            return self._append(attribute_node)
        if isinstance(node, ast.Call):
            if node.keywords:
                raise _parse_error(
                    "keyword_arguments_not_allowed",
                    "LLF calls may contain positional arguments only",
                    self.source_name,
                )
            if len(node.args) > MAX_CALL_ARGUMENTS:
                raise _parse_error(
                    "argument_limit_exceeded",
                    "LLF call exceeds the argument limit",
                    self.source_name,
                )
            callee_id = self.build(node.func, depth=depth + 1)
            argument_ids = tuple(self.build(argument, depth=depth + 1) for argument in node.args)
            return self._append(
                LlfCallNode(
                    node_id=self._node_id(),
                    callee_node_id=callee_id,
                    argument_node_ids=argument_ids,
                )
            )
        if isinstance(node, ast.Tuple) and isinstance(node.ctx, ast.Load):
            if len(node.elts) > MAX_COLLECTION_ITEMS:
                raise _parse_error(
                    "collection_limit_exceeded",
                    "LLF tuple exceeds the item limit",
                    self.source_name,
                )
            item_ids = tuple(self.build(item, depth=depth + 1) for item in node.elts)
            return self._append(LlfTupleNode(node_id=self._node_id(), item_node_ids=item_ids))
        if isinstance(node, ast.BoolOp) and isinstance(node.op, (ast.And, ast.Or)):
            if not 2 <= len(node.values) <= MAX_COLLECTION_ITEMS:
                raise _parse_error(
                    "collection_limit_exceeded",
                    "LLF boolean operation has an invalid operand count",
                    self.source_name,
                )
            operand_ids = tuple(self.build(operand, depth=depth + 1) for operand in node.values)
            operator: Literal["and", "or"] = "and" if isinstance(node.op, ast.And) else "or"
            return self._append(
                LlfBooleanOperationNode(
                    node_id=self._node_id(),
                    operator=operator,
                    operand_node_ids=operand_ids,
                )
            )
        raise _parse_error(
            "unsupported_syntax",
            f"AST node {type(node).__name__} is not LLF syntax",
            self.source_name,
        )


def parse_llf_semantic(
    source: str,
    *,
    source_name: str = "<memory>",
) -> LlfSemanticReference:
    """Parse one LLF reference into inert flat nodes without executing it."""

    if not isinstance(source, str):
        raise _parse_error("invalid_source", "logical form must be a string", source_name)
    if not source.strip():
        raise _parse_error("empty_source", "logical form must not be empty", source_name)
    if "\x00" in source:
        raise _parse_error("nul_not_allowed", "logical form contains NUL", source_name)
    try:
        source_bytes = source.encode("utf-8", errors="strict")
    except UnicodeEncodeError as exc:
        raise _parse_error(
            "invalid_unicode",
            "logical form is not valid UTF-8",
            source_name,
        ) from exc
    if len(source_bytes) > MAX_LLF_SOURCE_BYTES:
        raise _parse_error(
            "source_limit_exceeded",
            "logical form exceeds the source byte limit",
            source_name,
        )

    normalized = _normalize_keyword_identifiers(source, source_name=source_name)
    try:
        expression = ast.parse(normalized, filename=source_name, mode="eval")
    except (SyntaxError, ValueError) as exc:
        raise _parse_error(
            "malformed_expression",
            "logical form is not one complete expression",
            source_name,
        ) from exc

    builder = _FlatAstBuilder(source_name=source_name)
    root_node_id = builder.build(expression.body)
    return LlfSemanticReference(
        source_sha256=_sha256_bytes(source_bytes),
        root_node_id=root_node_id,
        nodes=tuple(builder.nodes),
    )


def canonical_llf_json(reference: LlfSemanticReference) -> str:
    """Return the canonical JSON representation including source lineage."""

    return _canonical_json_bytes(reference.model_dump(mode="json")).decode("utf-8")


def canonical_llf_sha256(reference: LlfSemanticReference) -> str:
    """Hash the complete canonical semantic reference and its source lineage."""

    return _sha256_bytes(canonical_llf_json(reference).encode("utf-8"))


def semantic_tree_sha256(reference: LlfSemanticReference) -> str:
    """Hash semantic syntax independently of original formatting."""

    payload = {
        "schema_version": reference.schema_version,
        "root_node_id": reference.root_node_id,
        "nodes": [node.model_dump(mode="json") for node in reference.nodes],
    }
    return _sha256_bytes(_canonical_json_bytes(payload))


def render_llf_semantic(reference: LlfSemanticReference) -> str:
    """Render canonical LLF source that reparses to the same semantic node table."""

    rendered: dict[str, str] = {}
    for node in reference.nodes:
        if isinstance(node, LlfSymbolNode):
            value = node.name
        elif isinstance(node, LlfStringNode):
            value = json.dumps(node.value, ensure_ascii=True)
        elif isinstance(node, LlfBooleanNode):
            value = "True" if node.value else "False"
        elif isinstance(node, LlfAttributeNode):
            value = f"{rendered[node.target_node_id]}.{node.attribute}"
        elif isinstance(node, LlfCallNode):
            arguments = ", ".join(rendered[item] for item in node.argument_node_ids)
            value = f"{rendered[node.callee_node_id]}({arguments})"
        elif isinstance(node, LlfTupleNode):
            items = [rendered[item] for item in node.item_node_ids]
            suffix = "," if len(items) == 1 else ""
            value = f"({', '.join(items)}{suffix})"
        else:
            operands = f" {node.operator} ".join(rendered[item] for item in node.operand_node_ids)
            value = f"({operands})"
        rendered[node.node_id] = value
    return rendered[reference.root_node_id]


type LlfSemanticValue = LlfSemanticOutput | LlfSemanticReference
SignatureCounter = Counter[str]


def _canonical_signature(value: object) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    )


def _is_commutative_call(
    node: LlfCallNode,
    by_id: Mapping[str, LlfSemanticNode],
) -> bool:
    callee = by_id[node.callee_node_id]
    return isinstance(callee, LlfSymbolNode) and callee.name in _COMMUTATIVE_CALL_NAMES


def _scoring_payloads(value: LlfSemanticValue) -> dict[str, dict[str, object]]:
    by_id = {node.node_id: node for node in value.nodes}
    payloads: dict[str, dict[str, object]] = {}
    for node in value.nodes:
        if isinstance(node, LlfSymbolNode):
            payload: dict[str, object] = {"kind": "symbol", "name": node.name}
        elif isinstance(node, LlfStringNode):
            payload = {"kind": "string", "value": node.value}
        elif isinstance(node, LlfBooleanNode):
            payload = {"kind": "boolean", "value": node.value}
        elif isinstance(node, LlfAttributeNode):
            payload = {
                "kind": "attribute",
                "attribute": node.attribute,
                "target": payloads[node.target_node_id],
            }
        elif isinstance(node, LlfCallNode):
            arguments = [payloads[item] for item in node.argument_node_ids]
            if _is_commutative_call(node, by_id):
                arguments.sort(key=_canonical_signature)
            payload = {
                "kind": "call",
                "callee": payloads[node.callee_node_id],
                "arguments": arguments,
            }
        elif isinstance(node, LlfTupleNode):
            payload = {
                "kind": "tuple",
                "items": [payloads[item] for item in node.item_node_ids],
            }
        else:
            operands = [payloads[item] for item in node.operand_node_ids]
            operands.sort(key=_canonical_signature)
            payload = {
                "kind": "boolean_operation",
                "operator": node.operator,
                "operands": operands,
            }
        payloads[node.node_id] = payload
    return payloads


def canonical_llf_scoring_json(value: LlfSemanticValue) -> str:
    """Canonical LLF tree used for scoring known commutative operators."""

    payloads = _scoring_payloads(value)
    return _canonical_json_bytes(payloads[value.root_node_id]).decode("utf-8")


def canonical_llf_scoring_sha256(value: LlfSemanticValue) -> str:
    """Hash the canonical scoring tree without source or case identity."""

    return _sha256_bytes(canonical_llf_scoring_json(value).encode("utf-8"))


@dataclass(frozen=True, slots=True)
class LlfMatchCounts:
    """Multiset matches with deterministic precision, recall, and F1."""

    true_positive: int
    false_positive: int
    false_negative: int

    @property
    def precision(self) -> float:
        return _ratio(self.true_positive, self.true_positive + self.false_positive)

    @property
    def recall(self) -> float:
        return _ratio(self.true_positive, self.true_positive + self.false_negative)

    @property
    def f1(self) -> float:
        denominator = 2 * self.true_positive + self.false_positive + self.false_negative
        return _ratio(2 * self.true_positive, denominator)

    def __add__(self, other: LlfMatchCounts) -> LlfMatchCounts:
        return LlfMatchCounts(
            true_positive=self.true_positive + other.true_positive,
            false_positive=self.false_positive + other.false_positive,
            false_negative=self.false_negative + other.false_negative,
        )


@dataclass(frozen=True, slots=True)
class LlfSemanticComponents:
    nodes: SignatureCounter
    edges: SignatureCounter
    calls: SignatureCounter
    method_attributes: SignatureCounter
    symbols: SignatureCounter
    strings: SignatureCounter
    booleans: SignatureCounter


@dataclass(frozen=True, slots=True)
class LlfSemanticComparison:
    """Exact and partial LLF-native metrics for one scorable reference."""

    exact_match: bool
    nodes: LlfMatchCounts
    edges: LlfMatchCounts
    calls: LlfMatchCounts
    method_attributes: LlfMatchCounts
    symbols: LlfMatchCounts
    strings: LlfMatchCounts
    booleans: LlfMatchCounts

    @property
    def structure(self) -> LlfMatchCounts:
        return self.nodes + self.edges

    @property
    def typed_components(self) -> LlfMatchCounts:
        return self.calls + self.method_attributes + self.symbols + self.strings + self.booleans


def _call_signature(
    node: LlfCallNode,
    by_id: Mapping[str, LlfSemanticNode],
) -> dict[str, object]:
    callee = by_id[node.callee_node_id]
    signature: dict[str, object] = {
        "kind": "call",
        "argument_count": len(node.argument_node_ids),
        "callee_kind": callee.kind,
    }
    if isinstance(callee, LlfSymbolNode):
        signature["callee_name"] = callee.name
    elif isinstance(callee, LlfAttributeNode):
        signature["callee_name"] = callee.attribute
    return signature


def _local_node_signature(
    node: LlfSemanticNode,
    by_id: Mapping[str, LlfSemanticNode],
) -> str:
    if isinstance(node, LlfSymbolNode):
        payload: dict[str, object] = {"kind": node.kind, "name": node.name}
    elif isinstance(node, LlfStringNode):
        payload = {"kind": node.kind, "value": node.value}
    elif isinstance(node, LlfBooleanNode):
        payload = {"kind": node.kind, "value": node.value}
    elif isinstance(node, LlfAttributeNode):
        payload = {"kind": node.kind, "attribute": node.attribute}
    elif isinstance(node, LlfCallNode):
        payload = _call_signature(node, by_id)
    elif isinstance(node, LlfTupleNode):
        payload = {"kind": node.kind, "item_count": len(node.item_node_ids)}
    else:
        payload = {
            "kind": node.kind,
            "operator": node.operator,
            "operand_count": len(node.operand_node_ids),
        }
    return _canonical_signature(payload)


def llf_semantic_components(value: LlfSemanticValue) -> LlfSemanticComponents:
    """Extract deterministic structural and typed LLF component multisets."""

    by_id = {node.node_id: node for node in value.nodes}
    payloads = _scoring_payloads(value)
    subtree_signatures = {
        node_id: _canonical_signature(payload) for node_id, payload in payloads.items()
    }
    local_signatures = {node.node_id: _local_node_signature(node, by_id) for node in value.nodes}
    nodes: SignatureCounter = Counter()
    edges: SignatureCounter = Counter()
    calls: SignatureCounter = Counter()
    method_attributes: SignatureCounter = Counter()
    symbols: SignatureCounter = Counter()
    strings: SignatureCounter = Counter()
    booleans: SignatureCounter = Counter()

    def add_edge(
        parent: LlfSemanticNode,
        child_id: str,
        *,
        role: str,
        position: int | None,
    ) -> None:
        edges[
            _canonical_signature(
                {
                    "parent": local_signatures[parent.node_id],
                    "child_subtree": subtree_signatures[child_id],
                    "role": role,
                    "position": position,
                }
            )
        ] += 1

    for node in value.nodes:
        local = local_signatures[node.node_id]
        nodes[local] += 1
        if isinstance(node, LlfSymbolNode):
            symbols[_canonical_signature({"name": node.name})] += 1
        elif isinstance(node, LlfStringNode):
            strings[_canonical_signature({"value": node.value})] += 1
        elif isinstance(node, LlfBooleanNode):
            booleans[_canonical_signature({"value": node.value})] += 1
        elif isinstance(node, LlfAttributeNode):
            add_edge(node, node.target_node_id, role="target", position=0)
        elif isinstance(node, LlfCallNode):
            call_signature = _canonical_signature(_call_signature(node, by_id))
            calls[call_signature] += 1
            callee = by_id[node.callee_node_id]
            if isinstance(callee, LlfAttributeNode):
                method_attributes[_canonical_signature({"attribute": callee.attribute})] += 1
            add_edge(node, node.callee_node_id, role="callee", position=0)
            commutative = _is_commutative_call(node, by_id)
            for position, child_id in enumerate(node.argument_node_ids):
                add_edge(
                    node,
                    child_id,
                    role="argument",
                    position=None if commutative else position,
                )
        elif isinstance(node, LlfTupleNode):
            for position, child_id in enumerate(node.item_node_ids):
                add_edge(node, child_id, role="item", position=position)
        elif isinstance(node, LlfBooleanOperationNode):
            for child_id in node.operand_node_ids:
                add_edge(node, child_id, role="operand", position=None)

    return LlfSemanticComponents(
        nodes=nodes,
        edges=edges,
        calls=calls,
        method_attributes=method_attributes,
        symbols=symbols,
        strings=strings,
        booleans=booleans,
    )


def _match_counts(
    prediction: SignatureCounter,
    reference: SignatureCounter,
) -> LlfMatchCounts:
    true_positive = sum((prediction & reference).values())
    return LlfMatchCounts(
        true_positive=true_positive,
        false_positive=sum(prediction.values()) - true_positive,
        false_negative=sum(reference.values()) - true_positive,
    )


def _failed_counts(reference: SignatureCounter) -> LlfMatchCounts:
    return LlfMatchCounts(0, 0, sum(reference.values()))


def compare_llf_semantics(
    prediction: LlfSemanticValue,
    reference: LlfSemanticValue,
) -> LlfSemanticComparison:
    """Score one completed model output against one human LLF AST."""

    predicted = llf_semantic_components(prediction)
    expected = llf_semantic_components(reference)
    return LlfSemanticComparison(
        exact_match=(
            canonical_llf_scoring_sha256(prediction) == canonical_llf_scoring_sha256(reference)
        ),
        nodes=_match_counts(predicted.nodes, expected.nodes),
        edges=_match_counts(predicted.edges, expected.edges),
        calls=_match_counts(predicted.calls, expected.calls),
        method_attributes=_match_counts(
            predicted.method_attributes,
            expected.method_attributes,
        ),
        symbols=_match_counts(predicted.symbols, expected.symbols),
        strings=_match_counts(predicted.strings, expected.strings),
        booleans=_match_counts(predicted.booleans, expected.booleans),
    )


def failed_llf_semantic_comparison(
    reference: LlfSemanticValue,
) -> LlfSemanticComparison:
    """Score refusal, timeout, invalid JSON, or other failure as an empty output."""

    expected = llf_semantic_components(reference)
    return LlfSemanticComparison(
        exact_match=False,
        nodes=_failed_counts(expected.nodes),
        edges=_failed_counts(expected.edges),
        calls=_failed_counts(expected.calls),
        method_attributes=_failed_counts(expected.method_attributes),
        symbols=_failed_counts(expected.symbols),
        strings=_failed_counts(expected.strings),
        booleans=_failed_counts(expected.booleans),
    )


CaseId = Annotated[StrictStr, Field(pattern=r"^NCT[0-9]{8}_[0-9]+$")]
TrialId = Annotated[StrictStr, Field(pattern=r"^NCT[0-9]{8}$")]
SplitName = Literal["development", "test"]
SplitSelection = Literal["development", "test", "all"]


class LlfGenerationCase(_SemanticModel):
    """Internal dispatch identity; convert to LlfModelInput before provider use."""

    case_id: CaseId
    trial_id: TrialId
    split: SplitName
    polarity: Literal["inclusion", "exclusion"]
    source_text: Annotated[StrictStr, Field(min_length=1, max_length=10_000)]
    source_sha256: Sha256

    @model_validator(mode="after")
    def source_hash_matches_text(self) -> LlfGenerationCase:
        if self.source_sha256 != _sha256_bytes(self.source_text.encode("utf-8")):
            raise ValueError("source_sha256 does not match source_text")
        return self


class LlfModelInput(_SemanticModel):
    """The complete provider-visible LLF request: text and polarity only."""

    polarity: Literal["inclusion", "exclusion"]
    source_text: Annotated[StrictStr, Field(min_length=1, max_length=10_000)]


def llf_model_input(case: LlfGenerationCase) -> LlfModelInput:
    """Remove all internal identity and split metadata at the provider boundary."""

    return LlfModelInput(polarity=case.polarity, source_text=case.source_text)


class LlfScoringReference(_SemanticModel):
    """Offline-only reference; never pass this object to a generation backend."""

    case_id: CaseId
    trial_id: TrialId
    split: SplitName
    source_sha256: Sha256
    reference_sha256: Sha256
    reference: LlfSemanticReference

    @model_validator(mode="after")
    def reference_hash_matches_tree_lineage(self) -> LlfScoringReference:
        if self.reference_sha256 != self.reference.source_sha256:
            raise ValueError("reference_sha256 does not match semantic source lineage")
        return self


class LlfReferenceCorpus(_SemanticModel):
    """Sealed primary LLF references with separate operational/semantic counts."""

    schema_version: Literal["llf-reference-corpus-v1"] = REFERENCE_CORPUS_SCHEMA_VERSION
    dataset_id: Literal["leaf-logical-forms"] = "leaf-logical-forms"
    dataset_version: Literal["llf-461288a"] = "llf-461288a"
    split: SplitName
    reference_artifact_sha256: Sha256
    coverage_sha256: Sha256
    operational_case_count: Annotated[StrictInt, Field(gt=0)]
    semantic_case_count: Annotated[StrictInt, Field(ge=0)]
    missing_upstream_case_ids: Annotated[
        tuple[CaseId, ...],
        Field(max_length=MAX_COLLECTION_ITEMS),
    ]
    references: Annotated[
        tuple[LlfScoringReference, ...],
        Field(max_length=2_000),
    ]

    @model_validator(mode="after")
    def denominators_and_identities_are_consistent(self) -> LlfReferenceCorpus:
        if self.semantic_case_count != len(self.references):
            raise ValueError("semantic_case_count must equal loaded reference count")
        if self.operational_case_count != (
            self.semantic_case_count + len(self.missing_upstream_case_ids)
        ):
            raise ValueError("operational count must include missing upstream references")
        reference_ids = [reference.case_id for reference in self.references]
        if len(reference_ids) != len(set(reference_ids)):
            raise ValueError("semantic reference case IDs must be unique")
        if set(reference_ids).intersection(self.missing_upstream_case_ids):
            raise ValueError("missing upstream cases cannot have semantic references")
        return self


def _sealed_json_object(raw: bytes, *, label: str) -> dict[str, object]:
    try:
        document = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"{label} is invalid JSON") from exc
    if not isinstance(document, dict) or any(not isinstance(key, str) for key in document):
        raise ValueError(f"{label} must contain one JSON object")
    payload = dict(cast(dict[str, object], document))
    seal = payload.pop("canonical_payload_sha256", None)
    if not isinstance(seal, str) or seal != _sha256_bytes(_canonical_json_bytes(payload)):
        raise ValueError(f"{label} canonical seal does not match")
    return cast(dict[str, object], document)


def _verified_generation_snapshot(generation_path: Path) -> bytes:
    if generation_path.name != "generation_cases.jsonl":
        raise ValueError("LLF generation input must be generation_cases.jsonl")
    manifest_path = generation_path.with_name("generation_manifest.json")
    manifest_bytes = manifest_path.read_bytes()
    manifest_sha256 = _sha256_bytes(manifest_bytes)
    if manifest_sha256 != PINNED_LLF_GENERATION_MANIFEST_SHA256:
        raise ValueError("LLF generation manifest does not match the frozen pin")
    document = _sealed_json_object(manifest_bytes, label="LLF generation manifest")
    if (
        document.get("schema_version") != "llf-generation-manifest-v1"
        or document.get("dataset_id") != "leaf-logical-forms"
        or document.get("dataset_version") != "llf-461288a"
    ):
        raise ValueError("LLF generation manifest identity does not match")

    generation_bytes = generation_path.read_bytes()
    generation_sha256 = _sha256_bytes(generation_bytes)
    if generation_sha256 != PINNED_LLF_GENERATION_CASES_SHA256:
        raise ValueError("LLF generation artifact does not match the frozen pin")
    artifacts = document.get("artifacts")
    if not isinstance(artifacts, list):
        raise ValueError("LLF generation manifest is missing artifact bindings")
    expected_binding = {
        "path": generation_path.name,
        "bytes": len(generation_bytes),
        "record_count": 2_000,
        "sha256": generation_sha256,
    }
    if expected_binding not in artifacts:
        raise ValueError("LLF generation manifest does not bind generation_cases.jsonl")
    return generation_bytes


def load_llf_generation_cases(
    generation_path: Path,
    *,
    split: SplitSelection,
) -> tuple[LlfGenerationCase, ...]:
    """Load one split from the physical source-only generation artifact."""

    generation_bytes = _verified_generation_snapshot(generation_path)
    records = load_llf_generation_records_bytes(
        generation_bytes,
        source_name=str(generation_path),
    )
    return tuple(
        LlfGenerationCase(
            case_id=record.case_id,
            trial_id=record.trial_id,
            split=record.split,
            polarity=record.polarity,
            source_text=record.source_text,
            source_sha256=record.source_sha256,
        )
        for record in records
        if split == "all" or record.split == split
    )


def _load_split_coverage(
    reference_path: Path,
    reference_bytes: bytes,
    coverage_path: Path,
    *,
    split: SplitName,
) -> tuple[dict[str, object], dict[str, object], str]:
    coverage_bytes = coverage_path.read_bytes()
    coverage_sha256 = _sha256_bytes(coverage_bytes)
    if coverage_sha256 != PINNED_LLF_SPLIT_COVERAGE_SHA256[split]:
        raise ValueError("LLF split coverage does not match the frozen pin")
    document = _sealed_json_object(coverage_bytes, label="LLF split coverage")
    if (
        document.get("schema_version") != SPLIT_COVERAGE_SCHEMA_VERSION
        or document.get("split") != split
        or document.get("dataset_id") != "leaf-logical-forms"
        or document.get("dataset_version") != "llf-461288a"
    ):
        raise ValueError("LLF split coverage identity does not match")

    reference_input = document.get("input")
    primary = document.get("coverage")
    vocabulary = document.get("vocabulary")
    if (
        not isinstance(reference_input, dict)
        or not isinstance(primary, dict)
        or not isinstance(vocabulary, dict)
    ):
        raise ValueError("LLF split coverage is missing its input or coverage")
    expected_input = {
        "path": reference_path.name,
        "bytes": len(reference_bytes),
        "sha256": _sha256_bytes(reference_bytes),
    }
    if reference_input != expected_input:
        raise ValueError("LLF split coverage does not bind the supplied references")
    return (
        cast(dict[str, object], primary),
        cast(dict[str, object], vocabulary),
        coverage_sha256,
    )


def load_llf_scoring_references(
    reference_path: Path,
    coverage_path: Path,
    *,
    split: SplitName,
) -> LlfReferenceCorpus:
    """Load one physically isolated, sealed primary split for offline scoring only."""

    expected_name = f"{split}_references.jsonl"
    if reference_path.name != expected_name:
        raise ValueError(f"LLF {split} scoring requires {expected_name}")
    reference_bytes = reference_path.read_bytes()
    reference_sha256 = _sha256_bytes(reference_bytes)
    if reference_sha256 != PINNED_LLF_SPLIT_REFERENCE_SHA256[split]:
        raise ValueError("LLF split reference artifact does not match the frozen pin")
    expected_primary, expected_vocabulary, coverage_sha256 = _load_split_coverage(
        reference_path,
        reference_bytes,
        coverage_path,
        split=split,
    )
    records = load_llf_records_bytes(reference_bytes, source_name=str(reference_path))
    if any(record.split != split for record in records):
        raise ValueError("LLF split reference artifact contains another split")
    references: list[LlfScoringReference] = []
    missing: list[str] = []
    accumulator = _CoverageAccumulator()
    computed_primary, parsed_by_source = _semantic_track_report_and_references(
        records,
        accumulator=accumulator,
    )
    if computed_primary != expected_primary or accumulator.report() != expected_vocabulary:
        raise ValueError("LLF split semantic coverage does not reproduce")
    for record in records:
        if record.logical_form is None:
            missing.append(record.case_id)
            continue
        parsed = parsed_by_source.get(record.source_path)
        if parsed is None:
            raise ValueError("available LLF primary reference did not parse")
        if record.reference_sha256 is None:
            raise ValueError("available LLF reference is missing its source hash")
        references.append(
            LlfScoringReference(
                case_id=record.case_id,
                trial_id=record.trial_id,
                split=record.split,
                source_sha256=record.raw_text_sha256,
                reference_sha256=record.reference_sha256,
                reference=parsed,
            )
        )

    expected_operational, expected_semantic, expected_missing = _EXPECTED_SPLIT_COUNTS[split]
    if (
        len(records),
        len(references),
        len(missing),
    ) != (expected_operational, expected_semantic, expected_missing):
        raise ValueError("LLF split counts do not match the frozen Real v1 protocol")

    return LlfReferenceCorpus(
        split=split,
        reference_artifact_sha256=reference_sha256,
        coverage_sha256=coverage_sha256,
        operational_case_count=len(records),
        semantic_case_count=len(references),
        missing_upstream_case_ids=tuple(sorted(missing)),
        references=tuple(references),
    )


def _ratio(numerator: int, denominator: int) -> float:
    return numerator / denominator if denominator else 0.0


class _CoverageAccumulator:
    def __init__(self) -> None:
        self.node_kinds: Counter[str] = Counter()
        self.direct_call_names: set[str] = set()
        self.method_call_names: set[str] = set()
        self.indirect_call_callee_kinds: Counter[str] = Counter()
        self.attribute_names: set[str] = set()
        self.symbol_names: set[str] = set()
        self.bare_symbol_names: set[str] = set()
        self.boolean_operators: set[str] = set()
        self.total_nodes = 0
        self.max_nodes_per_reference = 0
        self.max_depth = 0
        self.max_call_arguments = 0
        self.max_collection_items = 0
        self.max_identifier_length = 0
        self.max_string_bytes = 0

    def add(self, reference: LlfSemanticReference) -> None:
        by_id = {node.node_id: node for node in reference.nodes}
        callee_ids = {
            node.callee_node_id for node in reference.nodes if isinstance(node, LlfCallNode)
        }
        depths: dict[str, int] = {}
        for node in reference.nodes:
            children = _child_node_ids(node)
            depths[node.node_id] = 1 + max((depths[item] for item in children), default=0)
            self.node_kinds[node.kind] += 1
            if isinstance(node, LlfSymbolNode):
                self.symbol_names.add(node.name)
                self.max_identifier_length = max(self.max_identifier_length, len(node.name))
                if node.node_id not in callee_ids:
                    self.bare_symbol_names.add(node.name)
            elif isinstance(node, LlfStringNode):
                self.max_string_bytes = max(
                    self.max_string_bytes,
                    len(node.value.encode("utf-8")),
                )
            elif isinstance(node, LlfAttributeNode):
                self.attribute_names.add(node.attribute)
                self.max_identifier_length = max(
                    self.max_identifier_length,
                    len(node.attribute),
                )
            elif isinstance(node, LlfCallNode):
                callee = by_id[node.callee_node_id]
                if isinstance(callee, LlfSymbolNode):
                    self.direct_call_names.add(callee.name)
                elif isinstance(callee, LlfAttributeNode):
                    self.method_call_names.add(callee.attribute)
                else:
                    self.indirect_call_callee_kinds[callee.kind] += 1
                self.max_call_arguments = max(
                    self.max_call_arguments,
                    len(node.argument_node_ids),
                )
            elif isinstance(node, LlfTupleNode):
                self.max_collection_items = max(
                    self.max_collection_items,
                    len(node.item_node_ids),
                )
            elif isinstance(node, LlfBooleanOperationNode):
                self.boolean_operators.add(node.operator)
                self.max_collection_items = max(
                    self.max_collection_items,
                    len(node.operand_node_ids),
                )
        self.total_nodes += len(reference.nodes)
        self.max_nodes_per_reference = max(self.max_nodes_per_reference, len(reference.nodes))
        self.max_depth = max(self.max_depth, depths[reference.root_node_id])

    def report(self) -> dict[str, object]:
        return {
            "node_kind_counts": dict(sorted(self.node_kinds.items())),
            "total_semantic_nodes": self.total_nodes,
            "direct_call_names": sorted(self.direct_call_names),
            "method_call_names": sorted(self.method_call_names),
            "indirect_call_callee_kind_counts": dict(
                sorted(self.indirect_call_callee_kinds.items())
            ),
            "attribute_names": sorted(self.attribute_names),
            "symbol_names": sorted(self.symbol_names),
            "bare_symbol_names": sorted(self.bare_symbol_names),
            "boolean_operators": sorted(self.boolean_operators),
            "observed_bounds": {
                "maximum_nodes_per_reference": self.max_nodes_per_reference,
                "maximum_semantic_depth": self.max_depth,
                "maximum_call_arguments": self.max_call_arguments,
                "maximum_collection_items": self.max_collection_items,
                "maximum_identifier_characters": self.max_identifier_length,
                "maximum_string_bytes": self.max_string_bytes,
            },
        }


def _semantic_track_report_and_references(
    records: Iterable[LlfAnnotation],
    *,
    accumulator: _CoverageAccumulator,
) -> tuple[dict[str, object], dict[str, LlfSemanticReference]]:
    ordered = sorted(
        records,
        key=lambda record: (record.case_id, record.annotator_id, record.source_path),
    )
    parsed_rows: list[dict[str, object]] = []
    missing_rows: list[dict[str, object]] = []
    malformed_rows: list[dict[str, object]] = []
    parsed_by_source: dict[str, LlfSemanticReference] = {}
    available = 0
    for record in ordered:
        identity: dict[str, object] = {
            "case_id": record.case_id,
            "annotator_id": record.annotator_id,
            "source_path": record.source_path,
        }
        if record.logical_form is None:
            missing_rows.append(identity)
            continue
        available += 1
        try:
            semantic = parse_llf_semantic(
                record.logical_form,
                source_name=record.source_path,
            )
        except LlfSemanticParseError as exc:
            malformed_rows.append(
                {
                    **identity,
                    "reference_sha256": record.reference_sha256,
                    "error_code": exc.code,
                }
            )
            continue
        if semantic.source_sha256 != record.reference_sha256:
            raise ValueError(f"reference hash mismatch for {record.source_path}")
        parsed_by_source[record.source_path] = semantic
        accumulator.add(semantic)
        parsed_rows.append(
            {
                **identity,
                "reference_sha256": record.reference_sha256,
                "semantic_tree_sha256": semantic_tree_sha256(semantic),
                "canonical_reference_sha256": canonical_llf_sha256(semantic),
                "node_count": len(semantic.nodes),
            }
        )

    parsed_payload = b"".join(_canonical_json_bytes(row) for row in parsed_rows)
    total = len(ordered)
    parsed = len(parsed_rows)
    report: dict[str, object] = {
        "total_rows": total,
        "available_references": available,
        "parsed_references": parsed,
        "missing_upstream_references": len(missing_rows),
        "malformed_references": len(malformed_rows),
        "available_parse_rate": parsed / available if available else None,
        "all_rows_operational_rate": parsed / total if total else None,
        "parsed_semantics_sha256": _sha256_bytes(parsed_payload),
        "missing": missing_rows,
        "malformed": malformed_rows,
    }
    return report, parsed_by_source


def _semantic_track_report(
    records: Iterable[LlfAnnotation],
    *,
    accumulator: _CoverageAccumulator,
) -> dict[str, object]:
    report, _parsed = _semantic_track_report_and_references(
        records,
        accumulator=accumulator,
    )
    return report


def build_semantic_coverage_report(
    records_path: Path,
    agreement_path: Path,
) -> dict[str, object]:
    """Audit both committed LLF tracks and return a sealed deterministic report."""

    records_bytes = records_path.read_bytes()
    agreement_bytes = agreement_path.read_bytes()
    primary = load_llf_records_bytes(records_bytes, source_name=str(records_path))
    agreement = load_llf_records_bytes(
        agreement_bytes,
        source_name=str(agreement_path),
    )
    accumulator = _CoverageAccumulator()
    primary_report = _semantic_track_report(primary, accumulator=accumulator)
    agreement_report = _semantic_track_report(agreement, accumulator=accumulator)
    report: dict[str, object] = {
        "schema_version": COVERAGE_SCHEMA_VERSION,
        "dataset_id": "leaf-logical-forms",
        "dataset_version": "llf-461288a",
        "parser": {
            "version": PARSER_VERSION,
            "execution": "never_compile_eval_exec_or_import",
            "representation": "flat_lossless_syntax_tree_not_graph_v2",
            "keyword_normalization": ["and", "or", ".for", ".except"],
            "limits": {
                "source_bytes": MAX_LLF_SOURCE_BYTES,
                "semantic_nodes": MAX_SEMANTIC_NODES,
                "semantic_depth": MAX_SEMANTIC_DEPTH,
                "call_arguments": MAX_CALL_ARGUMENTS,
                "collection_items": MAX_COLLECTION_ITEMS,
                "identifier_characters": MAX_IDENTIFIER_LENGTH,
                "string_bytes": MAX_STRING_BYTES,
            },
        },
        "inputs": {
            "records": {
                "path": records_path.name,
                "bytes": len(records_bytes),
                "sha256": _sha256_bytes(records_bytes),
            },
            "agreement_annotations": {
                "path": agreement_path.name,
                "bytes": len(agreement_bytes),
                "sha256": _sha256_bytes(agreement_bytes),
            },
        },
        "coverage": {
            "primary": primary_report,
            "agreement": agreement_report,
        },
        "vocabulary": accumulator.report(),
    }
    report["canonical_payload_sha256"] = _sha256_bytes(_canonical_json_bytes(report))
    return report


def semantic_coverage_report_bytes(
    records_path: Path,
    agreement_path: Path,
) -> bytes:
    """Serialize the deterministic coverage audit for publication."""

    return _canonical_json_bytes(
        build_semantic_coverage_report(records_path, agreement_path),
        pretty=True,
    )


def build_split_semantic_coverage_report(
    reference_path: Path,
    *,
    split: SplitName,
) -> dict[str, object]:
    """Build a sealed coverage manifest from exactly one physical scoring split."""

    reference_bytes = reference_path.read_bytes()
    records = load_llf_records_bytes(reference_bytes, source_name=str(reference_path))
    if any(record.split != split for record in records):
        raise ValueError("split coverage input contains records from another split")
    expected_operational, _expected_semantic, _expected_missing = _EXPECTED_SPLIT_COUNTS[split]
    if len(records) != expected_operational:
        raise ValueError("split coverage input count does not match the frozen protocol")
    accumulator = _CoverageAccumulator()
    coverage = _semantic_track_report(records, accumulator=accumulator)
    report: dict[str, object] = {
        "schema_version": SPLIT_COVERAGE_SCHEMA_VERSION,
        "dataset_id": "leaf-logical-forms",
        "dataset_version": "llf-461288a",
        "split": split,
        "parser": {
            "version": PARSER_VERSION,
            "execution": "never_compile_eval_exec_or_import",
            "representation": "flat_lossless_syntax_tree_not_graph_v2",
            "keyword_normalization": ["and", "or", ".for", ".except"],
            "limits": {
                "source_bytes": MAX_LLF_SOURCE_BYTES,
                "semantic_nodes": MAX_SEMANTIC_NODES,
                "semantic_depth": MAX_SEMANTIC_DEPTH,
                "call_arguments": MAX_CALL_ARGUMENTS,
                "collection_items": MAX_COLLECTION_ITEMS,
                "identifier_characters": MAX_IDENTIFIER_LENGTH,
                "string_bytes": MAX_STRING_BYTES,
            },
        },
        "input": {
            "path": reference_path.name,
            "bytes": len(reference_bytes),
            "sha256": _sha256_bytes(reference_bytes),
        },
        "coverage": coverage,
        "vocabulary": accumulator.report(),
    }
    report["canonical_payload_sha256"] = _sha256_bytes(_canonical_json_bytes(report))
    return report


def split_semantic_coverage_report_bytes(
    reference_path: Path,
    *,
    split: SplitName,
) -> bytes:
    """Serialize one physically isolated split coverage manifest."""

    return _canonical_json_bytes(
        build_split_semantic_coverage_report(reference_path, split=split),
        pretty=True,
    )


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--records", type=Path)
    parser.add_argument("--agreement", type=Path)
    parser.add_argument("--split-reference", type=Path)
    parser.add_argument("--split", choices=("development", "test"))
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    if args.split_reference is not None:
        if args.split is None or args.records is not None or args.agreement is not None:
            parser.error("--split-reference requires --split and excludes full-corpus inputs")
        payload = split_semantic_coverage_report_bytes(
            args.split_reference,
            split=cast(SplitName, args.split),
        )
    else:
        if args.records is None or args.agreement is None or args.split is not None:
            parser.error("full coverage requires --records and --agreement")
        payload = semantic_coverage_report_bytes(args.records, args.agreement)
    sys.stdout.buffer.write(payload)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
