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
import * as React from "react";
import { WorkflowClientComponentProps } from "@/types/workflow-page.types";
import { Workflow } from "@/types/data-table.types";
import { formatTimestamp } from "@/lib/utils";

interface SummaryData {
  label: string;
  value: string | string[] | React.ReactElement | React.ReactElement[];
}

const WorkflowSummary: React.FC<WorkflowClientComponentProps> = ({
  workflow,
}) => {
  const getSummaryData = (workflow: Workflow): SummaryData[] => {
    const summaryData = [];

    // Site is always a 1-element list, if it is included.
    const site = workflow?.search_attributes?.Site;
    if (site) summaryData.push({ label: "Site", value: String(site[0]) });

    // DeviceName/DeviceID may include more than one element.
    const deviceName = workflow?.search_attributes?.DeviceName;
    const deviceId = workflow?.search_attributes?.DeviceID;
    if (deviceName && deviceId && deviceName.length > 0) {
      if (deviceName.length > 1) {
        // TODO: Handle case where more than one device is included.
      } else {
        summaryData.push({
          label: "Device",
          value: String(deviceName[0]),
        });
      }
    }

    // User is always a 1-element list, if it is included.
    const user = workflow?.search_attributes?.User;
    if (user) summaryData.push({ label: "User", value: String(user[0]) });

    const readRoles = workflow?.search_attributes?.ReadRoles;
    if (readRoles && readRoles.length > 0) {
      summaryData.push({
        label: "Read Roles",
        value: readRoles.map(String).join(", "),
      });
    }

    const executeRoles = workflow?.search_attributes?.ExecuteRoles;
    if (executeRoles && executeRoles.length > 0) {
      summaryData.push({
        label: "Execute Roles",
        value: executeRoles.map(String).join(", "),
      });
    }

    const startTime = workflow?.start_time;
    if (startTime)
      summaryData.push({
        label: "Start Time",
        value: formatTimestamp(startTime),
      });

    return summaryData;
  };
  return (
    <div className="p-4">
      <ul>
        {getSummaryData(workflow).map((item) => (
          <li key={item.label}>
            <span className="font-semibold">{item.label}:</span> {item.value}
          </li>
        ))}
      </ul>
    </div>
  );
};

export default WorkflowSummary;
