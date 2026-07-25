package git

import (
	"bufio"
	"context"
	"errors"
	"fmt"
	"os"
	"path/filepath"
	"regexp"
	"sort"
	"strconv"
	"strings"
	"time"
)

var (
	ErrInvalidBranch          = errors.New("invalid branch")
	ErrMissingTapdataBranch   = errors.New("tapdata branch not found")
	ErrBranchAlignmentBlocked = errors.New("branch alignment plan is blocked")
)

const (
	tapdataPluginPath       = "iengine/iengine-app/src/main/resources/pluginKit.properties"
	tapdataPluginVersionKey = "tapdata.api.verison"
)

var (
	tapdataCoreRepos = []string{
		"tapdata",
		"tapdata-enterprise",
		"tapdata-web",
		"tapdata-connectors",
		"tapdata-connectors-enterprise",
		"tapdata-license",
		"tapdata-common-lib",
	}
	tapdataKeepRepos = []string{
		"tapdata-application",
	}
)

type BranchAlignmentRequest struct {
	WorkRoot   string
	Remote     string
	BranchSpec string
	NoFetch    bool
}

type BranchAlignmentPlan struct {
	WorkRoot   string               `json:"work_root"`
	Remote     string               `json:"remote"`
	BranchSpec string               `json:"branch_spec"`
	TapBranch  string               `json:"tap_branch"`
	EntBranch  string               `json:"ent_branch,omitempty"`
	WebBranch  string               `json:"web_branch,omitempty"`
	Blocked    bool                 `json:"blocked"`
	Rows       []BranchAlignmentRow `json:"rows"`
}

type BranchAlignmentRow struct {
	Repo    string `json:"repo"`
	Current string `json:"current"`
	Target  string `json:"target"`
	Action  string `json:"action"`
	Reason  string `json:"reason"`
	Dirty   bool   `json:"dirty"`
}

type BranchAlignmentStatusRow struct {
	Repo        string `json:"repo"`
	Current     string `json:"current"`
	Upstream    string `json:"upstream"`
	AheadBehind string `json:"ahead_behind"`
	Dirty       bool   `json:"dirty"`
	Missing     bool   `json:"missing"`
}

func TapdataAlignmentRepos() []string {
	repos := append([]string{}, tapdataCoreRepos...)
	repos = append(repos, tapdataKeepRepos...)
	return repos
}

func PlanTapdataBranchAlignment(ctx context.Context, request BranchAlignmentRequest) (BranchAlignmentPlan, error) {
	request = normalizeBranchAlignmentRequest(request)
	tapBranch, entBranch, webBranch, err := parseBranchSpec(request.BranchSpec)
	if err != nil {
		return BranchAlignmentPlan{}, err
	}
	plan := BranchAlignmentPlan{
		WorkRoot:   request.WorkRoot,
		Remote:     request.Remote,
		BranchSpec: request.BranchSpec,
		TapBranch:  tapBranch,
		EntBranch:  entBranch,
		WebBranch:  webBranch,
	}
	if !repoExists(filepath.Join(request.WorkRoot, "tapdata")) {
		return plan, fmt.Errorf("tapdata repo missing under work root %q", request.WorkRoot)
	}
	if !branchExists(ctx, filepath.Join(request.WorkRoot, "tapdata"), request.Remote, tapBranch) {
		return plan, fmt.Errorf("%w: %s", ErrMissingTapdataBranch, tapBranch)
	}

	pluginRelease, pluginReleaseReason := tapdataPluginRelease(ctx, request.WorkRoot, request.Remote, tapBranch)
	tapMarker := tapdataBranchMarker(tapBranch)
	for _, repo := range tapdataCoreRepos {
		row := BranchAlignmentRow{Repo: repo}
		repoRoot := filepath.Join(request.WorkRoot, repo)
		if !repoExists(repoRoot) {
			row.Current = "MISSING"
			row.Target = "UNRESOLVED"
			row.Action = "blocked"
			row.Reason = "repo missing under work root"
			row.Dirty = false
			plan.Rows = append(plan.Rows, row)
			plan.Blocked = true
			continue
		}
		row.Current = currentBranchOrUnknown(ctx, repoRoot)
		row.Dirty = workspaceDirty(ctx, repoRoot)
		row.Target, row.Reason = targetForTapdataRepo(ctx, request.WorkRoot, request.Remote, repo, tapBranch, entBranch, webBranch, pluginRelease, pluginReleaseReason, tapMarker)
		if row.Target == "UNRESOLVED" {
			row.Action = "blocked"
			plan.Blocked = true
		} else if row.Target == row.Current {
			row.Action = "skip"
		} else {
			row.Action = "switch"
		}
		plan.Rows = append(plan.Rows, row)
	}
	for _, repo := range tapdataKeepRepos {
		row := BranchAlignmentRow{Repo: repo, Target: "KEEP_CURRENT", Action: "keep", Reason: "default not aligned"}
		repoRoot := filepath.Join(request.WorkRoot, repo)
		if !repoExists(repoRoot) {
			row.Current = "MISSING"
		} else {
			row.Current = currentBranchOrUnknown(ctx, repoRoot)
			row.Dirty = workspaceDirty(ctx, repoRoot)
		}
		plan.Rows = append(plan.Rows, row)
	}
	return plan, nil
}

