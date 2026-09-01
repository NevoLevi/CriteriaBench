# Benchmark methodology

CriteriaBench has two deliberately separate current evaluation paths and a third, offline artifact boundary for future model comparisons:

1. a one-case engineering smoke that verifies guarded extraction, deterministic scoring, and provenance/cost artifact plumbing; and
2. the unchanged 80-case Synthetic v0.1 dataset analyzed by offline-suite-v0.1.1, which verifies dataset, evaluator, baseline, slice/family analysis, and report regressions without network or model calls.

The separate `prediction-bundle-v1` importer can validate and score a future canonical prediction artifact, but cannot generate predictions or call a provider.

None of these paths establishes clinical accuracy or a research-grade benchmark. The offline suite compares two deterministic software baselines, not language models. The approved one-case Luna smokes remain separate engineering evidence and are not included in Synthetic v0.1.

## Implemented extraction contract

Each `ClinicalTrialEligibility` object contains:

- `schema_version` and `trial_id`;
- inclusion and exclusion criterion lists;
- per criterion: stable ID, kind/category, concept, operator, value, unit, negation, temporal constraint, logic group, and exact evidence span/quote; and
- an ambiguities list.

Semantic validation includes operator/value compatibility, between/list/existence forms, temporal duration/reference rules, single versus AND/OR group cardinality, and evidence quote equality. Extractor/provider prediction evidence offsets and quotes are cross-checked against the exact eligibility text before output is accepted.

Synthetic v0.1 additionally verifies every reference quote and offset against its source text during suite loading. The manually authored reference in the older one-case smoke remains typed but is not source-cross-checked by that CLI.

The representation is a flat set of grouped predicates with parent links, not a general Boolean abstract-syntax tree. It does not contain registry demographics, a queryability score, or a generated database query.

## Normalization and alignment

Exact criterion-text comparison uses `(criterion kind, normalize(normalized_text))` as its multiset key. Normalization applies Unicode-aware lowercasing, whitespace collapse, and punctuation/token handling implemented in the evaluator. A multiset counter preserves duplicate cardinality.

An exact criterion-text match says that criterion kind and normalized criterion text agree. It does not imply equality of category, concept, operator, value, unit, negation, temporal relation, logic connector, evidence, group topology, ambiguities, or the complete structured object.

For token and field scoring:

1. Only reference/prediction criteria of the same kind are candidates.
2. Candidate weight is token F1 over normalized text.
3. Weights below 0.25 are excluded.
4. Deterministic Hungarian assignment finds a maximum-weight one-to-one matching.
5. Unmatched references and extra predictions contribute zero to token and field scores.

Exact multiset intersection makes missing references lower recall and extra predictions lower precision. Token and each field score sum aligned-pair values and divide by `max(prediction_count, reference_count)`, so unmatched references and extra predictions each contribute zero. This prevents one prediction from being reused for multiple references and avoids giving structured-field credit to unrelated zero-overlap text.

## Implemented metrics

### Criterion-level

- exact criterion-text precision;
- exact criterion-text recall;
- exact criterion-text F1; and
- token-overlap score summed across aligned pairs and divided by `max(prediction_count, reference_count)`.

Both valid empty extraction/reference sets score as perfect agreement.

### Structured fields

Accuracy is computed for exactly eight fields:

1. category;
2. concept;
3. operator;
4. value;
5. unit;
6. negation;
7. temporal relation; and
8. logic connector.

The macro field score is the arithmetic mean of those eight accuracies.

Not yet scored separately:

- temporal quantity, unit, raw text, or reference event;
- evidence quote/offset similarity;
- logic group IDs, parent topology, or full Boolean equivalence; and
- ambiguity quality/completeness.

Source-bound evidence validation is a structural validity check, not an evidence-similarity metric. `schema_valid=true` means a typed object passed the boundary, not that it is medically correct.

## Built-in data and evaluation paths

The repository contains:

