# GitLab CI Variables

The GitLab pipeline is intentionally split into local includes under
`.gitlab/ci/`. Public-safe linting and tests should run in GitHub Actions.
Internal GitLab jobs keep private details behind CI/CD variables so the checked
in pipeline does not expose registry paths, scanner
credentials, scanner policy locations, deployment targets, or downstream
repository paths.

The mirror pipeline must not create Git tags or commits in the mirrored
repository. Default-branch updates run validation only: builds, tests, and
private scans. Releases happen only when an upstream semver tag is mirrored into
GitLab. Tag pipelines build and scan the tagged commit, then publish external
artifacts using `CI_COMMIT_TAG` as the version. The bundled Nautobot apps are
published to the GitLab PyPI package registry from the mirrored tag.
Release tags must be Docker-compatible semver such as `1.2.3` or
`1.2.3-rc.1`; tags with a `v` prefix or `+build` metadata are rejected by the
release pipeline.

Configure sensitive variables as masked and protected where possible. Configure
non-secret internal names as protected variables if they should not appear in
the public repository.

## Required For Internal Build And Release

| Variable | Purpose |
| -------- | ------- |
| `NVCM_IMAGE_TARGETS` | Newline-delimited image targets, one `name\|repository_prefix\|token_variable` record per target |
| `NVCM_IMAGE_TARGET` | Optional default image target for build/publish jobs; the first `NVCM_IMAGE_TARGETS` record is used when unset |
| `NVCM_VALUES_IMAGE_TARGET` | Image target name used when publishing charts and updating downstream deployment values |
| `NVCM_IMAGE_REPOSITORY` | Legacy/default image repository prefix; when `NVCM_IMAGE_TARGETS` is set, jobs derive this from the selected image target |
| `NVCM_NGC_API_BASE` | Base registry API URL |
| `NVCM_NGC_ORG` | Registry organization used by tag existence checks |
| `NVCM_NGC_TEAM` | Registry team used by tag existence checks |
| `NVCM_CHART_TARGETS` | Newline-delimited chart targets, one `type\|repository\|token_variable\|options` record per target |
| Token variables referenced by target records | Registry credentials used for configured image and chart targets |
| `CI_PUSH_TOKEN` | Token allowed to push CI-generated commits, chart branches, and package uploads |
| `CI_PUSH_EMAIL` | Commit email used by CI-generated commits |

Image and chart target records keep each publish target on one line. Token
fields are variable names, not token values, so each target can use a
masked/protected token without embedding the secret in a list.

Example image target layout:

```text
NVCM_IMAGE_TARGETS
internal|registry.example.com/example/team|REGISTRY_TOKEN

NVCM_VALUES_IMAGE_TARGET
internal
```

Example chart target layout:

```text
NVCM_CHART_TARGETS
oci|registry.example.com/example/components|CHART_REGISTRY_TOKEN
```

Supported optional chart target options are `org`, `registry`, `username`,
and `label`.

`deploy/helm/values.yaml` uses `registry.example.com/nvidia/...` placeholders
for checked-in service image repositories. Internal publishing and deployment
jobs rewrite those repositories from `NVCM_IMAGE_TARGETS` before packaging
charts or updating downstream values.

## Runner Tags And Image Overrides

| Name | Purpose |
| ---- | ------- |
| `dca` | Literal GitLab runner site/pool tag used by internal jobs |
| `linux/amd64` | Literal GitLab runner architecture tag for amd64 jobs |
| `linux/arm64` | Literal GitLab runner architecture tag for native arm64 image builds |
| `docker` | Literal GitLab runner capability tag for jobs that need Docker-capable runners |
| `NVCM_CI_IMAGE_ALPINE` | Alpine CI image |
| `NVCM_CI_IMAGE_AWS_CLI` | AWS CLI CI image |
| `NVCM_CI_IMAGE_DOCKER` | Docker CLI CI image |
| `NVCM_CI_IMAGE_DOCKER_DIND` | Docker-in-Docker service image |
| `NVCM_CI_IMAGE_GO` | Go CI image |
| `NVCM_CI_IMAGE_NODE` | Node.js CI image |
| `NVCM_CI_IMAGE_PLAYWRIGHT` | Playwright CI image |
| `NVCM_CI_IMAGE_PYTHON_311_BOOKWORM` | Python 3.11 Debian CI image |
| `NVCM_CI_IMAGE_PYTHON_313` | Python 3.13 CI image |
| `NVCM_CI_IMAGE_SONAR` | Sonar scanner image |
| `NVCM_CI_IMAGE_UBUNTU` | Ubuntu CI image |