func FetchTapdataAlignmentRepos(ctx context.Context, workRoot string, remote string, all bool) error {
	remote = normalizeRemote(remote)
	repos := []string{"tapdata"}
	if all {
		repos = tapdataCoreRepos
	}
	for _, repo := range repos {
		repoRoot := filepath.Join(workRoot, repo)
		if !repoExists(repoRoot) {
			continue
		}
		if _, err := runGit(ctx, repoRoot, "fetch", "--prune", remote); err != nil {
			return err
		}
	}
	return nil
}

func ApplyTapdataBranchAlignment(ctx context.Context, plan BranchAlignmentPlan) ([]BranchAlignmentRow, error) {
	if plan.Blocked {
		return plan.Rows, ErrBranchAlignmentBlocked
	}
	var switched []BranchAlignmentRow
	stashMessage := "tap-align-" + time.Now().Format("20060102150405")
	for _, row := range plan.Rows {
		if row.Action != "switch" {
			continue
		}
		repoRoot := filepath.Join(plan.WorkRoot, row.Repo)
		stashed := false
		if row.Dirty {
			if _, err := runGit(ctx, repoRoot, "stash", "push", "-u", "-m", stashMessage); err != nil {
				return switched, err
			}
			stashed = true
		}
		if err := switchToBranch(ctx, repoRoot, plan.Remote, row.Target); err != nil {
			return switched, err
		}
		if stashed {
			if _, err := runGit(ctx, repoRoot, "stash", "pop"); err != nil {
				return switched, err
			}
		}
		switched = append(switched, row)
	}
	return switched, nil
}

func ListTapdataBranches(ctx context.Context, workRoot string, remote string, filter string) ([]string, error) {
	repoRoot := filepath.Join(workRoot, "tapdata")
	if !repoExists(repoRoot) {
		return nil, fmt.Errorf("tapdata repo missing under work root %q", workRoot)
	}
	branches := allBranches(ctx, repoRoot, normalizeRemote(remote))
	if filter != "" {
		filtered := []string{}
		for _, branch := range branches {
			if strings.Contains(branch, filter) {
				filtered = append(filtered, branch)
			}
		}
		branches = filtered
	}
	sort.Strings(branches)
	return branches, nil
}

func TapdataAlignmentStatus(ctx context.Context, workRoot string) []BranchAlignmentStatusRow {
	var rows []BranchAlignmentStatusRow
	for _, repo := range TapdataAlignmentRepos() {
		repoRoot := filepath.Join(workRoot, repo)
		row := BranchAlignmentStatusRow{Repo: repo}
		if !repoExists(repoRoot) {
			row.Current = "MISSING"
			row.Missing = true
			rows = append(rows, row)
			continue
		}
		row.Current = currentBranchOrUnknown(ctx, repoRoot)
		row.Upstream = upstreamOrEmpty(ctx, repoRoot)
		row.AheadBehind = aheadBehindOrEmpty(ctx, repoRoot)
		row.Dirty = workspaceDirty(ctx, repoRoot)
		rows = append(rows, row)
	}
	return rows
}

