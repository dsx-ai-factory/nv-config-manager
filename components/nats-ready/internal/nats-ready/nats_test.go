/*
 * SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
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
package natsready

import (
	"strings"
	"testing"
)

func TestRedactAddress(t *testing.T) {
	tests := []struct {
		name    string
		address string
		want    string
	}{
		{
			name:    "masks password",
			address: "nats://kiwi:s3cret@nats:4222",
			want:    "nats://kiwi:xxxxx@nats:4222",
		},
		{
			name:    "keeps user without password",
			address: "nats://kiwi@nats:4222",
			want:    "nats://kiwi@nats:4222",
		},
		{
			name:    "no credentials",
			address: "nats://localhost:4222",
			want:    "nats://localhost:4222",
		},
		{
			name:    "unparseable",
			address: "nats://kiwi:s3cret@nats:bad port",
			want:    "<unparseable NATS address>",
		},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			got := RedactAddress(tt.address)
			if got != tt.want {
				t.Fatalf("RedactAddress(%q) = %q, want %q", tt.address, got, tt.want)
			}
			if strings.Contains(got, "s3cret") {
				t.Fatalf("RedactAddress(%q) leaked the password: %q", tt.address, got)
			}
		})
	}
}
