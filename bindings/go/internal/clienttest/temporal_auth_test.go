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
	"log"
	"net/http"
	"strings"
	"testing"

	"github.com/dsx-ai-factory/nv-config-manager/bindings/go/temporal"
)

type roundTripFunc func(*http.Request) (*http.Response, error)

func (fn roundTripFunc) RoundTrip(request *http.Request) (*http.Response, error) {
	return fn(request)
}

func TestTemporalClientAppliesBearerToken(t *testing.T) {
	var logOutput bytes.Buffer
	log.SetOutput(&logOutput)
	t.Cleanup(func() { log.SetOutput(io.Discard) })

	var authorizationHeaders []string
	configuration := temporal.NewConfiguration()
	configuration.Debug = true
	configuration.Servers[0].URL = "https://example.test"
	configuration.DefaultHeader["Authorization"] = "Bearer default-token"
	configuration.HTTPClient = &http.Client{
		Transport: roundTripFunc(func(request *http.Request) (*http.Response, error) {
			authorizationHeaders = append(authorizationHeaders, request.Header.Values("Authorization")...)
			return &http.Response{
				StatusCode: http.StatusOK,
				Status:     "200 OK",
				Header:     http.Header{"Content-Type": []string{"application/json"}},
				Body: io.NopCloser(strings.NewReader(
					`{"next_page_token":null,"page_count":0,"total_count":0,"workflows":[]}`,
				)),
			}, nil
		}),
	}
	client := temporal.NewAPIClient(configuration)
	ctx := context.WithValue(context.Background(), temporal.ContextAccessToken, "test-token")

	_, _, err := client.WorkflowAPI.GetWorkflowsV1WorkflowGet(ctx).User("query-secret").Execute()
	if err != nil {
		t.Fatalf("Execute() error = %v", err)
	}

	if len(authorizationHeaders) != 1 || authorizationHeaders[0] != "Bearer test-token" {
		t.Errorf("authorization headers = %q, want %q", authorizationHeaders, []string{"Bearer test-token"})
	}
	if strings.Contains(logOutput.String(), "test-token") || strings.Contains(logOutput.String(), "default-token") {
		t.Errorf("debug log contains an authorization token: %q", logOutput.String())
	}
	if strings.Contains(logOutput.String(), "query-secret") {
		t.Errorf("debug log contains a query value: %q", logOutput.String())
	}
}
