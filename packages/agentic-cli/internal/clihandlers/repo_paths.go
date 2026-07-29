package clihandlers

import (
	"errors"
	"github.com/tapstate/agentic-ops/packages/agentic-cli/internal/config"
	"github.com/tapstate/agentic-ops/packages/agentic-cli/internal/contract"
	"github.com/tapstate/agentic-ops/packages/agentic-cli/internal/process"
	"github.com/tapstate/agentic-ops/packages/agentic-cli/internal/profile"
	"os"
	"path/filepath"
	"strings"
)

func workspaceRoot() (string, error) {
	if root := os.Getenv("AGENTIC_OPS_WORKSPACE_ROOT"); root != "" {
		return root, nil
	}
	return os.Getwd()
}

func repoRoot() (string, error) {
	dir, err := os.Getwd()
	if err != nil {
		return "", err
	}
	if root, err := findRepoRootFrom(dir); err == nil {
		return root, nil
	}
	home, _ := os.UserHomeDir()
	installDir := os.Getenv("AGENTIC_OPS_HOME")
	if installDir == "" {
		installDir = config.DefaultInstallDir(home)
	}
	if root, err := findRepoRootFrom(installDir); err == nil {
		return root, nil
	}
	return "", os.ErrNotExist
}

func findRepoRootFrom(dir string) (string, error) {
	if dir == "" {
		return "", os.ErrNotExist
	}
	for {
		if _, err := os.Stat(filepath.Join(dir, "go.mod")); err == nil {
			if _, err := os.Stat(filepath.Join(repoBasicResourcesPath(dir), "contracts", "operations")); err == nil {
				return dir, nil
			}
		}
		parent := filepath.Dir(dir)
		if parent == dir {
			return "", os.ErrNotExist
		}
		dir = parent
	}
}

func repoBasicResourcesPath(root string) string {
	return filepath.Join(root, "install-resources", "basic")
}

func repoProfilePath(workspaceName string) (string, error) {
	return repoProjectProfilePath(workspaceName)
}

func repoProjectPath(workspaceName string) (string, error) {
	root, err := repoRoot()
	if err != nil {
		return "", err
	}
	return filepath.Join(repoBasicResourcesPath(root), "projects", workspaceName), nil
}

func repoProjectProfilePath(workspaceName string) (string, error) {
	projectPath, err := repoProjectPath(workspaceName)
	if err != nil {
		return "", err
	}
	return filepath.Join(projectPath, "profile.yaml"), nil
}

func repoCompanyPath() (string, error) {
	root, err := repoRoot()
	if err != nil {
		return "", err
	}
	return filepath.Join(repoBasicResourcesPath(root), "company"), nil
}

func repoPolicyPath() (string, error) {
	root, err := repoRoot()
	if err != nil {
		return "", err
	}
	return filepath.Join(repoBasicResourcesPath(root), "policies", "default.yaml"), nil
}

func repoProcessRegistry() (map[string]process.Process, error) {
	root, err := repoRoot()
	if err != nil {
		return nil, err
	}
	return process.LoadRegistry(filepath.Join(repoBasicResourcesPath(root), "contracts", "processes"))
}

func repoOperationContract(operation string) (contract.Operation, error) {
	root, err := repoRoot()
	if err != nil {
		return contract.Operation{}, err
	}
	path := filepath.Join(
		repoBasicResourcesPath(root),
		"contracts",
		"operations",
		strings.ReplaceAll(operation, "_", "-")+".yaml",
	)
	return contract.LoadFile(path)
}

func operationContractErrorCode(err error) string {
	if errors.Is(err, os.ErrNotExist) {
		return "operation_contract_not_found"
	}
	return "operation_contract_load_failed"
}

func defaultProcessRegistry() map[string]process.Process {
	return map[string]process.Process{
		"development_change_v1": {
			ProcessID:   "development_change_v1",
			TaskClasses: []string{"feature_change", "bug_fix", "technical_task"},
			EntryStage:  "waiting_takeover",
			Stages: []process.Stage{
				{ID: "waiting_takeover"},
				{ID: "implementation"},
				{ID: "completed"},
			},
		},
		"investigation_v1": {
			ProcessID:   "investigation_v1",
			TaskClasses: []string{"investigation"},
			EntryStage:  "waiting_takeover",
			Stages:      []process.Stage{{ID: "waiting_takeover"}, {ID: "investigation"}, {ID: "completed"}},
		},
		"agenticops_improvement_v1": {
			ProcessID:   "agenticops_improvement_v1",
			TaskClasses: []string{"process_improvement"},
			EntryStage:  "waiting_takeover",
			Stages:      []process.Stage{{ID: "waiting_takeover"}, {ID: "implementation"}, {ID: "completed"}},
		},
	}
}

func loadWorkspaceProfile(workspaceName string) (profile.Profile, error) {
	root, _ := workspaceRoot()
	return resolveEffectiveProfile(workspaceName, root)
}

func pathWithin(path string, root string) bool {
	pathAbs, err := filepath.Abs(path)
	if err != nil {
		return false
	}
	rootAbs, err := filepath.Abs(root)
	if err != nil {
		return false
	}
	rel, err := filepath.Rel(rootAbs, pathAbs)
	if err != nil {
		return false
	}
	return rel == "." || (!strings.HasPrefix(rel, ".."+string(os.PathSeparator)) && rel != "..")
}
