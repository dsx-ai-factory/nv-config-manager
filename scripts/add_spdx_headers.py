#!/usr/bin/env python3
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
"""Add SPDX license headers to source files in the NVIDIA Config Manager repository."""

import os
import re
import stat
import sys
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from enum import Enum, auto
from pathlib import Path
from typing import TextIO

PYTHON_HEADER = """\
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
"""

JS_TS_HEADER = """\
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
"""

GO_HEADER = """\
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
"""

PYTHON_DIRS = [
    "src/nv_config_manager",
    "src/tests",
    "db/migrations",
    "scripts",
    "packages/dcim",
    "plugins/dcim/nautobot-2x",
    "components/nautobot",
    "packages/templates",
    "development/",
    "installer/",
]

JS_TS_DIRS = [
    "ui/src",
    "ui/tests",
]

GO_DIRS = [
    "components",
]

SKIP_PATTERNS = {
    "__pycache__",
    ".pyc",
    "node_modules",
    ".git",
    ".venv",
    "uv.lock",
}


class HeaderResult(Enum):
    """Outcome of processing a single source file."""

    ADDED = auto()
    SKIPPED = auto()
    FAILED = auto()


_PYTHON_SHORT_HEADER = re.compile(
    r"\A(?P<preamble>\#![^\n]*\n)?"
    r"(?P<header>\# SPDX-FileCopyrightText:[^\n]*\n\# SPDX-License-Identifier: Apache-2\.0\n)"
)
_JS_TS_SHORT_HEADER = re.compile(
    r"\A(?P<preamble>(?:[ \t]*(?:\"use (?:client|server)\"|'use (?:client|server)')[ \t]*;?[ \t]*\n)?)"
    r"(?P<header>// SPDX-FileCopyrightText:[^\n]*\n// SPDX-License-Identifier: Apache-2\.0\n)"
)
_GO_SHORT_HEADER = re.compile(
    r"\A(?P<preamble>(?:(?://go:build[^\n]*|// \+build[^\n]*)\n)*(?:\n)?)"
    r"(?P<header>// SPDX-FileCopyrightText:[^\n]*\n// SPDX-License-Identifier: Apache-2\.0\n)"
)


def should_skip(path: Path) -> bool:
    path_str = str(path)
    return any(skip in path_str for skip in SKIP_PATTERNS)


def has_full_header(content: str) -> bool:
    return "SPDX-License-Identifier" in content and "Licensed under the Apache License" in content


def has_short_header(content: str) -> bool:
    return (
        "SPDX-License-Identifier" in content and "Licensed under the Apache License" not in content
    )


def replace_short_header_python(content: str) -> str:
    """Replace a Python short SPDX header at the source-file preamble."""
    return _replace_short_header(content, _PYTHON_SHORT_HEADER, PYTHON_HEADER)


def replace_short_header_js(content: str) -> str:
    """Replace a JavaScript short SPDX header at the source-file preamble."""
    return _replace_short_header(content, _JS_TS_SHORT_HEADER, JS_TS_HEADER)


def replace_short_header_go(content: str) -> str:
    """Replace a Go short SPDX header at the source-file preamble."""
    return _replace_short_header(content, _GO_SHORT_HEADER, GO_HEADER)


def _replace_short_header(content: str, pattern: re.Pattern[str], header: str) -> str:
    """Replace a matched preamble header while preserving language directives."""
    match = pattern.match(content)
    if match is None:
        return content
    return (match.group("preamble") or "") + header + content[match.end("header") :]


