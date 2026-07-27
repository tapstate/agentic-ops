package jira

import (
	"fmt"
	"sort"
	"strings"
)

func mergeDescriptionSections(description any, sections map[string]string) (map[string]any, error) {
	if len(sections) == 0 {
		return nil, fmt.Errorf("description sections are required")
	}
	if description == nil {
		description = map[string]any{
			"type":    "doc",
			"version": 1,
			"content": []any{},
		}
	}
	document, ok := description.(map[string]any)
	if !ok || document["type"] != "doc" {
		return nil, fmt.Errorf("unsupported jira description payload")
	}
	content, ok := document["content"].([]any)
	if !ok {
		return nil, fmt.Errorf("unsupported jira description content")
	}

	targets := make(map[string]string, len(sections))
	for title, value := range sections {
		normalized := normalizeSectionTitle(title)
		if normalized == "" {
			return nil, fmt.Errorf("description section title is required")
		}
		if _, exists := targets[normalized]; exists {
			return nil, fmt.Errorf("duplicate description section input: %s", normalized)
		}
		targets[normalized] = value
	}

	counts := map[string]int{}
	for _, node := range content {
		if title, ok := descriptionSectionTitle(node, targets); ok {
			counts[title]++
		}
	}
	for title, count := range counts {
		if count > 1 {
			return nil, fmt.Errorf("duplicate description section: %s", title)
		}
	}

	mergedContent := make([]any, 0, len(content)+len(targets)*2)
	replaced := map[string]bool{}
	for index := 0; index < len(content); {
		node := content[index]
		title, isTarget := descriptionSectionTitle(node, targets)
		if !isTarget {
			mergedContent = append(mergedContent, node)
			index++
			continue
		}

		mergedContent = append(mergedContent, node)
		mergedContent = append(mergedContent, adfParagraphs(targets[title])...)
		replaced[title] = true
		index++
		for index < len(content) && !isDescriptionSectionBoundary(content[index]) {
			index++
		}
	}

	missing := make([]string, 0, len(targets))
	for title := range targets {
		if !replaced[title] {
			missing = append(missing, title)
		}
	}
	sort.Strings(missing)
	for _, title := range missing {
		mergedContent = append(mergedContent, adfHeading(title, 2))
		mergedContent = append(mergedContent, adfParagraphs(targets[title])...)
	}

	merged := make(map[string]any, len(document))
	for key, value := range document {
		merged[key] = value
	}
	merged["content"] = mergedContent
	return merged, nil
}

func descriptionSectionTitle(node any, targets map[string]string) (string, bool) {
	title, ok := nodeTitle(node)
	if !ok {
		return "", false
	}
	title = normalizeSectionTitle(title)
	_, exists := targets[title]
	return title, exists
}

func isDescriptionSectionBoundary(node any) bool {
	object, ok := node.(map[string]any)
	return ok && object["type"] == "heading"
}

func nodeTitle(node any) (string, bool) {
	object, ok := node.(map[string]any)
	if !ok || object["type"] != "heading" {
		return "", false
	}
	return plainText(object), true
}

func adfHeading(title string, level int) map[string]any {
	return map[string]any{
		"type":  "heading",
		"attrs": map[string]any{"level": level},
		"content": []any{
			map[string]any{"type": "text", "text": title},
		},
	}
}

func adfParagraph(text string) map[string]any {
	node := map[string]any{"type": "paragraph"}
	if text != "" {
		node["content"] = []any{
			map[string]any{"type": "text", "text": text},
		}
	}
	return node
}

func adfParagraphs(value string) []any {
	lines := strings.Split(strings.ReplaceAll(value, "\r\n", "\n"), "\n")
	result := make([]any, 0, len(lines))
	for _, line := range lines {
		result = append(result, adfParagraph(line))
	}
	return result
}
