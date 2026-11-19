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

import { dirname } from "node:path";
import { fileURLToPath } from "node:url";
import parser from "@typescript-eslint/parser";
import sonarjs from "eslint-plugin-sonarjs";

import eslintConfig from "./eslint.config.mjs";

// An opt-in subset of SonarJS rules; no Sonar server or credentials are used.
export default [...eslintConfig, {
  files: ["src/**/*.ts", "src/**/*.tsx"],
  languageOptions: {
    parser,
    parserOptions: {
      project: "./tsconfig.json",
      tsconfigRootDir: dirname(fileURLToPath(import.meta.url)),
    },
  },
  plugins: { sonarjs },
  rules: {
    "sonarjs/cognitive-complexity": ["error", 15],
    "sonarjs/no-nested-functions": ["error", { threshold: 4 }],
    "sonarjs/no-alphabetical-sort": "error",
    "sonarjs/void-use": "error",
  },
}];
