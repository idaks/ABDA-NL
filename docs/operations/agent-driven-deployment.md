# Agent-driven deployment handoff

Status (2026-09-06): the operator completed device-code login with Azure CLI
2.90.0 in `/u/haoyang/.local/share/abda-azure/cli`. The agent independently
verified the exact identity and successfully read the live application,
deployment and job status, private PostgreSQL boundary, alert definitions,
and the configured Foundry account's deployment inventory. Routine Azure
operations no longer require the operator to relay Cloud Shell commands.

Local verification: shell syntax, eight isolated helper tests, and Ruff passed.
Before the operator logged in, the real CLI correctly reported no signed-in
account. The subsequent successful Azure reads establish live access in
addition to the local helper tests. They do not promise indefinite login
validity or authorize unrelated Azure resources.

## Why change the workflow

Routine development must not require the operator to copy a new Cloud Shell
script for every fix or repeat manual browser acceptance after unchanged
identity or project behavior. Local and CI tests should cover iterations;
the agent should deploy a tested candidate, inspect bounded diagnostics, and
complete automated acceptance once it has an authorized execution channel.
Human product acceptance should be batched around a complete candidate.

GitHub currently tests and publishes images but has no Azure deployment login.
The browser's Cloud Shell session is not available to the Delta agent. Provider
API keys do not authorize Azure Resource Manager operations. The near-term
bridge is an operator-initiated Azure CLI login in a dedicated Delta profile,
without creating a service principal, assigning a new role, contacting
CloudBank, or changing the hosted architecture.

## Authority and credential storage

This is a development operator session, not a permanent production identity.
It inherits the signed-in user's real Azure permissions. In the earlier
operator receipt those permissions included subscription-level Contributor.
Selecting the ABDA subscription or specifying a resource group does not narrow
that RBAC authority. The approved work remains ABDA-NL in `abda-nl-staging`;
unrelated resources and role assignments are out of scope. Existing trial and
OpenRouter ceilings are not permission to increase budgets or create arbitrary
billable infrastructure. Source releases now use the official `idaks/ABDA-NL`
`main` branch, as described in the
[ownership decision](../decisions/0007-service-repository-ownership-and-promotion.md).
Azure login does not itself authorize a source release or deployment.

Azure CLI stores its Linux MSAL cache in plaintext. The dedicated profile
is outside the repository at `~/.local/share/abda-azure/config`, with mode 700
directories and a mode-077 umask. Other processes running as the same Unix
user, and system administrators, may still access it. Never copy this cache to
GitHub Actions, log it, archive it with release evidence, or send it in chat.
The CLI's telemetry and file logging are disabled for this profile. This does
not disable Azure's service-side audit records.

## One interactive authorization

Run this in an ordinary Delta terminal as the repository owner, not in Azure
Cloud Shell and not directly in a laptop's local shell:

```bash
bash /u/haoyang/ABDA-NL/deploy/azure/agent-azure-session.sh login
```

Open the Microsoft page displayed by the command in your own browser. Enter
the device code from your terminal, sign in as `hliu2@cloudbank.org`, and
complete MFA there. This authorizes the private Delta CLI session. The helper
selects and verifies the exact tenant, subscription, enabled state, and user.
It does not query secrets or change any Azure resource. Success ends with:

```text
result: ABDA_AGENT_AZURE_SESSION_READY
```

Tell the agent that login succeeded; no code or token needs to be copied into
chat. If tenant policy rejects device-code login, stop and provide only the
non-secret error code. Do not disable MFA or Conditional Access. Session
expiration or sign-in policy may require another interactive login later;
this is not a promise of indefinitely unattended user authentication.

To check the cached account identity:

```bash
bash /u/haoyang/ABDA-NL/deploy/azure/agent-azure-session.sh status
```

This identity check is not proof of a still-valid access token or current cloud
permissions. The agent must make a bounded, read-only Azure request before
deployment work. To sign out the dedicated CLI profile:

```bash
bash /u/haoyang/ABDA-NL/deploy/azure/agent-azure-session.sh logout
```

Logout does not cancel subscriptions or remove resources. It clears this local
CLI session, not every Microsoft browser session or a previously issued token
already held by another process.

## Agent execution after login

First verify the session and read the current Azure application, jobs, image,
budgets, and deployment state. Do not assume the operator has not already run
one of the previously supplied gates. An existing cloud operation must be
observed rather than duplicated. Keep secrets in protected process state and
temporary files, and return only content-free receipts.

For approved commands, set the dedicated environment explicitly:

```bash
export AZURE_CONFIG_DIR=/u/haoyang/.local/share/abda-azure/config
export PATH=/u/haoyang/.local/share/abda-azure/cli/bin:$PATH
export AZURE_CORE_COLLECT_TELEMETRY=false
export AZURE_LOGGING_ENABLE_LOG_FILE=false
export AZURE_EXTENSION_USE_DYNAMIC_INSTALL=no
```

These are convenience settings, not an RBAC sandbox. The agent owns routine
test, build, image-update, polling, diagnostic, and compatible-recovery work
within the approved service boundary. Existing script confirmation strings
are not a requirement for a human to type every deployment command; the
agent may supply them when the underlying action is already authorized and
the exact target has been verified. A changed budget, real-user deletion,
destructive database migration, new identity grant, or unrelated resource
requires separate authority when not already included in the user's request.

Automated browser checks do not justify bypassing verified-email identity in
the public service. Use isolated test fixtures for CI and an authorized test
session for live authenticated checks, if available. Reuse prior live identity
evidence when the relevant behavior has not changed. Ask for human browser
acceptance in one batch, not after every image revision.

Long-term unattended CI deployment should use a workload identity with narrow
resource permissions. GitHub OIDC can provide that, but assigning its Azure
role needs authority that the observed Contributor role does not grant.
Do not turn that longer-term improvement into a new blocker for the current
development session or copy a personal refresh token into CI.

## Primary references

- [Azure CLI interactive and device-code login](https://learn.microsoft.com/en-us/cli/azure/authenticate-azure-cli-interactively)
- [Azure CLI configuration and isolated profiles](https://learn.microsoft.com/en-us/cli/azure/azure-cli-configuration)
- [MSAL cache storage on Linux](https://learn.microsoft.com/en-us/cli/azure/msal-based-azure-cli)
- [GitHub Actions Azure OIDC](https://learn.microsoft.com/en-us/azure/developer/github/connect-from-azure-openid-connect)