func parseBranchSpec(spec string) (string, string, string, error) {
	parts := strings.Split(strings.TrimSpace(spec), ",")
	if len(parts) == 0 || strings.TrimSpace(parts[0]) == "" {
		return "", "", "", ErrInvalidBranch
	}
	if len(parts) > 3 {
		return "", "", "", ErrInvalidBranch
	}
	for len(parts) < 3 {
		parts = append(parts, "")
	}
	tapBranch := strings.TrimSpace(parts[0])
	entBranch := strings.TrimSpace(parts[1])
	webBranch := strings.TrimSpace(parts[2])
	for _, branch := range []string{tapBranch, entBranch, webBranch} {
		if branch != "" && !isSafeBranchName(branch) {
			return "", "", "", ErrInvalidBranch
		}
	}
	return tapBranch, entBranch, webBranch, nil
}

func normalizeBranchAlignmentRequest(request BranchAlignmentRequest) BranchAlignmentRequest {
	request.WorkRoot = filepath.Clean(strings.TrimSpace(request.WorkRoot))
	request.Remote = normalizeRemote(request.Remote)
	request.BranchSpec = strings.TrimSpace(request.BranchSpec)
	return request
}

func normalizeRemote(remote string) string {
	remote = strings.TrimSpace(remote)
	if remote == "" {
		return "origin"
	}
	return remote
}

func repoExists(root string) bool {
	info, err := os.Stat(filepath.Join(root, ".git"))
	return err == nil && (info.IsDir() || info.Mode().IsRegular())
}

func currentBranchOrUnknown(ctx context.Context, root string) string {
	branch, err := runGit(ctx, root, "rev-parse", "--abbrev-ref", "HEAD")
	if err != nil {
		return "UNKNOWN"
	}
	return strings.TrimSpace(branch)
}

func workspaceDirty(ctx context.Context, root string) bool {
	status, err := runGit(ctx, root, "status", "--porcelain=v1")
	return err == nil && strings.TrimSpace(status) != ""
}

func branchExists(ctx context.Context, root string, remote string, branch string) bool {
	if _, err := runGit(ctx, root, "show-ref", "--verify", "--quiet", "refs/heads/"+branch); err == nil {
		return true
	}
	if _, err := runGit(ctx, root, "show-ref", "--verify", "--quiet", "refs/remotes/"+normalizeRemote(remote)+"/"+branch); err == nil {
		return true
	}
	return false
}

func allBranches(ctx context.Context, root string, remote string) []string {
	seen := map[string]bool{}
	out, err := runGit(ctx, root, "for-each-ref", "--format=%(refname:short)", "refs/heads", "refs/remotes/"+normalizeRemote(remote))
	if err != nil {
		return nil
	}
	for _, line := range strings.Split(out, "\n") {
		line = strings.TrimSpace(line)
		if line == "" || strings.HasSuffix(line, "/HEAD") {
			continue
		}
		line = strings.TrimPrefix(line, normalizeRemote(remote)+"/")
		seen[line] = true
	}
	branches := make([]string, 0, len(seen))
	for branch := range seen {
		branches = append(branches, branch)
	}
	sort.Strings(branches)
	return branches
}

