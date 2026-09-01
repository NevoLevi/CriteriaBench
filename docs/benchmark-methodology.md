# Benchmark methodology

CriteriaBench has two deliberately separate evaluation paths:

1. a one-case engineering smoke that verifies guarded extraction, deterministic scoring, and provenance/cost artifact plumbing; and
2. the 80-case Synthetic v0.1 offline suite, which verifies dataset, evaluator, baseline, slice-analysis, and report regressions without network or model calls.

Neither path establishes clinical accuracy. The offline suite compares two deterministic software baselines, not language models. The approved one-case Luna smokes remain separate engineering evidence and are not included in Synthetic v0.1.

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

Exact comparison uses `(criterion kind, normalize(normalized_text))` as its key. Normalization applies Unicode-aware lowercasing, whitespace collapse, and punctuation/token handling implemented in the evaluator. A multiset counter preserves duplicate cardinality.

For token and field scoring:

1. Only reference/prediction criteria of the same kind are candidates.
2. Candidate weight is token F1 over normalized text.
3. Weights below 0.25 are excluded.
4. Deterministic Hungarian assignment finds a maximum-weight one-to-one matching.
5. Unmatched references and extra predictions contribute zero to token and field scores.

Exact multiset intersection makes missing references lower recall and extra predictions lower precision. Token and each field score sum aligned-pair values and divide by `max(prediction_count, reference_count)`, so unmatched references and extra predictions each contribute zero. This prevents one prediction from being reused for multiple references and avoids giving structured-field credit to unrelated zero-overlap text.

## Implemented metrics

### Criterion-level

- exact precision;
- exact recall;
- exact F1; and
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

Synthetic v0.1 is locally authored from deterministic templates. It contains no patient records, personal data, network-sourced text, or model-generated text. The manifest records 10 balanced families and 20 declared slices. Sixteen hard cases represent one source bullet as multiple labels with AND or OR grouping.

Each of the 80 fixture bytes is pinned by SHA-256. The loader requires the exact ordered case set, unique trial IDs and hashes, strict schema validity, matching fixture/manifest provenance, sequential criterion IDs, correct inclusion/exclusion kinds, and exact source-bound evidence quotes and offsets.

The references are single-author template labels. Independent review and adjudication are still pending. The suite is small, English-only, template-shaped, and balanced by design rather than natural sampling. Its results are engineering regression evidence only: they do not measure clinical validity, real-registry generalization, inter-annotator agreement, fairness, safety, or production model quality.

See the [dataset card](../data/synthetic_v0_1/README.md) for the complete authoring and future independent-review procedure.

### Offline baselines

The suite accepts only these allowlisted configurations:

- `empty-v1`, which returns a schema-valid empty extraction; and
- `rules-v1`, a deterministic adapter over the repository's mock extraction provider.

Both baselines run sequentially and record `paid=false`, `network=false`, zero input/output tokens, and USD 0 estimated cost. They do not call an LLM. Their comparison checks that the evaluator and deterministic extraction path respond meaningfully to the constructed cases; it is not evidence that one production model outperforms another.

### Aggregation, slices, and error analysis

For each baseline, the report includes:

- completion and schema-valid rates;
- micro exact precision/recall/F1;
- mean exact F1, token F1, and macro field accuracy;
- trial-perfect rate;
- all-case and nonempty-gold cohorts;
- all 20 declared slices; and
- deterministic counts for missing/spurious criteria and text, category, concept, operator, value, unit, negation, temporal, logic, and evidence mismatches.

The error taxonomy reuses the evaluator's deterministic optimal alignment so its pair choices cannot diverge from scoring.

### Deterministic paired bootstrap intervals

Mean metrics and rules-minus-empty paired deltas use percentile bootstrap intervals with 10,000 resamples, seed `20260901`, and 95% coverage. Values are rounded to six decimal places for stable serialization.

These intervals quantify resampling variation only for this constructed 80-case mix. They do not establish population, clinical, or external-validity uncertainty. Because the dataset and baselines are deterministic and fixed, the bootstrap is a reproducible error-bar summary rather than evidence about repeated model sampling.

### Policy and report regression gate

`benchmarks/synthetic-v0.1-policy.json` freezes the expected dataset/config structure and requires:

- exactly 80 cases, 10 families, all 20 declared slices, and 16 hard one-bullet/multi-label cases;
- both `empty-v1` and `rules-v1`;
- 100% completion and schema validity;
- manifest hash and source-evidence validity through the loader contract;
- `paid=false`, `network=false`, zero tokens, and zero estimated cost for both baselines; and
- rules-v1 mean token F1 and mean macro field accuracy each at least 0.20 above empty-v1.

The primary regression gate is stronger than numeric thresholds: CI regenerates both report formats and requires byte-for-byte equality with the committed [JSON](results/synthetic-v0.1.json) and [Markdown report](results/synthetic-v0.1.md). This catches drift in the fixtures, analysis contract, ordering, rounding, wording, and output serialization. CI then uploads the generated pair as evidence while retaining the original one-case smoke.

Reproduce the suite from a frozen environment:

```powershell
uv run --frozen --no-env-file criteriabench-suite `
  data/synthetic_v0_1/manifest.json `
  --configs empty-v1 rules-v1 `
  --json-output artifacts/synthetic-v0.1.json `
  --markdown-output artifacts/synthetic-v0.1.md `
  --check-json docs/results/synthetic-v0.1.json `
  --check-markdown docs/results/synthetic-v0.1.md
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
- exact F1: 1.0;
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

Synthetic v0.1 does not use this live path. A future model evaluation would require a separately reviewed protocol and explicit authorization for any network or paid calls.

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
- “Synthetic v0.1 reproducibly evaluates two zero-network deterministic baselines on 80 constructed reference cases with hash-pinned inputs, source-bound evidence checks, per-slice/error analysis, and fixed-seed paired-bootstrap summaries.”

Unsafe claims:

- “The model understands clinical criteria.”
- “The extractor is clinically accurate.”
- “The system matches patients.”
- “Rules-v1 is a production model.”
- “Model A outperforms model B.”

The benchmark is deliberately useful as engineering evidence while remaining explicit about the amount, authorship, and synthetic nature of its data.