## Internal Package Mirrors

| Variable | Purpose |
| -------- | ------- |
| `NVCM_APT_MIRROR` | Optional Ubuntu package mirror URL passed into Docker builds |
| `NVCM_APT_MIRROR_DEBIAN` | Optional Debian package mirror URL passed into Docker builds |
| `NVCM_APT_MIRROR_GPG_KEY_URL` | Optional APT mirror GPG key URL |

## Security And Quality Scans

| Variable | Purpose |
| -------- | ------- |
| `FORCE_PULSE_SCAN` | Set to `true` to force image builds and Pulse scan jobs |
| `PULSE_NSPECT_ID` | Pulse project or engagement identifier |
| `SSA_CLIENT_ID` | Service account client ID used by Pulse scanner authentication |
| `SSA_CLIENT_SECRET` | Service account client secret used by Pulse scanner authentication |
| `NV_CONFIG_MANAGER_CONTAINER_SCAN_POLICY_TOKEN` | Token that can read the internal container scan policy file |
| `NVCM_CONTAINER_SCAN_POLICY_PROJECT` | URL-encoded GitLab project path for the internal scan policy project |
| `NVCM_CONTAINER_SCAN_POLICY_FILE` | URL-encoded internal scan policy file path |
| `NVCM_CONTAINER_SCAN_POLICY_REF` | Ref for the internal scan policy file, defaults to `main` |
| `SONAR_HOST_URL` | SonarQube URL |
| `SONAR_TOKEN` | Token authorized to analyze the SonarQube project and export its report |
| `NVCM_SONAR_PROJECT_KEY` | SonarQube project key; configure as a protected CI/CD variable |

SonarQube runs on every mirrored default-branch commit as an informational,
post-merge report. Both Sonar jobs intentionally use `allow_failure: true` and
do not gate upstream code. The default-branch Python test jobs run before every
scan so all configured coverage reports are available; the scanner skips an
analysis rather than replacing valid dashboard coverage with 0% if an expected
artifact is missing. Generated Go API clients under `bindings/go/` are excluded
from Sonar analysis.

Pulse scan jobs use the versioned Pulse `scan-images` CI/CD component. The
component scans each published image from the selected `NVCM_IMAGE_TARGETS`
repository for both `linux/amd64` and `linux/arm64`, mints a fresh SSA token at
scan time, and applies the configured internal policy file fetched from
`NVCM_CONTAINER_SCAN_POLICY_PROJECT`. The matrix also scans the exact pinned
upstream oauth2-proxy image shipped by the Helm chart on both architectures.

## Test-Environment Promote Pipeline (test / test01)

The GitOps promote flow (`pr-build.yml` + `promote-test-envs.yml`) builds a PR
once into immutable artifacts (a versioned OCI Helm chart plus images referenced
by digest) and promotes them by committing a machine-written `deploy-state.yaml`
to the environment's branch in the downstream ArgoCD values repository. It
targets ONLY the shared test environments; production stays on the tag-driven
release flow.

Security model: after copy-pr-bot's `/ok to test` gate, the image build runs
automatically on the unprotected `pull-request/<n>` mirror ref with no secrets
(no registry login; images are handed over as job artifacts). A push webhook
independently creates a request pipeline from the protected `main`
configuration; no credential is handed to PR-controlled YAML. The environment
buttons and credentialed promote jobs run only from that trusted configuration
and only operate on the finished artifacts. Because of that split, **every
secret CI/CD variable in this project and its groups must be flagged
Protected** - unprotected variables are visible to the untrusted
`pull-request/*` builds. Never protect `pull-request/*` refs.

