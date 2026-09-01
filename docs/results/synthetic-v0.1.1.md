# CriteriaBench offline suite report

- Suite: `offline-suite-v0.1.1`
- Analysis contract SHA-256: `4ae02b4d6d8a03b12127febb3f0ac7843ea090f3937d739cfdde50fc8e059a86`
- Dataset manifest SHA-256: `348ca04e94b00312d174c3011a2edfe7f65e731611536af636fc553bb4725508`

## Dataset card

- Name/version: CriteriaBench Synthetic v0.1 (`synthetic-v0.1`)
- Cases: 80
- Families: 10 (8 variants each)
- Derived base templates: 10
- License: MIT
- Authoring disclosure: AI-assisted deterministic templates; independent second-human and clinical-domain review/adjudication pending.
- Clinical validation: no

## Metric interpretation

Exact criterion-text F1 matches criterion kind plus evaluator-normalized text; it is not exact equality of the full structured object.
Agreement on the eight evaluated structured fields is reported separately.

### Family counts

| Family | Cases |
|---|---:|
| and_multi_clause | 8 |
| demographics_consent | 8 |
| laboratory | 8 |
| negation | 8 |
| numeric_thresholds | 8 |
| or_multi_clause | 8 |
| punctuation_evidence_span | 8 |
| range_between | 8 |
| simple_inclusion_exclusion | 8 |
| temporal_constraints | 8 |

### Derived family/template/variant lineage

Lineage is derived from frozen manifest family and record order; fixture and manifest bytes are unchanged.

| Family ID | Base template ID | Variant IDs |
|---|---|---|
| and_multi_clause | and_multi_clause-template-001 | and_multi_clause-template-001-variant-001, and_multi_clause-template-001-variant-002, and_multi_clause-template-001-variant-003, and_multi_clause-template-001-variant-004, and_multi_clause-template-001-variant-005, and_multi_clause-template-001-variant-006, and_multi_clause-template-001-variant-007, and_multi_clause-template-001-variant-008 |
| demographics_consent | demographics_consent-template-001 | demographics_consent-template-001-variant-001, demographics_consent-template-001-variant-002, demographics_consent-template-001-variant-003, demographics_consent-template-001-variant-004, demographics_consent-template-001-variant-005, demographics_consent-template-001-variant-006, demographics_consent-template-001-variant-007, demographics_consent-template-001-variant-008 |
| laboratory | laboratory-template-001 | laboratory-template-001-variant-001, laboratory-template-001-variant-002, laboratory-template-001-variant-003, laboratory-template-001-variant-004, laboratory-template-001-variant-005, laboratory-template-001-variant-006, laboratory-template-001-variant-007, laboratory-template-001-variant-008 |
| negation | negation-template-001 | negation-template-001-variant-001, negation-template-001-variant-002, negation-template-001-variant-003, negation-template-001-variant-004, negation-template-001-variant-005, negation-template-001-variant-006, negation-template-001-variant-007, negation-template-001-variant-008 |
| numeric_thresholds | numeric_thresholds-template-001 | numeric_thresholds-template-001-variant-001, numeric_thresholds-template-001-variant-002, numeric_thresholds-template-001-variant-003, numeric_thresholds-template-001-variant-004, numeric_thresholds-template-001-variant-005, numeric_thresholds-template-001-variant-006, numeric_thresholds-template-001-variant-007, numeric_thresholds-template-001-variant-008 |
| or_multi_clause | or_multi_clause-template-001 | or_multi_clause-template-001-variant-001, or_multi_clause-template-001-variant-002, or_multi_clause-template-001-variant-003, or_multi_clause-template-001-variant-004, or_multi_clause-template-001-variant-005, or_multi_clause-template-001-variant-006, or_multi_clause-template-001-variant-007, or_multi_clause-template-001-variant-008 |
| punctuation_evidence_span | punctuation_evidence_span-template-001 | punctuation_evidence_span-template-001-variant-001, punctuation_evidence_span-template-001-variant-002, punctuation_evidence_span-template-001-variant-003, punctuation_evidence_span-template-001-variant-004, punctuation_evidence_span-template-001-variant-005, punctuation_evidence_span-template-001-variant-006, punctuation_evidence_span-template-001-variant-007, punctuation_evidence_span-template-001-variant-008 |
| range_between | range_between-template-001 | range_between-template-001-variant-001, range_between-template-001-variant-002, range_between-template-001-variant-003, range_between-template-001-variant-004, range_between-template-001-variant-005, range_between-template-001-variant-006, range_between-template-001-variant-007, range_between-template-001-variant-008 |
| simple_inclusion_exclusion | simple_inclusion_exclusion-template-001 | simple_inclusion_exclusion-template-001-variant-001, simple_inclusion_exclusion-template-001-variant-002, simple_inclusion_exclusion-template-001-variant-003, simple_inclusion_exclusion-template-001-variant-004, simple_inclusion_exclusion-template-001-variant-005, simple_inclusion_exclusion-template-001-variant-006, simple_inclusion_exclusion-template-001-variant-007, simple_inclusion_exclusion-template-001-variant-008 |
| temporal_constraints | temporal_constraints-template-001 | temporal_constraints-template-001-variant-001, temporal_constraints-template-001-variant-002, temporal_constraints-template-001-variant-003, temporal_constraints-template-001-variant-004, temporal_constraints-template-001-variant-005, temporal_constraints-template-001-variant-006, temporal_constraints-template-001-variant-007, temporal_constraints-template-001-variant-008 |

