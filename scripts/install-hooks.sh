#!/bin/bash
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
#
# Install git hooks for the nv-config-manager repository.

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
HOOKS_DIR="$REPO_ROOT/.git/hooks"
SOURCE_HOOKS_DIR="$SCRIPT_DIR/hooks"

echo "Installing git hooks..."

mkdir -p "$HOOKS_DIR"

for hook in pre-commit commit-msg; do
    if [[ -f "$SOURCE_HOOKS_DIR/$hook" ]]; then
        cp "$SOURCE_HOOKS_DIR/$hook" "$HOOKS_DIR/$hook"
        chmod +x "$HOOKS_DIR/$hook"
        echo "  ✓ Installed $hook hook"
    else
        echo "  ✗ $hook hook not found at $SOURCE_HOOKS_DIR/$hook"
        exit 1
    fi
done

echo ""
echo "Git hooks installed successfully!"
echo ""
echo "Hooks installed:"
echo "  - pre-commit: Checks opted-in commit signing, formats staged Python, checks SPDX license headers"
echo "  - commit-msg: Requires a DCO Signed-off-by trailer"
echo ""

SIGNING_ENABLED="$(git -C "$REPO_ROOT" config --local --bool --get commit.gpgsign 2>/dev/null || true)"
SIGNING_KEY="$(git -C "$REPO_ROOT" config --local --get user.signingkey 2>/dev/null || true)"
SIGNING_FORMAT="$(git -C "$REPO_ROOT" config --local --get gpg.format 2>/dev/null || true)"

if [[ "$SIGNING_ENABLED" == "true" || -n "$SIGNING_KEY" || -n "$SIGNING_FORMAT" ]]; then
    if [[ -z "$SIGNING_FORMAT" ]]; then
        SIGNING_FORMAT="openpgp"
    fi

    SIGNING_READY="false"
    if [[ "$SIGNING_ENABLED" == "true" && -n "$SIGNING_KEY" ]]; then
        case "$SIGNING_FORMAT" in
            openpgp)
                GPG_PROGRAM="$(git -C "$REPO_ROOT" config --local --get gpg.program 2>/dev/null || true)"
                if [[ -z "$GPG_PROGRAM" ]]; then
                    if command -v gpg >/dev/null 2>&1; then
                        GPG_PROGRAM="gpg"
                    elif command -v gpg2 >/dev/null 2>&1; then
                        GPG_PROGRAM="gpg2"
                    fi
                fi

                if [[ -n "$GPG_PROGRAM" ]] && command -v "$GPG_PROGRAM" >/dev/null 2>&1; then
                    SECRET_KEY_OUTPUT="$(
                        "$GPG_PROGRAM" --batch --with-colons --list-secret-keys "$SIGNING_KEY" 2>/dev/null || true
                    )"
                    if printf '%s\n' "$SECRET_KEY_OUTPUT" | grep '^sec:' >/dev/null; then
                        SIGNING_READY="true"
                    fi
                fi
                ;;
            ssh)
                SIGNING_PROGRAM="$(git -C "$REPO_ROOT" config --local --get gpg.ssh.program 2>/dev/null || true)"
                SIGNING_PROGRAM="${SIGNING_PROGRAM:-ssh-keygen}"
                if command -v "$SIGNING_PROGRAM" >/dev/null 2>&1; then
                    SIGNING_READY="true"
                fi
                ;;
            x509)
                SIGNING_PROGRAM="$(git -C "$REPO_ROOT" config --local --get gpg.x509.program 2>/dev/null || true)"
                SIGNING_PROGRAM="${SIGNING_PROGRAM:-gpgsm}"
                if command -v "$SIGNING_PROGRAM" >/dev/null 2>&1; then
                    SIGNING_READY="true"
                fi
                ;;
            *)
                echo "Unsupported cryptographic commit signing format: $SIGNING_FORMAT."
                ;;
        esac
    fi

    if [[ "$SIGNING_READY" == "true" ]]; then
        echo "Cryptographic commit signing is configured with format $SIGNING_FORMAT."
    else
        echo "Cryptographic commit signing is incomplete."
        echo "Trustees should follow the internal signing setup guidance."
    fi
    echo ""
fi

echo "To skip local hooks for a specific commit, use:"
echo "  git commit --no-verify"
