# CriteriaBench offline suite report

- Suite: `offline-suite-v0.1`
- Analysis contract SHA-256: `c5a7c7777f9c120da14c8d6979a3fde27bcf47a7b6bea02bb3cc4fc74e655c1f`
- Dataset manifest SHA-256: `348ca04e94b00312d174c3011a2edfe7f65e731611536af636fc553bb4725508`

## Dataset card

- Name/version: CriteriaBench Synthetic v0.1 (`synthetic-v0.1`)
- Cases: 80
- Families: 10 (8 variants each)
- License: MIT
- Annotation: single-author deterministic templates; independent review pending
- Clinical validation: no

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

| Baseline | Complete | Schema | Paid | Network | Tokens in/out | Cost USD | Micro exact F1 | Mean exact F1 (95% CI) | Mean token F1 (95% CI) | Mean macro field accuracy (95% CI) | Trial-perfect |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| empty-v1 | 100.00% | 100.00% | false | false | 0/0 | 0.000000 | 0.000000 | 0.000000 [0.000000, 0.000000] | 0.000000 [0.000000, 0.000000] | 0.000000 [0.000000, 0.000000] | 0.00% |
| rules-v1 | 100.00% | 100.00% | false | false | 0/0 | 0.000000 | 0.808824 | 0.775000 [0.675000, 0.862500] | 0.859843 [0.797162, 0.918240] | 0.651563 [0.592969, 0.708594] | 77.50% |

### All versus nonempty-gold cases

| Baseline | Cohort | Cases | Exact TP / predicted / gold | Micro P / R / F1 | Mean exact / token / macro |
|---|---|---:|---:|---:|---:|
| empty-v1 | all | 80 | 0 / 0 / 144 | 0.000000 / 0.000000 / 0.000000 | 0.000000 / 0.000000 / 0.000000 |
| empty-v1 | nonempty gold | 80 | 0 / 0 / 144 | 0.000000 / 0.000000 / 0.000000 | 0.000000 / 0.000000 / 0.000000 |
| rules-v1 | all | 80 | 110 / 128 / 144 | 0.859375 / 0.763889 / 0.808824 | 0.775000 / 0.859843 / 0.651563 |
| rules-v1 | nonempty gold | 80 | 110 / 128 / 144 | 0.859375 / 0.763889 / 0.808824 | 0.775000 / 0.859843 / 0.651563 |

## Paired bootstrap comparisons

Paired percentile bootstrap: 10,000 resamples, seed 20260901, 95% intervals.

| Challenger - reference | Metric | Mean delta (95% CI) |
|---|---|---:|
| rules-v1 - empty-v1 | mean_exact_f1 | 0.775000 [0.675000, 0.862500] |
| rules-v1 - empty-v1 | mean_token_f1 | 0.859843 [0.797162, 0.918240] |
| rules-v1 - empty-v1 | mean_macro_field_accuracy | 0.651563 [0.592969, 0.708594] |

> Limitation: Percentile intervals quantify uncertainty only for this constructed 80-case mix; they do not establish population, clinical, or external-validity uncertainty.

## Per-slice results

| Baseline | Slice | Cases | Micro exact F1 | Mean exact F1 | Mean token F1 | Mean macro field accuracy |
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

Counts use the same deterministic optimal alignment as the evaluator.

| Baseline | Error type | Count |
|---|---|---:|
| empty-v1 | missing_criterion | 144 |
| empty-v1 | spurious_criterion | 0 |
| empty-v1 | text_mismatch | 0 |
| empty-v1 | category_mismatch | 0 |
| empty-v1 | concept_mismatch | 0 |
| empty-v1 | operator_mismatch | 0 |
| empty-v1 | value_mismatch | 0 |
| empty-v1 | unit_mismatch | 0 |
| empty-v1 | negation_mismatch | 0 |
| empty-v1 | temporal_relation_mismatch | 0 |
| empty-v1 | temporal_quantity_mismatch | 0 |
| empty-v1 | temporal_unit_mismatch | 0 |
| empty-v1 | temporal_reference_event_mismatch | 0 |
| empty-v1 | temporal_raw_text_mismatch | 0 |
| empty-v1 | logic_connector_mismatch | 0 |
| empty-v1 | logic_parent_mismatch | 0 |
| empty-v1 | evidence_quote_mismatch | 0 |
| empty-v1 | evidence_offset_mismatch | 0 |
| rules-v1 | missing_criterion | 16 |
| rules-v1 | spurious_criterion | 0 |
| rules-v1 | text_mismatch | 18 |
| rules-v1 | category_mismatch | 26 |
| rules-v1 | concept_mismatch | 96 |
| rules-v1 | operator_mismatch | 64 |
| rules-v1 | value_mismatch | 64 |
| rules-v1 | unit_mismatch | 24 |
| rules-v1 | negation_mismatch | 1 |
| rules-v1 | temporal_relation_mismatch | 8 |
| rules-v1 | temporal_quantity_mismatch | 8 |
| rules-v1 | temporal_unit_mismatch | 8 |
| rules-v1 | temporal_reference_event_mismatch | 0 |
| rules-v1 | temporal_raw_text_mismatch | 8 |
| rules-v1 | logic_connector_mismatch | 16 |
| rules-v1 | logic_parent_mismatch | 0 |
| rules-v1 | evidence_quote_mismatch | 23 |
| rules-v1 | evidence_offset_mismatch | 23 |

## Five deterministic examples

| Trial | Family | Slices | Gold criteria | Baseline exact F1 | Error totals |
|---|---|---|---:|---|---|
| CB-SYN-V01-001 | simple_inclusion_exclusion | simple, inclusion, exclusion | 2 | empty-v1=0.000000, rules-v1=1.000000 | empty-v1=2, rules-v1=3 |
| CB-SYN-V01-009 | numeric_thresholds | numeric_threshold, comparison, inclusion | 2 | empty-v1=0.000000, rules-v1=1.000000 | empty-v1=2, rules-v1=0 |
| CB-SYN-V01-017 | temporal_constraints | temporal, duration, inclusion, exclusion | 2 | empty-v1=0.000000, rules-v1=1.000000 | empty-v1=2, rules-v1=8 |
| CB-SYN-V01-049 | and_multi_clause | logic_and, multi_clause, one_bullet_multiple_labels, inclusion | 2 | empty-v1=0.000000, rules-v1=0.000000 | empty-v1=2, rules-v1=8 |
| CB-SYN-V01-057 | or_multi_clause | logic_or, multi_clause, one_bullet_multiple_labels, inclusion | 2 | empty-v1=0.000000, rules-v1=0.000000 | empty-v1=2, rules-v1=9 |

## Reproduce

```console
criteriabench-suite data/synthetic_v0_1/manifest.json --configs empty-v1 rules-v1 --json-output suite-results.json --markdown-output suite-results.md
```

## Limitations

- All 80 cases are constructed synthetic templates, not sampled clinical-trial records.
- The reference labels were authored by one person through deterministic templates.
- The labels have not received independent review or adjudication.
- The suite is not clinical validation and must not support clinical decisions.
- The offline baselines make no LLM calls, so their scores do not measure LLM quality.
- Results characterize only this fixed case mix and do not establish external validity.