### Slice counts

| Slice | Cases |
|---|---:|
| between | 8 |
| comparison | 8 |
| consent | 8 |
| demographic | 8 |
| duration | 8 |
| evidence_span | 8 |
| exclusion | 24 |
| format_variation | 8 |
| inclusion | 80 |
| laboratory | 8 |
| logic_and | 8 |
| logic_or | 8 |
| multi_clause | 16 |
| negation | 8 |
| numeric_threshold | 32 |
| one_bullet_multiple_labels | 16 |
| punctuation | 8 |
| range | 8 |
| simple | 8 |
| temporal | 8 |

## Baselines

Both baselines are paid=false, network=false, input_tokens=0, output_tokens=0, estimated_cost_usd=0.

| Baseline | Complete | Schema | Paid | Network | Tokens in/out | Cost USD | Micro criterion-text F1 | Mean criterion-text F1 (95% case-resampling sensitivity) | Mean token F1 (95% case-resampling sensitivity) | Mean macro field accuracy (95% case-resampling sensitivity) | Criterion-text-perfect |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| empty-v1 | 100.00% | 100.00% | false | false | 0/0 | 0.000000 | 0.000000 | 0.000000 [0.000000, 0.000000] | 0.000000 [0.000000, 0.000000] | 0.000000 [0.000000, 0.000000] | 0.00% |
| rules-v1 | 100.00% | 100.00% | false | false | 0/0 | 0.000000 | 0.808824 | 0.775000 [0.675000, 0.862500] | 0.859843 [0.797162, 0.918240] | 0.651563 [0.592969, 0.708594] | 77.50% |

### All versus nonempty-reference cases

| Baseline | Cohort | Cases | Criterion-text TP / FP / FN | Micro P / R / F1 | Mean criterion-text / token / macro |
|---|---|---:|---:|---:|---:|
| empty-v1 | all | 80 | 0 / 0 / 144 | 0.000000 / 0.000000 / 0.000000 | 0.000000 / 0.000000 / 0.000000 |
| empty-v1 | nonempty reference | 80 | 0 / 0 / 144 | 0.000000 / 0.000000 / 0.000000 | 0.000000 / 0.000000 / 0.000000 |
| rules-v1 | all | 80 | 110 / 18 / 34 | 0.859375 / 0.763889 / 0.808824 | 0.775000 / 0.859843 / 0.651563 |
| rules-v1 | nonempty reference | 80 | 110 / 18 / 34 | 0.859375 / 0.763889 / 0.808824 | 0.775000 / 0.859843 / 0.651563 |

