# Agent Instructions

## Scope and Credentials

Agents may inspect the repository, propose and implement focused code or
documentation changes, and run relevant local validation for the requested task.
Preserve unrelated work and report what changed, checks run, and remaining gaps.

Publishing releases, deploying to shared environments, changing repository
permissions, rewriting shared Git history, and accessing production data are
outside routine code-editing scope. Obtain explicit authorization for those
operations. Never bypass review, signing, or CI approval controls.

Never commit credentials, tokens, private keys, production configuration, or
customer data. Use placeholders in examples and the existing secret injection
mechanisms. Do not print credentials in logs or copy them into prompts, issues,
or test artifacts. Report suspected exposure privately using [SECURITY.md](SECURITY.md);
rotation and history remediation require coordination with the credential owner.

## Project Map and Validation

- `src/nv_config_manager/` and `src/tests/`: Python services and tests.
- `ci/tests/`: build, container, and CI tooling tests, run with `make test-ci`.
- `packages/` and `plugins/`: shared libraries and DCIM integrations.
- `installer/`: deployment configuration, CLI, TUI, and installer tests.
- `ui/`: Next.js UI and Playwright tests.
- `build/`, `components/`, and `deploy/helm/`: containers and deployment assets.
- `.github/workflows/` and `.gitlab/ci/`: public validation and mirrored CI.
- `docs/`: Fern pages; see [docs/README.md](docs/README.md) for publishing and checks.

Use [CONTRIBUTING.md](CONTRIBUTING.md) for coding and review requirements,
[README.md](README.md#testing) for test commands, and [RELEASE.md](RELEASE.md)
for the release process. Run checks appropriate to the changed component;
integration tests require a deployed environment. Do not claim unrun checks passed.

Place new build, container, and CI tooling tests in `ci/tests/` and service tests
in `src/tests/`. Keep tooling tests independent of application fixtures and
application coverage.

## Release Notes

For user-facing features, behavior or API changes, configuration changes, fixes,
security changes, and upgrade requirements, update both [CHANGELOG.md](CHANGELOG.md)
and the matching [Fern release notes](docs/fern/changelog/) in the same PR.
Keep the release scope, availability, compatibility notes, and required operator
actions consistent between the two resources.

- **Root changelog:** Add work in progress under `Unreleased`, then finalize the
  `## X.Y.Z` section before release promotion. The promotion workflow extracts
  GitHub release notes from these headings; preserve that format.
- **Fern changelog:** Maintain one announcement per release, with full details
  under nested Added, Changed, Fixed, and Security headings as applicable.
  Update the existing announcement as the release evolves. Use verified dates
  and clearly identify unreleased or candidate versions; do not imply stable
  availability before promotion.
- **Feature tags:** Tag the affected areas, such as `deployment`, `dhcp`,
  `security`, `telemetry`, and `workflows`. These are examples, not an exclusive
  list. Reuse existing tags when they fit and add clear new feature tags as
  needed, avoiding synonyms for existing tags. Keep version numbers and release
  status in the announcement headings rather than using them as filter tags.
  When releases share a dated file, use per-announcement tags so their feature
  labels remain distinct.

Before handing off release-note changes, check both resources for omissions and
contradictions, then run `make docs-lint`, `make docs-lint-fern`, and
`git diff --check`. Internal-only changes do not need release notes unless they
affect user-visible behavior.

## Python Imports

All imports MUST be placed at the top of the file, following standard Python conventions (stdlib, third-party, local). Do NOT place imports inside functions, methods, or conditional blocks.

The only exception is when a top-level import would cause a **circular dependency**. In that case, add a brief comment explaining why.

```python
# Correct — imports at the top
from datetime import UTC, datetime

from kubernetes.client import CoreV1Api

from nv_config_manager_installer.schema import NVConfigManagerInstallConfig


def do_something():
    v1 = CoreV1Api()
    ...

# Wrong — import buried inside a function
def do_something():
    from kubernetes.client import CoreV1Api
    v1 = CoreV1Api()
    ...

# Exception — circular dependency with comment
def get_app():
    from nv_config_manager_installer.tui.app import NVConfigManagerApp  # avoid circular import
    return NVConfigManagerApp()
```

## Python Tooling

Always use `uv run` to execute Python tools and utilities (ruff, pytest, mypy, etc.) inside this repository. Do NOT invoke them directly or via `python -m`.

```bash
# Correct
uv run ruff check src/
uv run ruff format src/
uv run pytest tests/
uv run mypy src/

# Wrong
ruff check src/
python -m ruff check src/
python -m pytest tests/
```

## Helm Charts (`deploy/helm/`)

When running `helm template` for testing or dry-run validation, always use `values-ci.yaml`:

```bash
helm template test . --values values-ci.yaml
```

Do NOT use `values.yaml` alone — it requires secrets/vault paths that aren't populated outside of a real deployment.

To test with the observability stack enabled, layer `values-observability.yaml` on top:

```bash
helm template test . --values values-ci.yaml --values values-observability.yaml
```

## Generated API Artifacts

The OpenAPI specifications in `docs/api-specs/` and Go clients in `bindings/go/` are generated by `make api-generate`.

Do not edit generated files in `bindings/go/<service>/` directly. Fix the source schema or application metadata when the API contract is wrong. Fix reusable Go client generation behavior in `scripts/openapi-generator-go.patch`, which patches the templates from the pinned OpenAPI Generator image. Then run `make api-generate` and commit all resulting specification and binding changes.

Code review findings in generated code are still actionable when they reveal a schema or generator-template defect. Apply the correction at its source and retain or add a non-generated regression test under `bindings/go/internal/` when practical.
