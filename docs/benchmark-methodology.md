# Benchmark methodology

CriteriaBench Real v1 evaluates whether a system can reproduce human-authored Leaf Logical Forms (LLF) from real clinical-trial eligibility criteria. This page explains the implemented data, parser, baselines, agreement analysis, metrics, and interpretation rules. Two sealed Luna configurations from the public runner and one separately sealed local Terra diagnostic completed on the same 25-case subset; their raw responses, predictions, and scored reports are intentionally not published.

## Evaluation unit

One case is one annotated inclusion or exclusion criterion associated with an NCT trial. The primary corpus contains 2,000 cases from 885 trials. The split is grouped by complete trial so that related criteria from one study cannot appear in both development and test:

- development: 200 cases from 86 trials;
- locked test: 1,800 cases from 799 trials; and
- agreement context: three annotations for each of 20 selected cases, kept in development.

The primary corpus has 1,997 available references. Three absent upstream logical-form bodies remain explicit operational cases. They are not fabricated, silently removed, or included in semantic denominators.

## Deterministic import and provenance

The LLF source is pinned to `uw-bionlp/leaf-corpora` commit `461288aeba8b37fabd43bd7c55f0e1cb1bb10b9e`. The importer records the repository tree/inventory, every source path and SHA-256, the split algorithm/seed, counts, and generated artifact hashes.

Each upstream file is treated as untrusted text. Import decodes only three bounded JavaScript string-literal assignments needed for criterion text, augmented text, and logical form. It does not run JavaScript.

The generated artifacts have distinct responsibilities:

| Artifact | Contains references? | Intended reader |
|---|---:|---|
| `generation_cases.jsonl` | No | Baseline/model generation |
| `generation_manifest.json` | No reference identities, availability, denominators, or hashes | Generation integrity checks |
| `split_assignments.json` | No | Both planes |
| `development_references.jsonl` | Development only | Development scorer/baseline |
| `test_references.jsonl` | Locked test only | Locked offline scorer |
| `agreement_annotations.jsonl` | Agreement subset | Human-agreement analysis |
| `records.jsonl` | All primary source/reference records | Mechanical audit only |

This physical separation is stronger than asking model code not to inspect a field in a combined object. The paid container never mounts either reference file.

## Safe LLF semantic representation

LLF annotations resemble chained Python expressions, but CriteriaBench never treats them as executable programs. The parser:

1. validates bounded UTF-8 input;
2. applies documented keyword normalization needed for LLF method names;
3. asks Python's parser only for an inert expression AST;
4. accepts an explicit allowlist of names, string/Boolean literals, attributes, calls without keyword arguments, tuples, and Boolean `and`/`or`;
5. rejects every other syntax node; and
6. serializes the result to a strict, flat, canonical postorder node table.

It never compiles, evaluates, executes, imports, or invokes the parsed expression. Bounds cover source bytes, decoded string bytes, semantic nodes, depth, identifier length, call arguments, and collection size. The output validator also requires one connected acyclic tree, canonical contiguous node IDs, backward-only child references, and no shared child nodes.

All 1,997 available primary references parse under this contract. Of 60 agreement annotations, 57 parse; the three malformed annotations and six unavailable pairs are disclosed.

## Canonicalization

Canonicalization is intentionally narrow. These direct call forms are treated as commutative:

- `intersect(...)`;
- `union(...)`;
- `and(...)`; and
- `or(...)`.

Infix Boolean `and` and `or` are also commutative. Their child payloads are sorted canonically for scoring. Method calls, `seq`, tuples, and all other argument lists preserve order. An undocumented method form named `union`, for example, remains ordered.

Exact match compares the complete canonical scoring payload. It is stricter than matching a bag of labels and insensitive only to the declared commutative reorderings.

## Direct model boundary

For the LLF paid lane, the strict provider response schema is deliberately small:

```json
{"logical_form":"one bounded LLF expression"}
```

Local trusted code then applies the stricter length constraints and safe parser. This avoids asking the provider to generate internal node IDs or provenance fields and avoids depending on unsupported remote JSON-Schema keywords.

