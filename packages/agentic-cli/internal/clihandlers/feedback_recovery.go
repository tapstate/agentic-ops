package clihandlers

import (
	"fmt"
	"io"
	"os"
	"path/filepath"
	"strings"
	"time"

	"github.com/tapstate/agentic-ops/packages/agentic-cli/internal/feedback"
	"github.com/tapstate/agentic-ops/packages/agentic-cli/internal/output"
)

const maxRecoveryEvidenceBytes = 1 << 20

func runFeedbackRecordRecovery(args []string, stdout io.Writer) int {
	if !hasFlag(args, "--confirm-recovery-record") {
		return writeJSON(stdout, output.FailureWithContext("feedback_record_recovery", output.FailureContext{
			Code:                "recovery_record_confirmation_required",
			Message:             "记录人工恢复事实需要显式确认",
			RequiredHumanAction: "请核对外部回读、证据和重试事实后添加 --confirm-recovery-record",
			TaskType:            "feedback_recovery",
			CurrentStage:        "recovery_record_gate",
			AgenticNextAction:   "ask_owner",
		}))
	}
	workspaceName := workspaceNameFromArgsOrAgentConfig(args, "default")
	runID := readFlag(args, "--run-id", "")
	if runID == "" {
		return recoveryInputFailure(stdout, "missing_agentic_run_id", "缺少 agentic_run_id", "请提供 --run-id")
	}
	originalOperation := readFlag(args, "--original-operation", "")
	if originalOperation == "" {
		return recoveryInputFailure(stdout, "missing_original_operation", "缺少原始操作", "请提供 --original-operation")
	}
	originalCode := readFlag(args, "--original-code", "")
	if originalCode == "" {
		return recoveryInputFailure(stdout, "missing_original_code", "缺少原始错误码", "请提供 --original-code")
	}
	evidenceFile := readFlag(args, "--evidence-file", "")
	if evidenceFile == "" {
		return recoveryInputFailure(stdout, "missing_evidence_file", "缺少恢复证据文件", "请提供 --evidence-file")
	}
	externalReference := strings.TrimSpace(readFlag(args, "--external-reference", ""))
	if externalReference == "" {
		return recoveryInputFailure(stdout, "missing_external_reference", "缺少外部事实引用", "请提供 --external-reference")
	}
	if len(externalReference) > 2048 || strings.ContainsAny(externalReference, "\r\n") {
		return recoveryInputFailure(stdout, "invalid_external_reference", "外部事实引用格式无效", "请提供不含换行且不超过 2048 字节的脱敏引用")
	}

	readbackVerified, code, err := explicitBoolFlag(args, "--readback-verified")
	if err != nil {
		return recoveryInputFailure(stdout, "invalid_boolean_flag", err.Error(), "请使用 --readback-verified=true|false")
	}
	if code == "" {
		return recoveryInputFailure(stdout, "missing_readback_verified", "缺少回读确认事实", "请显式提供 --readback-verified=true|false")
	}
	if !readbackVerified {
		return recoveryInputFailure(stdout, "readback_not_verified", "外部事实尚未通过回读确认", "请先从外部事实源回读并确认结果，再使用 --readback-verified=true 记录恢复证据")
	}
	remoteWriteCompleted, code, err := explicitBoolFlag(args, "--remote-write-completed")
	if err != nil {
		return recoveryInputFailure(stdout, "invalid_boolean_flag", err.Error(), "请使用 --remote-write-completed=true|false")
	}
	if code == "" {
		return recoveryInputFailure(stdout, "missing_remote_write_completed", "缺少远端写入完成事实", "请显式提供 --remote-write-completed=true|false")
	}
	retrySafe, code, err := explicitBoolFlag(args, "--retry-safe")
	if err != nil {
		return recoveryInputFailure(stdout, "invalid_boolean_flag", err.Error(), "请使用 --retry-safe=true|false")
	}
	if code == "" {
		return recoveryInputFailure(stdout, "missing_retry_safe", "缺少重试安全事实", "请显式提供 --retry-safe=true|false")
	}

	root, err := workspaceRoot()
	if err != nil {
		return writeJSON(stdout, output.Failure("feedback_record_recovery", "workspace_root_failed", err.Error(), "请在项目 AI 工作空间中重试"))
	}
	evidenceContent, resolvedEvidencePath, err := readRecoveryEvidence(root, evidenceFile)
	if err != nil {
		code := "recovery_evidence_read_failed"
		if strings.HasPrefix(err.Error(), "outside workspace:") {
			code = "recovery_evidence_outside_workspace"
		}
		return writeJSON(stdout, output.Failure("feedback_record_recovery", code, err.Error(), "请提供当前工作空间内的普通证据文件"))
	}
	runState, err := evidenceRunState(root, workspaceName, runID)
	if err != nil {
		return writeJSON(stdout, output.FailureWithContext("feedback_record_recovery", output.FailureContext{
			Code:                evidenceStateErrorCode(err),
			Message:             err.Error(),
			RequiredHumanAction: "请检查 run、workspace 和当前 AIAgent 绑定事实",
			TaskType:            "feedback_recovery",
			CurrentStage:        "recovery_record_gate",
			AgenticNextAction:   "ask_owner",
		}))
	}
	recovery := &feedback.RecoveryRecord{
		OriginalOperation:    originalOperation,
		OriginalCode:         originalCode,
		EvidenceSHA256:       feedback.EvidenceSHA256(evidenceContent),
		ExternalReference:    externalReference,
		ReadbackVerified:     readbackVerified,
		RemoteWriteCompleted: remoteWriteCompleted,
		RetrySafe:            retrySafe,
	}
	event := feedback.Event{
		Timestamp:           time.Now().UTC().Format(time.RFC3339),
		Workspace:           workspaceName,
		AgenticRunID:        runID,
		IssueKey:            runState.IssueKey,
		AgentTaskOpsVersion: Version,
		VersionState:        VersionState,
		AssetVersion:        readAssetVersion(),
		TaskType:            "feedback_recovery",
		Operation:           "feedback_record_recovery",
		CurrentStage:        "recovery_recorded",
		AgenticNextAction:   "review_proposals",
		AgentID:             runState.AgentID,
		AgenticID:           runState.AgenticID,
		TargetRepo:          runState.TargetRepo,
		TaskClass:           runState.TaskClass,
		ProcessID:           runState.ProcessID,
		AuditTarget:         "local_file",
		AuditSubmitted:      true,
		AuditReference:      resolvedEvidencePath,
		OK:                  true,
		Gate:                "recovery_record_confirmation",
		GateStatus:          "passed",
		HumanGate:           true,
		Recovery:            recovery,
	}
	fingerprint, appended, err := feedback.AppendRecoveryEvent(filepath.Join(root, ".agentic-ops", "feedback", "events.ndjson"), event)
	if err != nil {
		return writeJSON(stdout, output.Failure("feedback_record_recovery", "event_write_failed", err.Error(), "请检查工作空间反馈目录权限"))
	}
	return writeJSON(stdout, output.Success("feedback_record_recovery", map[string]any{
		"workspace":              workspaceName,
		"agentic_run_id":         runID,
		"issue_key":              runState.IssueKey,
		"original_operation":     originalOperation,
		"original_code":          originalCode,
		"evidence_sha256":        recovery.EvidenceSHA256,
		"external_reference":     externalReference,
		"readback_verified":      readbackVerified,
		"remote_write_completed": remoteWriteCompleted,
		"retry_safe":             retrySafe,
		"fingerprint":            fingerprint,
		"appended":               appended,
		"current_stage":          "recovery_recorded",
		"agentic_next_action":    "review_proposals",
	}))
}

