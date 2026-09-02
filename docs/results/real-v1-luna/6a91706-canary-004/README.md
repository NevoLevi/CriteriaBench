# LLF prompt v1.1 Luna medium canary 004: preauthorization evidence

This directory publishes the exact preregistration, paid-call plan, and one-execution binding
before authorization or any provider request. No authorization, API credential, model output,
score, or result existed when this packet was committed.

## Question and scope

Can gpt-5.6-luna with medium reasoning improve the LLF semantic-structure result on the same 25
development cases used by canary 003? The prompt, provider schema, parser, selected cases, scorer,
BM25 comparator, no-retry policy, and quality thresholds remain fixed. The profile changes from
reasoning effort `none` to `medium`; its non-binding output ceiling rises from 2,048 to 32,768
tokens and its request timeout rises from 60 to 240 seconds so the experiment does not constrain
the requested reasoning mode. The production advancement latency gate remains 60 seconds.

The repeated cases make this a paired, post-hoc development diagnostic. Earlier outcomes informed
the experiment, so it is not an unbiased estimate of generalization, not locked-test evidence, and
cannot establish that reasoning effort alone caused any difference because the output ceiling and
hard timeout also changed. Even a passing result requires fresh, untouched validation before a
locked-test execution is considered.

## Public implementation and image

- Source commit: 6a91706c4a5f054a474313ca8897f7707a57b865
- Contract: llf-semantic-ast-v1.1-prompt-card
- Prompt SHA-256: bc1ac1fe6fed66e2560d64b7c3b92b8e61f6e23c96bb080747e3d1a594637243
- Provider schema SHA-256: ab7cf39603900057b25d005b9722953987f3212ae6d10ab5cd9d6ef059fd8f15
- Parser SHA-256: 2bf8081cd7dc1e57ecb2a561295b283b63a1330c1b4b70142e9aa2991accbb38
- Execution implementation SHA-256:
  16ccc174f09cf440ef65bd05102aab177e9694c0a1151f45fd4074e89f01d9d9
- Immutable runtime image:
  `ghcr.io/nevolevi/criteriabench@sha256:954ea5fb97c500b825acf778677a6ef935d66bf4c9ed3c400075151449057f13`
- Image config ID:
  `sha256:1f7dda8d9ce56672f99ca7963d07da511d8cabc2cd30b49b273caadc299187b1`
- Scanned image archive SHA-256:
  20346dbb0008289fedee60f12a8cc6cfa531d42d12427cea2355944a89f62464
- Published artifact ZIP SHA-256:
  e6e6b57f506a395f72fc72856dfbb1e31427f33352021a0b23d60ead1b1b6c48
- GitHub Actions publish run:
  <https://github.com/NevoLevi/CriteriaBench/actions/runs/33670982271>
- Build attestation:
  <https://github.com/NevoLevi/CriteriaBench/attestations/44800310>

The attestation subject is the runtime manifest digest above; the separate registry attestation
artifact is not a runtime image digest. The published image reports the exact source revision and
runs as the non-root `app` user. The publish workflow recorded successful Sigstore signing and
Rekor upload.

## Sealed execution identities

- Preregistration semantic SHA-256:
  062c02acf0c18d13b966b55639f95f6a83a7f10c299262cbc7d63b3eea790ed5
- Preregistration file SHA-256:
  4ad69dc65efe092eacd19adf683ffffb6cdc9c9df2f25dc069f8b5c518b52541
- Plan semantic SHA-256:
  30b615ce292788ffd2c5142b1290416258df708d9d292bf2afe340a3d46b2d0f
- Plan file SHA-256:
  4416b3a1f10ff82458726488fa81a95b1aa342a9b727a3b1f50aa11df51c64f8
- Execution-binding semantic SHA-256:
  11a8e3643cd3883cea82d7a56d442bfdf4350a17af364eaa3ac9c1678c892180
- Execution-binding file SHA-256:
  caee15029e1c3e10f07aec821056055846d56a96af7e8f387878e676b90704e5
- Selected case-set SHA-256:
  675c19d64172aa4d9545dbff2232664025bf8cd3aca45622f76f03cc0add432e
- Run ID: cb-llf-luna-medium-6a91706-20260902-004
- Authorization ID: cb-llf-luna-medium-auth-6a91706-20260902-004
- Plan window: 2026-09-02T19:20:05Z through 2026-09-02T23:20:05Z
- Profile: medium reasoning, 32,768 maximum output tokens, 240-second request timeout
- Attempts: one per case, zero application retries, zero SDK retries
- Provider controls: default service tier, no tools, `store=false`
- Conservative input reservation: 16,384 tokens per case
- Conservative reservation per case: USD 0.043417600
- Conservative reservation for 25 cases: USD 1.085440000
- Hard application cap: USD 1.250000000

The reservation uses USD 0.20/M uncached input tokens, USD 0.25/M cache-write input tokens,
USD 0.02/M cached input tokens, and USD 1.20/M output/reasoning tokens. It is deliberately
conservative and is not a forecast or provider invoice. The pricing record is valid through
2026-09-02T23:59:59Z, after the sealed plan expires.

## Authorization boundary

The binding permits exactly one execution, prohibits optional stopping, and binds the exact run
ID, authorization ID, image, case set, output path scope, authorization-state path scope, validity
window, and hard cap. Any rerun requires a new public execution binding, fresh authorization, and
disclosure of all attempts. A quality failure requires a new versioned configuration and new
preregistration. This packet does not authorize a locked test or another configuration.
