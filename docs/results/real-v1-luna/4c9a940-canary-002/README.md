# Real-v1 Luna canary 002: preauthorization evidence

Status: **sealed and public; not authorized; not executed**.

This directory publishes byte-for-byte copies of the exact preregistration, paid-call plan,
and one-execution binding before any authorization or provider request. It contains no API
key, model response, score, or claim that the canary passed.

This packet supersedes canary 001, whose execution window expired without authorization or
execution. No artifact from canary 001 is reused as authorization for this packet.

## Bound execution

- Source commit: [`4c9a940e735043e00c42f7183a5f5d64bfaa697c`](https://github.com/NevoLevi/CriteriaBench/commit/4c9a940e735043e00c42f7183a5f5d64bfaa697c)
- Runtime image: `ghcr.io/nevolevi/criteriabench@sha256:bbd960a97d5d5d5d8ebdd08198c6a4d48182af56fa75d60417767779526ca8d8`
- Registry config/local image ID: `sha256:787c50fe0889153d36160fcaf191c4c06d076ac60c27f0a563ed9fc8df144c45`
- Build provenance: [GitHub attestation 44615785](https://github.com/NevoLevi/CriteriaBench/attestations/44615785)
- Model/API: `gpt-5.6-luna` through `POST /v1/responses`, Standard/default service tier
- Execution: 25 development cases from 25 trials, sequential, one attempt per case, no retries
- Request controls: structured output, `reasoning.effort=none`, `store=false`, no tools, 60-second request deadline
- Plan window: `2026-09-02T14:33:08Z` through `2026-09-02T18:33:08Z`
- Conservative reservation: `USD 0.163840000`; hard cap: `USD 0.170000000`
- Run ID: `cb-llf-luna-4c9a940-20260902-002`
- Authorization ID: `cb-llf-luna-auth-4c9a940-20260902-002`

The frozen rates match the official [GPT-5.6 Luna model page](https://developers.openai.com/api/docs/models/gpt-5.6-luna)
and [OpenAI API pricing](https://developers.openai.com/api/docs/pricing) reviewed on 2026-09-02:
`$0.20/M` uncached input, `$0.02/M` cached input, `$0.25/M` cache writes,
and `$1.20/M` output.

## Exact artifact seals

| Artifact | Semantic seal | File SHA-256 |
| --- | --- | --- |
| `llf-canary-preregistration.json` | `5dc461be09780c69dfdd3689e81e0a1c31530c853c57761745212e149ba7c9c4` | `228b67bf794402e24c7782c9872da1c307cfc23ecae41ff75f155d0769a95917` |
| `llf-canary-plan.json` | `e39e32f5108a15e24ebf9ee2d4f6c3caee9e8c398f4ee3651867b472a97dfb0d` | `7161611b1f6838aba4c858b94059bb1fef66efc50f1a9733db326088eaf1d8a2` |
| `execution-binding.json` | `04820fb69b77fb9b3be47dc71620a7e98b213d794c800afa222492758e448fac` | `d4fc50ddd827a5f11724c321fc9c63458ec70a5d84c6bd741c3ecee676eb69a8` |

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
