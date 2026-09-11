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
import {
  DeviceWorkflowForm,
  DeviceWorkflowFormSchema,
} from "@/components/forms/workflow";
import { useToast } from "@/components/ui/use-toast";
import { startWorkflow } from "@/lib/utils";
import { CertificateRotationWorkflowInput } from "@/types/data-table.types";

const CertificateRotationWorkflowForm = () => {
  const { toast } = useToast();
  const onSubmit = (data: DeviceWorkflowFormSchema) => {
    const params: CertificateRotationWorkflowInput = {
      device_id: data.device,
    };
    return startWorkflow("/v1/workflow/ngc/certificate_rotation", params).catch(
      (error) => {
        toast({
          variant: "destructive",
          title: "Workflow Failed",
          description: `${error}`,
        });
        throw error;
      }
    );
  };

  return (
    <DeviceWorkflowForm
      title="New Certificate Rotation Workflow"
      onSubmit={onSubmit}
      deviceFilterParams={[["platform", "Cumulus Linux"]]}
    />
  );
};

export default CertificateRotationWorkflowForm;
