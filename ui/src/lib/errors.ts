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
import { ERROR_CONFIGS, ErrorType } from "@/types/errorTypes";

export class TokenError extends Error {
  constructor(message: string) {
    super(message);
    this.name = "TokenError";
    // This is necessary for proper instanceof checks in TypeScript
    Object.setPrototypeOf(this, TokenError.prototype);
  }
}
export class APIError extends Error {
  status: number;
  /** The response body's `detail`, when it had one (e.g. a structured 503 diagnostic). */
  detail?: unknown;

  constructor(message: string, status: number, detail?: unknown) {
    super(message);
    this.name = "APIError";
    this.status = status;
    this.detail = detail;
    Object.setPrototypeOf(this, APIError.prototype);
  }
}

export function getErrorConfig(error: Error): ErrorType | undefined {
  if (error instanceof TokenError) {
    return ERROR_CONFIGS.UNAUTHORIZED;
  }

  if (error instanceof APIError) {
    switch (error.status) {
      case 403:
        return ERROR_CONFIGS.FORBIDDEN;
      case 401:
        return ERROR_CONFIGS.UNAUTHORIZED;
      case 404:
        return ERROR_CONFIGS.NOT_FOUND;
      case 500:
        return ERROR_CONFIGS.SERVER_ERROR;
      default:
        return undefined;
    }
  }

  return undefined;
}