def resolve_path_within(base_path: Path, candidate_path: Path) -> Path:
    """Resolve a non-symlink path and ensure it remains beneath a trusted directory."""
    trusted_root = base_path.resolve(strict=True)
    try:
        relative_path = candidate_path.relative_to(trusted_root)
    except ValueError as error:
        raise OSError(
            f"Path is outside trusted directory {trusted_root}: {candidate_path}"
        ) from error

    if not relative_path.parts or ".." in relative_path.parts:
        raise OSError(f"Path is outside trusted directory {trusted_root}: {candidate_path}")

    current_path = trusted_root
    for component in relative_path.parts:
        current_path /= component
        if current_path.is_symlink():
            raise OSError(f"Refusing to process symbolic link: {current_path}")

    resolved_path = candidate_path.resolve(strict=True)
    try:
        resolved_path.relative_to(trusted_root)
    except ValueError as error:
        raise OSError(
            f"Path is outside trusted directory {trusted_root}: {candidate_path}"
        ) from error
    return resolved_path


@contextmanager
def open_text_file_within(base_path: Path, candidate_path: Path) -> Iterator[TextIO]:
    """Open a regular file beneath a trusted root without following symbolic links."""
    trusted_root = base_path.resolve(strict=True)
    resolved_path = resolve_path_within(trusted_root, candidate_path)
    relative_path = resolved_path.relative_to(trusted_root)

    directory_flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    file_flags = os.O_RDWR | os.O_NOFOLLOW
    directory_fd = os.open(trusted_root, directory_flags)
    file_fd = -1
    try:
        for component in relative_path.parts[:-1]:
            next_directory_fd = os.open(component, directory_flags, dir_fd=directory_fd)
            os.close(directory_fd)
            directory_fd = next_directory_fd

        file_fd = os.open(relative_path.name, file_flags, dir_fd=directory_fd)
        if not stat.S_ISREG(os.fstat(file_fd).st_mode):
            raise OSError(f"Refusing to process non-regular file: {candidate_path}")

        source_file = os.fdopen(file_fd, "r+", encoding="utf-8")
        file_fd = -1
        with source_file:
            yield source_file
    finally:
        if file_fd >= 0:
            os.close(file_fd)
        os.close(directory_fd)


def _write_open_file(source_file: TextIO, content: str) -> None:
    """Replace the contents of an already validated and opened file."""
    source_file.seek(0)
    source_file.write(content)
    source_file.truncate()


def replace_existing_short_header(
    file_path: Path,
    source_file: TextIO,
    content: str,
    replace_header: Callable[[str], str],
) -> HeaderResult:
    """Write a normalized short header or report a header outside the preamble."""
    new_content = replace_header(content)
    if new_content == content:
        print(f"Error processing {file_path}: short SPDX header is not at the supported preamble")
        return HeaderResult.FAILED
    _write_open_file(source_file, new_content)
    return HeaderResult.ADDED


def add_header_to_python(file_path: Path, allowed_root: Path) -> HeaderResult:
    try:
        with open_text_file_within(allowed_root, file_path) as source_file:
            content = source_file.read()

            if has_full_header(content):
                return HeaderResult.SKIPPED

            if has_short_header(content):
                return replace_existing_short_header(
                    file_path, source_file, content, replace_short_header_python
                )

            if content.startswith("#!"):
                lines = content.split("\n", 1)
                new_content = lines[0] + "\n" + PYTHON_HEADER + (lines[1] if len(lines) > 1 else "")
            else:
                new_content = PYTHON_HEADER + content

            _write_open_file(source_file, new_content)
            return HeaderResult.ADDED
    except (OSError, UnicodeError) as error:
        print(f"Error processing {file_path}: {error}")
        return HeaderResult.FAILED


def add_header_to_js_ts(file_path: Path, allowed_root: Path) -> HeaderResult:
    try:
        with open_text_file_within(allowed_root, file_path) as source_file:
            content = source_file.read()

            if has_full_header(content):
                return HeaderResult.SKIPPED

            if has_short_header(content):
                return replace_existing_short_header(
                    file_path, source_file, content, replace_short_header_js
                )

            stripped = content.strip()
            if stripped.startswith(
                ('"use client"', "'use client'", '"use server"', "'use server'")
            ):
                lines = content.split("\n", 1)
                new_content = lines[0] + "\n" + JS_TS_HEADER + (lines[1] if len(lines) > 1 else "")
            else:
                new_content = JS_TS_HEADER + content

            _write_open_file(source_file, new_content)
            return HeaderResult.ADDED
    except (OSError, UnicodeError) as error:
        print(f"Error processing {file_path}: {error}")
        return HeaderResult.FAILED


