package feedback

import (
	"crypto/sha256"
	"fmt"
	"path/filepath"
	"testing"
)

func testRecoveryEvent() Event {
	evidence := []byte("恢复证据\n")
	return Event{
		Timestamp:    "2026-08-11T10:00:00Z",
		Workspace:    "tapstate",
		AgenticRunID: "run-1",
		IssueKey:     "TAP-123",
		AgentID:      "agent-1",
		Operation:    "feedback_record_recovery",
		OK:           true,
		Recovery: &RecoveryRecord{
			OriginalOperation:    "check_ci_status",
			OriginalCode:         "github_ci_read_failed",
			EvidenceSHA256:       EvidenceSHA256(evidence),
			ExternalReference:    "https://github.example/pull/1#checks",
			ReadbackVerified:     true,
			RemoteWriteCompleted: true,
			RetrySafe:            false,
		},
	}
}

func TestEvidenceSHA256AndRecoveryFingerprintAreStable(t *testing.T) {
	evidence := []byte("恢复证据\n")
	wantDigest := fmt.Sprintf("%x", sha256.Sum256(evidence))
	if got := EvidenceSHA256(evidence); got != wantDigest {
		t.Fatalf("EvidenceSHA256 = %s, want %s", got, wantDigest)
	}
	event := testRecoveryEvent()
	fingerprint := RecoveryFingerprint(event)
	if fingerprint == "" {
		t.Fatal("RecoveryFingerprint returned empty value")
	}
	changed := event
	changed.AgenticRunID = "run-2"
	if RecoveryFingerprint(changed) == fingerprint {
		t.Fatal("fingerprint did not change when run identity changed")
	}
	changed = event
	changed.Recovery = &RecoveryRecord{}
	*changed.Recovery = *event.Recovery
	changed.Recovery.OriginalOperation = "write_pr_evidence"
	if RecoveryFingerprint(changed) == fingerprint {
		t.Fatal("fingerprint did not change when operation identity changed")
	}
}

func TestAppendRecoveryEventIsIdempotent(t *testing.T) {
	path := filepath.Join(t.TempDir(), "events.ndjson")
	event := testRecoveryEvent()
	firstFingerprint, firstAppended, err := AppendRecoveryEvent(path, event)
	if err != nil {
		t.Fatalf("first append error = %v", err)
	}
	secondFingerprint, secondAppended, err := AppendRecoveryEvent(path, event)
	if err != nil {
		t.Fatalf("second append error = %v", err)
	}
	if !firstAppended || secondAppended {
		t.Fatalf("appended = %t then %t, want true then false", firstAppended, secondAppended)
	}
	if firstFingerprint != secondFingerprint {
		t.Fatalf("fingerprints differ: %s != %s", firstFingerprint, secondFingerprint)
	}
	events, err := ReadEvents(path)
	if err != nil {
		t.Fatalf("ReadEvents error = %v", err)
	}
	if len(events) != 1 {
		t.Fatalf("events = %d, want 1", len(events))
	}
}

func TestAnalyzeAndProposeIncludeRecoveredFailure(t *testing.T) {
	event := testRecoveryEvent()
	analysis := Analyze([]Event{event})
	if len(analysis.RecoveryPatterns) != 1 || analysis.RecoveryPatterns[0].Key != "github_ci_read_failed" {
		t.Fatalf("recovery patterns = %+v", analysis.RecoveryPatterns)
	}
	proposals := Propose([]Event{event})
	if len(proposals) != 1 {
		t.Fatalf("proposals = %+v", proposals)
	}
	if proposals[0].EvidenceCount != 1 || proposals[0].RecoveredCount != 1 || len(proposals[0].EvidenceRefs) != 1 {
		t.Fatalf("proposal = %+v", proposals[0])
	}
}

func TestProposeDeduplicatesOriginalFailureAndRecoveryEvidence(t *testing.T) {
	recovery := testRecoveryEvent()
	failure := Event{
		Timestamp:    "2026-08-11T09:00:00Z",
		Workspace:    recovery.Workspace,
		AgenticRunID: recovery.AgenticRunID,
		IssueKey:     recovery.IssueKey,
		AgentID:      recovery.AgentID,
		Operation:    recovery.Recovery.OriginalOperation,
		OK:           false,
		Code:         recovery.Recovery.OriginalCode,
	}

	proposals := Propose([]Event{failure, recovery})
	if len(proposals) != 1 {
		t.Fatalf("proposals = %+v", proposals)
	}
	if proposals[0].EvidenceCount != 1 || proposals[0].RecoveredCount != 1 || len(proposals[0].EvidenceRefs) != 1 {
		t.Fatalf("proposal = %+v", proposals[0])
	}
}
