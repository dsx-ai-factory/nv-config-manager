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

import { useState, useMemo, use } from "react";
import { useSearchParams } from "next/navigation";
import { useConfigVersions, useConfigDiff } from "@/lib/config-store-api";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from "@/components/ui/tooltip";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { formatDate, formatRelativeTime } from "@/lib/utils";
import { ArrowLeft, GitCompare, Info } from "lucide-react";
import Link from "next/link";
import { useTheme } from "next-themes";
import { parseDiff, Diff, Hunk } from "react-diff-view";
import "react-diff-view/style/index.css";

function DiffViewer({ 
  diffText 
}: { 
  diffText: string;
}) {
  const { theme, systemTheme } = useTheme();
  const currentTheme = theme === "system" ? systemTheme : theme;
  
  const files = useMemo(() => {
    try {
      return parseDiff(diffText);
    } catch (e) {
      console.error("Failed to parse diff:", e);
      return [];
    }
  }, [diffText]);

  if (files.length === 0) {
    return (
      <div className="p-4 text-center text-muted-foreground">
        No differences to display
      </div>
    );
  }

  return (
    <div className={`border rounded-lg overflow-hidden ${currentTheme === "dark" ? "diff-dark" : "diff-light"}`}>
      {files.map((file) => (
        <div key={`${file.oldPath ?? ""}-${file.newPath ?? ""}-${file.type}`}>
          <Diff
            viewType="split"
            diffType={file.type}
            hunks={file.hunks}
          >
            {(hunks) => hunks.map((hunk) => (
              <Hunk key={hunk.content} hunk={hunk} />
            ))}
          </Diff>
        </div>
      ))}
    </div>
  );
}

