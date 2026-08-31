# Benchmark methodology

CriteriaBench currently provides an **engineering smoke benchmark**. It verifies that a trial document can be extracted into a strict contract, compared deterministically with a gold reference, and recorded with portable provenance/cost fields. It does not establish clinical accuracy or comparative model quality.

## Implemented extraction contract

Each `ClinicalTrialEligibility` object contains:

- `schema_version` and `trial_id`;
- inclusion and exclusion criterion lists;
- per criterion: stable ID, kind/category, concept, operator, value, unit, negation, temporal constraint, logic group, and exact evidence span/quote; and
- an ambiguities list.

Semantic validation includes operator/value compatibility, between/list/existence forms, temporal duration/reference rules, single versus AND/OR group cardinality, and evidence quote equality. Extractor/provider prediction evidence offsets and quotes are also cross-checked against the exact eligibility text before output is accepted. Manually authored gold/reference evidence remains typed but is not currently cross-checked against the fixture source.

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

Source-bound evidence validation applies to extractor/provider predictions before their result is accepted. Gold/reference evidence is typed but is not currently cross-checked against the fixture source. `schema_valid=true` means both typed objects passed the boundary, not that either is medically correct.

## Built-in data

The repository contains:

- a minimally retained public ClinicalTrials.gov trial fixture/manifest for downloader and input demonstrations; and
- one synthetic trial with a manually specified gold extraction for the frozen smoke benchmark.

The benchmark fixture is intentionally synthetic so CI can run without network access, data drift, or paid inference. One case is sufficient to detect pipeline/artifact regressions, not to estimate generalization.

The CLI smoke does not exercise Redis, the worker, or PostgreSQL. Those paths have separate API/Compose/kind integration evidence.

## Reproducible artifact

The CLI writes a unique temporary file and atomically replaces the requested JSON output, refuses an existing path unless `--overwrite` is explicit, and removes temporary output after failure. It does not claim filesystem `fsync` durability.

The artifact records:

- artifact/schema version and creation time;
- provider and exact model label;
- paid flag and applied price assumptions;
- fixture version, relative source path, and SHA-256;
- extraction contract SHA-256 binding schema/provider implementation/service code;
- evaluation contract SHA-256 binding evaluator implementation;
- top-level maximum permitted attempts per case, batch projected authorization, aggregate counts/scores, and total usage-priced cost; and
- per-case extraction, optional evaluation, latency, token usage, and usage-priced cost.

The current artifact does not report latency percentiles, throughput, queue age, CPU/memory, cached-token pricing, or a raw provider response. Those are future benchmark dimensions.

## Frozen smoke expectations

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

Before the first request, the CLI:

1. verifies all live flags/provider/key/model/rate constraints;
2. requires an explicit budget no greater than USD 2;
3. verifies each input against the committed manifest;
4. hashes and parses the same bytes;
5. estimates prompt/schema/trial/max-output cost for every permitted attempt and every case; and
6. rejects the complete batch if the projection exceeds the selected budget.

After a provider call starts, the authorization ledger consumes at least the conservative reservation even if the call times out. Usage-priced cost is separately computed from returned token usage and configured rates. Neither value is a provider-side spending cap.

## What a research benchmark would require

Before making an accuracy or model-comparison claim, add:

- a substantially larger frozen corpus with documented inclusion/exclusion sampling;
- trial-level train/development/test separation where training occurs;
- at least two independent annotators plus adjudication guidance;
- recorded disagreements and inter-annotator agreement;
- complete fixture provenance and licensing/redistribution review;
- a frozen schema, evaluator, prompt, model snapshot/settings, dependency lock, and code revision;
- multiple runs for nondeterministic conditions;
- bootstrap confidence intervals and paired significance/effect analysis;
- error categories and qualitative source-grounded review; and
- explicit reporting of missing fields, exclusions, failures, and cost/latency.

Possible future metrics include evidence-span overlap, temporal component accuracy, logic-topology equivalence, ambiguity calibration, schema-failure rate, per-category recall, and trial-level all-criteria correctness.

## Interpretation rules

Safe claim: “The frozen synthetic smoke completed reproducibly and caught contract/evaluator regressions.”

Unsafe claims: “The model understands clinical criteria,” “the extractor is clinically accurate,” “the system matches patients,” or “model A outperforms model B.”

The benchmark is deliberately useful as engineering evidence while remaining honest about the amount and nature of its data.
