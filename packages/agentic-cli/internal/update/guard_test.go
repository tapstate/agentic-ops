package update

import (
	"strings"
	"testing"
)

func TestGuardOperationBlocksOnlyManifestOperationsForRequiredUpdate(t *testing.T) {
	installDir := t.TempDir()
	result := CheckResult{
		CurrentVersion:      "RES-v0.1.11-a68372d",
		CurrentAssetVersion: "AST-v0.1.11-a68372d",
		LatestVersion:       "RES-v0.1.20-deadbee",
		AssetVersion:        "AST-v0.1.20-deadbee",
		CompatibilityPolicy: "exact_pair",
		CompatibilityState:  "update_required",
		MigrationRequired:   true,
		Severity:            "required",
		BlockedOperations:   []string{"takeover_task", "write_evidence"},
	}
	if err := SaveCheckState(installDir, result); err != nil {
		t.Fatalf("SaveCheckState error = %v", err)
	}

	err := GuardOperation(installDir, "takeover_task")
	if err == nil || !strings.Contains(err.Error(), "required_update_blocked") {
		t.Fatalf("err = %v", err)
	}
	if err := GuardOperation(installDir, "inspect_task"); err != nil {
		t.Fatalf("inspect_task unexpectedly blocked: %v", err)
	}
}

func TestGuardOperationAlwaysExemptsRecoveryCommands(t *testing.T) {
	installDir := t.TempDir()
	result := CheckResult{
		CompatibilityState: "update_required",
		Severity:           "required",
		BlockedOperations:  []string{"doctor", "preflight", "update_check", "update_apply", "update_rollback"},
	}
	if err := SaveCheckState(installDir, result); err != nil {
		t.Fatalf("SaveCheckState error = %v", err)
	}
	for _, operation := range []string{"doctor", "preflight", "update_check", "update_apply", "update_rollback"} {
		if err := GuardOperation(installDir, operation); err != nil {
			t.Fatalf("%s unexpectedly blocked: %v", operation, err)
		}
	}
}

func TestGuardOperationDoesNotUseNetworkOrBlockWithoutLocalState(t *testing.T) {
	if err := GuardOperation(t.TempDir(), "takeover_task"); err != nil {
		t.Fatalf("GuardOperation error = %v", err)
	}
}
