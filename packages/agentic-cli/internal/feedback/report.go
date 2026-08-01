package feedback

import (
	"fmt"
	"os"
	"path/filepath"
	"sort"
	"strings"
	"time"
)

type Report struct {
	Runs          int            `json:"runs"`
	Succeeded     int            `json:"succeeded"`
	Blocked       int            `json:"blocked"`
	Failed        int            `json:"failed"`
	MissingFields map[string]int `json:"missing_fields,omitempty"`
}

type EventFilter struct {
	Workspace    string
	AgenticRunID string
	IssueKey     string
	TaskType     string
	Code         string
	Date         string
	From         string
	To           string
}

type Pattern struct {
	Key            string `json:"key"`
	Count          int    `json:"count"`
	SuggestedAsset string `json:"suggested_asset,omitempty"`
}

type Analysis struct {
	Runs               int       `json:"runs"`
	FailurePatterns    []Pattern `json:"failure_patterns,omitempty"`
	HumanGateHotspots  []Pattern `json:"human_gate_hotspots,omitempty"`
	MissingFieldTrends []Pattern `json:"missing_field_trends,omitempty"`
	SuggestedAssets    []string  `json:"suggested_assets,omitempty"`
}

type Proposal struct {
	Key              string `json:"key"`
	Title            string `json:"title"`
	EvidenceCount    int    `json:"evidence_count"`
	RecommendedAsset string `json:"recommended_asset"`
	Rationale        string `json:"rationale"`
}

func FilterEvents(events []Event, filter EventFilter) ([]Event, error) {
	start, hasStart, err := parseBound(filter.From, false)
	if err != nil {
		return nil, fmt.Errorf("invalid from time: %w", err)
	}
	end, hasEnd, err := parseBound(filter.To, true)
	if err != nil {
		return nil, fmt.Errorf("invalid to time: %w", err)
	}
	if filter.Date != "" {
		dateStart, dateHasStart, dateErr := parseBound(filter.Date, false)
		if dateErr != nil || !dateHasStart {
			return nil, fmt.Errorf("invalid date: %s", filter.Date)
		}
		dateEnd, _, dateEndErr := parseBound(filter.Date, true)
		if dateEndErr != nil {
			return nil, fmt.Errorf("invalid date: %s", filter.Date)
		}
		start, hasStart = dateStart, true
		end, hasEnd = dateEnd, true
	}
	if hasStart && hasEnd && !start.Before(end) {
		return nil, fmt.Errorf("from must be before to")
	}

	filtered := make([]Event, 0, len(events))
	for _, event := range events {
		if filter.Workspace != "" && event.Workspace != filter.Workspace {
			continue
		}
		if filter.AgenticRunID != "" && event.AgenticRunID != filter.AgenticRunID {
			continue
		}
		if filter.IssueKey != "" && event.IssueKey != filter.IssueKey {
			continue
		}
		if filter.TaskType != "" && event.TaskType != filter.TaskType && event.TaskClass != filter.TaskType {
			continue
		}
		if filter.Code != "" && event.Code != filter.Code {
			continue
		}
		if hasStart || hasEnd {
			eventTime, eventErr := parseEventTime(event.Timestamp)
			if eventErr != nil {
				return nil, fmt.Errorf("invalid event timestamp %q: %w", event.Timestamp, eventErr)
			}
			if hasStart && eventTime.Before(start) {
				continue
			}
			if hasEnd && !eventTime.Before(end) {
				continue
			}
		}
		filtered = append(filtered, event)
	}
	return filtered, nil
}

func Analyze(events []Event) Analysis {
	analysis := Analysis{Runs: len(events)}
	failureCounts := map[string]int{}
	humanGateCounts := map[string]int{}
	missingFieldCounts := map[string]int{}
	for _, event := range events {
		if !event.OK {
			key := event.Code
			if key == "" {
				key = event.Operation
			}
			if key != "" {
				failureCounts[key]++
			}
		}
		if event.RequiresHumanAction || event.AgenticNextAction == "ask_owner" {
			key := event.Gate
			if key == "" {
				key = event.Operation
			}
			if key != "" {
				humanGateCounts[key]++
			}
		}
		if event.MissingField != "" {
			missingFieldCounts[event.MissingField]++
		}
		for _, field := range event.MissingFields {
			if field != "" && field != event.MissingField {
				missingFieldCounts[field]++
			}
		}
	}
	analysis.FailurePatterns = patternsFromCounts(failureCounts)
	analysis.HumanGateHotspots = patternsFromCounts(humanGateCounts)
	analysis.MissingFieldTrends = patternsFromCounts(missingFieldCounts)
	proposalList := Propose(events)
	seenAssets := map[string]bool{}
	for _, proposal := range proposalList {
		if !seenAssets[proposal.RecommendedAsset] {
			analysis.SuggestedAssets = append(analysis.SuggestedAssets, proposal.RecommendedAsset)
			seenAssets[proposal.RecommendedAsset] = true
		}
	}
	return analysis
}

func Propose(events []Event) []Proposal {
	counts := map[string]int{}
	for _, event := range events {
		if event.OK {
			continue
		}
		key := event.Code
		if key == "" {
			key = event.Operation
		}
		if key != "" {
			counts[key]++
		}
	}
	keys := make([]string, 0, len(counts))
	for key := range counts {
		keys = append(keys, key)
	}
	sort.Strings(keys)
	proposals := make([]Proposal, 0, len(keys))
	for _, key := range keys {
		asset, rationale := proposalAsset(key)
		proposals = append(proposals, Proposal{
			Key:              key,
			Title:            "分析重复失败模式：" + key,
			EvidenceCount:    counts[key],
			RecommendedAsset: asset,
			Rationale:        rationale,
		})
	}
	return proposals
}

