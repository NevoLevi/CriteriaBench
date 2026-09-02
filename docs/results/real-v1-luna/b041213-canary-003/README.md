# LLF prompt v1.1 Luna canary 003: preauthorization evidence

This directory publishes the exact preregistration, paid-call plan, and one-execution binding
before authorization or any provider request. No authorization, API credential, model output,
score, or result existed when this packet was committed.

## Question and scope

Can the same gpt-5.6-luna configuration improve on canary 002 when only the LLF instructions are
clarified? The provider schema, parser identity, five examples, selected 25 development cases,
reasoning effort (none), request limits, no-retry policy, scorer, BM25 comparator, and quality
thresholds remain fixed. The changed prompt adds a compact grammar card for exact source literals,
criterion-section polarity, call versus method roles, composition, attachment, contraindication,
temporal wrappers, and stable/unstable direction.

The 25 cases are deliberately identical to canary 002 for a paired engineering comparison. Their
earlier errors informed the prompt, so this is post-hoc development evidence and not an unbiased
estimate of generalization or locked-test performance. Even a passing result will require fresh,
untouched validation before any locked-test execution is considered.

## Public implementation and image

- Source commit: b041213dda36ebf8405320c4e14b465142559970
- Contract: llf-semantic-ast-v1.1-prompt-card
- Prompt SHA-256: bc1ac1fe6fed66e2560d64b7c3b92b8e61f6e23c96bb080747e3d1a594637243
- Provider schema SHA-256: ab7cf39603900057b25d005b9722953987f3212ae6d10ab5cd9d6ef059fd8f15
- Parser identity SHA-256: 2bf8081cd7dc1e57ecb2a561295b283b63a1330c1b4b70142e9aa2991accbb38
- Execution implementation SHA-256:
  5a288084da08f02bbc578c89ce78ef03a7f5766a606943f974b60abfbb6f06ea
- Image tag: ghcr.io/nevolevi/criteriabench:sha-b041213
- Registry manifest digest:
  sha256:7eef0b9173fd82f779f3364de269062925c6aee3a6b35f5f83f77598be4169ce
- Image config ID:
  sha256:7e70bd221894f830760e232d9432ebb076a23552c855fb83617d257f897db9f8
- Scanned image archive SHA-256:
  f4c74be4ec32152b79ccf5c3a873a5fc624c733dc0e6921baf9ae6b89df104b4
- GitHub Actions publish run:
  <https://github.com/NevoLevi/CriteriaBench/actions/runs/33654965609>
- Build attestation:
  <https://github.com/NevoLevi/CriteriaBench/attestations/44762137>

CI, Windows canonical replay, CodeQL, container build, and the high/critical vulnerability scan
completed successfully for the source commit.

## Sealed execution identities

- Preregistration semantic SHA-256:
  6e6b630a73326a0260d52ff66988f20fda9f3db616bfbb4bf56c7ae541e2d777
- Preregistration file SHA-256:
  7eca0c2d9826f90a945e5deb1fc9391ec2c758b0499e8a07909367e9b28521d4
- Plan semantic SHA-256:
  c3b79d6cddb682df042eda1588b1bc46bc0c3a7fd375d4e92bfc542f41a17dcb
- Plan file SHA-256:
  34251701777129bbf24bf469b3493833cfd79675f6a596705a1e84577dedad36
- Execution-binding semantic SHA-256:
  7bd22fc624b8100b38793967a860a7d648ee13f0b51aa6aa0b593672163d653d
- Execution-binding file SHA-256:
  6ec5db049bd18fb3f28462857987b048065eb31d83f4e312daf530b3871ae473
- Selected case-set SHA-256:
  675c19d64172aa4d9545dbff2232664025bf8cd3aca45622f76f03cc0add432e
- Run ID: cb-llf-luna-b041213-20260902-003
- Authorization ID: cb-llf-luna-auth-b041213-20260902-003
- Plan window: 2026-09-02T16:35:09Z through 2026-09-02T20:35:09Z
- Conservative reservation: USD 0.163840000
- Hard application cap: USD 0.170000000

The reservation is deliberately conservative and is not a forecast or provider invoice. Canary
002's usage-priced calculation was USD 0.007031750, but this run may differ.

## Authorization boundary

The exact acknowledgement required after independent review of this public packet is:

I authorize this exact sealed 25-case LLF semantic paid Luna canary plan.

That acknowledgement authorizes only this exact plan, binding, run ID, authorization ID, image,
case set, paths, validity window, and hard cap. It does not authorize a retry, a locked test, or
another configuration. The previous canary-002 authorization was consumed and cannot be reused.