func targetForTapdataRepo(ctx context.Context, workRoot string, remote string, repo string, tapBranch string, entBranch string, webBranch string, pluginRelease string, pluginReleaseReason string, tapMarker string) (string, string) {
	repoRoot := filepath.Join(workRoot, repo)
	if repo == "tapdata" {
		return tapBranch, "tapdata source branch"
	}
	if repo == "tapdata-enterprise" && entBranch != "" {
		return entBranch, "enterprise override"
	}
	if repo == "tapdata-web" && webBranch != "" {
		return webBranch, "web override"
	}
	if tapBranch == "main" {
		return "main", "main explicit"
	}
	if tapBranch == "develop" {
		switch repo {
		case "tapdata-license":
			return "main", "develop uses license main"
		case "tapdata-connectors", "tapdata-connectors-enterprise", "tapdata-common-lib":
			return releaseTargetOrMain(ctx, repoRoot, remote, pluginRelease, pluginReleaseReason)
		default:
			return "develop", "develop aligned"
		}
	}
	if tapMarker != "" {
		if branchExists(ctx, repoRoot, remote, tapBranch) {
			return tapBranch, "same task branch"
		}
		if matched := firstBranchContainingMarker(ctx, repoRoot, remote, tapMarker); matched != "" {
			return matched, "matched task marker " + tapMarker
		}
	}
	if !isStandardTapdataBranch(tapBranch) && branchExists(ctx, repoRoot, remote, tapBranch) {
		return tapBranch, "same non-standard branch"
	}
	switch repo {
	case "tapdata-enterprise":
		if branchExists(ctx, repoRoot, remote, tapBranch) {
			return tapBranch, "same enterprise branch"
		}
		return "UNRESOLVED", "enterprise branch unresolved"
	case "tapdata-web":
		if branchExists(ctx, repoRoot, remote, tapBranch) {
			return tapBranch, "same web branch"
		}
		return "UNRESOLVED", "web branch unresolved"
	case "tapdata-connectors", "tapdata-connectors-enterprise", "tapdata-common-lib":
		return releaseTargetOrMain(ctx, repoRoot, remote, pluginRelease, pluginReleaseReason)
	case "tapdata-license":
		if isReleaseBranch(tapBranch) {
			if matched := firstReleaseGreaterOrEqual(ctx, repoRoot, remote, releaseVersion(tapBranch)); matched != "" {
				return matched, "license release >= tapdata release"
			}
		}
		return "main", "license fallback main"
	default:
		return "UNRESOLVED", "repo branch unresolved"
	}
}

func releaseTargetOrMain(ctx context.Context, repoRoot string, remote string, pluginRelease string, reason string) (string, string) {
	if pluginRelease == "" {
		return "main", "plugin release unavailable; fallback main: " + reason
	}
	if matched := firstReleaseGreaterOrEqual(ctx, repoRoot, remote, pluginRelease); matched != "" {
		return matched, "plugin release >= " + pluginRelease
	}
	return "main", "no release >= " + pluginRelease + "; fallback main"
}

func tapdataPluginRelease(ctx context.Context, workRoot string, remote string, tapBranch string) (string, string) {
	repoRoot := filepath.Join(workRoot, "tapdata")
	if tapBranch == currentBranchOrUnknown(ctx, repoRoot) {
		version, err := readPluginVersionFromFile(filepath.Join(repoRoot, tapdataPluginPath))
		if err == nil {
			return normalizePluginRelease(version), "pluginKit current checkout"
		}
	}
	content, err := runGit(ctx, repoRoot, "show", tapBranch+":"+tapdataPluginPath)
	if err != nil {
		content, err = runGit(ctx, repoRoot, "show", normalizeRemote(remote)+"/"+tapBranch+":"+tapdataPluginPath)
	}
	if err != nil {
		return "", err.Error()
	}
	version, err := readPluginVersion(strings.NewReader(content))
	if err != nil {
		return "", err.Error()
	}
	return normalizePluginRelease(version), "pluginKit " + tapBranch
}

func readPluginVersionFromFile(path string) (string, error) {
	file, err := os.Open(path)
	if err != nil {
		return "", err
	}
	defer file.Close()
	return readPluginVersion(file)
}

func readPluginVersion(reader interface{ Read([]byte) (int, error) }) (string, error) {
	scanner := bufio.NewScanner(reader)
	for scanner.Scan() {
		line := strings.TrimSpace(scanner.Text())
		if strings.HasPrefix(line, "#") || !strings.Contains(line, "=") {
			continue
		}
		key, value, _ := strings.Cut(line, "=")
		if strings.TrimSpace(key) == tapdataPluginVersionKey {
			version := strings.TrimSpace(value)
			if version == "" {
				return "", fmt.Errorf("%s is empty", tapdataPluginVersionKey)
			}
			return version, nil
		}
	}
	if err := scanner.Err(); err != nil {
		return "", err
	}
	return "", fmt.Errorf("%s not found", tapdataPluginVersionKey)
}

func normalizePluginRelease(version string) string {
	version = strings.TrimSpace(version)
	version = strings.TrimSuffix(version, "-SNAPSHOT")
	version = strings.TrimPrefix(version, "v")
	version = strings.TrimPrefix(version, "release-v")
	return version
}

