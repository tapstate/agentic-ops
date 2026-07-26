package clihandlers

import (
	"context"
	"io"

	gitops "github.com/tapstate/agentic-ops/packages/agentic-cli/internal/git"
	"github.com/tapstate/agentic-ops/packages/agentic-cli/internal/github"
	"github.com/tapstate/agentic-ops/packages/agentic-cli/internal/profile"
)

type JiraClientSelection = jiraClientSelection

func SetVersionInfo(version string, versionState string, iterationVersion string, commitIndex string, commit string, buildTime string) {
	Version = version
	VersionState = versionState
	IterationVersion = iterationVersion
	CommitIndex = commitIndex
	Commit = commit
	BuildTime = buildTime
}

func RunDoctor(args []string, stdout io.Writer) int {
	return runDoctor(args, stdout)
}

func RunPreflight(args []string, stdout io.Writer) int {
	return runPreflight(args, stdout)
}

func RunWorkspaceInit(args []string, stdout io.Writer) int {
	return runWorkspaceInit(args, nil, stdout, io.Discard, false)
}

func RunWorkspaceInitInteractive(args []string, stdin io.Reader, stdout io.Writer, stderr io.Writer, interactive bool) int {
	return runWorkspaceInit(args, stdin, stdout, stderr, interactive)
}

func RunAgentInit(args []string, stdout io.Writer) int {
	return runAgentInit(args, stdout)
}

func RunAssetsInstall(args []string, stdout io.Writer) int {
	return runAssetsInstall(args, stdout)
}

func RunUpdateCheck(args []string, stdout io.Writer) int {
	return runUpdateCheck(args, stdout)
}

func RunUpdateApply(args []string, stdout io.Writer) int {
	return runUpdateApply(args, stdout)
}

func RunListTasks(args []string, stdout io.Writer) int {
	return runListTasks(args, stdout)
}

func RunContractValidate(args []string, stdout io.Writer) int {
	return runContractValidate(args, stdout)
}

func RunProfileValidate(args []string, stdout io.Writer) int {
	return runProfileValidate(args, stdout)
}

func RunProfileResolve(args []string, stdout io.Writer) int {
	return runProfileResolve(args, stdout)
}

func RunProfileUpdate(args []string, stdout io.Writer) int {
	return runProfileUpdate(args, stdout)
}

func RunProfileRollback(args []string, stdout io.Writer) int {
	return runProfileRollback(args, stdout)
}

func RunPolicyValidate(args []string, stdout io.Writer) int {
	return runPolicyValidate(args, stdout)
}

func RunPolicyUpdate(args []string, stdout io.Writer) int {
	return runPolicyUpdate(args, stdout)
}

func RunPolicyRollback(args []string, stdout io.Writer) int {
	return runPolicyRollback(args, stdout)
}

func RunTakeoverTask(args []string, stdout io.Writer) int {
	return runTakeoverTask(args, stdout)
}

func RunResumeTakeover(args []string, stdout io.Writer) int {
	return runResumeTakeover(args, stdout)
}

func RunTaskRun(args []string, stdout io.Writer) int {
	return runTaskRun(args, stdout)
}

func RunWriteEvidence(args []string, stdout io.Writer) int {
	return runWriteEvidence(args, stdout)
}

func RunReleaseAgent(args []string, stdout io.Writer) int {
	return runReleaseAgent(args, stdout)
}

func RunInspectWorkspace(args []string, stdout io.Writer) int {
	return runInspectWorkspace(args, stdout)
}

func RunBranchAlign(args []string, stdout io.Writer) int {
	return runBranchAlign(args, stdout)
}

func RunPreparePR(args []string, stdout io.Writer) int {
	return runPreparePR(args, stdout)
}

func RunReadPRComments(args []string, stdout io.Writer) int {
	return runReadPRComments(args, stdout)
}

func RunCheckCIStatus(args []string, stdout io.Writer) int {
	return runCheckCIStatus(args, stdout)
}

func RunFixPRComments(args []string, stdout io.Writer) int {
	return runFixPRComments(args, stdout)
}

func RunFeedbackReport(args []string, stdout io.Writer) int {
	return runFeedbackReport(args, stdout)
}

func RunFeedbackBundle(args []string, stdout io.Writer) int {
	return runFeedbackBundle(args, stdout)
}

func JiraTakeoverFields(workspaceProfile profile.Profile, currentAgentID string, takeoverAt string) map[string]any {
	return jiraTakeoverFields(workspaceProfile, currentAgentID, takeoverAt)
}

func JiraReleaseFields(workspaceProfile profile.Profile) map[string]any {
	return jiraReleaseFields(workspaceProfile)
}

func DefaultJiraClient(workspaceName string, workspaceProfile profile.Profile) (JiraClientSelection, error) {
	return defaultJiraClient(workspaceName, workspaceProfile)
}

func SetRunGitHubAuthStatusForTest(fn func(context.Context) error) func() {
	original := runGitHubAuthStatus
	runGitHubAuthStatus = fn
	return func() { runGitHubAuthStatus = original }
}

func SetCommandAvailableForTest(fn func(string) bool) func() {
	original := commandAvailable
	commandAvailable = fn
	return func() { commandAvailable = original }
}

func SetInspectGitWorkspaceForTest(fn func(context.Context, string) (gitops.WorkspaceStatus, error)) func() {
	original := inspectGitWorkspace
	inspectGitWorkspace = fn
	return func() { inspectGitWorkspace = original }
}

func SetGitHubClientForTest(client github.Client) func() {
	original := gitHubClient
	gitHubClient = client
	return func() { gitHubClient = original }
}

func SetJiraClientSelectorForTest(fn func(string, profile.Profile) (JiraClientSelection, error)) func() {
	original := selectJiraClient
	selectJiraClient = fn
	return func() { selectJiraClient = original }
}
