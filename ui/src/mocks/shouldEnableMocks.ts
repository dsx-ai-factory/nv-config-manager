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

export interface MockGateInputs {
  hasWindow: boolean;
  bypassWindowFlag: boolean;
  bypassEnvFlag: string | undefined;
  nodeEnv: string | undefined;
}

const TRUTHY_ENV_VALUES = new Set(["true", "1", "yes", "on"]);

function isTruthyEnvFlag(value: string | undefined): boolean {
  if (!value) return false;
  return TRUTHY_ENV_VALUES.has(value.trim().toLowerCase());
}

export function shouldEnableMocks({
  hasWindow,
  bypassWindowFlag,
  bypassEnvFlag,
  nodeEnv,
}: MockGateInputs): boolean {
  if (!hasWindow) return false;
  if (bypassWindowFlag) return false;
  if (isTruthyEnvFlag(bypassEnvFlag)) return false;
  return nodeEnv === "development";
}

export function shouldEnableMocksFromGlobals(): boolean {
  const hasWindow = typeof globalThis.window !== "undefined";
  return shouldEnableMocks({
    hasWindow,
    bypassWindowFlag: hasWindow ? Boolean(globalThis.window.BYPASS_MSW) : false,
    bypassEnvFlag: process.env.NEXT_PUBLIC_BYPASS_MSW,
    nodeEnv: process.env.NODE_ENV,
  });
}