### All eight structured-field accuracies

| Baseline | Category | Concept | Operator | Value | Unit | Negated | Temporal relation | Logic connector |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| empty-v1 | 0.000000 | 0.000000 | 0.000000 | 0.000000 | 0.000000 | 0.000000 | 0.000000 | 0.000000 |
| rules-v1 | 0.706250 | 0.393750 | 0.400000 | 0.368750 | 0.800000 | 0.893750 | 0.850000 | 0.800000 |

### Interpretation boundary

On this fixed constructed suite, `rules-v1` is stronger at recovering or overlapping criterion text than at reproducing the eight evaluated structured fields: mean exact criterion-text F1 0.775000, mean token-overlap F1 0.859843, and mean structured-field macro accuracy 0.651563. These differently aggregated metrics are descriptive engineering evidence, not proof of semantic understanding.

The zero exact criterion-text scores for the `logic_and`, `logic_or`, and `multi_clause` slices reflect the known segmentation/grouping mismatch: `rules-v1` emits one criterion for a source bullet whose reference contains two grouped criteria. They do not by themselves prove a general reasoning limitation.

### Per-family results

| Baseline | Family | Cases | Criterion-text TP / FP / FN | Micro P / R / F1 | Mean criterion-text / token / macro |
|---|---|---:|---:|---:|---:|
| empty-v1 | and_multi_clause | 8 | 0 / 0 / 16 | 0.000000 / 0.000000 / 0.000000 | 0.000000 / 0.000000 / 0.000000 |
| empty-v1 | demographics_consent | 8 | 0 / 0 / 16 | 0.000000 / 0.000000 / 0.000000 | 0.000000 / 0.000000 / 0.000000 |
| empty-v1 | laboratory | 8 | 0 / 0 / 16 | 0.000000 / 0.000000 / 0.000000 | 0.000000 / 0.000000 / 0.000000 |
| empty-v1 | negation | 8 | 0 / 0 / 16 | 0.000000 / 0.000000 / 0.000000 | 0.000000 / 0.000000 / 0.000000 |
| empty-v1 | numeric_thresholds | 8 | 0 / 0 / 16 | 0.000000 / 0.000000 / 0.000000 | 0.000000 / 0.000000 / 0.000000 |
| empty-v1 | or_multi_clause | 8 | 0 / 0 / 16 | 0.000000 / 0.000000 / 0.000000 | 0.000000 / 0.000000 / 0.000000 |
| empty-v1 | punctuation_evidence_span | 8 | 0 / 0 / 8 | 0.000000 / 0.000000 / 0.000000 | 0.000000 / 0.000000 / 0.000000 |
| empty-v1 | range_between | 8 | 0 / 0 / 8 | 0.000000 / 0.000000 / 0.000000 | 0.000000 / 0.000000 / 0.000000 |
| empty-v1 | simple_inclusion_exclusion | 8 | 0 / 0 / 16 | 0.000000 / 0.000000 / 0.000000 | 0.000000 / 0.000000 / 0.000000 |
| empty-v1 | temporal_constraints | 8 | 0 / 0 / 16 | 0.000000 / 0.000000 / 0.000000 | 0.000000 / 0.000000 / 0.000000 |
| rules-v1 | and_multi_clause | 8 | 0 / 8 / 16 | 0.000000 / 0.000000 / 0.000000 | 0.000000 / 0.333333 / 0.250000 |
| rules-v1 | demographics_consent | 8 | 16 / 0 / 0 | 1.000000 / 1.000000 / 1.000000 | 1.000000 / 1.000000 / 0.812500 |
| rules-v1 | laboratory | 8 | 16 / 0 / 0 | 1.000000 / 1.000000 / 1.000000 | 1.000000 / 1.000000 / 0.898438 |
| rules-v1 | negation | 8 | 16 / 0 / 0 | 1.000000 / 1.000000 / 1.000000 | 1.000000 / 1.000000 / 0.562500 |
| rules-v1 | numeric_thresholds | 8 | 16 / 0 / 0 | 1.000000 / 1.000000 / 1.000000 | 1.000000 / 1.000000 / 1.000000 |
| rules-v1 | or_multi_clause | 8 | 0 / 8 / 16 | 0.000000 / 0.000000 / 0.000000 | 0.000000 / 0.300812 / 0.179688 |
| rules-v1 | punctuation_evidence_span | 8 | 6 / 2 / 2 | 0.750000 / 0.750000 / 0.750000 | 0.750000 / 0.964286 / 0.640625 |
| rules-v1 | range_between | 8 | 8 / 0 / 0 | 1.000000 / 1.000000 / 1.000000 | 1.000000 / 1.000000 / 0.625000 |
| rules-v1 | simple_inclusion_exclusion | 8 | 16 / 0 / 0 | 1.000000 / 1.000000 / 1.000000 | 1.000000 / 1.000000 / 0.859375 |
| rules-v1 | temporal_constraints | 8 | 16 / 0 / 0 | 1.000000 / 1.000000 / 1.000000 | 1.000000 / 1.000000 / 0.687500 |

