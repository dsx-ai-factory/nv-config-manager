"use client"
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

import { useState } from "react"
import { Copy } from "lucide-react"

import { cn } from "@/lib/utils"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"

interface ErrorTracebackViewerProps {
  error: {
    traceback: string
  }
  className?: string
}

interface ParsedError {
  message: string
  type: string
  frames: TracebackFrame[]
  cause?: ParsedError
}

interface TracebackFrame {
  file: string
  line?: string
  function?: string
  code?: string
  pointer?: string
}

interface TracebackParseState {
  currentError: ParsedError | null
  errors: ParsedError[]
  index: number
  lines: string[]
}

const CAUSE_SEPARATOR = "The above exception was the direct cause of the following exception:"
const TRACEBACK_HEADER = "Traceback (most recent call last):"
const FRAME_PREFIX = "File "
const FRAME_PATTERN = /File "([^"]+)", line (\d+), in (.+)/

const createParsedError = (type = "Unknown Error", message = ""): ParsedError => ({
  type,
  message,
  frames: [],
})

const parseErrorLine = (line: string): ParsedError | null => {
  if (!line.includes("Error:") && !line.includes("Exception:")) {
    return null
  }

  const separatorIndex = line.indexOf(": ")
  const type = separatorIndex === -1 ? line : line.slice(0, separatorIndex)
  const message = separatorIndex === -1 ? "" : line.slice(separatorIndex + 2)
  return createParsedError(type.trim(), message.trim())
}

const isFrameLine = (line: string) => line.trim().startsWith(FRAME_PREFIX)

const isCodeLine = (line: string | undefined): line is string =>
  line !== undefined && Boolean(line.trim()) && !isFrameLine(line)

const ensureCurrentError = (state: TracebackParseState) => {
  if (state.currentError) {
    return
  }

  state.currentError = createParsedError()
  state.errors.push(state.currentError)
}

const addStackFrame = (state: TracebackParseState, line: string) => {
  const fileMatch = FRAME_PATTERN.exec(line)
  if (!fileMatch || !state.currentError) {
    return
  }

  const frame: TracebackFrame = {
    file: fileMatch[1],
    line: fileMatch[2],
    function: fileMatch[3],
    code: "",
    pointer: "",
  }
  const codeLine = state.lines[state.index + 1]

  if (isCodeLine(codeLine)) {
    frame.code = codeLine.trim()
    state.index++

    const pointerLine = state.lines[state.index + 1]
    if (pointerLine?.includes("^")) {
      frame.pointer = pointerLine.trim()
      state.index++
    }
  }

  state.currentError.frames.push(frame)
}

const appendErrorMessage = (currentError: ParsedError | null, line: string) => {
  const message = line.trim()
  if (!currentError || !message || line.includes("Traceback")) {
    return
  }

  currentError.message = [currentError.message, message].filter(Boolean).join("\n")
}

const mergeParsedError = (state: TracebackParseState, parsedError: ParsedError) => {
  if (!state.currentError) {
    state.currentError = parsedError
    state.errors.push(parsedError)
    return
  }

  state.currentError.type = parsedError.type
  state.currentError.message = parsedError.message
}

const processTracebackLine = (state: TracebackParseState) => {
  const line = state.lines[state.index]
  const parsedError = parseErrorLine(line)

  if (parsedError) {
    mergeParsedError(state, parsedError)
    return
  }

  if (line.includes(CAUSE_SEPARATOR)) {
    state.currentError = null
    return
  }

  if (line.includes(TRACEBACK_HEADER)) {
    ensureCurrentError(state)
    return
  }

  if (isFrameLine(line)) {
    addStackFrame(state, line)
    return
  }

  appendErrorMessage(state.currentError, line)
}

const linkErrorCauses = (errors: ParsedError[]): ParsedError | undefined => {
  const errorsInDisplayOrder = [...errors].reverse()

  errorsInDisplayOrder.slice(0, -1).forEach((error, index) => {
    error.cause = errorsInDisplayOrder[index + 1]
  })

  return errorsInDisplayOrder[0]
}

