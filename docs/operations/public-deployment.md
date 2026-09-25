# Public Azure deployment runbook

This runbook deploys ABDA-NL as a small public research service on Azure
Container Apps. It uses the same Python entrypoint as local and Delta runs,
but keeps persistent state in a private PostgreSQL server and exposes only the
web application over HTTPS.

The [September 6 public release](public-release-20260906.md) records the initial
rollout and public promotion. It is a historical checkpoint. Routine changes now
use the [authorized Delta CLI session](agent-driven-deployment.md), not a
manual Cloud Shell handoff after every change. Initial provisioning commands
below are reference procedures, not required next steps for the live service.

Source releases use `idaks/ABDA-NL:main`; new images are published to
`ghcr.io/idaks/abda-nl`. See the
[source and release ownership decision](../decisions/0007-service-repository-ownership-and-promotion.md).
Publishing an image does not change the live demo. Before any deployment,
read the current Azure image and settings and verify the new digest.

The initial deployment should use the generated
`*.azurecontainerapps.io` origin. The preferred permanent name is
`demo.DOMAIN` under an operator-owned domain. Cloudflare can be the registrar
and authoritative DNS provider without proxying application traffic. This
avoids depending on institutional DNS administration while retaining a clear
ABDA-NL identity.

## Architecture and responsibility boundary

The tracked Bicep modules create:

- an Azure Container Apps environment with one public web application
- a private virtual network and a private PostgreSQL Flexible Server
- a manual migration job that runs before each web deployment and provisions a
  restricted database login for the web replicas
- Log Analytics with 30 days of container log retention
- one to three web replicas, health probes, and an HTTP concurrency scale rule
- a five-connection database budget per replica, for at most 15 web
  connections on the 35-user-connection `Standard_B1ms` database

The database has no public endpoint. The administrator credential is supplied
only to infrastructure deployment and the manual migration job. Web replicas
receive a separate login with connect, schema usage, table CRUD, and sequence
permissions, but no database, role, schema, temporary-object, replication, row
security bypass, or object-ownership privileges. Production startup verifies
this boundary before accepting traffic. Azure terminates HTTPS at Container
Apps. The application also refuses production startup unless the database's
Alembic revision exactly matches the application image. It requires OIDC,
funded Foundry credentials, OpenRouter outage capacity, and registered-user
BYOK support. The Azure module selects the Container Apps proxy mode, which
uses only the platform-appended rightmost client address for anonymous rate
limits. Direct local and Delta runs ignore forwarded client headers.

The public service image is built from this public repository, smoke-tested,
and attested in GitHub Actions. Azure pulls it anonymously from GitHub
Container Registry by its immutable SHA-256 digest. Azure credentials, DNS
authority, the Auth0 tenant, funded Foundry credentials, and the OpenRouter
account remain operator-owned external resources. Do not place any of their
secrets in Git, shell history, tickets, or deployment command arguments.

## Prerequisites

Use a private operator shell with `set -x` disabled. Load secret values from the
lab's password or secret manager. The deploying identity needs Contributor on
the resource group. No Azure role assignment, private registry, or managed
pull identity is created. Install a current Azure CLI and Container Apps
extension. Bicep 0.46.1 is the version verified in CI.

Complete [the operator account and domain bootstrap](operator-service-bootstrap.md)
before creating billable Azure resources. The required external inputs are:

- an Azure subscription on which the operator has Contributor access
- Write access to the official GitHub repository for routine source and image
  releases, plus a package administrator for the one-time public visibility
  setting (repository policy changes require a repository administrator)
- an Auth0 Regular Web Application and email OTP connection
- a production email provider for Auth0
- the currently deployed CloudBank Foundry Messages endpoint, deployment name,
  and key
- an OpenRouter key whose account limit agrees with the configured hard cap
- an operator-owned domain and DNS zone when the permanent hostname is enabled

The initial operator completed these account, provider-registration, domain,
mail, Auth0, repository, and public-package prerequisites on 2026-08-28. See
the dated
[external prerequisites checkpoint](external-prerequisites-20260828.md).
Deployment still needs its own exact image, what-if review, infrastructure,
migration, application, DNS, and live acceptance evidence.

