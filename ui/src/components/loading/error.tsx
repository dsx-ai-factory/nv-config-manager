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
import Link from "next/link";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { LoadingSpinner } from "@/components/ui/loading-spinner";
import { AlertCircle } from "lucide-react";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { ErrorType } from "@/types/errorTypes";
interface TokenExpiryDialogProps {
  open: boolean;
  setOpen: React.Dispatch<React.SetStateAction<boolean>>;
}

export const TokenExpiryDialog: React.FC<TokenExpiryDialogProps> = ({
  open,
  setOpen,
}) => {
  return (
    <Dialog open={open} onOpenChange={setOpen}>
      <DialogContent
        className="sm:max-w-[425px] [&>button]:hidden"
        onInteractOutside={(e) => {
          e.preventDefault();
        }}
      >
        <DialogHeader>
          <DialogTitle>SSO Token Expired</DialogTitle>
        </DialogHeader>
        <DialogDescription>
          To re-enable background data updates, refresh your browser tab. You
          can continue without refreshing, but background updates will remain
          paused.
        </DialogDescription>
        <DialogFooter>
          <Button
            variant="approval"
            onClick={() => {
              globalThis.location.reload();
            }}
          >
            Refresh
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
};

interface WorkflowErrorPageProps {
  error: Error;
  reset: () => void;
  errorConfig?: ErrorType;
}

const WorkflowErrorPage: React.FC<WorkflowErrorPageProps> = ({
  error,
  reset,
  errorConfig,
}) => {
  const [isLoading, setIsLoading] = React.useState<boolean>(false);

  const handleClick = () => {
    try {
      setIsLoading(true);
      reset();
    } catch (error) {
      console.error(error);
    } finally {
      setIsLoading(false);
    }
  };

  return (
    <div className="flex items-center justify-center min-h-screen p-6">
      <Card className="w-full max-w-lg">
        <CardHeader>
          <CardTitle>{errorConfig?.title || 'Error'}</CardTitle>
        </CardHeader>
        <CardContent>
          <Alert variant="destructive" className="mb-4">
            <AlertCircle className="h-4 w-4" />
            <AlertTitle>{errorConfig?.title || 'Something went wrong'}</AlertTitle>
            <AlertDescription>{errorConfig?.message || error.message}</AlertDescription>
          </Alert>
          <div className="flex space-x-4">
            <Button className="w-32" disabled={isLoading} onClick={handleClick}>
              {isLoading ? <LoadingSpinner /> : "Try Again"}
            </Button>
            <Button variant="outline">
              <Link href={errorConfig?.actionHref || "/workflows"}>
                {errorConfig?.actionText || "Return to Workflows"}
              </Link>
              </Button>
          </div>
        </CardContent>
      </Card>
    </div>
  );
};

export default WorkflowErrorPage;