### Leave-one-family-out sensitivity

| Baseline | Excluded family | Cases | Criterion-text TP / FP / FN | Micro P / R / F1 | Mean criterion-text / token / macro |
|---|---|---:|---:|---:|---:|
| empty-v1 | and_multi_clause | 72 | 0 / 0 / 128 | 0.000000 / 0.000000 / 0.000000 | 0.000000 / 0.000000 / 0.000000 |
| empty-v1 | demographics_consent | 72 | 0 / 0 / 128 | 0.000000 / 0.000000 / 0.000000 | 0.000000 / 0.000000 / 0.000000 |
| empty-v1 | laboratory | 72 | 0 / 0 / 128 | 0.000000 / 0.000000 / 0.000000 | 0.000000 / 0.000000 / 0.000000 |
| empty-v1 | negation | 72 | 0 / 0 / 128 | 0.000000 / 0.000000 / 0.000000 | 0.000000 / 0.000000 / 0.000000 |
| empty-v1 | numeric_thresholds | 72 | 0 / 0 / 128 | 0.000000 / 0.000000 / 0.000000 | 0.000000 / 0.000000 / 0.000000 |
| empty-v1 | or_multi_clause | 72 | 0 / 0 / 128 | 0.000000 / 0.000000 / 0.000000 | 0.000000 / 0.000000 / 0.000000 |
| empty-v1 | punctuation_evidence_span | 72 | 0 / 0 / 136 | 0.000000 / 0.000000 / 0.000000 | 0.000000 / 0.000000 / 0.000000 |
| empty-v1 | range_between | 72 | 0 / 0 / 136 | 0.000000 / 0.000000 / 0.000000 | 0.000000 / 0.000000 / 0.000000 |
| empty-v1 | simple_inclusion_exclusion | 72 | 0 / 0 / 128 | 0.000000 / 0.000000 / 0.000000 | 0.000000 / 0.000000 / 0.000000 |
| empty-v1 | temporal_constraints | 72 | 0 / 0 / 128 | 0.000000 / 0.000000 / 0.000000 | 0.000000 / 0.000000 / 0.000000 |
| rules-v1 | and_multi_clause | 72 | 110 / 10 / 18 | 0.916667 / 0.859375 / 0.887097 | 0.861111 / 0.918344 / 0.696181 |
| rules-v1 | demographics_consent | 72 | 94 / 18 / 34 | 0.839286 / 0.734375 / 0.783333 | 0.750000 / 0.844270 / 0.633681 |
| rules-v1 | laboratory | 72 | 94 / 18 / 34 | 0.839286 / 0.734375 / 0.783333 | 0.750000 / 0.844270 / 0.624132 |
| rules-v1 | negation | 72 | 94 / 18 / 34 | 0.839286 / 0.734375 / 0.783333 | 0.750000 / 0.844270 / 0.661458 |
| rules-v1 | numeric_thresholds | 72 | 94 / 18 / 34 | 0.839286 / 0.734375 / 0.783333 | 0.750000 / 0.844270 / 0.612847 |
| rules-v1 | or_multi_clause | 72 | 110 / 10 / 18 | 0.916667 / 0.859375 / 0.887097 | 0.861111 / 0.921958 / 0.703993 |
| rules-v1 | punctuation_evidence_span | 72 | 104 / 16 / 32 | 0.866667 / 0.764706 / 0.812500 | 0.777778 / 0.848238 / 0.652778 |
| rules-v1 | range_between | 72 | 102 / 18 / 34 | 0.850000 / 0.750000 / 0.796875 | 0.750000 / 0.844270 / 0.654514 |
| rules-v1 | simple_inclusion_exclusion | 72 | 94 / 18 / 34 | 0.839286 / 0.734375 / 0.783333 | 0.750000 / 0.844270 / 0.628472 |
| rules-v1 | temporal_constraints | 72 | 94 / 18 / 34 | 0.839286 / 0.734375 / 0.783333 | 0.750000 / 0.844270 / 0.647569 |