| Variable | Purpose |
| -------- | ------- |
| `NV_CONFIG_MANAGER_VALUES_PUSH_TOKEN` | Token with write access to the downstream values repository; protected + masked |
| `NVCM_VALUES_REPO_PATH` | Downstream values repository path |
| `NV_CONFIG_MANAGER_VALUES_REPO_URL` | Optional full downstream values repo override |
| `NVCM_MIRROR_API_TOKEN` | Project access token (Reporter, `read_api`) used to verify the source pipeline/job and artifact jobs; protected + masked |
| `NVCM_TEST_ENV_TARGETS` | One record per env: `env\|env_branch\|namespace\|release_name\|baseline_values\|state_dir\|argocd_application` (see `scripts/test_env_config.sh`) |
| `NVCM_CHART_REPO` | Helm repo URL ArgoCD reads the promoted chart from, e.g. `https://helm.ngc.nvidia.com/nvidian/cfa` (must match the `ngc` target in `NVCM_CHART_TARGETS`); written into deploy-state as `chartRepo` |
| `NVCM_ARGOCD_SERVER` | ArgoCD API base URL used by the post-deployment health gate |
| `NVCM_ARGOCD_AUTH_TOKEN` | Protected, masked, and hidden token for a read-only ArgoCD role allowed to `get` only the shared test Applications; disable **Expand variable reference** and confirm the saved variable remains masked |
| `NVCM_ARGOCD_APPLICATION_NAMESPACE` | Optional namespace containing the shared test Applications; uses the deployment default when unset |
| `NVCM_ARGOCD_PROJECT` | Required ArgoCD project containing the shared test Applications |
| `NVCM_ARGOCD_SYNC_TIMEOUT` / `NVCM_ARGOCD_POLL_INTERVAL` | Optional health-gate tuning in seconds; defaults to 1800 / 10. The sync timeout may be lowered but must not exceed 1800, preserving headroom under the job's 35-minute timeout; the poll interval must be greater than zero |
| `NVCM_UPSTREAM_GITHUB_REPO` | Optional override for the upstream GitHub repo checked by the stale-HEAD guard (default `dsx-ai-factory/nv-config-manager`) |

The ArgoCD token should come from a project role with only this policy:

```text
p, proj:<project>:nvcm-promoter, applications, get, <project>/<test-application>, allow
```

Add the policy line once for each shared test Application, substituting
the deployment's project and Application names.

The observer cannot start or terminate an ArgoCD operation. Keep its token
available only to the protected promotion pipeline; never expose it to a PR
build job. The target Application must use multiple sources and automated sync;
the observer only reports convergence and never starts the rollout itself.

Runbooks:

- **Deploy a PR**: a vetted `pull-request/<n>` mirror sync automatically starts
  the no-secrets build and a protected `main` request pipeline named for the
  PR. Select **promote-to-test** or **promote-to-test01**; there is no separate
  **Run pipeline** step and no variables to enter. The runnerless button creates
  a same-project child pipeline, which waits for the build if necessary. The
  request validator does not occupy a runner while the PR build runs, and the
  child is intentionally triggered without a status-mirroring strategy so a
  promotion failure does not replace the default branch's ref status. The
  small request validator still fails visibly for a malformed or superseded
  webhook event; the newer sync creates the later request pipeline. The
  protected promotion verifies
  the webhook payload, original build pipeline, PR SHA, trusted button job,
  and environment, then pushes images (capturing digests) and publishes the
  chart as
  `0.0.0-pr<n>.<sha>`, validates the render against the environment's baseline
  + overrides, commits the deploy-state, and succeeds only after ArgoCD reports
  that exact chart and env-branch commit as `Synced` and `Healthy` following a
  successful operation. The gate is read-only; if rollout does not converge,
  it reports the last observed state for investigation in ArgoCD or Kubernetes.
  Re-vet and sync a newer PR snapshot when a fresh build is needed.
  - What deploys is the *vetted snapshot*, which can lag the PR's live HEAD
    (untrusted authors re-copy only on `/ok to test`). If they differ, the run
    warns and proceeds; to deploy newer commits, re-vet them first. Set
    `NVCM_PROMOTE_REQUIRE_PR_HEAD=true` to hard-fail instead when the snapshot
    lags PR HEAD.
  - The run refuses a closed PR, and **fails closed if GitHub can't be reached**
    to confirm the PR is open. Set `NVCM_PROMOTE_ALLOW_UNVERIFIED_PR_STATE=true`
    to override during a GitHub outage.
