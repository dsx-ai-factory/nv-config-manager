/*
 * SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
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

import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import { copyFile, mkdir, mkdtemp, rm, symlink, writeFile } from "node:fs/promises";
import { createRequire } from "node:module";
import { tmpdir } from "node:os";
import { dirname, join } from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";

const uiRoot = fileURLToPath(new URL("../../ui/", import.meta.url));
const requireUi = createRequire(new URL("../../ui/package.json", import.meta.url));
const eslint = join(dirname(requireUi.resolve("eslint/package.json")), "bin/eslint.js");

// Exercise the checked-in configuration with a real TypeScript project, without
// application fixtures, a Sonar server, or dependencies on the developer's config.
const lint = async (source, extension) => {
  const directory = await mkdtemp(join(tmpdir(), "nvcm-ui-sonar-"));
  try {
    await mkdir(join(directory, "src"));
    await symlink(join(uiRoot, "node_modules"), join(directory, "node_modules"), "dir");
    await copyFile(join(uiRoot, "eslint.sonar.config.mjs"), join(directory, "eslint.sonar.config.mjs"));
    await writeFile(join(directory, "tsconfig.json"), JSON.stringify({
      compilerOptions: { strict: true, skipLibCheck: true, noEmit: true, jsx: "preserve" },
      include: ["src/**/*.ts", "src/**/*.tsx"],
    }));
    const filename = `src/fixture.${extension}`;
    await writeFile(join(directory, filename), source);
    const result = spawnSync(process.execPath, [
      eslint, "--no-config-lookup", "--config", "eslint.sonar.config.mjs",
      "--format", "json", filename,
    ], { cwd: directory, encoding: "utf8", timeout: 30_000 });
    assert.ifError(result.error);
    assert.ok(result.status === 0 || result.status === 1, result.stderr);
    const [report] = JSON.parse(result.stdout);
    return { status: result.status, messages: report.messages };
  } finally {
    await rm(directory, { recursive: true, force: true });
  }
};

for (const extension of ["ts", "tsx"]) {
  test(`SonarJS accepts valid .${extension} without running normal lint rules`, async () => {
    const result = await lint(`
      export const sorted = ["z", "a"].sort((a, b) => a.localeCompare(b));
      export const double = (value: number): number => value * 2;
      const unused = 1;
      // eslint-disable-next-line @typescript-eslint/no-unused-vars
      const ignored = 2;
      // eslint-disable-next-line react-hooks/exhaustive-deps
      export const unrelated = () => 42;
      ${extension === "tsx" ? "export const View = () => <div>{sorted.join(',')}</div>;" : ""}
    `, extension);
    assert.equal(result.status, 0, JSON.stringify(result.messages));
    assert.deepEqual(result.messages, []);
  });

  test(`SonarJS reports complexity, nesting, sort, and void rules in .${extension}`, async () => {
    const result = await lint(`
      export function complex(flags: boolean[]) {
        let total = 0;
        if (flags[0]) {
          if (flags[1]) {
            if (flags[2]) {
              if (flags[3]) {
                if (flags[4]) {
                  if (flags[5]) total += 1;
                }
              }
            }
          }
        }
        return total;
      }
      export const nested = () => () => () => () => () => 1;
      const values: string[] = ["z", "a"];
      values.sort();
      const noop = (): void => {};
      void noop();
      ${extension === "tsx" ? "export const View = () => <div>Fixture</div>;" : ""}
    `, extension);
    assert.equal(result.status, 1);
    const rules = new Set(result.messages.map((message) => message.ruleId));
    for (const rule of [
      "sonarjs/cognitive-complexity",
      "sonarjs/no-nested-functions",
      "sonarjs/no-alphabetical-sort",
      "sonarjs/void-use",
    ]) {
      assert.ok(rules.has(rule), `Missing ${rule}: ${JSON.stringify(result.messages)}`);
    }
  });
}
