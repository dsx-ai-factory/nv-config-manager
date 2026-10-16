// SPDX-FileCopyrightText: Copyright (c) 2024-2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
// SPDX-License-Identifier: Apache-2.0
//
// Licensed under the Apache License, Version 2.0 (the "License");
// you may not use this file except in compliance with the License.
// You may obtain a copy of the License at
//
// http://www.apache.org/licenses/LICENSE-2.0
//
// Unless required by applicable law or agreed to in writing, software
// distributed under the License is distributed on an "AS IS" BASIS,
// WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
// See the License for the specific language governing permissions and
// limitations under the License.

package clienttest

import (
	"bytes"
	"context"
	"io"
	"net/http"
	"testing"

	"github.com/nvidia/nv-config-manager/bindings/go/ztp"
)

func TestZTPDownloadPreservesBinaryContent(t *testing.T) {
	t.Setenv("TMPDIR", t.TempDir())
	content := []byte{0, 255, 128, 'f', 'w', '\r', '\n'}
	configuration := ztp.NewConfiguration()
	configuration.Servers[0].URL = "https://example.test"
	configuration.HTTPClient = &http.Client{
		Transport: roundTripFunc(func(request *http.Request) (*http.Response, error) {
			if request.Method != http.MethodGet || request.URL.Path != "/v1/files/platform/1.0/firmware.bin" {
				t.Errorf("unexpected request: %s %s", request.Method, request.URL.Path)
			}
			return &http.Response{
				StatusCode: http.StatusOK,
				Header:     http.Header{"Content-Type": []string{"application/octet-stream"}},
				Body:       io.NopCloser(bytes.NewReader(content)),
			}, nil
		}),
	}
	client := ztp.NewAPIClient(configuration)
	file, response, err := client.FilesAPI.LoadObjectV1FilesPlatformVersionFilenameGet(
		context.Background(), "platform", "1.0", "firmware.bin",
	).Execute()
	if err != nil {
		t.Fatalf("download failed: %v", err)
	}
	defer response.Body.Close()
	if file == nil {
		t.Fatal("download returned no file")
	}
	defer file.Close()
	actual, err := io.ReadAll(file)
	if err != nil {
		t.Fatalf("read download: %v", err)
	}
	if !bytes.Equal(actual, content) {
		t.Errorf("download = %v, want %v", actual, content)
	}
}
