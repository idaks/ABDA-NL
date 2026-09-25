# ABDA-NL operations

Start with [source and release ownership](../decisions/0007-service-repository-ownership-and-promotion.md),
[public Azure deployment](public-deployment.md), and the
[agent-driven deployment handoff](agent-driven-deployment.md).

`idaks/ABDA-NL:main` is the canonical source. Publish new images to
`ghcr.io/idaks/abda-nl`; publication does not deploy them. Verify the exact image
and read the current Azure configuration before changing the live service.
The authorized Delta CLI session lets the agent execute approved routine work
directly while its credentials remain valid.

Dated checkpoints describe the release observed on that date. Their image,
schema, model catalog, and acceptance results may have been superseded.
Do not reconstruct a current command from a historical checkpoint or rewrite
its immutable commit, image digest, revision, or checksum. The
[final operator batch](final-operator-batch.md) records an earlier completed
cloud sequence, not a batch to replay against today's service.

## Service runbooks

- [Public and COMMA release checklist](release-checklist.md)
- [Requirements traceability](requirements-traceability.md)
- [Source license review](source-license-review.md)
- [Operator accounts, domains, and external services](operator-service-bootstrap.md)
- [Auth0 email OTP](auth0-email-otp.md)
- [Cloudflare root-domain redirect](cloudflare-apex-redirect.md)
- [Model evaluation and promotion](model-promotion.md)
- [Privacy access and deletion requests](privacy-requests.md)
- [PostgreSQL recovery and incident handoff](database-recovery.md)
- [Observability alerts](observability-alerts.md)
- [COMMA 2026 demonstration playbook](comma-2026-demo-playbook.md)

Reading a recovery runbook does not authorize or require a billable restore.
If an operation fails, preserve its sanitized output and exit code, inspect
what actually completed, and use a reviewed recovery procedure.

## Historical release evidence

Retain these records for their exact source, image, configuration, and
observation window. A historical rollback image is usable only after checking
compatibility with the live schema and stored data.

- [September 10 qualified-model rollout](hosted-final-rollout-result-20260910.md)
- [Safe symbol renaming](symbol-renaming-20260909.md)
- [Unified scenario editor](unified-scenario-editor-20260908.md)
- [Three-part scenario materials](scenario-materials-20260908.md)
- [Reviewed community examples](community-examples-20260908.md)
- [Create and open scenarios](scenario-library-20260908.md)
- [Conference layout repair](conference-layout-20260906.md)
- [September 6 public release](public-release-20260906.md)
- [GPL distribution checkpoint](gpl-distribution-checkpoint-20260905.md)
- [Source-security checkpoint](source-security-checkpoint-20260902.md)
- [Development source checkpoint](development-source-checkpoint-20260904.md)
- [Container security checkpoint](container-security-checkpoint-20260904.md)
- [Rate-limit retention checkpoint](rate-limit-retention-checkpoint-20260904.md)
- [Account-suspension integrity checkpoint](suspension-integrity-checkpoint-20260904.md)
- [Provider accounting integrity checkpoint](accounting-integrity-checkpoint-20260904.md)
- [Provider lifecycle checkpoint](provider-lifecycle-checkpoint-20260904.md)
- [Deterministic engine validation](deterministic-engine-validation-20260904.md)
- [Deterministic computation budget](deterministic-computation-budget-20260904.md)
- [MCP read abuse boundary](mcp-read-abuse-boundary-20260904.md)
- [Initial staging deployment](staging-deployment-record-20260828.md)
- [Funded trial pilot](staging-trial-pilot-20260828.md)
- [Early live acceptance](staging-live-acceptance-20260828.md)
- [First release candidate](staging-release-candidate-20260829.md)
- [Authenticated browser acceptance](staging-gate8-authenticated-acceptance-20260829.md)
- [OpenRouter outage drill](staging-openrouter-outage-drill.md)
- [Delta contingency](staging-delta-contingency-20260829.md)
- [Consolidated staging release](consolidated-release-acceptance-20260830.md)
- [Managed-boundary release](managed-boundary-release-acceptance-20260902.md)

Historical image labels reproduce immutable metadata. The source license
review governs new public releases, including preserved component notices.