def add_header_to_go(file_path: Path, allowed_root: Path) -> HeaderResult:
    try:
        with open_text_file_within(allowed_root, file_path) as source_file:
            content = source_file.read()

            if has_full_header(content):
                return HeaderResult.SKIPPED

            if has_short_header(content):
                return replace_existing_short_header(
                    file_path, source_file, content, replace_short_header_go
                )

            if content.startswith("//go:build") or content.startswith("// +build"):
                lines = content.split("\n")
                build_tag_end = 0
                for i, line in enumerate(lines):
                    if line.startswith("//go:build") or line.startswith("// +build") or line == "":
                        build_tag_end = i + 1
                    else:
                        break
                new_content = (
                    "\n".join(lines[:build_tag_end])
                    + "\n"
                    + GO_HEADER
                    + "\n".join(lines[build_tag_end:])
                )
            else:
                new_content = GO_HEADER + content

            _write_open_file(source_file, new_content)
            return HeaderResult.ADDED
    except (OSError, UnicodeError) as error:
        print(f"Error processing {file_path}: {error}")
        return HeaderResult.FAILED


def process_directory(
    base_path: Path,
    dir_path: str,
    extension: str,
    add_header_func: Callable[[Path, Path], HeaderResult],
) -> tuple[int, int, int]:
    candidate_path = base_path / dir_path
    try:
        trusted_base = base_path.resolve(strict=True)
        candidate_path = trusted_base / dir_path
        full_path = resolve_path_within(trusted_base, candidate_path)
    except FileNotFoundError:
        print(f"  Directory not found: {candidate_path}")
        return 0, 0, 0
    except OSError as error:
        print(f"  Error processing directory {candidate_path}: {error}")
        return 0, 0, 1

    if not full_path.is_dir():
        print(f"  Directory not found: {full_path}")
        return 0, 0, 0

    modified = 0
    skipped = 0
    failed = 0

    for file_path in sorted(full_path.rglob(f"*{extension}")):
        if should_skip(file_path):
            continue
        result = add_header_func(file_path, trusted_base)
        if result is HeaderResult.ADDED:
            print(f"  Added header: {file_path.relative_to(trusted_base)}")
            modified += 1
        elif result is HeaderResult.SKIPPED:
            skipped += 1
        else:
            failed += 1

    return modified, skipped, failed


def main() -> int:
    script_path = Path(__file__).resolve()
    repo_root = script_path.parent.parent

    if not (repo_root / "pyproject.toml").exists():
        print("Error: Could not find repository root")
        return 1

    print(f"Repository root: {repo_root}")
    print()

    total_modified = 0
    total_skipped = 0
    total_failed = 0

    print("Processing Python files...")
    for dir_path in PYTHON_DIRS:
        modified, skipped, failed = process_directory(
            repo_root, dir_path, ".py", add_header_to_python
        )
        total_modified += modified
        total_skipped += skipped
        total_failed += failed

    print("\nProcessing TypeScript/JavaScript files...")
    for dir_path in JS_TS_DIRS:
        for ext in (".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs"):
            modified, skipped, failed = process_directory(
                repo_root, dir_path, ext, add_header_to_js_ts
            )
            total_modified += modified
            total_skipped += skipped
            total_failed += failed

    print("\nProcessing Go files...")
    for dir_path in GO_DIRS:
        modified, skipped, failed = process_directory(repo_root, dir_path, ".go", add_header_to_go)
        total_modified += modified
        total_skipped += skipped
        total_failed += failed

    print()
    print(f"Total files modified: {total_modified}")
    print(f"Total files skipped (already had header): {total_skipped}")
    print(f"Total files failed: {total_failed}")
    if total_modified:
        print("Re-stage modified files before committing.")
    return int(total_failed > 0)


if __name__ == "__main__":
    sys.exit(main())
