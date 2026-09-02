# CriteriaBench Real v1 protocol

Status: **pre-execution protocol freeze**. The real corpus, split, parser, scorer, BM25 comparator, human-agreement analysis, guarded runner, and canonical static preregistration exist. The preregistration reproduces byte-for-byte and is committed with this protocol before any plan. The 25-case Luna canary has not run. This document contains no Real-v1 model result.

## Research question

How accurately can a low-cost language model reproduce the structural logical form attached by human annotators to real ClinicalTrials.gov eligibility criteria?

The benchmark evaluates annotation reproduction. It does not evaluate whether a patient qualifies for a trial, clinical correctness, or deployment safety.

## Frozen public corpus

Real v1 imports the Leaf Logical Forms corpus from [`uw-bionlp/leaf-corpora`](https://github.com/uw-bionlp/leaf-corpora), pinned to commit `461288aeba8b37fabd43bd7c55f0e1cb1bb10b9e`.

- 2,000 primary human-annotated criteria from 885 NCT trials;
- 1,997 available primary logical forms and three explicit `missing_upstream` references;
- 20 agreement cases with three annotations each, or 60 annotation rows; and
- 2,060 source JavaScript files in the audited upstream inventory.

Every available primary reference parses with the bounded LLF parser. The three missing references—`NCT03868891_6`, `NCT03923894_9`, and `NCT03928561_4`—are retained as operational cases and never fabricated.

The import preserves the upstream license and data attribution, the pinned revision, artifact hashes, source-file inventory, and every disclosed malformed or missing row. Agreement annotations are 60 annotations over 20 cases, not 60 independent criteria.

## Trial-disjoint split and leakage boundary

The deterministic split unit is the complete NCT trial:

| Split | Cases | Trials | Available references | Use |
|---|---:|---:|---:|---|
| Development | 200 | 86 | 200 | Parser work, prompt examples, baseline analysis, canary selection, and readiness scoring |
| Locked test | 1,800 | 799 | 1,797 | One separately authorized final evaluation only after every canary gate passes |

No trial crosses the split. All agreement trials are development-only. Trials used in frozen prompt examples—`NCT03860038`, `NCT03860324`, `NCT03862937`, and `NCT03865043`—are development-only. `NCT03925818` is also development-only because one reference was exposed during release auditing. Any future manual reference inspection must be disclosed and forced into development before a new split/version is frozen.

The files enforce the intended process boundary:

- `generation_cases.jsonl` contains only case/trial identity, polarity, source text, split, and source hash;
- `generation_manifest.json` seals source-only inputs and deliberately omits reference availability, missing-reference identities, semantic denominators, and reference-artifact hashes;
- `development_references.jsonl` and `test_references.jsonl` are physically separate;
- split-specific coverage files bind the corresponding scorer inputs; and
- `records.jsonl` and full coverage are mechanical audit inputs, not paid-generation inputs.

A paid container mounts only the source-only generation files. Development scoring later mounts development references. Locked scoring, if ever authorized, mounts locked-test references only after predictions are sealed.

Mechanical full-corpus parsing was allowed once before evaluation to prove grammar coverage and publish aggregate vocabulary and hashes. It did not call a model, select examples from model errors, or provide references to the live process.

## Task and output contract

The direct Luna lane returns one strict provider object:

```json
{"logical_form":"cond(\"example\")"}
```

The provider does **not** produce the trusted AST, source hash, case ID, score, or reference. Local code applies character/byte limits, parses the string with the bounded LLF allowlist, and constructs an identity-free, nonrecursive flat syntax tree.

Supported semantic nodes preserve:

- symbols, strings, and Boolean literals;
- attributes and ordered method chains;
- calls and positional arguments;
- tuples; and
- infix Boolean `and`/`or`.

The parser never calls `compile`, `eval`, `exec`, or `import`, and never invokes an LLF function. Every node must be allowed, bounded, connected, acyclic, reachable from one root, and represented in canonical postorder.

Canonical scoring treats only documented direct `intersect`, `union`, `and`, and `or` calls, plus infix Boolean `and`/`or`, as commutative. Method forms, `seq`, tuples, and every other structure remain ordered.

`EligibilityGraphV2` is a separate future product/evidence representation. The paid GraphV2 lane is structurally disabled; it is not silently mapped from LLF, scored against LLF references, or included in Real-v1 claims.

## Compared systems

Real v1 currently defines only two LLF-native systems:

1. `llf-bm25-nearest-development-v1`, a deterministic zero-network retrieval baseline; and
2. direct `gpt-5.6-luna`, requested once under the sealed live configuration.

BM25 uses NFKC/casefold Unicode tokenization, Okapi BM25 with `k1=1.2` and `b=0.75`, exact polarity matching, and case-ID tie breaking. A development target excludes every candidate from its own trial; a test target can retrieve only from the 200 development cases.

The direct Luna request is frozen to the official Responses endpoint, `store=false`, service tier `default`, no tools, no redirects, no HTTP proxy/environment trust, zero SDK retries, zero application retries, and sequential calls. The historical/default and locked-compatible `none` profile fixes `max_output_tokens=2048` with a 60-second application deadline. The versioned development-only `medium` profile fixes `max_output_tokens=32768` with a 240-second application deadline; it cannot advance the locked-none lane. Five prompt examples are development-only. The model sees criterion text and inclusion/exclusion kind, not reference labels or case identity.

`gpt-5.6-luna` is a requested alias, not a dated immutable weight snapshot. The run records the requested model and the returned provider model, response object, service tier, response-ID hash, usage, and latency for every successful response.

## Metrics

The primary semantic metric is **micro F1 over the combined multiset of canonical LLF nodes and edges**. Counts are summed before F1 is computed.

Mandatory secondary metrics are:

- exact canonical-tree match count and accuracy;
- node and edge precision, recall, and F1 separately; and
- calls, method attributes, symbols, strings, Booleans, and their combined typed-component precision, recall, and F1.

Operational reporting includes attempted, completed, failed, unattempted, fatal-abort, and schema-valid counts; known/unknown token usage; charged authorization consumption and usage-priced cost; p50/p95 latency; response-ID coverage and uniqueness; returned provider model/object/service-tier coverage; and safe failure categories.

Every failed scorable case becomes an empty prediction: all reference nodes and edges are false negatives. Failures are never removed from the semantic denominator. The three missing upstream references stay in operational accounting but cannot enter semantic scores.

The LLF track does not claim concept-span, patient-decision, clinical-equivalence, review-routing, or GraphV2 evidence metrics. Those require separately annotated references.

## Development evidence frozen before Luna

The deterministic BM25 baseline on all 200 development cases, excluding the target's entire trial, produces:

| Measure | Value |
|---|---:|
| Exact trees | 8 / 200 |
| Node F1 | 0.369699 |
| Edge F1 | 0.213939 |
| Combined node-plus-edge F1 | 0.293919 |

The preregistered 25-case canary subset uses 25 different development trials and excludes prompt-example trials. On exactly that subset, BM25 produces 1/25 exact, node F1 `0.259307`, edge F1 `0.142661`, typed-component F1 `0.259307`, and combined node-plus-edge F1 `0.202918`.

Human-human context comes from the 20 triple-annotation cases. Of 60 annotations, 57 parse; 54 of 60 possible pairs are available. Equal-weight case-macro results are exact `0.466667`, node F1 `0.879308`, edge F1 `0.788692`, and typed-component F1 `0.880009`; seven cases have full three-way exact consensus. These are descriptive consistency values on a selected subset, not adjudicated truth or a model ceiling.

## Canary preregistration and advancement gates

The canary is a **development-only readiness gate**, not an unbiased performance estimate. Its static preregistration binds the dataset, 25 selected cases, BM25 predictions and scores, prompt/output/parser identities, implementation hashes, container dependencies, model settings, pricing, cost reservation, and all advancement gates before a paid call.

One exact execution binding must then be published before authorization. It binds the static preregistration bytes, exact plan bytes and hash, image ID, case set, configuration, output and external-state path hashes, intended run ID, intended authorization ID, and one-execution policy.

The historical/default `none` profile reserves `USD 0.006553600` per case and `USD 0.163840000` total under an exact `USD 0.170000000` application cap, using 16,384 input and 2,048 output tokens per case. The versioned `medium` development experiment uses the same input reservation, 32,768 output/reasoning tokens, `USD 0.043417600` per case, `USD 1.085440000` total, and an exact `USD 1.250000000` cap. It uses the same 25 cases for a paired diagnostic but changes reasoning effort and the nonbinding output ceiling together; it cannot isolate those effects and is neither independent test evidence nor a production-performance estimate. The locked lane remains `none` and medium advancement into it is prohibited.

Advancement is the conjunction of every gate:

1. purpose is `development_llf_canary_25`, split is development, and terminal state is completed;
2. exactly 25 cases are attempted and completed; zero are failed, unattempted, or fatally aborted;
3. usage and latency are observed for all 25 cases;
4. all 25 have unique response IDs and complete returned provider model/object provenance consistent with the frozen contract;
5. each case has exactly one permitted attempt; SDK and application retries are zero;
6. charged authorization consumption is no more than the exact sealed profile cap;
7. p95 latency is no more than 60,000 ms;
8. combined node-plus-edge F1 is at least `0.50` and at least `0.10` above the frozen BM25 comparator; and
9. at least 2 of 25 trees are exact.

Any failed check sets the decision to **do not authorize or run the locked test**.

## Sealed one-shot execution

The paid lifecycle is fixed:

```text
static preregistration
  -> exact offline plan
  -> public one-execution binding
  -> fresh exact authorization
  -> durable external authorization claim
  -> append-only external per-ordinal attempt claims
  -> one-shot provider run
  -> network-disabled scoring
  -> sealed conjunctive decision
```

Planning, binding verification, and authorization run without network access. The API key is entered at a hidden terminal prompt immediately before execution and crosses standard input only. It is not stored in a file, environment variable, Docker argument, plan, artifact, or shell history.

The run directory and a durable authorization-state directory are separate and path-bound. The external claim and local consumption record must both be absent before first use or both exist and agree during recovery. Copying a run to a new path, deleting its output while its external claim remains, a dangling attempt claim, a mismatched pending request, a duplicate response ID, a fatal provider/configuration outcome, or any artifact tampering fails closed.

Attempts and outcomes are append-only per ordinal. A process interruption after an attempt begins is recovered as an explicit failed outcome; it cannot silently call the same case again. A fatal recovered prefix is sealed as aborted before another provider call.

Operational reruns require a new public execution binding, fresh authorization, and disclosure of all attempts. Quality failures require a new versioned configuration and new preregistration. Reauthorizing the same configuration to select a better random outcome is prohibited.

The offline scorer imports no OpenAI, HTTP, or transport code and runs with Docker networking disabled. It verifies the complete plan/binding/authorization/claim/consumption/attempt/outcome/summary chain before opening development references.

SHA-256 seals support reproducible internal lineage and tamper detection among published artifacts. Provider-returned identifiers and dashboard records remain external evidence; repository hashes are not provider attestation.

## Cost and authorization boundary

The frozen 2026-09-02 Luna rate snapshot records, per million tokens:

- uncached input: USD 0.20;
- cached input: USD 0.02;
- cache-write input: USD 0.25; and
- output: USD 1.20.

The snapshot is valid only through `2026-09-02T23:59:59Z`. A plan outside that window is invalid and pricing must be reviewed and refrozen before any request. Application reservations and usage-priced estimates are not provider invoices or account-level caps. After execution, response IDs and token usage must be reconciled independently with the provider dashboard.

The exact canary acknowledgement is:

> I authorize this exact sealed 25-case LLF semantic paid Luna canary plan.

This acknowledgement is valid only with the reviewed plan, execution binding, case-set hash, image ID, path bindings, run/authorization IDs, expiry, and the exact sealed profile cap (`USD 0.170000000` for `none` or `USD 1.250000000` for `medium`). Earlier general budget approval is not reused as the execution artifact.

## Locked-test rule

Only a `none`-profile canary PASS permits creation and review of a new locked-test plan. It does not authorize a locked run. A `medium` diagnostic receives a failing locked-profile compatibility check by design even when all operational and quality checks pass.

The current code reserves 1,800 cases under an `USD 11.800000000` application cap, but no locked plan is presently executable or authorized. The current four-hour plan and two-hour authorization lifetimes cannot cover the conservative worst case of `1,800 × 60 seconds = 30 hours`; the current price validity also expires. After a canary pass, locked execution therefore needs refreshed pricing, a redesigned mechanically sufficient validity window, a new exact plan, and separate explicit authorization.

Every locked case would need one sealed outcome or counted failure before scoring. The 1,797 available references would be scored exactly once; the three missing references would remain operational-only.

## Interpretation and future evidence

LLF is public. It may have appeared in Luna training data, and the alias can drift over time. Real v1 therefore supports a narrow claim: reproducible performance on this pinned public benchmark without runtime access to gold references. It does **not** demonstrate contamination-resistant generalization.

A future stronger extension would freeze at least 200 newly retrieved post-cutoff criteria from at least 50 trials, use independent double annotation without model outputs, obtain biomedical adjudication, publish pre-adjudication agreement, and evaluate once on a temporal holdout. That extension is not part of Real v1.