The model input contains only criterion text and inclusion/exclusion kind. Case ID, trial ID, source hash, reference, score, and neighbouring annotations are excluded. The frozen prompt includes five development-only examples and the development-derived LLF vocabulary. No tool use or web retrieval is enabled.

## Deterministic BM25 comparator

`llf-bm25-nearest-development-v1` is a retrieval baseline, not a language model. It asks whether lexical similarity alone can recover a useful logical form.

The frozen algorithm uses:

- Unicode NFKC normalization and casefolding;
- tokens made from letters, numbers, and attached combining marks;
- Okapi BM25 with `k1=1.2`, `b=0.75`, binary query-term frequency, and the documented smoothed IDF;
- exact inclusion/exclusion polarity matching;
- ascending development case ID for deterministic ties;
- all 200 development references as the training set for test targets; and
- leave-the-entire-target-trial-out training for development targets.

The implementation accepts already loaded typed objects and has no file, environment, provider, or network entry point.

### Development evidence

On all 200 development cases, the leave-trial-out baseline returns a valid prediction for every case:

| Metric | Value |
|---|---:|
| Exact canonical tree | 8 / 200 |
| Node F1 | 0.369699 |
| Edge F1 | 0.213939 |
| Combined node-plus-edge F1 | 0.293919 |

On the frozen 25-case canary subset, its exact count is 1/25 and combined node-plus-edge F1 is `0.202918` (153 TP, 624 FP, 578 FN). These are development results used to set a meaningful go/no-go threshold, not test performance.

## Human-agreement context

The agreement analysis forms all three unordered annotator pairs within each of 20 cases. Pair orientation is deterministic. Metrics are calculated in both directions, averaged within pair, then averaged within case; the headline gives every case equal weight.

Pairs touching a malformed annotation are unavailable rather than assigned zero. Consequently:

- 60 annotations are present;
- 57 annotations parse;
- 54 of 60 possible pairs are available;
- 17 cases are fully parseable and three partially parseable; and
- 7 cases have full three-annotation exact consensus.

The case-macro results are:

| Metric | Value |
|---|---:|
| Canonical exact agreement | 0.466667 |
| Node F1 | 0.879308 |
| Edge F1 | 0.788692 |
| Typed-component F1 | 0.880009 |

These values describe consistency on a selected subset. They do not prove annotation correctness, form a formal model ceiling, or substitute for biomedical adjudication.

## Semantic metrics

For each prediction/reference pair, the evaluator extracts multisets of local structural signatures.

### Nodes

A node signature records its local kind and relevant local value—for example symbol name, string value, Boolean value, attribute name, call shape, tuple arity, or Boolean operator/arity. Duplicate signatures are counted, not collapsed.

### Edges

An edge signature records the local parent and child signatures plus the relationship role: attribute target, call callee, call argument, tuple item, or Boolean operand. Position is retained for ordered structures and removed only for declared commutative structures.

### Typed components

Separate multisets measure calls, method attributes, symbols, strings, and Booleans. Their counts are also combined into a typed-component metric.

### Aggregation

For any multiset, true positives are the summed minimum signature multiplicities. Remaining predicted multiplicity is false positive; remaining reference multiplicity is false negative.

The primary score sums node and edge TP/FP/FN across all scorable cases and computes one micro F1:

```text
precision = TP / (TP + FP)
recall    = TP / (TP + FN)
F1        = 2TP / (2TP + FP + FN)
```

Exact canonical-tree accuracy, node/edge metrics, and every typed component are mandatory secondary results. Scores are rounded for reporting only after integer counts are fixed.

An operational failure with an available reference is represented as an empty prediction: zero true positives, zero false positives, and every reference component as a false negative. This keeps failures in the denominator. A missing upstream reference remains in operational completion figures but has no defensible semantic target.

## Live operational metrics

