package feedback

import (
	"bufio"
	"encoding/json"
	"errors"
	"fmt"
	"os"
	"path/filepath"
	"strings"
	"time"
)

type Event struct {
	Timestamp                 string   `json:"timestamp"`
	Workspace                 string   `json:"workspace"`
	AgenticRunID              string   `json:"agentic_run_id"`
	IssueKey                  string   `json:"issue_key,omitempty"`
	AgentTaskOpsVersion       string   `json:"agentic_cli_version"`
	VersionState              string   `json:"version_state"`
	AssetVersion              string   `json:"asset_version"`
	TaskType                  string   `json:"task_type"`
	Operation                 string   `json:"operation"`
	CurrentStage              string   `json:"current_stage"`
	AgenticNextAction         string   `json:"agentic_next_action"`
	AgentID                   string   `json:"agent_id,omitempty"`
	AgenticID                 string   `json:"agentic_id,omitempty"`
	AgenticTakeoverAt         string   `json:"agentic_takeover_at,omitempty"`
	AgenticHeartbeatAt        string   `json:"agentic_heartbeat_at,omitempty"`
	TargetRepo                string   `json:"target_repo,omitempty"`
	CompletedAt               string   `json:"completed_at,omitempty"`
	AgenticCompletionEvidence string   `json:"agentic_completion_evidence,omitempty"`
	TaskClass                 string   `json:"task_class,omitempty"`
	TaskClassSource           string   `json:"task_class_source,omitempty"`
	ProcessID                 string   `json:"process_id,omitempty"`
	CapabilityID              string   `json:"capability_id,omitempty"`
	DefectComplexity          string   `json:"defect_complexity,omitempty"`
	AgenticIDCleared          bool     `json:"agentic_id_cleared,omitempty"`
	AuditTarget               string   `json:"audit_target,omitempty"`
	AuditSubmitted            bool     `json:"audit_submitted,omitempty"`
	AuditReference            string   `json:"audit_reference,omitempty"`
	ResolutionType            string   `json:"resolution_type,omitempty"`
	ResolutionVersion         string   `json:"resolution_version,omitempty"`
	ResolutionStatus          string   `json:"resolution_status,omitempty"`
	OK                        bool     `json:"ok"`
	Code                      string   `json:"code,omitempty"`
	MissingField              string   `json:"missing_field,omitempty"`
	MissingFields             []string `json:"missing_fields,omitempty"`
	Gate                      string   `json:"gate"`
	GateStatus                string   `json:"gate_status"`
	HumanGate                 bool     `json:"human_gate"`
	RequiresHumanAction       bool     `json:"requires_human_action"`
}

func AgenticRunID(issueKey string, taskType string, now time.Time, suffix string) string {
	cleanIssue := strings.ReplaceAll(strings.TrimSpace(issueKey), " ", "-")
	cleanTask := strings.TrimPrefix(strings.TrimSpace(taskType), "task_")
	return fmt.Sprintf("%s-%s-%s-%s", cleanIssue, cleanTask, now.Format("20060102150405"), suffix)
}

func AppendEvent(path string, event Event) error {
	if err := os.MkdirAll(filepath.Dir(path), 0o755); err != nil {
		return err
	}
	encoded, err := json.Marshal(event)
	if err != nil {
		return err
	}
	file, err := os.OpenFile(path, os.O_CREATE|os.O_APPEND|os.O_WRONLY, 0o644)
	if err != nil {
		return err
	}
	defer file.Close()
	_, err = file.Write(append(encoded, '\n'))
	return err
}

func ReadEvents(path string) ([]Event, error) {
	file, err := os.Open(path)
	if err != nil {
		if errors.Is(err, os.ErrNotExist) {
			return nil, nil
		}
		return nil, err
	}
	defer file.Close()

	var events []Event
	scanner := bufio.NewScanner(file)
	for scanner.Scan() {
		line := strings.TrimSpace(scanner.Text())
		if line == "" {
			continue
		}
		var event Event
		if err := json.Unmarshal([]byte(line), &event); err != nil {
			return nil, err
		}
		events = append(events, event)
	}
	if err := scanner.Err(); err != nil {
		return nil, err
	}
	return events, nil
}