func explicitBoolFlag(args []string, name string) (value bool, presentCode string, err error) {
	for _, arg := range args {
		if arg == name {
			return true, name, nil
		}
		prefix := name + "="
		if !strings.HasPrefix(arg, prefix) {
			continue
		}
		switch strings.TrimPrefix(arg, prefix) {
		case "true":
			return true, name, nil
		case "false":
			return false, name, nil
		default:
			return false, name, fmt.Errorf("%s 必须是 true 或 false", name)
		}
	}
	return false, "", nil
}

func readRecoveryEvidence(root string, requestedPath string) ([]byte, string, error) {
	rootPath, err := filepath.Abs(root)
	if err != nil {
		return nil, "", err
	}
	rootPath, err = filepath.EvalSymlinks(rootPath)
	if err != nil {
		return nil, "", err
	}
	path := requestedPath
	if !filepath.IsAbs(path) {
		path = filepath.Join(rootPath, path)
	}
	path, err = filepath.Abs(path)
	if err != nil {
		return nil, "", err
	}
	path, err = filepath.EvalSymlinks(path)
	if err != nil {
		return nil, "", err
	}
	relative, err := filepath.Rel(rootPath, path)
	if err != nil {
		return nil, "", err
	}
	if relative == ".." || strings.HasPrefix(relative, ".."+string(filepath.Separator)) {
		return nil, "", fmt.Errorf("outside workspace: %s", requestedPath)
	}
	file, err := os.Open(path)
	if err != nil {
		return nil, "", err
	}
	defer file.Close()
	info, err := file.Stat()
	if err != nil {
		return nil, "", err
	}
	if !info.Mode().IsRegular() {
		return nil, "", fmt.Errorf("evidence file is not a regular file")
	}
	content, err := io.ReadAll(io.LimitReader(file, maxRecoveryEvidenceBytes+1))
	if err != nil {
		return nil, "", err
	}
	if len(content) > maxRecoveryEvidenceBytes {
		return nil, "", fmt.Errorf("evidence file exceeds %d bytes", maxRecoveryEvidenceBytes)
	}
	if len(content) == 0 {
		return nil, "", fmt.Errorf("evidence file is empty")
	}
	return content, path, nil
}

func recoveryInputFailure(stdout io.Writer, code string, message string, action string) int {
	return writeJSON(stdout, output.FailureWithContext("feedback_record_recovery", output.FailureContext{
		Code:                code,
		Message:             message,
		RequiredHumanAction: action,
		TaskType:            "feedback_recovery",
		CurrentStage:        "recovery_record_gate",
		AgenticNextAction:   "ask_owner",
	}))
}