- **Rollback** (run a pipeline on the default branch): set only
  `NVCM_PROMOTE_ENV`, start `test-rollback-env`. Without
  `NVCM_ROLLBACK_TO` it lists recent deploy-states and fails; re-run with
  `NVCM_ROLLBACK_TO=<env-branch commit sha>` to restore that exact snapshot -
  chart version, digests, and the baseline values that state was rendered
  against. Both the deploy-state and the baseline snapshot are taken from that
  one commit, so the restore does not depend on the values repo's `main`.
- **Free a slot** (run a pipeline on the default branch): set only
  `NVCM_PROMOTE_ENV`, start `test-release-env`. It
  resets the env's deploy-state, overrides and baseline snapshot to the
  canonicals on the values repo's `main`.

### Where the baseline lives

ArgoCD refuses a multi-source Application that references one repository at two
revisions, so the appset cannot read baseline values from the values repo's
`main` while reading overrides from the env branch. Everything it renders comes
from a single revision - the env branch.

The promote pipeline therefore **snapshots** the baseline onto the env branch,
in the same commit as `deploy-state.yaml`. `main` stays the source of truth
(humans edit it via MR); the snapshot is what deploys, and it refreshes on every
promote or release.

Two consequences worth knowing:

- A baseline change merged to the values repo's `main` does not reach an
  environment until the next promote or release copies it across.
- `deploy-state.yaml`'s `baselineRevision` is **provenance only** - it records
  which `main` SHA the snapshot came from. Nothing renders from it.

Project settings required (GitLab UI):

- Maximum artifacts size ≥ 1.5 GB (the build hands images over as artifacts).
- Pull mirroring must replicate `pull-request/*` branches.
- Only `main` and release-tag patterns protected; `pull-request/*` unprotected.
- Create a pipeline trigger token whose owner can run pipelines on protected
  `main`. Store it only in a **Push events** project webhook with URL
  `https://<gitlab>/api/v4/projects/<project-id>/ref/main/trigger/pipeline?token=<token>`.
  Select **Wildcard pattern** with `pull-request/*`; do not enable pipeline
  events. No trigger token is exposed as a CI/CD variable.
- On GitLab Self-Managed, the administrator must allow webhook requests to the
  instance's own hostname (Settings > Network > Outbound requests), or add that
  hostname to the local-request allowlist.
- Protect the `test` and `test01` GitLab environments and grant deploy access
  only to the people or groups allowed to use the promotion buttons.
- The **values repo** (`NVCM_VALUES_REPO_PATH`) must allow this project in its
  inbound **job token allowlist**. `test-promote-chart` reads the baseline and
  overrides with `CI_JOB_TOKEN` rather than the push token, since it only reads;
  without the allowlist entry that clone fails with an auth error. Only the
  deploy-state / rollback / release jobs use
  `NV_CONFIG_MANAGER_VALUES_PUSH_TOKEN`, and only they write.

## Air-Gapped Bundles

| Variable | Purpose |
| -------- | ------- |
| `AWS_ACCESS_KEY_ID` | AWS access key for air-gapped bundle uploads |
| `AWS_SECRET_ACCESS_KEY` | AWS secret key for air-gapped bundle uploads |
| `NVCM_AIRGAPPED_S3_BUCKET` | S3 bucket for air-gapped bundles |
| `NVCM_AIRGAPPED_S3_REGION` | AWS region for air-gapped bundle uploads |
