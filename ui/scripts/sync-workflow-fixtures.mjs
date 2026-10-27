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
import {
  mkdirSync,
  readFileSync,
  readdirSync,
  unlinkSync,
  writeFileSync,
} from "node:fs";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const uiRoot = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const canonicalDirectory = resolve(
  uiRoot,
  "../packages/workflows/tests/fixtures/workflow_summaries"
);
const browserDirectory = resolve(uiRoot, "src/mocks/data/workflows/fixtures");
const canonicalFiles = readdirSync(canonicalDirectory).filter((file) =>
  file.endsWith(".json")
);

mkdirSync(browserDirectory, { recursive: true });
for (const file of readdirSync(browserDirectory)) {
  if (file.endsWith(".json") && !canonicalFiles.includes(file)) {
    unlinkSync(join(browserDirectory, file));
  }
}
for (const file of canonicalFiles) {
  writeFileSync(
    join(browserDirectory, file),
    readFileSync(join(canonicalDirectory, file))
  );
}