export default function ConfigHistoryPage({ 
  params 
}: Readonly<{ 
  params: Promise<{ uuid: string; filename: string }>
}>) {
  const { uuid, filename } = use(params);
  const searchParams = useSearchParams();
  const fileType = (searchParams?.get("file_type") as "intended" | "backup") || "intended";
  const decodedFilename = decodeURIComponent(filename);
  const { data: versions, error, isLoading } = useConfigVersions(uuid, decodedFilename, fileType);
  const [selectedVersions, setSelectedVersions] = useState<[number | null, number | null]>([null, null]);
  
  const fromVersion = selectedVersions[0];
  const toVersion = selectedVersions[1];
  const { data: diffData } = useConfigDiff(
    fromVersion !== null && toVersion !== null ? uuid : null,
    fromVersion !== null && toVersion !== null ? decodedFilename : null,
    fromVersion,
    toVersion,
    fileType
  );

  if (isLoading) {
    return (
      <div className="container py-6">
        <div className="flex items-center justify-center">
          <p>Loading version history...</p>
        </div>
      </div>
    );
  }

  if (error) {
    return (
      <div className="container py-6">
        <Card className="border-destructive">
          <CardHeader>
            <CardTitle className="text-destructive">Error Loading History</CardTitle>
          </CardHeader>
        </Card>
      </div>
    );
  }

  const handleVersionSelect = (version: number) => {
    // If clicking on an already selected version, unselect it
    if (selectedVersions[0] === version) {
      // If second version is selected, move it to first slot
      if (selectedVersions[1] === null) {
        setSelectedVersions([null, null]);
      } else {
        setSelectedVersions([selectedVersions[1], null]);
      }
    } else if (selectedVersions[1] === version) {
      setSelectedVersions([selectedVersions[0], null]);
    } else if (selectedVersions[0] === null) {
      // No versions selected, select this one
      setSelectedVersions([version, null]);
    } else if (selectedVersions[1] === null) {
      // One version selected, select this as the second
      if (version !== selectedVersions[0]) {
        // Ensure from_version is always less than to_version
        if (version < selectedVersions[0]) {
          setSelectedVersions([version, selectedVersions[0]]);
        } else {
          setSelectedVersions([selectedVersions[0], version]);
        }
      }
    } else {
      // Both versions selected, replace with this one
      setSelectedVersions([version, null]);
    }
  };

  const device = versions?.device;

  return (
    <div className="container py-6">
      <div className="flex flex-col gap-6">
        <div>
          <div className="flex items-center gap-4 mb-2">
            <Button variant="ghost" size="sm" asChild>
              <Link
                href={`/device/${uuid}/${filename}?file_type=${fileType}`}
                className="no-underline"
              >
                <ArrowLeft className="mr-2 h-4 w-4" />
                Back to File
              </Link>
            </Button>
          </div>
          <h1 className="text-3xl font-bold tracking-tight">
            Version History: {decodedFilename}
          </h1>
          {device && (
            <p className="text-muted-foreground mt-2">
              {device.name} - {device.site}
            </p>
          )}
        </div>

        <TooltipProvider>
          <Tooltip>
            <TooltipTrigger asChild>
              <span className="inline-flex w-fit cursor-help items-center gap-1.5 text-sm text-muted-foreground">
                <Info className="h-4 w-4" />
                Stored versions, not a live device diff
              </span>
            </TooltipTrigger>
            <TooltipContent className="max-w-sm">
              Diffs only the two selected Config Store versions, not the running
              device configuration. To compare against the live device, run the
              Configuration Diff workflow.
            </TooltipContent>
          </Tooltip>
        </TooltipProvider>

        <Tabs defaultValue="history" className="w-full">
          <TabsList>
            <TabsTrigger value="history">Version History</TabsTrigger>
            <TabsTrigger value="diff" disabled={!diffData}>
              {diffData ? `Compare v${fromVersion} → v${toVersion}` : "Select 2 Versions to Compare"}
            </TabsTrigger>
          </TabsList>

          <TabsContent value="history" className="mt-4">
            <Card>
              <CardHeader>
                <CardTitle>All Versions</CardTitle>
                {selectedVersions[0] !== null && selectedVersions[1] === null && (
                  <p className="text-sm text-muted-foreground">
                    Selected v{selectedVersions[0]}. Select another version to compare.
                    <Button 
                      variant="link" 
                      size="sm" 
                      onClick={() => setSelectedVersions([null, null])}
                      className="ml-2"
                    >
                      Clear
                    </Button>
                  </p>
                )}
              </CardHeader>
              <CardContent>
                {versions && versions.versions.length > 0 ? (
                  <Table>
                    <TableHeader>
                      <TableRow>
                        <TableHead>Select</TableHead>
                        <TableHead>Version</TableHead>
                        <TableHead>Author</TableHead>
                        <TableHead>Date</TableHead>
                        <TableHead>Message</TableHead>
                        <TableHead>Hash</TableHead>
                      </TableRow>
                    </TableHeader>
                    <TableBody>
                      {versions.versions.map((version) => (
                        <TableRow 
                          key={version.version}
                          className={
                            selectedVersions.includes(version.version)
                              ? "bg-muted"
                              : ""
                          }
                        >
                          <TableCell>
                            <Button
                              variant="outline"
                              size="sm"
                              onClick={() => handleVersionSelect(version.version)}
                              disabled={selectedVersions.filter(v => v !== null).length >= 2 && !selectedVersions.includes(version.version)}
                            >
                              {selectedVersions.includes(version.version) ? (
                                <GitCompare className="h-4 w-4" />
                              ) : (
                                "Select"
                              )}
                            </Button>
                          </TableCell>
                          <TableCell>
                            <Link href={`/device/${uuid}/${filename}?file_type=${fileType}&version=${version.version}`}>
                              <Badge 
                                variant={version.version === versions.versions[0].version ? "default" : "secondary"}
                                className="cursor-pointer hover:opacity-80"
                              >
                                v{version.version}
                                {version.version === versions.versions[0].version && " (latest)"}
                              </Badge>
                            </Link>
                          </TableCell>
                          <TableCell className="text-sm">{version.author}</TableCell>
                          <TableCell className="text-sm">
                            <div>{formatDate(version.created_at)}</div>
                            <div className="text-xs text-muted-foreground">
                              {formatRelativeTime(version.created_at)}
                            </div>
                          </TableCell>
                          <TableCell className="text-sm max-w-md truncate">
                            {version.commit_message}
                          </TableCell>
                          <TableCell>
                            <code className="text-xs bg-muted px-1 py-0.5 rounded">
                              {version.content_hash.substring(0, 8)}
                            </code>
                          </TableCell>
                        </TableRow>
                      ))}
                    </TableBody>
                  </Table>
                ) : (
                  <p className="text-center text-muted-foreground py-8">
                    No version history available
                  </p>
                )}
              </CardContent>
            </Card>
          </TabsContent>

          <TabsContent value="diff" className="mt-4">
            {diffData && (
              <div className="space-y-4">
                <Card>
                  <CardHeader>
                    <CardTitle>Diff Statistics</CardTitle>
                  </CardHeader>
                  <CardContent>
                    <div className="grid grid-cols-4 gap-4 text-center">
                      <div>
                        <div className="text-2xl font-bold">{diffData.diff_stats.from_lines}</div>
                        <div className="text-sm text-muted-foreground">Old Lines</div>
                      </div>
                      <div>
                        <div className="text-2xl font-bold">{diffData.diff_stats.to_lines}</div>
                        <div className="text-sm text-muted-foreground">New Lines</div>
                      </div>
                      <div>
                        <div className="text-2xl font-bold text-green-600">
                          +{diffData.diff_stats.additions}
                        </div>
                        <div className="text-sm text-muted-foreground">Additions</div>
                      </div>
                      <div>
                        <div className="text-2xl font-bold text-red-600">
                          -{diffData.diff_stats.deletions}
                        </div>
                        <div className="text-sm text-muted-foreground">Deletions</div>
                      </div>
                    </div>
                  </CardContent>
                </Card>

                <Card>
                  <CardHeader>
                    <CardTitle>Side-by-Side Comparison</CardTitle>
                  </CardHeader>
                  <CardContent>
                    <DiffViewer 
                      diffText={diffData.diff} 
                    />
                  </CardContent>
                </Card>
              </div>
            )}
          </TabsContent>
        </Tabs>
      </div>
    </div>
  );
}