func patternsFromCounts(counts map[string]int) []Pattern {
	patterns := make([]Pattern, 0, len(counts))
	for key, count := range counts {
		patterns = append(patterns, Pattern{Key: key, Count: count})
	}
	sort.Slice(patterns, func(i, j int) bool {
		if patterns[i].Count == patterns[j].Count {
			return patterns[i].Key < patterns[j].Key
		}
		return patterns[i].Count > patterns[j].Count
	})
	return patterns
}

func proposalAsset(key string) (string, string) {
	switch key {
	case "missing_jira_field", "missing_field":
		return "项目工作流配置或补卡模板", "重复缺失字段说明任务准入或字段映射需要补强，优先检查项目工作流配置和补卡模板。"
	case "policy_gate_required":
		return "策略门禁或运行手册", "重复触发人工门禁说明策略、人工确认路径或异常运行手册需要复核，不应直接放开高风险动作。"
	default:
		return "运行手册或操作契约", "该失败模式已形成可分析证据，建议先补充运行手册或操作契约边界，再由维护者确认是否固化。"
	}
}

func parseBound(value string, endOfDay bool) (time.Time, bool, error) {
	if value == "" {
		return time.Time{}, false, nil
	}
	if parsed, err := time.Parse("2006-01-02", value); err == nil {
		if endOfDay {
			return parsed.AddDate(0, 0, 1), true, nil
		}
		return parsed, true, nil
	}
	parsed, err := time.Parse(time.RFC3339, value)
	return parsed, true, err
}

func parseEventTime(value string) (time.Time, error) {
	if strings.TrimSpace(value) == "" {
		return time.Time{}, fmt.Errorf("timestamp is empty")
	}
	if parsed, err := time.Parse(time.RFC3339, value); err == nil {
		return parsed, nil
	}
	return time.Parse("2006-01-02", value)
}

func Summarize(events []Event) Report {
	report := Report{}
	for _, event := range events {
		report.Runs++
		if event.OK {
			report.Succeeded++
			continue
		}
		if event.MissingField != "" {
			if report.MissingFields == nil {
				report.MissingFields = map[string]int{}
			}
			report.MissingFields[event.MissingField]++
		}
		if event.RequiresHumanAction || event.AgenticNextAction == "ask_owner" {
			report.Blocked++
			continue
		}
		report.Failed++
	}
	return report
}

func WriteMarkdown(path string, workspace string, date string, report Report) error {
	if err := os.MkdirAll(filepath.Dir(path), 0o755); err != nil {
		return err
	}
	content := fmt.Sprintf(`# AgenticOps Feedback Report

- workspace: %s
- date: %s
- runs: %d
- succeeded: %d
- blocked: %d
- failed: %d
`, workspace, date, report.Runs, report.Succeeded, report.Blocked, report.Failed)
	if len(report.MissingFields) > 0 {
		content += "\n## Missing fields\n\n"
		fields := make([]string, 0, len(report.MissingFields))
		for field := range report.MissingFields {
			fields = append(fields, field)
		}
		sort.Strings(fields)
		for _, field := range fields {
			count := report.MissingFields[field]
			content += fmt.Sprintf("- %s: %d\n", field, count)
		}
	}
	return os.WriteFile(path, []byte(content), 0o644)
}

func WriteAnalysisMarkdown(path string, workspace string, scope string, analysis Analysis) error {
	if err := os.MkdirAll(filepath.Dir(path), 0o755); err != nil {
		return err
	}
	content := fmt.Sprintf("# AgenticOps Feedback Analysis\n\n- workspace: %s\n- scope: %s\n- runs: %d\n", workspace, scope, analysis.Runs)
	content += "\n## Failure patterns\n\n"
	for _, pattern := range analysis.FailurePatterns {
		content += fmt.Sprintf("- %s: %d\n", pattern.Key, pattern.Count)
	}
	content += "\n## Human gate hotspots\n\n"
	for _, pattern := range analysis.HumanGateHotspots {
		content += fmt.Sprintf("- %s: %d\n", pattern.Key, pattern.Count)
	}
	content += "\n## Missing field trends\n\n"
	for _, pattern := range analysis.MissingFieldTrends {
		content += fmt.Sprintf("- %s: %d\n", pattern.Key, pattern.Count)
	}
	return os.WriteFile(path, []byte(content), 0o644)
}

func WriteProposalsMarkdown(path string, workspace string, scope string, proposals []Proposal) error {
	if err := os.MkdirAll(filepath.Dir(path), 0o755); err != nil {
		return err
	}
	content := fmt.Sprintf("# AgenticOps Feedback Proposals\n\n- workspace: %s\n- scope: %s\n", workspace, scope)
	for _, proposal := range proposals {
		content += fmt.Sprintf("\n## %s\n\n- evidence_count: %d\n- recommended_asset: %s\n- rationale: %s\n", proposal.Title, proposal.EvidenceCount, proposal.RecommendedAsset, proposal.Rationale)
	}
	return os.WriteFile(path, []byte(content), 0o644)
}