Authenticate and select the intended subscription explicitly:

```bash
az login
az account list --output table
az account set --subscription 'SUBSCRIPTION_ID_OR_NAME'
az account show --query '{name:name,id:id,tenantId:tenantId}' --output table
az extension add --name containerapp --upgrade
```

## 1. Load non-secret deployment values

These names are examples, but use one consistent set for every later step.

```bash
export ABDA_DEPLOY_RESOURCE_GROUP='abda-nl-staging'
export ABDA_DEPLOY_LOCATION='eastus2'
export ABDA_DEPLOY_PREFIX='abda-nl-stg'
export ABDA_DEPLOY_POSTGRES_ADMIN_LOGIN='abdaadmin'
export ABDA_DEPLOY_INFRA_NAME='abda-nl-stg-infra'
export ABDA_DEPLOY_MIGRATION_NAME='abda-nl-stg-migration'
export ABDA_DEPLOY_APP_NAME_DEPLOYMENT='abda-nl-stg-app'
export ABDA_DEPLOY_ENVIRONMENT='staging'
export ABDA_DEPLOY_TRIAL_ENABLED='false'
export ABDA_DEPLOY_OPENROUTER_FAILOVER_ENABLED='false'
export ABDA_DEPLOY_SERVICE_DOMAIN='REPLACE_WITH_REGISTERED_DOMAIN'
export ABDA_DEPLOY_DESIRED_HOSTNAME="demo.${ABDA_DEPLOY_SERVICE_DOMAIN}"
```

Load two independent database passwords from the secret manager. The
administrator password must be at least 16 characters and is used only by
infrastructure deployment and the migration job. The application password must
be at least 32 characters. Keep the application login name stable across
releases so a deployment does not leave an obsolete role behind.

Load both passwords with hidden prompts so neither value enters shell history:

```bash
read -rsp 'PostgreSQL administrator password: ' \
  ABDA_DEPLOY_POSTGRES_ADMIN_PASSWORD
printf '\n'
export ABDA_DEPLOY_POSTGRES_ADMIN_PASSWORD
export ABDA_DEPLOY_POSTGRES_APP_LOGIN='abda_app'
read -rsp 'Restricted application database password: ' \
  ABDA_DEPLOY_POSTGRES_APP_PASSWORD
printf '\n'
export ABDA_DEPLOY_POSTGRES_APP_PASSWORD
```

Register the providers and create the resource group:

```bash
for ABDA_DEPLOY_PROVIDER in \
  Microsoft.App \
  Microsoft.DBforPostgreSQL \
  Microsoft.Network \
  Microsoft.OperationalInsights
do
  az provider register --namespace "$ABDA_DEPLOY_PROVIDER" --wait
done

az group create \
  --name "$ABDA_DEPLOY_RESOURCE_GROUP" \
  --location "$ABDA_DEPLOY_LOCATION"
```

The earlier registration of `Microsoft.ContainerRegistry` and
`Microsoft.ManagedIdentity` can remain. Provider registration creates no
registry or identity and does not by itself incur resource charges. This
deployment does not use either provider.

## 2. Review and create the shared infrastructure

The `.bicepparam` files read `ABDA_DEPLOY_*` variables during compilation. This
keeps secret values out of tracked files and ordinary command arguments.

```bash
az deployment group what-if \
  --name "$ABDA_DEPLOY_INFRA_NAME" \
  --resource-group "$ABDA_DEPLOY_RESOURCE_GROUP" \
  --parameters deploy/azure/infra.bicepparam

az deployment group create \
  --name "$ABDA_DEPLOY_INFRA_NAME" \
  --resource-group "$ABDA_DEPLOY_RESOURCE_GROUP" \
  --parameters deploy/azure/infra.bicepparam
```

The template deliberately serializes both subnet writes and the two delegated
service attachments. Azure Network can reject parallel operations against one
virtual network with `AnotherOperationInProgress`. If a deployment encounters
that transient conflict, wait for the existing network operation to finish,
inspect the partial resources, rerun `what-if`, and use an incremental
redeployment. Do not delete a healthy partial resource group merely to retry.

