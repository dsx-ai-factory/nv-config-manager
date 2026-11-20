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

import { createRequire } from "node:module";
import { dirname } from "node:path";
import { fileURLToPath } from "node:url";
import sonarjs from "eslint-plugin-sonarjs";

const require = createRequire(import.meta.url);
const requireNext = createRequire(require.resolve("eslint-config-next/package.json"));

// An opt-in subset of SonarJS rules; no Sonar server or credentials are used.
export default [{
  files: ["src/**/*.ts", "src/**/*.tsx"],
  linterOptions: { reportUnusedDisableDirectives: "off" },
  languageOptions: {
    // Resolve from Next so both lint commands use the same installed parser.
    parser: requireNext("@typescript-eslint/parser"),
    parserOptions: {
      project: "./tsconfig.json",
      tsconfigRootDir: dirname(fileURLToPath(import.meta.url)),
    },
  },
  plugins: {
    sonarjs,
    // Register these definitions for existing disable comments, without enabling
    // their rules or running the normal Next/TypeScript lint configuration.
    "@typescript-eslint": requireNext("@typescript-eslint/eslint-plugin"),
    "react-hooks": requireNext("eslint-plugin-react-hooks"),
  },
  rules: {
    "sonarjs/cognitive-complexity": ["error", 15],
    "sonarjs/no-nested-functions": ["error", { threshold: 4 }],
    "sonarjs/no-alphabetical-sort": "error",
    "sonarjs/void-use": "error",
  },
}];
