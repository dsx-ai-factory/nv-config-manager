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

import { useState, useEffect, use } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import { useDeviceConfigs } from "@/lib/config-store-api";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { Button } from "@/components/ui/button";
import { formatRelativeTime } from "@/lib/utils";
import { useRuntimeConfig } from "@/config/runtime";
import { downloadConfigsAsZip } from "@/lib/download-config";
import { FileText, ExternalLink, History, Download } from "lucide-react";
import Link from "next/link";

export default function DevicePage({ params }: Readonly<{ params: Promise<{ uuid: string }> }>) {
  const { uuid } = use(params);
  const router = useRouter();
  const searchParams = useSearchParams();
  const initialFileType = (searchParams?.get("file_type") as "intended" | "backup") || "intended";
  const [fileType, setFileType] = useState<"intended" | "backup">(initialFileType);
  const [isDownloading, setIsDownloading] = useState(false);
  const { data: configs, error, isLoading } = useDeviceConfigs(uuid, fileType);
  const { config: runtimeConfig } = useRuntimeConfig();

  const device = configs?.[0]?.device;
  const deviceUrl = device?.device_url ?? device?.nautobot_url;
  const dcimDisplayName = runtimeConfig?.dcimDisplayName ?? "DCIM";
  const handleDownloadAll = async () => {
    if (!configs?.length) return;
    setIsDownloading(true);
    try {
      await downloadConfigsAsZip(
        configs.map((c) => ({
          filename: c.filename,
          content: c.content,
          deviceName: device?.name,
        })),
        { filename: `${device?.name ?? uuid}-configs.zip` }
      );
    } finally {
      setIsDownloading(false);
    }
  };

  const navigateToConfig = (filename: string) => {
    router.push(`/device/${uuid}/${filename}?file_type=${fileType}`);
  };

  // Update fileType when URL changes
  useEffect(() => {
    const urlFileType = searchParams?.get("file_type") as "intended" | "backup";
    if (urlFileType) {
      setFileType(urlFileType);
    }
  }, [searchParams]);

  if (isLoading) {
    return (
      <div className="container py-6">
        <div className="flex items-center justify-center">
          <p>Loading device configs...</p>
        </div>
      </div>
    );
  }

  if (error) {
    return (
      <div className="container py-6">
        <Card className="border-destructive">
          <CardHeader>
            <CardTitle className="text-destructive">Error Loading Configs</CardTitle>
            <CardDescription>{error.message}</CardDescription>
          </CardHeader>
        </Card>
      </div>
    );
  }

  return (
    <div className="container py-6">
      <div className="flex flex-col gap-6">
        <div>
          <div className="flex items-center justify-between">
            <div>
              <h1 className="text-3xl font-bold tracking-tight">
                {device?.name || uuid}
              </h1>
              {device && (
                <div className="flex gap-2 mt-2">
                  <Badge variant="outline">{device.site}</Badge>
                  {device.platform && <Badge variant="outline">{device.platform}</Badge>}
                  {device.role && <Badge variant="outline">{device.role}</Badge>}
                  {device.rack && <Badge variant="secondary">{device.rack}</Badge>}
                </div>
              )}
            </div>
            {deviceUrl && (
              <Button variant="outline" asChild>
                <a
                  href={deviceUrl}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="no-underline"
                >
                  <ExternalLink className="mr-2 h-4 w-4" />
                  View in {dcimDisplayName}
                </a>
              </Button>
            )}
          </div>
          <p className="text-muted-foreground mt-2">
            UUID: <code className="text-xs bg-muted px-1 py-0.5 rounded">{uuid}</code>
          </p>
        </div>

        <Tabs value={fileType} onValueChange={(value) => setFileType(value as "intended" | "backup")}>
          <TabsList className="grid w-full max-w-md grid-cols-2">
            <TabsTrigger value="intended">Intended Configs</TabsTrigger>
            <TabsTrigger value="backup">Backup Configs</TabsTrigger>
          </TabsList>

          <TabsContent value={fileType} className="space-y-6">
            <Card>
              <CardHeader>
                <div className="flex items-start justify-between gap-4">
                  <div>
                    <CardTitle>Configuration Files</CardTitle>
                    <CardDescription>
                      {configs?.length || 0} {fileType} files found
                    </CardDescription>
                  </div>
                  {configs && configs.length > 0 && (
                    <Button
                      variant="outline"
                      onClick={handleDownloadAll}
                      disabled={isDownloading}
                    >
                      <Download className="mr-2 h-4 w-4" />
                      {isDownloading ? "Preparing..." : "Download All"}
                    </Button>
                  )}
                </div>
              </CardHeader>
              <CardContent>
            {configs && configs.length > 0 ? (
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead>Filename</TableHead>
                    <TableHead>Version</TableHead>
                    <TableHead>Author</TableHead>
                    <TableHead>Last Modified</TableHead>
                    <TableHead>Actions</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {configs.map((config) => (
                    <TableRow
                      key={config.id}
                      className="cursor-pointer hover:bg-muted/50"
                      onClick={() => navigateToConfig(config.filename)}
                    >
                      <TableCell className="font-mono text-sm">
                        <FileText className="inline mr-2 h-4 w-4" />
                        {config.filename}
                      </TableCell>
                      <TableCell>
                        <Badge variant="secondary">v{config.version}</Badge>
                      </TableCell>
                      <TableCell className="text-sm">{config.author}</TableCell>
                      <TableCell className="text-sm text-muted-foreground">
                        {formatRelativeTime(config.created_at)}
                      </TableCell>
                      <TableCell onClick={(e) => e.stopPropagation()}>
                        <div className="flex gap-2">
                          <Button variant="outline" size="sm" asChild>
                            <Link
                              href={`/device/${uuid}/${config.filename}?file_type=${fileType}`}
                              className="no-underline"
                            >
                              View
                            </Link>
                          </Button>
                          <Button variant="outline" size="sm" asChild>
                            <Link
                              href={`/device/${uuid}/${config.filename}/history?file_type=${fileType}`}
                              className="no-underline"
                            >
                              <History className="mr-1 h-3 w-3" />
                              History
                            </Link>
                          </Button>
                        </div>
                      </TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            ) : (
              <p className="text-center text-muted-foreground py-8">
                No configuration files found for this device
              </p>
            )}
              </CardContent>
            </Card>
          </TabsContent>
        </Tabs>
      </div>
    </div>
  );
}