Capture the authoritative outputs rather than reconstructing Azure-generated
names:

```bash
export ABDA_DEPLOY_ENVIRONMENT_NAME="$(az deployment group show \
  --name "$ABDA_DEPLOY_INFRA_NAME" \
  --resource-group "$ABDA_DEPLOY_RESOURCE_GROUP" \
  --query properties.outputs.containerAppsEnvironmentName.value --output tsv)"
export ABDA_DEPLOY_APP_NAME="$(az deployment group show \
  --name "$ABDA_DEPLOY_INFRA_NAME" \
  --resource-group "$ABDA_DEPLOY_RESOURCE_GROUP" \
  --query properties.outputs.expectedAppName.value --output tsv)"
export ABDA_DEPLOY_MIGRATION_JOB_NAME="$(az deployment group show \
  --name "$ABDA_DEPLOY_INFRA_NAME" \
  --resource-group "$ABDA_DEPLOY_RESOURCE_GROUP" \
  --query properties.outputs.migrationJobName.value --output tsv)"
export ABDA_DEPLOY_POSTGRES_HOST="$(az deployment group show \
  --name "$ABDA_DEPLOY_INFRA_NAME" \
  --resource-group "$ABDA_DEPLOY_RESOURCE_GROUP" \
  --query properties.outputs.postgresHost.value --output tsv)"
export ABDA_DEPLOY_GENERATED_ORIGIN="$(az deployment group show \
  --name "$ABDA_DEPLOY_INFRA_NAME" \
  --resource-group "$ABDA_DEPLOY_RESOURCE_GROUP" \
  --query properties.outputs.expectedPublicOrigin.value --output tsv)"
```

## 3. Publish and select one immutable image

Deploy only a committed revision whose complete CI run passed. The Docker
context excludes `.env`, local state, the paper, the requirements document,
tests, and operations documentation. Publishing is intentionally triggered by
a `service-image-*` Git tag. Ordinary commits do not publish an image or
deploy to Azure. Merge the reviewed change into official `main` first, and
run the following from a clean checkout of that exact commit:

```bash
(
set -e
test -z "$(git status --porcelain)"
export ABDA_IMAGE_REMOTE='origin'
test "$(git remote get-url "$ABDA_IMAGE_REMOTE")" = \
  'https://github.com/idaks/ABDA-NL.git'
git fetch "$ABDA_IMAGE_REMOTE" main
export ABDA_IMAGE_COMMIT="$(git rev-parse --verify HEAD)"
test "$ABDA_IMAGE_COMMIT" = "$(git rev-parse "$ABDA_IMAGE_REMOTE/main")"
export ABDA_IMAGE_TRIGGER_TAG="service-image-$(date -u +%Y%m%d-%H%M%S)"
git tag --annotate "$ABDA_IMAGE_TRIGGER_TAG" "$ABDA_IMAGE_COMMIT" \
  --message "Publish ABDA-NL service image $ABDA_IMAGE_COMMIT"
git push "$ABDA_IMAGE_REMOTE" "$ABDA_IMAGE_TRIGGER_TAG"
)
```

In the shared Delta checkout, `origin` is `idaks/ABDA-NL` and `personal` is
the retained personal repository. The remote guard above expects the shared
checkout's HTTPS URL; an SSH clone must check the equivalent official SSH URL.
Do not push new release tags to `personal`.

The `Publish service image` workflow reruns the source checks, dependency
audits, and complete non-browser test suite. It publishes one Linux AMD64 image,
pulls the exact pushed digest, smoke-tests the service, and creates a GitHub
provenance attestation. It refuses to replace an existing commit image tag.

