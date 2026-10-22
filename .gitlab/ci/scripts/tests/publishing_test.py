#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Exercise release destination selection and chart packages without registry writes."""

import hashlib
import json
import os
import shutil
import subprocess
import sys
import tarfile
import tempfile
import unittest
from pathlib import Path

from ruamel.yaml import YAML

ROOT = Path(__file__).resolve().parents[4]
SCRIPTS = ROOT / ".gitlab/ci/scripts"
IMAGES = (
    "nv-config-manager",
    "nv-config-manager-ui",
    "nv-config-manager-kea",
    "nv-config-manager-kea-admin",
    "nv-config-manager-nautobot",
    "nv-config-manager-nats-ready",
    "nv-config-manager-temporal",
    "nv-config-manager-temporal-bootstrap",
    "nv-config-manager-temporal-ui",
)
IMAGE_KEYS = (
    "nvConfigManager",
    "nvConfigManagerUi",
    "kea",
    "keaAdmin",
    "nautobot",
    "natsReady",
    "temporalServer",
    "temporalBootstrap",
    "temporalUi",
)
DIGEST = "sha256:" + "a" * 64
YAML_READER = YAML(typ="safe")

# Registry operations are isolated; Helm really packages each chart. The yq
# fallback evaluates the helper's assignments when Go yq is absent in CI.
TOOL = r"""
import json, os, re, shutil, subprocess, sys
from pathlib import Path
from ruamel.yaml import YAML
tool = Path(sys.argv[0]).name
args = sys.argv[1:]
root = Path(os.environ["PUBLISH_TEST_DIR"])
def option(name):
    return args[args.index(name) + 1]
if tool == "skopeo":
    if args[0] == "login":
        Path(option("--authfile")).write_text(sys.stdin.read())
    elif args[0] == "inspect":
        assert Path(option("--authfile")).read_text() == "internal-key"
        print(os.environ["MOCK_SOURCE_DIGEST"])
    elif args[0] == "copy":
        assert "--all" in args and "--preserve-digests" in args
        assert Path(option("--src-authfile")).read_text() == "internal-key"
        expected = "staging-key" if "/staging/" in args[-1] else "mirror-key"
        assert Path(option("--dest-authfile")).read_text() == expected
        if os.environ.get("MOCK_COPY_FAIL"):
            sys.exit(1)
        if "--digestfile" in args:
            Path(option("--digestfile")).write_text(os.environ.get("MOCK_COPY_DIGEST", os.environ["MOCK_SOURCE_DIGEST"]))
    else:
        raise AssertionError(args)
elif tool == "helm":
    if args[0] == "package":
        sys.exit(subprocess.call([os.environ["REAL_HELM"], *args]))
    elif args[:2] == ["registry", "login"]:
        assert sys.stdin.read().strip() == "chart-key"
    elif args[0] == "push":
        name = args[-1].removeprefix("oci://").replace("/", "_")
        shutil.copyfile(args[1], root / f"{name}.tgz")
    else:
        raise AssertionError(args)
elif tool == "yq":
    yaml = YAML(typ="safe")
    path = Path(args[-1])
    data = yaml.load(path.read_text())
    expr = args[1]
    for key, variable, suffix in re.findall(r'\.global\.images\.(\w+)\.repository = strenv\((\w+)\) \+ "([^"]+)"', expr):
        data["global"]["images"][key]["repository"] = os.environ[variable] + suffix
    for key, variable in re.findall(r'\.global\.images\.(\w+)\.tag = strenv\((\w+)\)', expr):
        data["global"]["images"][key]["tag"] = os.environ[variable]
    with path.open("w") as output:
        yaml.dump(data, output)
    sys.exit(0)
with (root / "calls.jsonl").open("a") as output:
    output.write(json.dumps({"tool": tool, "args": args}) + "\n")
"""


class PublishingTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.bin = self.root / "bin"
        self.bin.mkdir()
        self.env = {key: value for key, value in os.environ.items() if not key.startswith("NVCM_")}
        self.env.update(
            PATH=f"{self.bin}:{os.environ['PATH']}",
            PUBLISH_TEST_DIR=str(self.root),
            REAL_HELM=shutil.which("helm") or "",
            MOCK_SOURCE_DIGEST=DIGEST,
            INTERNAL_TOKEN="internal-key",
            STAGING_TOKEN="staging-key",
            MIRROR_TOKEN="mirror-key",
            CHART_TOKEN="chart-key",
            NVCM_IMAGE_TARGET="internal",
            # Deliberately choose the second target as the build source.
            NVCM_IMAGE_TARGETS=(
                "# image destinations\n"
                " staging | registry.example/staging/team | STAGING_TOKEN \n"
                "internal|registry.example/internal/team|INTERNAL_TOKEN\n"
                "mirror|mirror.example/team|MIRROR_TOKEN|username=robot\n"
            ),
        )
        for name in ("skopeo", "helm", "yq"):
            if name == "yq" and os.environ.get("NVCM_TEST_REAL_YQ") == "1":
                self.assertIsNotNone(shutil.which("yq"))
                continue
            path = self.bin / name
            path.write_text(f"#!{sys.executable}\n" + TOOL)
            path.chmod(0o755)

    def run_script(self, name: str, *args: str, success: bool = True) -> str:
        result = subprocess.run(
            ["bash", str(SCRIPTS / name), *args],
            cwd=self.root,
            env=self.env,
            capture_output=True,
            text=True,
            timeout=30,
        )
        output = result.stdout + result.stderr
        if success:
            self.assertEqual(result.returncode, 0, output)
        else:
            self.assertNotEqual(result.returncode, 0, output)
        for token in ("internal-key", "staging-key", "mirror-key", "chart-key"):
            self.assertNotIn(token, output)
        return output

    def calls(self, command: str) -> list[list[str]]:
        path = self.root / "calls.jsonl"
        records = (
            [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []
        )
        return [record["args"] for record in records if record["args"][0] == command]

    def test_rc_copies_all_images_to_all_destinations_with_separate_credentials(self) -> None:
        self.run_script("publish_image_targets.sh", "1.4.0-rc.12")
        copies = self.calls("copy")
        expected = {
            f"docker://{repository}/{image}:1.4.0-rc.12"
            for repository in ("registry.example/staging/team", "mirror.example/team")
            for image in IMAGES
        }
        self.assertEqual({args[-1] for args in copies}, expected)
        self.assertEqual(len(copies), len(expected))
        for args in copies:
            self.assertTrue(args[-2].startswith("docker://registry.example/internal/team/"))
            self.assertTrue(args[-2].endswith(f"@{DIGEST}"))
        for args in self.calls("login"):
            self.assertFalse(Path(args[args.index("--authfile") + 1]).exists())

    def test_stable_updates_latest_at_every_destination(self) -> None:
        self.run_script("publish_image_targets.sh", "1.4.0")
        copies = self.calls("copy")
        self.assertEqual(len(copies), len(IMAGES) * 4)
        self.assertEqual(sum(args[-1].endswith(":latest") for args in copies), len(IMAGES) * 2)

    def test_missing_credentials_fail_before_login_or_copy(self) -> None:
        self.env.pop("MIRROR_TOKEN")
        self.run_script("publish_image_targets.sh", "1.4.0-rc.12", success=False)
        self.assertEqual(self.calls("login"), [])
        self.assertEqual(self.calls("copy"), [])

    def test_failed_copy_stops_publication_and_cleans_credentials(self) -> None:
        self.env["MOCK_COPY_FAIL"] = "1"
        self.run_script("publish_image_targets.sh", "1.4.0-rc.12", success=False)
        for args in self.calls("login"):
            self.assertFalse(Path(args[args.index("--authfile") + 1]).exists())
        self.assertEqual(self.calls("copy"), [])

    def test_invalid_source_or_changed_destination_digest_fails(self) -> None:
        for variable in ("MOCK_SOURCE_DIGEST", "MOCK_COPY_DIGEST"):
            with self.subTest(variable=variable):
                self.env[variable] = "invalid"
                self.run_script("publish_image_targets.sh", "1.4.0-rc.12", success=False)
                self.env.pop(variable)
                self.env["MOCK_SOURCE_DIGEST"] = DIGEST

    def test_duplicate_or_empty_target_names_fail_before_publication(self) -> None:
        for record in (
            "internal|elsewhere.example/team|MIRROR_TOKEN",
            "|elsewhere.example/team|MIRROR_TOKEN",
        ):
            with self.subTest(record=record):
                self.env["NVCM_IMAGE_TARGETS"] += "\n" + record
                self.run_script("publish_image_targets.sh", "1.4.0-rc.12", success=False)
                self.assertEqual(self.calls("copy"), [])
                self.env["NVCM_IMAGE_TARGETS"] = self.env["NVCM_IMAGE_TARGETS"].rsplit("\n", 1)[0]

    def test_legacy_single_destination_needs_no_copy(self) -> None:
        self.env.pop("NVCM_IMAGE_TARGETS")
        self.env["NVCM_IMAGE_REPOSITORY"] = "registry.example/internal/team"
        self.run_script("publish_image_targets.sh", "1.4.0-rc.12")
        self.assertEqual(self.calls("login"), [])

    def make_chart(self) -> Path:
        self.assertTrue(self.env["REAL_HELM"], "Helm must be installed for chart package tests")
        chart = self.root / "nv-config-manager"
        chart.mkdir()
        (chart / "Chart.yaml").write_text(
            "apiVersion: v2\nname: nv-config-manager\nversion: 1.4.0-rc.12\nappVersion: 1.4.0-rc.12\n"
        )
        values = {
            "global": {
                "images": {
                    key: {
                        "repository": f"registry.example/internal/team/{image}",
                        "tag": "1.4.0-rc.12",
                    }
                    for key, image in zip(IMAGE_KEYS, IMAGES, strict=True)
                }
            }
        }
        with (chart / "values.yaml").open("w") as output:
            YAML_READER.dump(values, output)
        package = self.root / "nv-config-manager-1.4.0-rc.12.tgz"
        with tarfile.open(package, "w:gz") as archive:
            archive.add(chart, arcname=chart.name)
        return package

    def test_chart_destinations_get_independent_packages(self) -> None:
        package = self.make_chart()
        original = hashlib.sha256(package.read_bytes()).hexdigest()
        self.env["NVCM_CHART_TARGETS"] = (
            "oci|charts.example/internal|CHART_TOKEN\n"
            "oci|charts.example/staging|CHART_TOKEN|image_target=staging\n"
            "oci|charts.example/mirror|CHART_TOKEN|image_target=mirror\n"
        )
        self.run_script(
            "publish_chart_targets.sh", str(package), "1.4.0-rc.12", "nv-config-manager"
        )
        for name, repository in (
            ("internal", "registry.example/internal/team"),
            ("staging", "registry.example/staging/team"),
            ("mirror", "mirror.example/team"),
        ):
            with tarfile.open(self.root / f"charts.example_{name}.tgz") as archive:
                values = YAML_READER.load(archive.extractfile("nv-config-manager/values.yaml"))
                for key, image in zip(IMAGE_KEYS, IMAGES, strict=True):
                    self.assertEqual(
                        values["global"]["images"][key]["repository"], f"{repository}/{image}"
                    )
                    self.assertEqual(values["global"]["images"][key]["tag"], "1.4.0-rc.12")
        self.assertEqual(hashlib.sha256(package.read_bytes()).hexdigest(), original)

    def test_unknown_chart_image_target_prevents_push(self) -> None:
        package = self.make_chart()
        self.env["NVCM_CHART_TARGETS"] = (
            "oci|charts.example/staging|CHART_TOKEN|image_target=missing"
        )
        self.run_script(
            "publish_chart_targets.sh",
            str(package),
            "1.4.0-rc.12",
            "nv-config-manager",
            success=False,
        )
        self.assertEqual(self.calls("push"), [])

    def test_legacy_chart_lists_support_image_target_mapping(self) -> None:
        package = self.make_chart()
        self.env.update(
            NVCM_CHART_PUBLISH_TYPES="oci",
            NVCM_CHART_PUBLISH_REPOSITORIES="charts.example/staging",
            NVCM_CHART_PUBLISH_TOKEN_VARS="CHART_TOKEN",
            NVCM_CHART_PUBLISH_IMAGE_TARGETS="staging",
        )
        self.run_script(
            "publish_chart_targets.sh", str(package), "1.4.0-rc.12", "nv-config-manager"
        )
        with tarfile.open(self.root / "charts.example_staging.tgz") as archive:
            values = YAML_READER.load(archive.extractfile("nv-config-manager/values.yaml"))
            self.assertEqual(
                values["global"]["images"]["nvConfigManager"]["repository"],
                "registry.example/staging/team/nv-config-manager",
            )

    def test_charts_and_release_wait_for_all_image_destinations(self) -> None:
        jobs = YAML_READER.load((ROOT / ".gitlab/ci/release.yml").read_text())
        for name in ("helm-publish-release", "make-release"):
            self.assertIn("publish-images-release", [need["job"] for need in jobs[name]["needs"]])
        copy_job = jobs["publish-images-release"]
        self.assertIn("update-version", [need["job"] for need in copy_job["needs"]])
        self.assertEqual(copy_job["rules"], jobs["helm-publish-release"]["rules"])
        self.assertNotIn("allow_failure", copy_job)


if __name__ == "__main__":
    unittest.main()
