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

import * as React from "react";
import { DataTable } from "@/components/data-table/data-table";
import { getWorkflowColumns } from "./columns";
import type { WorkflowCatalogEntry } from "@/types/workflow-catalog.types";

type WorkflowTableProps = {
  title?: string;
  workflowCatalog: readonly WorkflowCatalogEntry[];
};

const WorkflowTable: React.FC<WorkflowTableProps> = ({
  title = "Workflows",
  workflowCatalog,
}) => {
  const workflowColumns = React.useMemo(
    () => getWorkflowColumns(workflowCatalog),
    [workflowCatalog]
  );

  return (
    <div className="w-full px-8 py-6" key={title}>
      <div className="mb-4 flex flex-row items-center justify-between">
        <h1 className="mr-4 text-2xl font-semibold">{title}</h1>
      </div>
      <DataTable columns={workflowColumns} />
    </div>
  );
};

export default WorkflowTable;
