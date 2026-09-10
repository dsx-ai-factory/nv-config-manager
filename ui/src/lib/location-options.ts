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
import { LocationOption, LocationResult } from "@/types/workflow-form.types";

const LOCATION_OPTION_PREFIX = "dcim-location:";

const encodeLocationOption = (location: LocationResult): string =>
  `${LOCATION_OPTION_PREFIX}${JSON.stringify([location.model, location.id])}`;

/** Convert API locations into selectable options without collapsing colliding IDs. */
export const mapLocationOptions = (
  locations: LocationResult[] | undefined
): LocationOption[] => {
  if (!Array.isArray(locations)) return [];

  return locations.map((location) => ({
    key: location.name,
    value: location.model ? encodeLocationOption(location) : location.id,
    id: location.id,
    model: location.model ?? undefined,
  }));
};

/** Decode the UI-only location value while accepting legacy bare IDs. */
export const parseLocationValue = (
  value: string | null | undefined
): { id: string; model?: string } | undefined => {
  if (!value) return undefined;
  if (!value.startsWith(LOCATION_OPTION_PREFIX)) return { id: value };

  try {
    const parsed: unknown = JSON.parse(value.slice(LOCATION_OPTION_PREFIX.length));
    if (
      Array.isArray(parsed) &&
      parsed.length === 2 &&
      typeof parsed[0] === "string" &&
      typeof parsed[1] === "string"
    ) {
      return { id: parsed[1], model: parsed[0] };
    }
  } catch {
    // Treat malformed or provider-owned values using the reserved prefix as bare IDs.
  }
  return { id: value };
};

/** Resolve a selected or legacy query-string value to its provider ID and model. */
export const resolveLocationOption = (
  options: LocationOption[],
  value: string | null | undefined
): LocationOption | undefined => {
  if (!value) return undefined;
  return options.find(
    (option) =>
      option.value === value || option.id === value || option.key === value
  );
};

/** Return the form value corresponding to a provider ID, display name, or encoded option. */
export const resolveLocationFormValue = (
  options: LocationOption[],
  value: string | null | undefined
): string | undefined => resolveLocationOption(options, value)?.value;
