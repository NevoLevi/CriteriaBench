# CriteriaBench synthetic reference dataset v0.1

This directory contains a deterministic, template-generated evaluation set for
testing structured eligibility extraction. It is software-test material, not a
clinical dataset and not a source of patient-care guidance.

## Scope and provenance

- Version: `synthetic-v0.1`
- Size: exactly 80 cases (10 families with 8 controlled variants each)
- Origin: AI-assisted template design and label construction encoded in a
  deterministic, source-controlled generator; no patient records, personal data,
  network-sourced clinical-trial text, or proprietary corpus material
- License: MIT, matching the repository license
- Annotation status: one AI-assisted authoring workflow, with independent
  second-human and clinical-domain review/adjudication still pending
- Clinical validation: false

Every fixture records its synthetic kind, deterministic-template annotation
method, family/slices, and `independent_review_pending` status in its
`provenance` object. The manifest separately records the MIT license and
`clinical_validation=false`, and pins the exact UTF-8 JSON bytes of every case
with SHA-256 so regeneration and review can detect any drift.

The frozen manifest uses `single_author` and `deterministic_templates` for its
historical authoring workflow. Those immutable values do not mean unaided human
authorship. They are supplemented here rather than rewritten so the published
v0.1 fixture and manifest bytes remain reproducible. Generating and evaluating
the committed suite is nevertheless offline and makes no model or network calls.

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
reference criteria sharing a logic group. This exposes a controlled
segmentation/grouping failure in a one-criterion-per-bullet baseline; it is not
general evidence that a system can or cannot reason over arbitrary Boolean logic.

### Derived lineage

The corrected v0.1.1 analysis derives a lineage record for each immutable case:

- `family_id`: the manifest's family;
- `base_template_id`: one source template identifier per family; and
- `variant_id`: the controlled within-family variant, numbered 1 through 8.

This yields 10 base templates and 80 lineage records. The lineage is derived from
the ordered manifest and trial IDs; it is not written back into v0.1 fixture bytes.

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

Before claiming independently reviewed labels, at least one second human who did
not participate in the authoring workflow and an appropriate clinical-domain
reviewer should:

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
published results remain reproducible. An analysis/report correction that leaves
all dataset bytes unchanged receives a new analysis version, as v0.1.1 does; it
does not relabel the underlying dataset.

## Regeneration

From an installed development environment at the repository root:

```powershell
uv run --frozen --no-env-file python -m criteriabench.evaluation.synthetic_v01
```

The generator writes `case_001.json` through `case_080.json` and then creates
`manifest.json` from the exact rendered bytes. JSON keys and indentation are
stable across runs.

## Limitations

The set is synthetic, AI-assisted, template-shaped, small, English-only, and
intentionally narrow. It does not measure clinical validity, generalization to
real trial registries, inter-annotator agreement, fairness, safety, or production
model quality. Its slice balance is designed rather than naturally sampled. Some
labels make one defensible normalization choice where real annotation could
require a formal guideline and adjudication.

There are only 10 upper-level families/base templates, each with eight related
variants. Case-resampling can understate that dependence; the 10-family-cluster
view and leave-one-family-out results are sensitivity checks only. Resampling
cannot create missing template diversity or establish population uncertainty.
Results are engineering regression evidence, not research-grade or clinical
validation.