### 10-family-cluster baseline sensitivity

These are fixed-suite sensitivity intervals, not population confidence intervals.

| Baseline | Metric | Mean (95% family-cluster sensitivity) |
|---|---|---:|
| empty-v1 | mean_criterion_text_f1 | 0.000000 [0.000000, 0.000000] |
| empty-v1 | mean_token_f1 | 0.000000 [0.000000, 0.000000] |
| empty-v1 | mean_macro_field_accuracy | 0.000000 [0.000000, 0.000000] |
| rules-v1 | mean_criterion_text_f1 | 0.775000 [0.500000, 1.000000] |
| rules-v1 | mean_token_f1 | 0.859843 [0.659843, 1.000000] |
| rules-v1 | mean_macro_field_accuracy | 0.651563 [0.488281, 0.800801] |

## Paired fixed-suite sensitivity comparisons

Case and whole-family percentile resampling: 10,000 draws, seed 20260901.

| Challenger - reference | Resampling unit | Metric | Mean delta (95% resampling sensitivity) |
|---|---|---|---:|
| rules-v1 - empty-v1 | case | mean_criterion_text_f1 | 0.775000 [0.675000, 0.862500] |
| rules-v1 - empty-v1 | case | mean_token_f1 | 0.859843 [0.797162, 0.918240] |
| rules-v1 - empty-v1 | case | mean_macro_field_accuracy | 0.651563 [0.592969, 0.708594] |
| rules-v1 - empty-v1 | family (10 clusters) | mean_criterion_text_f1 | 0.775000 [0.500000, 1.000000] |
| rules-v1 - empty-v1 | family (10 clusters) | mean_token_f1 | 0.859843 [0.659843, 1.000000] |
| rules-v1 - empty-v1 | family (10 clusters) | mean_macro_field_accuracy | 0.651563 [0.488281, 0.800801] |

> Limitation: Case-resampled and 10-family-cluster percentile intervals are descriptive sensitivity analyses for this fixed constructed suite, not population confidence intervals.

## Per-slice results