const parseTraceback = (traceback: string): ParsedError[] => {
  const state: TracebackParseState = {
    currentError: null,
    errors: [],
    index: 0,
    lines: traceback.split("\n"),
  }

  while (state.index < state.lines.length) {
    processTracebackLine(state)
    state.index++
  }

  const rootError = linkErrorCauses(state.errors)
  return rootError ? [rootError] : []
}

const getFrameKey = (frame: TracebackFrame) =>
  [frame.file, frame.line, frame.function, frame.code].filter(Boolean).join(":")

const getKeyedFrames = (frames: ParsedError["frames"]) => {
  const seenFrameCounts = new Map<string, number>()

  return frames.map((frame) => {
    const frameKey = getFrameKey(frame)
    const occurrence = seenFrameCounts.get(frameKey) ?? 0
    seenFrameCounts.set(frameKey, occurrence + 1)

    return {
      frame,
      key: `${frameKey}|occurrence:${occurrence}`,
    }
  })
}

const getErrorKey = (error: ParsedError) =>
  [error.type, error.message].filter(Boolean).join(":")

const ErrorFrame = ({ frame }: { frame: TracebackFrame }) => (
  <div className="mb-3">
    <div className="text-xs text-slate-500 dark:text-slate-400 font-mono">
      File <span className="text-slate-700 dark:text-slate-300">{frame.file}</span>, line{" "}
      <span className="text-slate-700 dark:text-slate-300">{frame.line}</span>, in{" "}
      <span className="text-blue-600 dark:text-blue-400">{frame.function}</span>
    </div>

    {frame.code && (
      <div className="mt-1 pl-4 font-mono text-xs">
        <div className="text-slate-800 dark:text-slate-200">{frame.code}</div>
        {frame.pointer && <div className="text-red-500">{frame.pointer}</div>}
      </div>
    )}
  </div>
)

const ErrorWithCauses = ({ error }: { error: ParsedError }) => (
  <div className="mb-4">
    <div className="bg-red-50/50 dark:bg-red-950/10 p-4 border-l-4 border-red-500">
      <div
        data-testid="error-traceback-type"
        className="font-semibold text-red-700 dark:text-red-400 font-mono"
      >
        {error.type}
      </div>
      <div className="font-mono text-sm whitespace-pre-wrap mt-1">{error.message}</div>
    </div>

    {error.frames.length > 0 && (
      <div className="mt-2 pl-4 border-l border-l-slate-200 dark:border-l-slate-700">
        {getKeyedFrames(error.frames).map(({ frame, key }) => (
          <ErrorFrame key={key} frame={frame} />
        ))}
      </div>
    )}

    {error.cause && (
      <div className="mt-4">
        <div className="text-xs text-slate-500 italic mb-2">Caused by:</div>
        <ErrorWithCauses error={error.cause} />
      </div>
    )}
  </div>
)

export function ErrorTracebackViewer({
  error,
  className,
}: Readonly<ErrorTracebackViewerProps>) {
  const [copied, setCopied] = useState(false)
  const parsedErrors = parseTraceback(error.traceback)

  const copyToClipboard = () => {
    navigator.clipboard.writeText(error.traceback)
    setCopied(true)
    setTimeout(() => setCopied(false), 2000)
  }

  return (
    <Card data-testid="error-traceback-card" className={cn("w-full overflow-hidden", className)}>
      <CardHeader className="bg-red-50 dark:bg-red-950/20 border-b">
        <div className="flex items-center justify-between">
          <CardTitle className="text-red-700 dark:text-red-400">
            <span>Traceback</span>
          </CardTitle>
          <Button variant="outline" size="sm" onClick={copyToClipboard} className="h-8 gap-1">
            <Copy className="h-3.5 w-3.5" />
            <span>{copied ? "Copied!" : "Copy"}</span>
          </Button>
        </div>
      </CardHeader>
      <CardContent className="p-4">
        {parsedErrors.length > 0 ? (
          parsedErrors.map((error) => <ErrorWithCauses key={getErrorKey(error)} error={error} />)
        ) : (
          <div className="text-slate-500 italic">No traceback information available</div>
        )}
      </CardContent>
    </Card>
  )
}
