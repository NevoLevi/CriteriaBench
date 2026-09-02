# Leaf Logical Forms: CriteriaBench Real v1 import

This directory is generated deterministically from the pinned LLF checkout. Do not hand-edit
the artifacts. Regenerate them with `python -m criteriabench.real.llf` and verify
`manifest.sha256` plus every artifact hash recorded in `manifest.json`.

## Contents

- `records.jsonl`: all 2,000 primary cases, including 1,997 available references and three
  explicit `missing_upstream` cases.
- `generation_cases.jsonl`: the physically separate source-only model input. It contains no
  logical forms, reference hashes, augmented text, annotator metadata, or source paths.
- `generation_manifest.json`: a source-only seal for generation. It contains no reference
  availability, missing-reference identities, scorable counts, or reference-artifact hashes.
- `development_references.jsonl` and `test_references.jsonl`: physically split offline-only
  references. A development scorer never opens or deserializes locked-test logical forms.
- `agreement_annotations.jsonl`: three annotations for each of 20 sensitivity cases (60 rows).
- `source_manifest.jsonl`: byte length and SHA-256 lineage for all 2,060 upstream `.js` files.
- `split_assignments.json`: the frozen, trial-disjoint development/test assignment.
- `manifest.json` and `manifest.sha256`: import settings, audited counts, and artifact hashes.
- `ATTRIBUTION.md` and `LICENSE.upstream.txt`: required provenance and license notices.

The importer decodes only three bounded JavaScript string literals. It never executes
JavaScript; each logical-form body remains inert source text for a separate reviewed parser.
The three absent bodies are retained in the evaluation denominator rather than dropped or
fabricated. These annotations are benchmark references, not clinical advice or patient-level
eligibility decisions. Upstream `.js` and license checkout text is canonicalized from CRLF or
LF to LF before source byte counts, SHA-256 lineage, parsing, and generated output; unsupported
bare carriage returns are rejected.