| Baseline | Slice | Cases | Micro criterion-text F1 | Mean criterion-text F1 | Mean token F1 | Mean macro field accuracy |
|---|---|---:|---:|---:|---:|---:|
| empty-v1 | between | 8 | 0.000000 | 0.000000 | 0.000000 | 0.000000 |
| empty-v1 | comparison | 8 | 0.000000 | 0.000000 | 0.000000 | 0.000000 |
| empty-v1 | consent | 8 | 0.000000 | 0.000000 | 0.000000 | 0.000000 |
| empty-v1 | demographic | 8 | 0.000000 | 0.000000 | 0.000000 | 0.000000 |
| empty-v1 | duration | 8 | 0.000000 | 0.000000 | 0.000000 | 0.000000 |
| empty-v1 | evidence_span | 8 | 0.000000 | 0.000000 | 0.000000 | 0.000000 |
| empty-v1 | exclusion | 24 | 0.000000 | 0.000000 | 0.000000 | 0.000000 |
| empty-v1 | format_variation | 8 | 0.000000 | 0.000000 | 0.000000 | 0.000000 |
| empty-v1 | inclusion | 80 | 0.000000 | 0.000000 | 0.000000 | 0.000000 |
| empty-v1 | laboratory | 8 | 0.000000 | 0.000000 | 0.000000 | 0.000000 |
| empty-v1 | logic_and | 8 | 0.000000 | 0.000000 | 0.000000 | 0.000000 |
| empty-v1 | logic_or | 8 | 0.000000 | 0.000000 | 0.000000 | 0.000000 |
| empty-v1 | multi_clause | 16 | 0.000000 | 0.000000 | 0.000000 | 0.000000 |
| empty-v1 | negation | 8 | 0.000000 | 0.000000 | 0.000000 | 0.000000 |
| empty-v1 | numeric_threshold | 32 | 0.000000 | 0.000000 | 0.000000 | 0.000000 |
| empty-v1 | one_bullet_multiple_labels | 16 | 0.000000 | 0.000000 | 0.000000 | 0.000000 |
| empty-v1 | punctuation | 8 | 0.000000 | 0.000000 | 0.000000 | 0.000000 |
| empty-v1 | range | 8 | 0.000000 | 0.000000 | 0.000000 | 0.000000 |
| empty-v1 | simple | 8 | 0.000000 | 0.000000 | 0.000000 | 0.000000 |
| empty-v1 | temporal | 8 | 0.000000 | 0.000000 | 0.000000 | 0.000000 |
| rules-v1 | between | 8 | 1.000000 | 1.000000 | 1.000000 | 0.625000 |
| rules-v1 | comparison | 8 | 1.000000 | 1.000000 | 1.000000 | 1.000000 |
| rules-v1 | consent | 8 | 1.000000 | 1.000000 | 1.000000 | 0.812500 |
| rules-v1 | demographic | 8 | 1.000000 | 1.000000 | 1.000000 | 0.812500 |
| rules-v1 | duration | 8 | 1.000000 | 1.000000 | 1.000000 | 0.687500 |
| rules-v1 | evidence_span | 8 | 0.750000 | 0.750000 | 0.964286 | 0.640625 |
| rules-v1 | exclusion | 24 | 1.000000 | 1.000000 | 1.000000 | 0.703125 |
| rules-v1 | format_variation | 8 | 0.750000 | 0.750000 | 0.964286 | 0.640625 |
| rules-v1 | inclusion | 80 | 0.808824 | 0.775000 | 0.859843 | 0.651563 |
| rules-v1 | laboratory | 8 | 1.000000 | 1.000000 | 1.000000 | 0.898438 |
| rules-v1 | logic_and | 8 | 0.000000 | 0.000000 | 0.333333 | 0.250000 |
| rules-v1 | logic_or | 8 | 0.000000 | 0.000000 | 0.300812 | 0.179688 |
| rules-v1 | multi_clause | 16 | 0.000000 | 0.000000 | 0.317072 | 0.214844 |
| rules-v1 | negation | 8 | 1.000000 | 1.000000 | 1.000000 | 0.562500 |
| rules-v1 | numeric_threshold | 32 | 1.000000 | 1.000000 | 1.000000 | 0.833984 |
| rules-v1 | one_bullet_multiple_labels | 16 | 0.000000 | 0.000000 | 0.317072 | 0.214844 |
| rules-v1 | punctuation | 8 | 0.750000 | 0.750000 | 0.964286 | 0.640625 |
| rules-v1 | range | 8 | 1.000000 | 1.000000 | 1.000000 | 0.625000 |
| rules-v1 | simple | 8 | 1.000000 | 1.000000 | 1.000000 | 0.859375 |
| rules-v1 | temporal | 8 | 1.000000 | 1.000000 | 1.000000 | 0.687500 |

