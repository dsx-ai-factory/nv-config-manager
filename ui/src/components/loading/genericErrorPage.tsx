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

import { AlertCircle, ArrowLeft } from "lucide-react";
import { Button } from "@/components/ui/button";
import { useRouter } from "next/navigation";

export type ErrorType = {
  status: number;
  title: string;
  message: string;
  actionText?: string;
  actionHref?: string;
};

export const ERROR_TYPES = {
  FORBIDDEN: {
    status: 403,
    title: "Access Denied",
    message: "You do not have permission to access this resource.",
  },
  NOT_FOUND: {
    status: 404,
    title: "Not Found",
    message: "The requested resource could not be found.",
  },
  UNAUTHORIZED: {
    status: 401,
    title: "Unauthorized",
    message: "Please log in to access this resource.",
  },
  SERVER_ERROR: {
    status: 500,
    title: "Server Error",
    message: "An unexpected error occurred. Please try again later.",
  },
} as const;

interface ErrorPageProps {
  error: ErrorType;
  showBackButton?: boolean;
  customAction?: () => void;
}

export function ErrorPage({
  error,
  showBackButton = true,
  customAction,
}: Readonly<ErrorPageProps>) {
  const router = useRouter();

  const handleBack = () => {
    if (customAction) {
      customAction();
    } else {
      router.back();
    }
  };

  return (
    <div className="flex h-[calc(100vh-4rem)] items-center justify-center p-5 w-full">
      <div className="text-center">
        <div className="inline-flex rounded-full bg-red-100 p-4">
          <div className="rounded-full stroke-red-600 bg-red-200 p-4">
            <AlertCircle className="w-16 h-16 text-red-600" />
          </div>
        </div>
        <h1 className="mt-5 text-[36px] font-bold text-slate-800 lg:text-[50px]">
          {error.status} - {error.title}
        </h1>
        <p className="text-slate-600 mt-5 lg:text-lg">{error.message}</p>
        <div className="mt-8 flex items-center justify-center gap-4">
          {showBackButton && (
            <Button
              onClick={handleBack}
              variant="outline"
              className="flex items-center gap-2"
            >
              <ArrowLeft className="w-4 h-4" />
              Go Back
            </Button>
          )}
          {error.actionText && error.actionHref && (
            <Button
              onClick={() => router.push(error.actionHref!)}
              className="flex items-center gap-2"
            >
              {error.actionText}
            </Button>
          )}
        </div>
      </div>
    </div>
  );
}
