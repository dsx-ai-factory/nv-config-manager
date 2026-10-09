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
import { useMemo } from "react";
import useSWR from "swr";

import { useRuntimeConfig } from "@/config/runtime";
import { fetcher } from "@/lib/fetcher";
import {
  buildOptionSourceRequest,
  deriveOptionSourceState,
  mapOptionEnvelope,
  mapOptionRows,
  type DependencyValues,
  type ExtraParams,
  type OptionSourceRequest,
  type OptionSourceState,
} from "@/lib/option-source";
import type { OptionSource } from "@/types/workflow-catalog.types";

export type { OptionSourceState, OptionSourceStatus } from "@/lib/option-source";

const NO_EXTRA: ExtraParams = {};

/**
 * Load options for a v1 option source.
 *
 * - `idle`: no source, runtime config not loaded yet, or a required dependency is
 *   empty (`missingDependencies` names the properties).
 * - `loading`: request in flight for the current dependency values.
 * - `empty` / `success`: the current request resolved with zero / some options.
 * - `error`: invalid endpoint or path value, failed request, or a non-list response.
 *
 * Dependencies are read from `values` (the form's projected data); `extra` adds query
 * parameters such as a device field's scope filters.
 *
 * The SWR key is the canonical request URL (endpoint with path values + sorted static,
 * dependency, and extra parameters), so a dependency change switches keys and a late
 * response for earlier values is cached under its own key and never shown. Each URL is
 * stable while the form remains mounted: focus and reconnect events do not revalidate
 * them. A remount revalidates cached options, so returning to a form does not retain a
 * stale list for the rest of the browser session. Values are never cleared here: core
 * fields apply `clear_on_change` by comparing `dependencySignature`.
 */
const useOptionSource = (
  source: OptionSource | undefined,
  values: DependencyValues,
  extra: ExtraParams = NO_EXTRA
): OptionSourceState => {
  const { config } = useRuntimeConfig();
  const apiURL = config?.workflowApiUrl;

  const request =
    apiURL && source ? buildOptionSourceRequest(apiURL, source, values, extra) : undefined;
  const url = request?.kind === "ready" ? request.url : null;
  const invalidMessage = request?.kind === "invalid" ? request.message : undefined;
  const missingSignature =
    request?.kind === "waiting" ? JSON.stringify(request.missingDependencies) : "";

  const { data, error } = useSWR<unknown, Error>(url, fetcher, {
    revalidateOnFocus: false,
    revalidateIfStale: true,
    revalidateOnReconnect: false,
  });

  const labelKey = source?.label_key;
  const valueKey = source?.value_key;
  const typeKey = source?.type_key;
  const mapping = useMemo(
    () =>
      data !== undefined && source?.response === "options-v1"
        ? mapOptionEnvelope(data)
        : data !== undefined && labelKey && valueKey
          ? mapOptionRows(data, labelKey, valueKey, typeKey)
        : undefined,
    [data, source?.response, labelKey, valueKey, typeKey]
  );

  // Rebuild the request from primitives so the returned state keeps its identity
  // across renders that do not change the request or its response.
  return useMemo(() => {
    let stableRequest: OptionSourceRequest | undefined;
    if (url !== null) {
      stableRequest = { kind: "ready", url };
    } else if (invalidMessage !== undefined) {
      stableRequest = { kind: "invalid", message: invalidMessage };
    } else if (missingSignature !== "") {
      stableRequest = {
        kind: "waiting",
        missingDependencies: JSON.parse(missingSignature) as string[],
      };
    }
    return deriveOptionSourceState({ request: stableRequest, error, mapping });
  }, [url, invalidMessage, missingSignature, error, mapping]);
};

export default useOptionSource;
