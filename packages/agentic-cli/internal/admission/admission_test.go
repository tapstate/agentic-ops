package admission

import (
	"path/filepath"
	"strings"
	"testing"
)

func TestLoadTapdataDefectFixAdmissionStandard(t *testing.T) {
	standard, err := LoadFile(filepath.Join("..", "..", "..", "..", "install-resources", "basic", "projects", "tapdata", "admission", "defect-fix.yaml"))
	if err != nil {
		t.Fatalf("LoadFile error = %v", err)
	}
	if standard.TaskClass != "bug_fix" {
		t.Fatalf("TaskClass = %s", standard.TaskClass)
	}
	want := []string{"problem_branch", "target_branch", "problem_summary"}
	if strings.Join(standard.RequiredFields, ",") != strings.Join(want, ",") {
		t.Fatalf("RequiredFields = %#v, want %#v", standard.RequiredFields, want)
	}
	optional := []string{"reproduction_path", "acceptance_criteria"}
	if strings.Join(standard.OptionalFields, ",") != strings.Join(optional, ",") {
		t.Fatalf("OptionalFields = %#v, want %#v", standard.OptionalFields, optional)
	}
	if standard.Template != "templates/admission/defect-fix-missing.md" {
		t.Fatalf("Template = %s", standard.Template)
	}
	if standard.Guidance["problem_branch"].Label != "问题分支" {
		t.Fatalf("problem_branch guidance = %#v", standard.Guidance["problem_branch"])
	}
	if !standard.PreFixGate.Required || !standard.PreFixGate.MustConfirm {
		t.Fatalf("PreFixGate = %#v", standard.PreFixGate)
	}
}

func TestCheckReportsMissingFieldsInAdmissionOrder(t *testing.T) {
	standard := Standard{
		TaskClass:      "bug_fix",
		RequiredFields: []string{"problem_branch", "target_branch", "problem_summary"},
		Guidance: map[string]FieldGuidance{
			"problem_branch":  {Label: "问题分支"},
			"target_branch":   {Label: "修复分支"},
			"problem_summary": {Label: "问题现象"},
		},
	}

	result := Check(standard, map[string]string{
		"target_branch": "develop",
	})

	if result.OK {
		t.Fatalf("Check OK, want missing fields")
	}
	want := []string{"problem_branch", "problem_summary"}
	if strings.Join(result.MissingFields, ",") != strings.Join(want, ",") {
		t.Fatalf("MissingFields = %#v, want %#v", result.MissingFields, want)
	}
	if len(result.Guidance) != 2 || result.Guidance[0].Label != "问题分支" || result.Guidance[1].Label != "问题现象" {
		t.Fatalf("Guidance = %#v", result.Guidance)
	}
}
