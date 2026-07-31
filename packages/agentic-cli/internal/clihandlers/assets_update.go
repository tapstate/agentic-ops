package clihandlers

import (
	"github.com/tapstate/agentic-ops/packages/agentic-cli/internal/assets"
	"github.com/tapstate/agentic-ops/packages/agentic-cli/internal/output"
	"github.com/tapstate/agentic-ops/packages/agentic-cli/internal/update"
	"io"
	"runtime"
)

func runAssetsInstall(args []string, stdout io.Writer) int {
	source := readFlag(args, "--source", "")
	if source == "" {
		return writeJSON(stdout, output.Failure("assets_install", "missing_source", "缺少资产源目录", "请提供 --source"))
	}
	version := readFlag(args, "--version", "")
	if version == "" {
		return writeJSON(stdout, output.Failure("assets_install", "missing_asset_version", "缺少资产版本", "请提供 --version"))
	}
	installDir := readInstallDir(args)
	result, err := assets.Install(source, installDir, version)
	if err != nil {
		return writeJSON(stdout, output.Failure("assets_install", "assets_install_failed", err.Error(), "请检查资产源目录和安装目录权限"))
	}
	return writeJSON(stdout, output.Success("assets_install", map[string]any{
		"asset_version":       result.AssetVersion,
		"assets_dir":          result.AssetsDir,
		"current":             result.CurrentPath,
		"agentic_next_action": "agent_init",
	}))
}

func runUpdateCheck(args []string, stdout io.Writer) int {
	manifestPath := readFlag(args, "--manifest", "")
	manifestURL := readFlag(args, "--manifest-url", "")
	if manifestPath == "" && manifestURL == "" {
		return writeJSON(stdout, output.FailureWithContext("update_check", output.FailureContext{
			Code:                "missing_manifest",
			Message:             "缺少 release manifest",
			RequiredHumanAction: "请提供 --manifest 或 --manifest-url",
			TaskType:            "update",
			CurrentStage:        "update_check",
			AgenticNextAction:   "ask_owner",
		}))
	}
	source := "local"
	var result update.CheckResult
	var err error
	if manifestURL != "" {
		source = "remote"
		result, err = update.CheckRemote(manifestURL, Version)
	} else {
		result, err = update.Check(manifestPath, Version)
	}
	if err != nil {
		return writeJSON(stdout, output.FailureWithContext("update_check", output.FailureContext{
			Code:                "update_manifest_invalid",
			Message:             err.Error(),
			RequiredHumanAction: "请检查 release manifest 路径、URL 和格式",
			TaskType:            "update",
			CurrentStage:        "update_check",
			AgenticNextAction:   "fix_manifest",
		}))
	}
	return writeJSON(stdout, output.Success("update_check", map[string]any{
		"source":              source,
		"current_version":     result.CurrentVersion,
		"latest_version":      result.LatestVersion,
		"asset_version":       result.AssetVersion,
		"update_available":    result.UpdateAvailable,
		"severity":            result.Severity,
		"reason":              result.Reason,
		"blocked_operations":  result.BlockedOperations,
		"agentic_next_action": result.AgenticNextAction,
	}))
}

func runUpdateApply(args []string, stdout io.Writer) int {
	manifestPath := readFlag(args, "--manifest", "")
	manifestURL := readFlag(args, "--manifest-url", "")
	if manifestPath == "" && manifestURL == "" {
		return writeJSON(stdout, output.FailureWithContext("update_apply", output.FailureContext{
			Code:                "missing_manifest",
			Message:             "缺少 release manifest",
			RequiredHumanAction: "请提供 --manifest 或 --manifest-url",
			TaskType:            "update",
			CurrentStage:        "update_apply",
			AgenticNextAction:   "ask_owner",
		}))
	}
	installDir := readInstallDir(args)
	target := readFlag(args, "--target", runtime.GOOS+"-"+runtime.GOARCH)
	source := "local"
	var result update.ApplyResult
	var err error
	if manifestURL != "" {
		source = "remote"
		result, err = update.ApplyRemote(manifestURL, installDir, target)
	} else {
		result, err = update.Apply(manifestPath, installDir)
	}
	if err != nil {
		return writeJSON(stdout, output.FailureWithContext("update_apply", output.FailureContext{
			Code:                "update_apply_failed",
			Message:             err.Error(),
			RequiredHumanAction: "请检查 release manifest、artifact checksum 和安装目录权限",
			TaskType:            "update",
			CurrentStage:        "update_apply",
			AgenticNextAction:   "fix_update_source",
		}))
	}
	downloadedArtifacts := result.DownloadedArtifacts
	if downloadedArtifacts == nil {
		downloadedArtifacts = []string{}
	}
	return writeJSON(stdout, output.Success("update_apply", map[string]any{
		"source":                 source,
		"version":                result.AgenticCLIVersion,
		"asset_version":          result.AssetVersion,
		"previous_version":       result.PreviousAgenticCLIVersion,
		"previous_asset_version": result.PreviousAssetVersion,
		"current":                result.CurrentPath,
		"downloaded_artifacts":   downloadedArtifacts,
		"activated_binary":       result.ActivatedBinary,
		"agentic_next_action":    "doctor",
	}))
}
