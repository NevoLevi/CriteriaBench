# Real-v1 Luna canary 001: preauthorization evidence

Status: **sealed and public; not authorized; not executed**.

This directory publishes byte-for-byte copies of the exact preregistration, paid-call plan,
and one-execution binding before any authorization or provider request. It contains no API
key, model response, score, or claim that the canary passed.

## Bound execution

- Source commit: [`4c9a940e735043e00c42f7183a5f5d64bfaa697c`](https://github.com/NevoLevi/CriteriaBench/commit/4c9a940e735043e00c42f7183a5f5d64bfaa697c)
- Runtime image: `ghcr.io/nevolevi/criteriabench@sha256:bbd960a97d5d5d5d8ebdd08198c6a4d48182af56fa75d60417767779526ca8d8`
- Registry config/local image ID: `sha256:787c50fe0889153d36160fcaf191c4c06d076ac60c27f0a563ed9fc8df144c45`
- Build provenance: [GitHub attestation 44615785](https://github.com/NevoLevi/CriteriaBench/attestations/44615785)
- Model/API: `gpt-5.6-luna` through `POST /v1/responses`, Standard/default service tier
- Execution: 25 development cases from 25 trials, sequential, one attempt per case, no retries
- Request controls: structured output, `reasoning.effort=none`, `store=false`, no tools, 60-second request deadline
- Plan window: `2026-09-02T06:29:58Z` through `2026-09-02T10:29:58Z`
- Conservative reservation: `USD 0.163840000`; hard cap: `USD 0.170000000`
- Run ID: `cb-llf-luna-4c9a940-20260902-001`
- Authorization ID: `cb-llf-luna-auth-4c9a940-20260902-001`

The frozen rates match the official [GPT-5.6 Luna model page](https://developers.openai.com/api/docs/models/gpt-5.6-luna)
and [OpenAI API pricing](https://developers.openai.com/api/docs/pricing) reviewed on 2026-09-02:
`$0.20/M` uncached input, `$0.02/M` cached input, `$0.25/M` cache writes,
and `$1.20/M` output.

## Exact artifact seals

| Artifact | Semantic seal | File SHA-256 |
| --- | --- | --- |
| `llf-canary-preregistration.json` | `5dc461be09780c69dfdd3689e81e0a1c31530c853c57761745212e149ba7c9c4` | `228b67bf794402e24c7782c9872da1c307cfc23ecae41ff75f155d0769a95917` |
| `llf-canary-plan.json` | `1e897bccd3f236201bfda59a9e2a085cf4814bf939752b1f3529f05255900932` | `48a9fe2a1fa8404bc216a299cc53a6b0997add27d1feee8e6f2f2c8b8ae640fb` |
| `execution-binding.json` | `78c07bb1c4331a3a17a2a42c362df02295b3165b5513a4d7c94c9bace0accd7a` | `37a93ec84a547d3230cefbbe9042fb293d4ea6154480e732cec2267e7c2eefad` |

Selected 25-case set: `675c19d64172aa4d9545dbff2232664025bf8cd3aca45622f76f03cc0add432e`.

The binding permits at most one execution, prohibits optional stopping, and requires a new
public binding plus fresh authorization for an operational rerun. A quality failure requires
a new versioned configuration and preregistration. The locked test remains unavailable unless
every frozen canary advancement gate passes.

## Verification

```text
docker buildx imagetools inspect ghcr.io/nevolevi/criteriabench:sha-4c9a940
gh attestation verify oci://ghcr.io/nevolevi/criteriabench@sha256:bbd960a97d5d5d5d8ebdd08198c6a4d48182af56fa75d60417767779526ca8d8 --repo NevoLevi/CriteriaBench --signer-workflow NevoLevi/CriteriaBench/.github/workflows/publish.yml --source-digest 4c9a940e735043e00c42f7183a5f5d64bfaa697c --source-ref refs/heads/main --deny-self-hosted-runners
```