- a minimally retained public ClinicalTrials.gov trial fixture/manifest for downloader and input demonstrations;
- one synthetic trial with a manually specified gold extraction for the original frozen smoke benchmark; and
- Synthetic v0.1, exactly 80 constructed reference cases across 10 families with 8 controlled variants per family.

The one-case smoke is sufficient to detect guarded-pipeline and artifact regressions, not to estimate generalization. Its CLI does not exercise Redis, the worker, or PostgreSQL; those paths have separate API/Compose/kind integration evidence.

### Synthetic v0.1 design

Synthetic v0.1 was created through AI-assisted template design and label construction, then encoded in a deterministic, source-controlled generator. Generating and evaluating the committed fixtures is offline and makes no model or network calls. The content contains no patient records, personal data, network-sourced trial text, or proprietary corpus material.

The manifest records 10 balanced families, eight controlled variants per family, and 20 declared slices. Sixteen cases represent one source bullet as multiple labels with AND or OR grouping.

Each of the 80 fixture bytes is pinned by SHA-256. The loader requires the exact ordered case set, unique trial IDs and hashes, strict schema validity, matching fixture/manifest provenance, sequential criterion IDs, correct inclusion/exclusion kinds, and exact source-bound evidence quotes and offsets.

The frozen manifest uses `single_author` and `deterministic_templates` for one historical authoring workflow; those fields do not mean unaided human authorship. Independent second-human and clinical-domain review/adjudication are still pending. The suite is small, English-only, template-shaped, and balanced by design rather than natural sampling. Its results are engineering regression evidence only: they are not research-grade and do not measure clinical validity, real-registry generalization, inter-annotator agreement, fairness, safety, or production model quality.

See the [dataset card](../data/synthetic_v0_1/README.md) for the complete authoring and future independent-review procedure.

### Offline baselines

The suite accepts only these allowlisted configurations:

- `empty-v1`, which returns a schema-valid empty extraction; and
- `rules-v1`, a deterministic adapter over the repository's mock extraction provider.

Both baselines run sequentially and record `paid=false`, `network=false`, zero input/output tokens, and USD 0 estimated cost. They do not call an LLM. Their comparison checks that the evaluator and deterministic extraction path respond meaningfully to the constructed cases; it is not evidence that one production model outperforms another.

### Aggregation, slices, and error analysis

For each baseline, the report includes:

- completion and schema-valid rates;
- micro exact criterion-text precision/recall/F1 and TP/FP/FN counts;
- mean exact criterion-text F1, token F1, and macro field accuracy;
- separate case-weighted means for all eight structured fields;
- criterion-text-perfect trial rate;
- all-case and nonempty-reference cohorts;
- all 20 declared slices;
- all 10 families and 10 leave-one-family-out subsets;
- case-resampled and family-cluster sensitivity intervals;
- derived family/base-template/variant lineage; and
- deterministic count/denominator/rate/basis entries for missing/spurious criteria and aligned text, field, temporal, logic, and evidence mismatch events.

The error taxonomy reuses the evaluator's deterministic optimal alignment so its pair choices cannot diverge from scoring. Missing-criterion rates use reference criteria, spurious-criterion rates use predicted criteria, and paired mismatch rates use aligned pairs as their denominator.

Taxonomy events can overlap: one aligned pair can contribute text, concept, operator, value, evidence, and other mismatch events simultaneously. Counts therefore cannot be summed as unique cases, criteria, or failures. The same-kind 0.25 token-F1 alignment threshold can also affect which paired events are classified.

The AND/OR families deliberately encode two grouped reference criteria in one source bullet. `rules-v1` emits one unsplit criterion per bullet, producing a known segmentation/grouping mismatch and zero exact criterion-text F1 on those slices. This controlled failure is useful, but it is not general evidence that a system can or cannot reason over arbitrary Boolean logic. The current schema remains a flat grouped-predicate representation, not a general Boolean AST.

