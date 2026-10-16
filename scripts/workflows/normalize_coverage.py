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
"""Map installed workflow-package coverage paths back to repository sources."""

import argparse
from pathlib import Path
from xml.etree import ElementTree

_PACKAGE_DIRECTORY = "nv_config_manager_workflows"
_SOURCE_PREFIX = Path("packages/workflows/src")


def normalize_coverage(report: Path, repository: Path) -> None:
    """Rewrite installed-package paths as Sonar-resolvable repository paths."""
    tree = ElementTree.parse(report)
    root = tree.getroot()
    classes = root.findall(".//class")
    if not classes:
        raise ValueError(f"coverage report contains no classes: {report}")

    for class_element in classes:
        filename = class_element.get("filename")
        if filename is None:
            raise ValueError("coverage class is missing its filename")
        parts = filename.replace("\\", "/").split("/")
        try:
            package_index = parts.index(_PACKAGE_DIRECTORY)
        except ValueError as error:
            raise ValueError(
                f"coverage path is outside {_PACKAGE_DIRECTORY}: {filename}"
            ) from error

        source_path = _SOURCE_PREFIX.joinpath(*parts[package_index:])
        if not (repository / source_path).is_file():
            raise ValueError(f"coverage source does not exist: {source_path}")
        class_element.set("filename", source_path.as_posix())

    for package_element in root.findall(".//package"):
        package_name = package_element.get("name", "")
        package_index = package_name.find(_PACKAGE_DIRECTORY)
        if package_index == -1:
            raise ValueError(f"coverage package is outside {_PACKAGE_DIRECTORY}: {package_name}")
        package_element.set("name", package_name[package_index:])

    sources = root.find("sources")
    if sources is None:
        sources = ElementTree.SubElement(root, "sources")
    for source in list(sources):
        sources.remove(source)
    ElementTree.SubElement(sources, "source").text = "."

    ElementTree.indent(tree, space="\t")
    tree.write(report, encoding="utf-8", xml_declaration=True)


def main() -> None:
    """Normalize the requested Cobertura report."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("report", type=Path)
    parser.add_argument("--repository", type=Path, required=True)
    args = parser.parse_args()
    normalize_coverage(args.report, args.repository.resolve())


if __name__ == "__main__":
    main()