## Error taxonomy

Counts use evaluator alignment and evaluator normalization for scored structured fields. Mismatch categories can overlap.

| Baseline | Error type | Count | Denominator | Rate | Denominator basis |
|---|---|---:|---:|---:|---|
| empty-v1 | missing_criterion | 144 | 144 | 1.000000 | reference_criteria |
| empty-v1 | spurious_criterion | 0 | 0 | 0.000000 | predicted_criteria |
| empty-v1 | text_mismatch | 0 | 0 | 0.000000 | aligned_pairs |
| empty-v1 | category_mismatch | 0 | 0 | 0.000000 | aligned_pairs |
| empty-v1 | concept_mismatch | 0 | 0 | 0.000000 | aligned_pairs |
| empty-v1 | operator_mismatch | 0 | 0 | 0.000000 | aligned_pairs |
| empty-v1 | value_mismatch | 0 | 0 | 0.000000 | aligned_pairs |
| empty-v1 | unit_mismatch | 0 | 0 | 0.000000 | aligned_pairs |
| empty-v1 | negation_mismatch | 0 | 0 | 0.000000 | aligned_pairs |
| empty-v1 | temporal_relation_mismatch | 0 | 0 | 0.000000 | aligned_pairs |
| empty-v1 | temporal_quantity_mismatch | 0 | 0 | 0.000000 | aligned_pairs |
| empty-v1 | temporal_unit_mismatch | 0 | 0 | 0.000000 | aligned_pairs |
| empty-v1 | temporal_reference_event_mismatch | 0 | 0 | 0.000000 | aligned_pairs |
| empty-v1 | temporal_raw_text_mismatch | 0 | 0 | 0.000000 | aligned_pairs |
| empty-v1 | logic_connector_mismatch | 0 | 0 | 0.000000 | aligned_pairs |
| empty-v1 | logic_parent_mismatch | 0 | 0 | 0.000000 | aligned_pairs |
| empty-v1 | evidence_quote_mismatch | 0 | 0 | 0.000000 | aligned_pairs |
| empty-v1 | evidence_offset_mismatch | 0 | 0 | 0.000000 | aligned_pairs |
> empty-v1: aligned pairs=0. Mismatch categories can overlap on the same aligned criterion; raw counts must not be summed as a count of unique erroneous criteria or cases.
| rules-v1 | missing_criterion | 16 | 144 | 0.111111 | reference_criteria |
| rules-v1 | spurious_criterion | 0 | 128 | 0.000000 | predicted_criteria |
| rules-v1 | text_mismatch | 18 | 128 | 0.140625 | aligned_pairs |
| rules-v1 | category_mismatch | 26 | 128 | 0.203125 | aligned_pairs |
| rules-v1 | concept_mismatch | 79 | 128 | 0.617188 | aligned_pairs |
| rules-v1 | operator_mismatch | 64 | 128 | 0.500000 | aligned_pairs |
| rules-v1 | value_mismatch | 69 | 128 | 0.539062 | aligned_pairs |
| rules-v1 | unit_mismatch | 8 | 128 | 0.062500 | aligned_pairs |
| rules-v1 | negation_mismatch | 1 | 128 | 0.007812 | aligned_pairs |
| rules-v1 | temporal_relation_mismatch | 8 | 128 | 0.062500 | aligned_pairs |
| rules-v1 | temporal_quantity_mismatch | 8 | 128 | 0.062500 | aligned_pairs |
| rules-v1 | temporal_unit_mismatch | 8 | 128 | 0.062500 | aligned_pairs |
| rules-v1 | temporal_reference_event_mismatch | 0 | 128 | 0.000000 | aligned_pairs |
| rules-v1 | temporal_raw_text_mismatch | 8 | 128 | 0.062500 | aligned_pairs |
| rules-v1 | logic_connector_mismatch | 16 | 128 | 0.125000 | aligned_pairs |
| rules-v1 | logic_parent_mismatch | 0 | 128 | 0.000000 | aligned_pairs |
| rules-v1 | evidence_quote_mismatch | 23 | 128 | 0.179688 | aligned_pairs |
| rules-v1 | evidence_offset_mismatch | 23 | 128 | 0.179688 | aligned_pairs |
> rules-v1: aligned pairs=128. Mismatch categories can overlap on the same aligned criterion; raw counts must not be summed as a count of unique erroneous criteria or cases.

