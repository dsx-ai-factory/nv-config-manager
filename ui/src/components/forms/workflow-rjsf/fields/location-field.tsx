"use client";
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

/**
 * `ui:field: "location"`: an API-backed location picker. With a `typeField`, the
 * selected row's id and type are written to the property and its sibling in ONE
 * `setFields` call, so a dependent never renders or requests options with a new id
 * and an old type, or the reverse.
 */
import * as React from "react";
import { getUiOptions, type FieldProps } from "@rjsf/utils";

import type { OptionSource } from "@/types/workflow-catalog.types";

import { corePrefillParams } from "../prefill";
import { SourceOptionsField } from "./api-options-field";

export const LocationField = (props: FieldProps) => {
  const options = getUiOptions(props.uiSchema);
  const prefillParams = React.useMemo(
    () => corePrefillParams("location", props.name, options),
    // `options` is rebuilt every render; its inputs are the field's ui_schema and name.
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [props.uiSchema, props.name]
  );
  return (
    <SourceOptionsField
      field={props}
      source={options.source as OptionSource}
      typeField={typeof options.typeField === "string" ? options.typeField : undefined}
      prefillParams={prefillParams}
    />
  );
};
