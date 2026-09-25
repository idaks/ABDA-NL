# Decision 0007: Service repository ownership and promotion

Date: 2026-08-27. Updated: 2026-09-25.

## Status

Accepted. Official source promotion is complete. Organization-owned image
publication and Azure deployment are separate steps.

## Context

The hosted service was developed in `Liu-Hy/ABDA-NL:development` while the
paper demo stayed in `idaks/ABDA-NL`. On September 15, the official `main`
branch received the complete reviewed service at commit `77197ce0`.
The earlier paper artifact remains available at `comma-2026-paper-snapshot`.
The paper's repository URL stays valid.

## Decision

1. `idaks/ABDA-NL:main` is the canonical source for the demo and future releases.
   Develop on a branch in that repository, run CI, and merge through a pull
   request. The personal `development` branch is retained for recovery; it is
   no longer the release target or a mirror that must be kept in sync.
2. In the shared Delta checkout, `origin` names `idaks/ABDA-NL` and `personal`
   names `Liu-Hy/ABDA-NL`. Push new work and `service-image-*` tags to `origin`.
   Use a separate worktree when the local demo still uses the older checkout.
3. The image workflow derives `ghcr.io/OWNER/abda-nl` from its repository owner.
   Official releases therefore publish to `ghcr.io/idaks/abda-nl`. Each image
   has a full-commit tag, a fixed digest, source labels, and a build attestation.
   The workflow refuses to overwrite an existing commit image.
4. Repository Write access is sufficient for the current release workflow.
   A package administrator handles the one-time Public visibility setting and,
   if needed, grants the official repository workflow access to the package.
   Routine releases do not require organization administrator approval under
   the current repository rules. Future branch policies may add review rules.
5. GitHub publication does not deploy to Azure. After a successful official
   publication, verify anonymous pull and provenance before changing the web
   app and persistent migration job to the verified digest. Preserve all
   non-image settings and the live schema. Do not run a migration for an
   ownership-only change.
6. Keep the personal repository and its current public image available until
   the official image has been deployed and checked. Record the live image
   before switching so it remains a known recovery target.

## Operating boundary

Use the [public deployment runbook](../operations/public-deployment.md) for
image publication and the
[agent deployment handoff](../operations/agent-driven-deployment.md) for Azure
access. Azure credentials, the domain, accounts, user data, and provider funding
are not transferred by a GitHub source or image release.

Dated deployment scripts and release records retain their original source
URLs, digests, and checksums. They are historical evidence, not templates to
rewrite or replay. For current changes, use the reusable deployment templates
and a fresh snapshot of the actual Azure configuration.
