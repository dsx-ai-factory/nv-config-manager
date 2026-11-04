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

// check-go-version inspects binaries without executing them, including binaries
// built for another architecture. Image builds fail when CA distrust is missing.
package main

import (
	"debug/buildinfo"
	"fmt"
	"go/version"
	"os"
)

func patched(goVersion string) bool {
	if !version.IsValid(goVersion) {
		return false
	}
	switch version.Lang(goVersion) {
	case "go1.26":
		return version.Compare(goVersion, "go1.26.10") >= 0
	case "go1.27":
		return version.Compare(goVersion, "go1.27.3") >= 0
	default:
		return version.Compare(version.Lang(goVersion), "go1.27") > 0
	}
}

func checkBinary(path string) error {
	info, err := buildinfo.ReadFile(path)
	if err != nil {
		return fmt.Errorf("%s: cannot read Go build metadata: %w", path, err)
	}
	if !patched(info.GoVersion) {
		return fmt.Errorf("%s: %s lacks the required CVE-2026-111943 fix (need Go 1.26.10 or 1.27.3 or newer)", path, info.GoVersion)
	}
	fmt.Printf("%s: verified %s\n", path, info.GoVersion)
	return nil
}

func main() {
	if len(os.Args) < 2 {
		fmt.Fprintln(os.Stderr, "Usage: check-go-version BINARY...")
		os.Exit(2)
	}
	for _, path := range os.Args[1:] {
		if err := checkBinary(path); err != nil {
			fmt.Fprintln(os.Stderr, err)
			os.Exit(1)
		}
	}
}
