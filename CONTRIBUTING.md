# Contributing to NVIDIA Config Manager

Thank you for your interest in contributing to NVIDIA Config Manager! This document provides guidelines and instructions for contributing.

## Table of Contents

- [Developer Certificate of Origin (DCO)](#developer-certificate-of-origin-dco)
- [Cryptographically Signing Commits](#cryptographically-signing-commits)
- [Getting Started](#getting-started)
- [How to Contribute](#how-to-contribute)
- [Contributing a DCIM Provider](#contributing-a-dcim-provider)
- [Pull Request Process](#pull-request-process)
- [Coding Standards](#coding-standards)
- [License](#license)

## Developer Certificate of Origin (DCO)

NVIDIA Config Manager requires the Developer Certificate of Origin (DCO) process for all contributions.

The DCO is a lightweight way for contributors to certify that they wrote or otherwise have the right to submit the code they are contributing to the project. Here is the full text of the [DCO](https://developercertificate.org/):

```text
Developer Certificate of Origin
Version 1.1

Copyright (C) 2004, 2006 The Linux Foundation and its contributors.

Everyone is permitted to copy and distribute verbatim copies of this
license document, but changing it is not allowed.

Developer's Certificate of Origin 1.1

By making a contribution to this project, I certify that:

(a) The contribution was created in whole or in part by me and I
    have the right to submit it under the open source license
    indicated in the file; or

(b) The contribution is based upon previous work that, to the best
    of my knowledge, is covered under an appropriate open source
    license and I have the right under that license to submit that
    work with modifications, whether created in whole or in part
    by me, under the same open source license (unless I am
    permitted to submit under a different license), as indicated
    in the file; or

(c) The contribution was provided directly to me by some other
    person who certified (a), (b) or (c) and I have not modified
    it.

(d) I understand and agree that this project and the contribution
    are public and that a record of the contribution (including all
    personal information I submit with it, including my sign-off) is
    maintained indefinitely and may be redistributed consistent with
    this project or the open source license(s) involved.
```

### Signing Off Your Commits

To sign off your commits, add a `Signed-off-by` line to your commit messages:

```text
This is my commit message

Signed-off-by: Your Name <your.email@example.com>
```

You can do this automatically by using the `-s` or `--signoff` flag when committing:

```bash
git commit -s -m "Your commit message"
```

If you've already made commits without signing off, you can amend your last commit:

```bash
git commit --amend -s
```

Or rebase to sign off multiple commits:

```bash
git rebase --signoff HEAD~<number_of_commits>
```

**Note:** Your sign-off must use your real name (no pseudonyms or anonymous contributions) and must match the author information in your Git configuration.

## Cryptographically Signing Commits

Trustees who want copy-pr-bot to automatically sync their ready pull requests
must configure OpenPGP, SSH, or X.509/S/MIME signing so GitHub can verify every
commit. Other contributors may cryptographically sign their commits, but doing
so does not remove the maintainer-approval step.

A `Verified` signature does not grant trustee status or authorize CI by itself.
Copy-pr-bot automatically syncs ready pull requests from configured trustees
when every commit is `Verified`. Pull requests from other contributors, and
draft pull requests, require an authorized maintainer to approve the current
commit with:

```text
/ok to test <sha>
```

Approval applies only to that exact commit. After the pull request is updated,
an authorized maintainer must approve the new commit before CI can use it.

GitHub documents how to configure each supported
[commit-signing method](https://docs.github.com/en/authentication/managing-commit-signature-verification/signing-commits).
Trustees should follow the internal setup guidance for their chosen method.
Once configured, `commit.gpgsign=true` signs new commits automatically.

The pre-commit hook recognizes all three supported formats, but signing checks
are opt-in. The hook remains silent unless repository-local
`commit.gpgsign=true`, `user.signingkey`, or `gpg.format` settings indicate that
signing is being used. Contributors who do not rely on trustee auto-sync are
therefore not asked to configure a signing key. When opted in, the check reports
incomplete local setup without blocking the commit. It cannot determine whether
GitHub recognizes the key, so confirm the `Verified` status after pushing.

Verify a new commit locally and confirm that GitHub displays `Verified` after it is
pushed:

```bash
git log --show-signature -1
```

To re-sign every commit on an existing pull-request branch, first make sure the
branch is clean, then run:

```bash
git fetch origin
base="$(git merge-base origin/main HEAD)"
git rebase --exec 'git commit --amend --no-edit -S' "$base"
git push --force-with-lease
```

This rewrites commit IDs. Coordinate with anyone else using the branch before
force-pushing it.

## Getting Started

1. **Fork the repository** on GitHub
2. **Clone your fork** locally:
   ```bash
   git clone https://github.com/YOUR_USERNAME/nv-config-manager.git
   cd nv-config-manager
   ```
3. **Set up the development environment**:
   ```bash
   # Install uv (Python package manager)
   curl -LsSf https://astral.sh/uv/install.sh | sh

   # Install dependencies
   uv sync --dev

   # For UI development
   cd ui && npm install
   ```
4. **Install git hooks** (required for contributions):
   ```bash
   ./scripts/install-hooks.sh
   ```
   This installs:

   - `pre-commit`, which checks opted-in commit signing, auto-formats staged
     Python files outside ignored/generated directories with `ruff format`, and
     verifies SPDX license headers in supported source files.
   - `commit-msg`, which rejects commit messages missing a DCO
     `Signed-off-by: Name <email>` trailer.

   The installer reports signing readiness only when repository-local signing
   settings are present. Trustees who rely on automatic sync should follow the
   internal setup guidance for a GitHub-supported signing method.

5. **Create a branch** for your changes:
   ```bash
   git checkout -b feature/your-feature-name
   ```

## How to Contribute

### Reporting Bugs

- Use the GitHub issue tracker to report bugs
- Describe the bug clearly with steps to reproduce
- Include version information and environment details
- Attach relevant logs or screenshots if applicable

### Suggesting Enhancements

- Use the GitHub issue tracker for feature requests
- Clearly describe the proposed enhancement and its use case
- Discuss potential implementation approaches if you have ideas

### Submitting Code Changes

1. Ensure your code follows the project's coding standards
2. Write or update tests as needed
3. Update documentation if applicable
4. Sign off all commits as described above
5. Submit a pull request

### Contributing a DCIM Provider

DCIM providers are standalone packages that depend on the provider-neutral SDK
rather than on Config Manager service code. Follow the [DCIM provider
contribution guide](docs/development/contributing-dcim-provider.mdx) for the
entry-point, Pydantic model, event, render-data, and test contract.

## Pull Request Process

1. **Ensure all tests pass** before submitting:
   ```bash
   # Python tests
   uv run pytest

   # Linting
   uv run ruff check .
   uv run mypy src/

   # UI tests
   cd ui && npm run lint && npm run test:e2e:ci
   ```

2. **Update the README.md** or documentation if your changes affect usage

3. **Follow the PR template** and provide a clear description of changes

4. **Address review feedback** promptly and constructively

5. **Ensure your PR**:
   - Has a clear title and description
   - References any related issues
   - Has all commits signed off
   - Passes CI checks

## Coding Standards

### Python

- Follow [PEP 8](https://pep8.org/) style guidelines
- Use type hints for all function signatures
- Write docstrings in Google style format
- Run `uv run ruff check` and `uv run mypy` before committing
- Target Python 3.13+

The repository's Ruff baseline and pinned version are defined in the root
`pyproject.toml`. The installer extends that configuration from
`installer/pyproject.toml`, uses the same pinned Ruff version, and adds only its
package-specific test exclusions. Keep shared settings such as the Python
target, line length, enabled rules, ignores, and formatter behavior in the root
configuration so they cannot drift between the two packages.

Run Ruff through each package's `uv` environment from the repository root:

```bash
# Root package
uv run ruff format --check src/
uv run ruff check src/

# Installer package
uv run --project installer ruff format --check installer/src/ installer/tests/
uv run --project installer ruff check installer/src/ installer/tests/
```

Lists where order is insignificant (a registry or lookup table where entries
are read by name, not by position — e.g. `REGISTERED_WORKFLOWS` in
`src/nv_config_manager/temporal/ngc/workflows/__init__.py`) should stay
alphabetically ordered for readability. Wrap those in `# keep-sorted start` /
`# keep-sorted end` comments, enforced by
[keep-sorted](https://github.com/google/keep-sorted), pinned via
`KEEP_SORTED_VERSION` in the root `Makefile`. `make lint` checks the order;
`make format` fixes it. Do not add these markers to lists where order carries
meaning (execution order, priority, migration sequence, etc.) — sorting those
would silently change behavior.

### TypeScript/JavaScript (UI)

- Follow the ESLint configuration in the project
- Use TypeScript for all new code
- Follow React best practices and hooks patterns
- Run `npm run lint` before committing

### Go

- Follow standard Go formatting (`go fmt`)
- Use meaningful variable and function names
- Write tests for new functionality

### General Guidelines

- Keep commits atomic and focused
- Write clear, descriptive commit messages
- Add tests for new functionality
- Update documentation as needed
- Don't introduce unnecessary dependencies

## License

By contributing to NVIDIA Config Manager, you agree that your contributions will be licensed under the Apache License 2.0. See the [LICENSE](LICENSE) file for details.

All contributions must:
- Include the appropriate SPDX license identifier in new source files
- Be signed off using the DCO process described above
- Not include code from incompatible licenses without prior approval
### SPDX License Headers

All source files must include SPDX license headers. The pre-commit hook will check for these automatically.

**Python files** (`.py`):
```python
# SPDX-FileCopyrightText: Copyright (c) 2024-2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
# http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
```

**TypeScript/JavaScript/Go files** (`.ts`, `.tsx`, `.js`, `.go`):
```typescript
/*
 * SPDX-FileCopyrightText: Copyright (c) 2024-2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
 * SPDX-License-Identifier: Apache-2.0
 *
 * Licensed under the Apache License, Version 2.0 (the "License");
 * you may not use this file except in compliance with the License.
 * You may obtain a copy of the License at
 *
 * http://www.apache.org/licenses/LICENSE-2.0
 *
 * Unless required by applicable law or agreed to in writing, software
 * distributed under the License is distributed on an "AS IS" BASIS,
 * WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
 * See the License for the specific language governing permissions and
 * limitations under the License.
 */
```

To automatically add headers to all source files, run:
```bash
uv run python scripts/add_spdx_headers.py
```

---

Thank you for contributing to NVIDIA Config Manager!