func firstBranchContainingMarker(ctx context.Context, root string, remote string, marker string) string {
	for _, branch := range allBranches(ctx, root, remote) {
		if strings.Contains(strings.ToUpper(branch), marker) {
			return branch
		}
	}
	return ""
}

func firstReleaseGreaterOrEqual(ctx context.Context, root string, remote string, target string) string {
	targetVersion := parseVersionParts(target)
	if len(targetVersion) == 0 {
		return ""
	}
	releases := []string{}
	for _, branch := range allBranches(ctx, root, remote) {
		if isReleaseBranch(branch) {
			releases = append(releases, branch)
		}
	}
	sort.Slice(releases, func(i, j int) bool {
		return compareVersions(parseVersionParts(releaseVersion(releases[i])), parseVersionParts(releaseVersion(releases[j]))) < 0
	})
	for _, release := range releases {
		if compareVersions(parseVersionParts(releaseVersion(release)), targetVersion) >= 0 {
			return release
		}
	}
	return ""
}

func switchToBranch(ctx context.Context, root string, remote string, branch string) error {
	current := currentBranchOrUnknown(ctx, root)
	if current == branch {
		return nil
	}
	if _, err := runGit(ctx, root, "show-ref", "--verify", "--quiet", "refs/heads/"+branch); err == nil {
		_, err = runGit(ctx, root, "switch", branch)
		return err
	}
	if _, err := runGit(ctx, root, "show-ref", "--verify", "--quiet", "refs/remotes/"+normalizeRemote(remote)+"/"+branch); err == nil {
		_, err = runGit(ctx, root, "switch", "-c", branch, "--track", normalizeRemote(remote)+"/"+branch)
		return err
	}
	return fmt.Errorf("branch %q not found", branch)
}

func upstreamOrEmpty(ctx context.Context, root string) string {
	upstream, err := runGit(ctx, root, "rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}")
	if err != nil {
		return ""
	}
	return strings.TrimSpace(upstream)
}

func aheadBehindOrEmpty(ctx context.Context, root string) string {
	if upstreamOrEmpty(ctx, root) == "" {
		return ""
	}
	out, err := runGit(ctx, root, "rev-list", "--left-right", "--count", "HEAD...@{u}")
	if err != nil {
		return ""
	}
	fields := strings.Fields(out)
	if len(fields) != 2 {
		return ""
	}
	return fields[0] + "/" + fields[1]
}

func tapdataBranchMarker(branch string) string {
	match := regexp.MustCompile(`(?i)TAP-[0-9]+`).FindString(branch)
	return strings.ToUpper(match)
}

func isSafeBranchName(branch string) bool {
	if branch == "" ||
		strings.HasPrefix(branch, "-") ||
		strings.Contains(branch, "..") ||
		strings.Contains(branch, "\\") ||
		strings.ContainsAny(branch, " \t\r\n") ||
		strings.HasSuffix(branch, "/") ||
		strings.HasSuffix(branch, ".") ||
		strings.Contains(branch, "@{") {
		return false
	}
	return regexp.MustCompile(`^[A-Za-z0-9._/-]+$`).MatchString(branch)
}

func isStandardTapdataBranch(branch string) bool {
	return branch == "main" || branch == "develop" || isReleaseBranch(branch)
}

func isReleaseBranch(branch string) bool {
	return strings.HasPrefix(branch, "release-v")
}

func releaseVersion(branch string) string {
	return strings.TrimPrefix(branch, "release-v")
}

func parseVersionParts(version string) []int {
	version = normalizePluginRelease(version)
	if version == "" {
		return nil
	}
	parts := []int{}
	for _, part := range strings.Split(version, ".") {
		part = strings.TrimSpace(part)
		if part == "" {
			return nil
		}
		value, err := strconv.Atoi(part)
		if err != nil {
			return nil
		}
		parts = append(parts, value)
	}
	return parts
}

func compareVersions(left []int, right []int) int {
	maxLen := len(left)
	if len(right) > maxLen {
		maxLen = len(right)
	}
	for i := 0; i < maxLen; i++ {
		lv, rv := 0, 0
		if i < len(left) {
			lv = left[i]
		}
		if i < len(right) {
			rv = right[i]
		}
		if lv < rv {
			return -1
		}
		if lv > rv {
			return 1
		}
	}
	return 0
}
