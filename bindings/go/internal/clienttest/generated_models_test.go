// SPDX-FileCopyrightText: Copyright (c) 2024-2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

package clienttest

import (
	"context"
	"encoding/json"
	"go/ast"
	"go/parser"
	"go/token"
	"io"
	"mime"
	"mime/multipart"
	"net/http"
	"path/filepath"
	"strings"
	"testing"

	"github.com/nvidia/nv-config-manager/bindings/go/config-store"
	"github.com/nvidia/nv-config-manager/bindings/go/dhcp"
	"github.com/nvidia/nv-config-manager/bindings/go/render"
	"github.com/nvidia/nv-config-manager/bindings/go/ztp"
)

func TestGeneratedDeviceCableValidationInputDocumentsDevice(t *testing.T) {
	const expected = "Preloaded data for the target network device, if available."
	comments := generatedStructFieldComments(t, "model_device_cable_validation_input.go")
	if comments["Device"] != expected {
		t.Fatalf("Device comment = %q, want %q", comments["Device"], expected)
	}
}

func TestGeneratedBackupInputDocumentsOptionalMetadata(t *testing.T) {
	expected := map[string]string{
		"IntendedConfigCommitId":    "Config Store commit containing the intended configuration.",
		"SuppressDriftNotification": "Suppress the Slack notification when configuration drift is detected.",
		"User":                      "User that requested the backup.",
		"UserDomain":                "Domain of the user requesting the backup.",
		"WorkflowId":                "Identifier of the parent workflow, if any.",
	}
	comments := generatedStructFieldComments(t, "model_backup_input.go")
	for field, expectedComment := range expected {
		if comments[field] != expectedComment {
			t.Errorf("%s comment = %q, want %q", field, comments[field], expectedComment)
		}
	}
}

func generatedStructFieldComments(t *testing.T, filename string) map[string]string {
	t.Helper()
	path := filepath.Join("..", "..", "temporal", filename)
	file, err := parser.ParseFile(token.NewFileSet(), path, nil, parser.ParseComments)
	if err != nil {
		t.Fatalf("parse generated model: %v", err)
	}

	comments := make(map[string]string)
	ast.Inspect(file, func(node ast.Node) bool {
		field, ok := node.(*ast.Field)
		if !ok || len(field.Names) != 1 || field.Doc == nil {
			return true
		}
		comments[field.Names[0].Name] = strings.TrimSpace(field.Doc.Text())
		return true
	})
	return comments
}

func TestGeneratedModelsRejectNullForRequiredFields(t *testing.T) {
	testCases := []struct {
		name    string
		payload string
		target  interface{}
	}{
		{name: "device UUID", payload: `{"uuid":null}`, target: &configstore.DeviceUUID{}},
		{name: "identity", payload: `{"roles":[],"user":null}`, target: &dhcp.WhoamiResponse{}},
		{name: "consumer list", payload: `{"consumers":null}`, target: &render.ConsumerListResponse{}},
	}

	for _, testCase := range testCases {
		t.Run(testCase.name, func(t *testing.T) {
			if err := json.Unmarshal([]byte(testCase.payload), testCase.target); err == nil {
				t.Fatal("json.Unmarshal() accepted null for a required non-nullable field")
			}
		})
	}
}

func TestGeneratedDhcpIpVersionUsesExportedNames(t *testing.T) {
	if dhcp.V4 != dhcp.IpVersion(4) {
		t.Fatalf("dhcp.V4 = %d, want 4", dhcp.V4)
	}
	if dhcp.V6 != dhcp.IpVersion(6) {
		t.Fatalf("dhcp.V6 = %d, want 6", dhcp.V6)
	}
}

func TestGeneratedMultipartLengthIncludesClosingBoundary(t *testing.T) {
	configuration := ztp.NewConfiguration()
	configuration.Servers[0].URL = "https://example.test"
	configuration.HTTPClient = &http.Client{
		Transport: roundTripFunc(func(request *http.Request) (*http.Response, error) {
			body, err := io.ReadAll(request.Body)
			if err != nil {
				t.Fatalf("read multipart body: %v", err)
			}
			if request.ContentLength != int64(len(body)) {
				t.Errorf("Content-Length = %d, body length = %d", request.ContentLength, len(body))
			}

			_, parameters, err := mime.ParseMediaType(request.Header.Get("Content-Type"))
			if err != nil {
				t.Fatalf("parse Content-Type: %v", err)
			}
			form, err := multipart.NewReader(strings.NewReader(string(body)), parameters["boundary"]).ReadForm(1024)
			if err != nil {
				t.Fatalf("parse finalized multipart body: %v", err)
			}
			if got := form.Value["file"]; len(got) != 1 || got[0] != "contents" {
				t.Errorf("multipart file field = %q, want %q", got, []string{"contents"})
			}

			return &http.Response{
				StatusCode: http.StatusOK,
				Status:     "200 OK",
				Header:     http.Header{"Content-Type": []string{"application/json"}},
				Body:       io.NopCloser(strings.NewReader(`"ok"`)),
			}, nil
		}),
	}

	client := ztp.NewAPIClient(configuration)
	_, _, err := client.FilesAPI.UploadFileV1FilesPlatformVersionFilenamePost(
		context.Background(), "platform", "version", "filename",
	).Checksum("checksum").File("contents").Execute()
	if err != nil {
		t.Fatalf("Execute() error = %v", err)
	}
}

func TestGeneratedUnionRejectsNullAndEmptyValue(t *testing.T) {
	var location configstore.LocationInner
	if err := json.Unmarshal([]byte("null"), &location); err == nil {
		t.Fatal("json.Unmarshal() accepted null for a non-nullable union")
	}
	if _, err := json.Marshal(configstore.LocationInner{}); err == nil {
		t.Fatal("json.Marshal() accepted an empty non-nullable union")
	}
}