### Deterministic resampling sensitivity

Case-resampled mean metrics and rules-minus-empty paired deltas retain the historical percentile view with 10,000 resamples, seed `20260901`, and 95% coverage. This view treats all 80 controlled variants as exchangeable and can understate their shared-template dependence.

The separate 10-family-cluster sensitivity resamples whole families, keeping each family's eight variants together. It is explicitly labeled a sensitivity analysis rather than an unqualified confidence interval.

Leave-one-family-out results show how the aggregate changes when each family is omitted in turn.

Only 10 upper-level clusters exist, and each family is confounded with one root template structure. Neither resampling view estimates clinical, registry, model-run, or broad template-population uncertainty; bootstrap resampling cannot create missing template diversity. All serialized values remain fixed-seed and rounded to six decimals.

### Policy and report regression gate

`benchmarks/synthetic-v0.1-policy.json` freezes the expected dataset/config structure and requires:

- exactly 80 cases, 10 families, all 20 declared slices, and 16 one-bullet/multi-label cases;
- both `empty-v1` and `rules-v1`;
- suite version `offline-suite-v0.1.1`;
- all eight named mean structured-field accuracies;
- per-family and leave-one-family-out results for all 10 families;
- 10-family-cluster sensitivity metadata;
- taxonomy count/denominator/rate/basis fields and overlap disclosure;
- 80 derived lineage entries across 10 base templates and variants 1–8;
- 100% completion and schema validity;
- manifest hash and source-evidence validity through the loader contract;
- `paid=false`, `network=false`, zero tokens, and zero estimated cost for both baselines; and
- rules-v1 mean token F1 and mean macro field accuracy each at least 0.20 above empty-v1.

The primary regression gate is stronger than numeric thresholds: CI regenerates both report formats and requires byte-for-byte equality with the current v0.1.1 [JSON](results/synthetic-v0.1.1.json) and [Markdown report](results/synthetic-v0.1.1.md). This catches drift in the fixtures, analysis contract, ordering, rounding, wording, and output serialization. The historical v0.1 [JSON](results/synthetic-v0.1.json) and [Markdown](results/synthetic-v0.1.md) remain byte-immutable and SHA-256 pinned; corrections are published under a new analysis version rather than silently rewriting old evidence.

Reproduce the suite from a frozen environment:

```powershell
uv run --frozen --no-env-file criteriabench-suite `
  data/synthetic_v0_1/manifest.json `
  --configs empty-v1 rules-v1 `
  --json-output artifacts/synthetic-v0.1.1.json `
  --markdown-output artifacts/synthetic-v0.1.1.md `
  --check-json docs/results/synthetic-v0.1.1.json `
  --check-markdown docs/results/synthetic-v0.1.1.md
```

The CLI rejects environment-style paths and existing output files unless `--overwrite` is explicit.

## One-case smoke artifact

The original CLI writes a unique temporary file and atomically replaces the requested JSON output, refuses an existing path unless `--overwrite` is explicit, and removes temporary output after failure. It does not claim filesystem `fsync` durability.

Its artifact records:

- artifact/schema version and creation time;
- provider and exact model label;
- paid flag and applied price assumptions;
- fixture version, relative source path, and SHA-256;
- extraction contract SHA-256 binding schema/provider implementation/service code;
- evaluation contract SHA-256 binding evaluator implementation;
- top-level maximum permitted attempts per case, batch projected authorization, aggregate counts/scores, and total usage-priced cost; and
- per-case extraction, optional evaluation, latency, token usage, and usage-priced cost.

The current artifact does not report latency percentiles, throughput, queue age, CPU/memory, cached-token pricing, or a raw provider response. Those are future benchmark dimensions.

### Frozen smoke expectations

The canonical mock case is gated in tests/CI with these expected values:

- provider: deterministic mock;
- paid: false;
- evaluated cases: 1;
- exact criterion-text F1: 1.0;
- token F1: 1.0;
- macro field accuracy: 0.875;
- usage-priced cost: USD 0; and
- 64-character hexadecimal extraction/evaluation hashes.