The workflow publishes to `ghcr.io/OWNER/abda-nl`, where `OWNER` is the
lowercase owner of the repository running the workflow. After the first
successful workflow, verify anonymous registry access to the exact digest. If
GitHub created the package as private, a package administrator must open the
`abda-nl` package settings and change its visibility to Public. GitHub does not
permit a public package to be made private again. Confirm that the image
contains only public repository content before accepting that one-time change.
For the official package, use
[its settings page](https://github.com/orgs/idaks/packages/container/abda-nl/settings).
If publishing fails for lack of package access, a package administrator can
grant `idaks/ABDA-NL` Write access under **Manage Actions access**. The workflow
already requests `packages: write`; no personal registry token or Azure login
is needed for publication.

Copy the digest URI from the successful workflow summary and verify it:

```bash
export ABDA_DEPLOY_SOURCE_REPOSITORY='idaks/ABDA-NL'
export ABDA_DEPLOY_IMAGE_REPOSITORY='ghcr.io/idaks/abda-nl'
export ABDA_DEPLOY_IMAGE="${ABDA_DEPLOY_IMAGE_REPOSITORY}@sha256:COPY_64_HEX_DIGEST"
export ABDA_DEPLOY_IMAGE_SHA256="${ABDA_DEPLOY_IMAGE#*@sha256:}"
test "$(printf '%s' "$ABDA_DEPLOY_IMAGE_SHA256" | wc -c)" -eq 64
[[ "$ABDA_DEPLOY_IMAGE_SHA256" =~ ^[0-9a-f]{64}$ ]]
gh attestation verify \
  "oci://$ABDA_DEPLOY_IMAGE" \
  --repo "$ABDA_DEPLOY_SOURCE_REPOSITORY"
```

Do not deploy a tag, including the full-commit tag. Deploy only the digest URI
from a successful smoke-tested and attested workflow. Record the Git commit,
digest URI, workflow run, and attestation in the release record. The Bicep
parameters accept the public owner-scoped image repository and only the
64-character digest suffix. The application and migration job therefore deploy
the same immutable image while remaining portable between repository owners.

## 4. Configure verified-email OIDC

Follow [the Auth0 email OTP runbook](auth0-email-otp.md). Begin with the
generated origin from `ABDA_DEPLOY_GENERATED_ORIGIN` and configure this exact
callback:

```text
GENERATED_ORIGIN/auth/callback
```

Then load the non-secret Auth0 values and use a hidden prompt for the client
secret:

```bash
export ABDA_DEPLOY_OIDC_METADATA_URL='https://AUTH0_TENANT/.well-known/openid-configuration'
export ABDA_DEPLOY_OIDC_ISSUER='https://AUTH0_TENANT/'
export ABDA_DEPLOY_OIDC_CLIENT_ID='LOAD_FROM_AUTH0'
read -rsp 'Auth0 application client secret: ' \
  ABDA_DEPLOY_OIDC_CLIENT_SECRET
printf '\n'
export ABDA_DEPLOY_OIDC_CLIENT_SECRET
```

## 5. Load application secrets and provider configuration

Generate four independent random secrets once, store them in the private
operator password manager, and reuse them during ordinary redeployments.
Rotating the MCP pepper intentionally invalidates all MCP credentials. Use
hidden prompts so secret values stay out of shell history. The credit eligibility
key must remain stable for the program lifetime, including recovery images;
changing it makes retained markers unusable and startup fails closed.

```bash
read -rsp 'ABDA session secret: ' ABDA_DEPLOY_SESSION_SECRET
printf '\n'
export ABDA_DEPLOY_SESSION_SECRET
read -rsp 'ABDA MCP token pepper: ' ABDA_DEPLOY_MCP_TOKEN_PEPPER
printf '\n'
export ABDA_DEPLOY_MCP_TOKEN_PEPPER
read -rsp 'Stable credit eligibility key: ' ABDA_DEPLOY_CREDIT_ELIGIBILITY_PEPPER
printf '\n'
export ABDA_DEPLOY_CREDIT_ELIGIBILITY_PEPPER
read -rsp 'ABDA metrics bearer token: ' ABDA_DEPLOY_METRICS_TOKEN
printf '\n'
export ABDA_DEPLOY_METRICS_TOKEN

export ABDA_DEPLOY_FOUNDRY_ENDPOINT='https://RESOURCE.services.ai.azure.com/anthropic'
export ABDA_DEPLOY_FOUNDRY_OPENAI_ENDPOINT='https://RESOURCE.openai.azure.com'
export ABDA_DEPLOY_CLAUDE_DEPLOYMENT='claude-sonnet-5'
export ABDA_DEPLOY_FOUNDRY_OPUS_ENDPOINT='https://OPUS_RESOURCE.services.ai.azure.com'
read -rsp 'Scoped Opus deployment key: ' ABDA_DEPLOY_FOUNDRY_OPUS_API_KEY
printf '\n'
export ABDA_DEPLOY_FOUNDRY_OPUS_API_KEY
export ABDA_DEPLOY_GCP_PROJECT='CLOUDBANK_PROJECT'
read -rsp 'Verified funded ADC JSON: ' ABDA_DEPLOY_GCP_ADC_JSON
printf '\n'
export ABDA_DEPLOY_GCP_ADC_JSON
read -rsp 'CloudBank Foundry API key: ' ABDA_DEPLOY_FOUNDRY_API_KEY
printf '\n'
export ABDA_DEPLOY_FOUNDRY_API_KEY
read -rsp 'OpenRouter API key: ' ABDA_DEPLOY_OPENROUTER_API_KEY
printf '\n'
export ABDA_DEPLOY_OPENROUTER_API_KEY
export ABDA_DEPLOY_OPENROUTER_BUDGET_MICROUSD='500000000'
```

For the currently validated route, obtain the endpoint from the private
`ANTHROPIC_FOUNDRY_BASE_URL` or `ANTHROPIC_FOUNDRY_PROJECT_ENDPOINT` value,
the deployment name from
`ANTHROPIC_FOUNDRY_CLAUDE_SONNET_5_MODEL`, and the key from
`AZURE_OPENAI_API_KEY`. These are source names only. Never print their values
to copy them from a shared terminal.

The template supplies all public Azure and GCP routes, including the separate
Opus credential and the funded ADC secret mount. The GCP project and ADC quota
project must match the verified CloudBank billing setup. A newer model is
not selected merely because it appears in the Foundry catalog. Follow
[the model promotion runbook](model-promotion.md) after CloudBank deploys a
candidate.

## 6. Run the database migration job

Create or update the manual job with the same immutable image. The job first
runs Alembic with the administrator credential. It then creates or rotates the
application login, removes broad database and schema defaults, resets that
login's grants, and grants only the permissions required by the web service. It
converts the application password to a SCRAM verifier before sending role DDL
to PostgreSQL. The job fails if either credential is missing, shared, or
unsafe.

```bash
az deployment group what-if \
  --name "$ABDA_DEPLOY_MIGRATION_NAME" \
  --resource-group "$ABDA_DEPLOY_RESOURCE_GROUP" \
  --parameters deploy/azure/migration-job.bicepparam

az deployment group create \
  --name "$ABDA_DEPLOY_MIGRATION_NAME" \
  --resource-group "$ABDA_DEPLOY_RESOURCE_GROUP" \
  --parameters deploy/azure/migration-job.bicepparam
```

Start one execution and wait for its exact result:

```bash
export ABDA_DEPLOY_MIGRATION_EXECUTION="$(az containerapp job start \
  --name "$ABDA_DEPLOY_MIGRATION_JOB_NAME" \
  --resource-group "$ABDA_DEPLOY_RESOURCE_GROUP" \
  --query name --output tsv)"

while true; do
  ABDA_DEPLOY_MIGRATION_STATUS="$(az containerapp job execution show \
    --name "$ABDA_DEPLOY_MIGRATION_JOB_NAME" \
    --resource-group "$ABDA_DEPLOY_RESOURCE_GROUP" \
    --job-execution-name "$ABDA_DEPLOY_MIGRATION_EXECUTION" \
    --query properties.status --output tsv)"
  case "$ABDA_DEPLOY_MIGRATION_STATUS" in
    Succeeded)
      break
      ;;
    Failed|Stopped)
      echo "Migration did not succeed: $ABDA_DEPLOY_MIGRATION_STATUS" >&2
      exit 1
      ;;
    *)
      sleep 5
      ;;
  esac
done
```

Never deploy the new web image after a failed or unknown migration result.
Inspect the job execution and Log Analytics first.

## 7. Deploy the web application

Leave the custom-domain variables empty for the first deployment. This module
receives only `ABDA_DEPLOY_POSTGRES_APP_PASSWORD`; it has no administrator
database parameter or secret.

```bash
export ABDA_DEPLOY_CUSTOM_HOSTNAME=''
export ABDA_DEPLOY_CUSTOM_DOMAIN_CERTIFICATE_ID=''

az deployment group what-if \
  --name "$ABDA_DEPLOY_APP_NAME_DEPLOYMENT" \
  --resource-group "$ABDA_DEPLOY_RESOURCE_GROUP" \
  --parameters deploy/azure/app.bicepparam

az deployment group create \
  --name "$ABDA_DEPLOY_APP_NAME_DEPLOYMENT" \
  --resource-group "$ABDA_DEPLOY_RESOURCE_GROUP" \
  --parameters deploy/azure/app.bicepparam

export ABDA_DEPLOY_PUBLIC_ORIGIN="$(az deployment group show \
  --name "$ABDA_DEPLOY_APP_NAME_DEPLOYMENT" \
  --resource-group "$ABDA_DEPLOY_RESOURCE_GROUP" \
  --query properties.outputs.publicOrigin.value --output tsv)"
```

## 8. Acceptance checks

Run the executable release check from a network outside Azure. Load the metrics
token from the secret manager into the environment. The command never accepts
the token as an argument or includes it in its evidence output.

```bash
abda-nl-release-check \
  --metrics-token-env ABDA_DEPLOY_METRICS_TOKEN \
  --expected-trial-enabled false \
  --expected-openrouter-enabled false \
  --expected-openrouter-budget-microusd "$ABDA_DEPLOY_OPENROUTER_BUDGET_MICROUSD" \
  "$ABDA_DEPLOY_PUBLIC_ORIGIN"
```

The command validates the TLS certificate, plaintext HTTP behavior, liveness,
readiness, policy pages, HSTS, CSP, browser security headers, safe `/config`
content, unauthenticated metrics rejection, and authenticated trial and
OpenRouter budget metrics. It expects only the `balanced` funded profile by
default. It also requires both reservation ledgers to be idle and verifies the
exact trial and OpenRouter caps. Repeat `--expected-profile PROFILE_ID` only
after another profile has passed the documented model-promotion gate. Save the
sanitized JSON output in the release record.

Complete these browser checks with an invited email address while public signup,
trial activation, and OpenRouter fallback remain disabled:

1. Sign in using the email OTP and confirm that the account shows as verified.
2. Open an example, create a private project, reload it, and save one change.
3. Use one BYOK request, reload the tab, and confirm that the key is gone.
4. Create a read-only share link in a private window and then revoke it.
5. Create an MCP read token, use `list_projects`, revoke it, and confirm that the
   same token is rejected.

After those checks pass, enable trial activation only while Auth0 signup remains
restricted to the invited pilot. Activate one trial, confirm the $5.00 balance,
and run one funded grounded request. Enable and force-test OpenRouter fallback
separately. Return either flag to `false` immediately if its ledger or route
cannot be reconciled. Set `ABDA_DEPLOY_ENVIRONMENT=production`, enable both
flags, rerun every automated and browser check, and only then open public
signup.

## 9. Bind the operator-owned hostname

Use the operator-owned domain recorded in
`ABDA_DEPLOY_SERVICE_DOMAIN`. In Cloudflare DNS, create a direct CNAME from
`demo` to the generated Container Apps hostname and a TXT record named
`asuid.demo` with the Azure verification value. Keep the CNAME in DNS-only
mode, shown by a gray cloud. Cloudflare proxying would change the client-address
trust boundary and is not part of this deployment.

Get the exact values:

```bash
export ABDA_DEPLOY_GENERATED_HOSTNAME="$(az containerapp show \
  --name "$ABDA_DEPLOY_APP_NAME" \
  --resource-group "$ABDA_DEPLOY_RESOURCE_GROUP" \
  --query properties.configuration.ingress.fqdn --output tsv)"
export ABDA_DEPLOY_DOMAIN_VERIFICATION_ID="$(az containerapp show \
  --name "$ABDA_DEPLOY_APP_NAME" \
  --resource-group "$ABDA_DEPLOY_RESOURCE_GROUP" \
  --query properties.customDomainVerificationId --output tsv)"
```

After DNS resolves correctly, add and bind the hostname:

```bash
export ABDA_DEPLOY_CUSTOM_HOSTNAME="$ABDA_DEPLOY_DESIRED_HOSTNAME"

az containerapp hostname add \
  --name "$ABDA_DEPLOY_APP_NAME" \
  --resource-group "$ABDA_DEPLOY_RESOURCE_GROUP" \
  --hostname "$ABDA_DEPLOY_CUSTOM_HOSTNAME"

az containerapp hostname bind \
  --name "$ABDA_DEPLOY_APP_NAME" \
  --resource-group "$ABDA_DEPLOY_RESOURCE_GROUP" \
  --hostname "$ABDA_DEPLOY_CUSTOM_HOSTNAME" \
  --environment "$ABDA_DEPLOY_ENVIRONMENT_NAME" \
  --validation-method CNAME

export ABDA_DEPLOY_CUSTOM_DOMAIN_CERTIFICATE_ID="$(az containerapp show \
  --name "$ABDA_DEPLOY_APP_NAME" \
  --resource-group "$ABDA_DEPLOY_RESOURCE_GROUP" \
  --query "properties.configuration.ingress.customDomains[?name=='${ABDA_DEPLOY_CUSTOM_HOSTNAME}'].certificateId | [0]" \
  --output tsv)"
test -n "$ABDA_DEPLOY_CUSTOM_DOMAIN_CERTIFICATE_ID"
```

Add the custom callback and origin to Auth0 before changing the application's
canonical origin. Retain the generated callback during the transition. Then
redeploy `app.bicepparam` with both custom-domain variables set. The Bicep module
will preserve the managed certificate on later updates.

```bash
az deployment group create \
  --name "$ABDA_DEPLOY_APP_NAME_DEPLOYMENT" \
  --resource-group "$ABDA_DEPLOY_RESOURCE_GROUP" \
  --parameters deploy/azure/app.bicepparam
```

Repeat every acceptance check against
`https://${ABDA_DEPLOY_CUSTOM_HOSTNAME}`, then make that URL public.

## Updates and rollback

The [September 6 release](public-release-20260906.md) records the original
public promotion and its then-current image and limits. Privacy acceptance, GPL
rollout, compatible rollback and restoration, and the 100-user promotion are
complete. The numbered transition Gates are retained historical procedures,
not a requirement to create a new manual Gate for every fix. Do not replay
pilot-bound rollback or promotion scripts against the public revision.

Routine updates are agent-operated through the
[authorized Delta profile](agent-driven-deployment.md):

1. Group code changes, test locally and in CI, and publish an attested digest.
   Verify anonymous pull, provenance, and the old-to-new schema diff. Retain
   the last known healthy image as the recovery target.
2. Read the exact current app, revision, deployment state, and budget settings.
   Save a private before-state snapshot and check for conflicting operations.
   Do not assume the live image still matches an older runbook.
3. For an image-only change with unchanged schema and migration parity, update
   only the image and revision suffix. Preserve environment values, secrets,
   probes, scaling, ingress, and the promoted budgets.
4. Observe the submitted revision rather than resubmitting during provisioning.
   Check readiness, replica health, and relevant public behavior with bounded
   calls. Compare configurable settings without treating Azure's read-only
   response metadata as desired configuration.
5. If the new image fails, inspect the failure and restore the recorded
   compatible healthy image through the same image-only operation. Do not
   change budgets or restore a database to repair an image regression.
6. Record automated acceptance and batch remaining human checks. Reuse prior
   identity and project evidence when that behavior has not changed. An
   already authorized routine action does not require another operator-typed
   confirmation.

The image-only operation, after the two reviewed values have been selected,
has this shape. It is not a command to rerun for the already healthy service:

```bash
: "${ABDA_REVIEWED_IMAGE:?Set the verified ghcr.io/idaks/abda-nl@sha256 digest URI}"
: "${ABDA_REVIEWED_REVISION_SUFFIX:?Set the reviewed unique revision suffix}"
az containerapp update \
  --subscription 00e62f6e-2174-40b2-b428-8ebfd7c2ac54 \
  --resource-group abda-nl-staging --name abda-nl-stg-web \
  --container-name web --image "$ABDA_REVIEWED_IMAGE" \
  --revision-suffix "$ABDA_REVIEWED_REVISION_SUFFIX" \
  --only-show-errors --output none
```

A schema-changing release is different. Review migration and compatibility,
run the appropriate migration job, require success, then deploy and verify
the web revision. Destructive migrations or new infrastructure need separate
authority if not already approved. Do not use a full Bicep redeployment with
default parameters for a routine code rollback: defaults can reset public
settings that the image-only operation should preserve.

Treat an application database password change as a coordinated maintenance
event. The migration job rotates the role password before the new web revision
receives it, so deploy the web module immediately after the successful job and
verify readiness. Ordinary releases reuse the existing application password.

Do not run an automatic Alembic downgrade. A rollback is safe only when the
previous application version is compatible with the current schema.
Schema changes should therefore remain
backward-compatible across at least one release.

Azure PostgreSQL keeps seven days of backups in this deployment. A point-in-time
restore creates a new billable server and never overwrites the source. Use the
[PostgreSQL recovery runbook](database-recovery.md) to select a reviewed UTC
restore point, preserve private networking, validate the new server without a
public cutover, and separate restoration from any later application-secret
change. Do not overwrite or delete the original server during investigation.

If the public service fails during COMMA, keep the public status message simple,
disable new trial activations if accounting is uncertain, and use the tested
Delta or local deterministic demo. Provider outages may use the bounded
OpenRouter route. Database, identity, or accounting failures must not bypass
their safety checks.

## Primary references

- [Azure Container Apps jobs](https://learn.microsoft.com/en-us/azure/container-apps/jobs)
- [Container Apps health probes](https://learn.microsoft.com/en-us/azure/container-apps/health-probes)
- [Container Apps container registries](https://learn.microsoft.com/en-us/azure/container-apps/containers)
- [Free managed certificates](https://learn.microsoft.com/en-us/azure/container-apps/custom-domains-managed-certificates)
- [GitHub Container Registry](https://docs.github.com/en/packages/working-with-a-github-packages-registry/working-with-the-container-registry)
- [GitHub artifact attestations](https://docs.github.com/en/actions/how-tos/secure-your-work/use-artifact-attestations/use-artifact-attestations)
- [PostgreSQL private networking](https://learn.microsoft.com/en-us/azure/postgresql/flexible-server/concepts-networking-private)
- [PostgreSQL Flexible Server access management](https://learn.microsoft.com/en-us/azure/postgresql/security/security-access-control)
- [PostgreSQL default privileges](https://www.postgresql.org/docs/current/sql-alterdefaultprivileges.html)
- [Azure PostgreSQL Flexible Server limits](https://learn.microsoft.com/en-us/azure/postgresql/configure-maintain/concepts-limits)
- [Azure PostgreSQL backup and restore](https://learn.microsoft.com/en-us/azure/postgresql/backup-restore/concepts-backup-restore)
- [Azure PostgreSQL custom restore point](https://learn.microsoft.com/en-us/azure/postgresql/backup-restore/how-to-restore-custom-restore-point)
- [SQLAlchemy engine pool configuration](https://docs.sqlalchemy.org/en/20/core/engines.html)
- [Bicep parameter environment variables](https://learn.microsoft.com/en-us/azure/azure-resource-manager/bicep/bicep-functions-parameters-file)
- [Secure Bicep parameters](https://learn.microsoft.com/en-us/azure/azure-resource-manager/bicep/scenarios-secrets)
