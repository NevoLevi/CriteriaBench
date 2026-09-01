# CriteriaBench synthetic reference dataset v0.1

This directory contains a deterministic, template-generated evaluation set for
testing structured eligibility extraction. It is software-test material, not a
clinical dataset and not a source of patient-care guidance.

## Scope and provenance

- Version: `synthetic-v0.1`
- Size: exactly 80 cases (10 families with 8 controlled variants each)
- Origin: locally authored deterministic templates; no patient records, personal
  data, network sources, model generations, or external clinical-trial text
- License: MIT, matching the repository license
- Annotation status: single-author template labels, with independent review still
  pending
- Clinical validation: false

Every fixture repeats this status in its `provenance` object. The manifest pins
the exact UTF-8 JSON bytes of every case with SHA-256 so regeneration and review
can detect any drift.

## Inclusion design

The suite is balanced by construction across these families:

1. simple inclusion and exclusion statements;
2. numeric thresholds;
3. temporal constraints;
4. explicit negation;
5. demographic age plus consent;
6. laboratory thresholds and units;
7. one-bullet AND clauses;
8. one-bullet OR clauses;
9. numeric ranges represented by `between`; and
10. punctuation and evidence-span variation.

Each family has eight deterministic variants. The `slices` provenance field is a
comma-separated list of evaluation attributes, and `manifest.json` reports their
aggregate counts. AND and OR cases intentionally split one source bullet into two
gold criteria sharing a logic group. This tests a structural failure mode that a
one-criterion-per-bullet rules baseline cannot solve perfectly.

## Label contract

Each `case_NNN.json` is compatible with the CriteriaBench `BenchmarkFixture`
shape:

- `trial` contains a synthetic identifier, title, eligibility text, and a null
  source URL;
- `reference` contains schema-version `1.0` inclusion/exclusion labels;
- criterion identifiers and group identifiers follow the domain-schema patterns;
- normalized operator, value, unit, temporal, negation, and logic fields use the
  repository's strict enums and validation rules; and
- `evidence.start_char:end_char` selects exactly `evidence.quote`, which also
  equals the criterion's `source_text`.

Offsets are Python Unicode code-point offsets into the decoded eligibility text.
They are computed from the completed source string, including punctuation and
Unicode symbols, rather than copied by hand.

## Independent review procedure

Before claiming reviewed or clinically meaningful labels, a reviewer who did not
write the templates should:

1. regenerate the dataset and confirm byte-for-byte equality with the committed
   cases and manifest;
2. inspect all 80 source texts for inadvertent personal information or external
   copied material;
3. validate each criterion's kind, category, concept, operator, value, unit,
   negation, temporal fields, and logic grouping against the source wording;
4. confirm every evidence quote and offset directly against `eligibility_text`;
5. record disagreements and adjudications in a separately versioned review log;
6. rerun schema, hash, formatting, and test checks; and
7. change `review_status` only in a new dataset version after review evidence is
   committed.

Review should not silently rewrite v0.1. Corrections create a new version so
published results remain reproducible.

## Regeneration

From an installed development environment at the repository root:

```powershell
uv run --frozen --no-env-file python -m criteriabench.evaluation.synthetic_v01
```

The generator writes `case_001.json` through `case_080.json` and then creates
`manifest.json` from the exact rendered bytes. JSON keys and indentation are
stable across runs.

## Limitations

The set is synthetic, template-shaped, small, English-only, and intentionally
narrow. It does not measure clinical validity, generalization to real trial
registries, inter-annotator agreement, fairness, safety, or production model
quality. Its slice balance is designed rather than naturally sampled. Some labels
make one defensible normalization choice where real annotation could require a
formal guideline and adjudication. Results on this set must therefore be reported
as engineering benchmark evidence only, never as clinical validation.