The non-perfect macro score is useful: it shows the smoke measures the implementation rather than hard-coding every metric to one.

## Paid-run controls

Live use is optional and currently accepts only the reviewed `gpt-5.6-luna` model with the repository's exact configured rates. Terra, Sol, arbitrary aliases, alternate endpoints, and zero/stale rates are rejected.

Before the first request, the live CLI:

1. verifies all live flags/provider/key/model/rate constraints;
2. requires an explicit budget no greater than USD 2;
3. verifies each input against the committed manifest;
4. hashes and parses the same bytes;
5. estimates prompt/schema/trial/max-output cost for every permitted attempt and every case; and
6. rejects the complete batch if the projection exceeds the selected budget.

After a provider call starts, the authorization ledger consumes at least the conservative reservation even if the call times out. Usage-priced cost is separately computed from returned token usage and configured rates. Neither value is a provider-side spending cap.

Synthetic v0.1 and offline-suite-v0.1.1 do not use this live path. A future model evaluation requires a separately reviewed protocol and explicit authorization for any network or paid calls.

## Offline prediction-bundle import and scoring

Future model results must cross a separate artifact boundary. A separately reviewed and explicitly authorized workflow may produce a canonical `prediction-bundle-v1` outside CI.

```powershell
python -m criteriabench.predictions `
  --bundle <canonical.json> `
  --manifest data/synthetic_v0_1/manifest.json `
  --check <canonical.json.sha256> `
  --output <new-score-report.json>
```

The importer validates the bundle against the hash-pinned manifest and cases, then emits `prediction-score-v1`. It imports no settings, provider, HTTP client, or network path and exposes no generation/live mode. It independently recomputes observed usage-priced token costs from hash-bound run rates; if any call's usage is unavailable, the score reports coverage and marks observed monetary totals as lower bounds, not proof of provider billing.

CI may hash-check and replay a reviewed committed bundle. It must never generate model predictions, accept a key, or make a model/network call.

No model-comparison claim exists until a reviewed canonical bundle, prompt/model protocol, and score artifact are actually committed.

## What a research or model-comparison benchmark would require

Before making an accuracy, clinical, or model-comparison claim, add:

- a substantially larger frozen corpus with documented inclusion/exclusion sampling;
- trial-level train/development/test separation where training occurs;
- at least two independent annotators plus adjudication guidance;
- recorded disagreements and inter-annotator agreement;
- complete fixture provenance and licensing/redistribution review;
- a frozen schema, evaluator, prompt, model snapshot/settings, dependency lock, and code revision;
- multiple runs for nondeterministic conditions;
- confidence intervals and paired significance/effect analysis appropriate to the target population;
- error categories and qualitative source-grounded review; and
- explicit reporting of missing fields, exclusions, failures, and cost/latency.

Possible future metrics include evidence-span overlap, temporal component accuracy, logic-topology equivalence, ambiguity calibration, schema-failure rate, per-category recall, and trial-level all-criteria correctness.

## Interpretation rules

Safe claims:

- “The frozen one-case smoke completed reproducibly and caught guarded pipeline/artifact regressions.”
- “Offline-suite-v0.1.1 reproducibly evaluates two zero-network deterministic baselines on the unchanged 80-case Synthetic v0.1 dataset with hash-pinned inputs, exact criterion-text and structured-field metrics, per-slice/per-family analysis, leave-one-family-out and 10-family-cluster sensitivity checks, and denominator-aware overlapping error events.”

Unsafe claims:

- “The model understands clinical criteria.”
- “The extractor is clinically accurate.”
- “The system matches patients.”
- “Rules-v1 is a production model.”
- “Model A outperforms model B.”

The benchmark is deliberately useful as engineering evidence while remaining explicit about the amount, authorship, and synthetic nature of its data.
