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

package main

import (
	"os"
	"path/filepath"
	"testing"
)

func TestPatched(t *testing.T) {
	for _, test := range []struct {
		version string
		want    bool
	}{
		{"go1.25.9", false},
		{"go1.26.8", false},
		{"go1.26.9", false},
		{"go1.26.10", true},
		{"go1.26.11", true},
		{"go1.27rc1", false},
		{"go1.27.2", false},
		{"go1.27.3", true},
		{"go1.28.0", true},
		{"devel go1.28", false},
		{"", false},
	} {
		t.Run(test.version, func(t *testing.T) {
			if got := patched(test.version); got != test.want {
				t.Fatalf("patched(%q) = %v, want %v", test.version, got, test.want)
			}
		})
	}
}

func TestRejectUnreadableMetadata(t *testing.T) {
	path := filepath.Join(t.TempDir(), "not-a-go-binary")
	if err := os.WriteFile(path, []byte("no build metadata"), 0600); err != nil {
		t.Fatal(err)
	}
	if err := checkBinary(path); err == nil {
		t.Fatal("accepted a file without Go build metadata")
	}
}