## Five deterministic examples

| Trial | Family | Base template | Variant | Slices | Reference criteria | Criterion-text F1 | Overlapping mismatch-event totals |
|---|---|---|---|---|---:|---|---|
| CB-SYN-V01-001 | simple_inclusion_exclusion | simple_inclusion_exclusion-template-001 | simple_inclusion_exclusion-template-001-variant-001 | simple, inclusion, exclusion | 2 | empty-v1=0.000000, rules-v1=1.000000 | empty-v1=2, rules-v1=3 |
| CB-SYN-V01-009 | numeric_thresholds | numeric_thresholds-template-001 | numeric_thresholds-template-001-variant-001 | numeric_threshold, comparison, inclusion | 2 | empty-v1=0.000000, rules-v1=1.000000 | empty-v1=2, rules-v1=0 |
| CB-SYN-V01-017 | temporal_constraints | temporal_constraints-template-001 | temporal_constraints-template-001-variant-001 | temporal, duration, inclusion, exclusion | 2 | empty-v1=0.000000, rules-v1=1.000000 | empty-v1=2, rules-v1=8 |
| CB-SYN-V01-049 | and_multi_clause | and_multi_clause-template-001 | and_multi_clause-template-001-variant-001 | logic_and, multi_clause, one_bullet_multiple_labels, inclusion | 2 | empty-v1=0.000000, rules-v1=0.000000 | empty-v1=2, rules-v1=8 |
| CB-SYN-V01-057 | or_multi_clause | or_multi_clause-template-001 | or_multi_clause-template-001-variant-001 | logic_or, multi_clause, one_bullet_multiple_labels, inclusion | 2 | empty-v1=0.000000, rules-v1=0.000000 | empty-v1=2, rules-v1=9 |

## Reproduce

```console
uv run --frozen --no-env-file criteriabench-suite data/synthetic_v0_1/manifest.json --configs empty-v1 rules-v1 --markdown-output artifacts/synthetic-v0.1.1.md --json-output artifacts/synthetic-v0.1.1.json --check-json docs/results/synthetic-v0.1.1.json --check-markdown docs/results/synthetic-v0.1.1.md
```

## Limitations

- This fixed constructed suite is engineering regression evidence, not a research-grade benchmark.
- All 80 cases are generated from 10 parametric synthetic templates, not sampled records.
- The AI-assisted deterministic references were produced in a single-author workflow.
- Labels lack independent second-human and clinical-domain review and adjudication.
- The suite is not clinical validation and must not support clinical decisions.
- The offline baselines make no LLM calls, so their scores do not measure LLM quality.
- Sensitivity intervals describe only this fixed case mix and are not population estimates.
- Fixed-suite sensitivities do not measure model stochasticity; both baselines are deterministic.