The semantic report is accepted only after the offline scorer validates the sealed plan, preregistration, execution binding, authorization, external claim, local authorization consumption, request/attempt/outcome chain, and terminal summary. It rejects missing, extra, duplicate, symlinked, nonregular, inconsistent, or path-mismatched artifacts.

The report includes:

- attempted, completed, failed, unattempted, and fatal-abort counts;
- schema/output-contract status;
- per-category token usage and unknown-usage coverage;
- authorization consumption and usage-priced cost under the frozen rates;
- p50/p95 observed latency;
- response-ID coverage and uniqueness; and
- returned provider model, response object, and service-tier coverage.

Request IDs and returned identity hashes are recorded provenance. They are not cryptographic proof of which weights executed. Provider dashboard reconciliation is separate.

## Canary decision

The 25-case development canary is selected deterministically across 25 trials, inclusion/exclusion polarity, and source-length tertiles, with prompt-example trials excluded. Its exact case set and BM25 predictions are sealed before a paid call.

The measurement gates are conjunctive: 25/25 clean completion and provenance, no retry, known usage/latency, p95 at most 60 seconds, consumption at most the exact sealed profile cap, at least two exact trees, and combined structural F1 at least `0.50` and at least `0.10` above BM25. Advancement also requires the locked-compatible `none` profile. The versioned `medium` experiment is a paired development diagnostic and fails that compatibility gate by design; there is no discretionary pass.

A failed quality gate requires a new versioned configuration and preregistration. A genuine operational rerun requires a new public execution binding, fresh authorization, and full disclosure. The procedure prohibits repeated sampling of the same preregistration followed by selective publication.

## Statistical and validity boundaries

No confidence interval or repeated-run variance is claimed for the one-shot canary. It is a readiness gate with a small development sample. The locked public test, if separately authorized, would be a larger public-benchmark estimate but still may be contaminated by model pretraining.

The correct claim is “performance on a pinned public LLF benchmark with no runtime gold leakage.” Do not call it contamination-resistant generalization. A fresh temporal holdout with independent annotation and adjudication is future work.

Structural LLF agreement also does not establish:

- clinical correctness;
- patient-trial eligibility accuracy;
- safe handling of protected health information;
- transfer to the separate GraphV2 contract;
- API production readiness; or
- model superiority before the preregistered result exists.

## Legacy deterministic regressions

The one-case synthetic gold smoke and 80-case Synthetic v0.1/v0.1.1 suite remain useful for testing the older API/extractor/evaluator/report plumbing at zero cost. The latter includes deterministic empty/rules baselines, exact criterion-text diagnostics, a 10-family-cluster sensitivity, and byte-stable JSON/Markdown reports.

Those constructed, AI-assisted labels are not part of Real v1 and are not model-quality evidence. Their older criterion-text and field-accuracy metrics are a different evaluation contract and must not be compared numerically with LLF node/edge scores.

The legacy `prediction-bundle-v1` importer remains offline-only. CI may hash-check and replay a reviewed committed bundle, but CI must never generate model predictions or receive paid credentials. If provider usage is missing, imported usage-priced totals are reported as lower bounds and are not proof of provider billing. The synthetic suite is not research-grade, has no independent second-human adjudication, and is not clinical validation. Its overlapping taxonomy categories and known segmentation/grouping errors must not be generalized into evidence of general reasoning ability.

- [Current legacy Synthetic v0.1.1 report](results/synthetic-v0.1.1.md)
- [Historical immutable Synthetic v0.1 report](results/synthetic-v0.1.md)

## Reproduction

The focused zero-network verification is:

```powershell
.\.tools\uv\uv.exe run --frozen --no-env-file pytest `
  tests/test_llf_import.py `
  tests/test_llf_semantics.py `
  tests/test_llf_semantic_evaluation.py `
  tests/test_llf_baselines.py `
  tests/test_llf_agreement.py `
  tests/test_llf_canary_preregistration.py `
  tests/test_llf_live_score.py
```

The live process is intentionally separate. See [Real-v1 protocol](real-v1-protocol.md) and [Operations runbook](operations-runbook.md).
