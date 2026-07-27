package jira

import (
	"strings"
	"testing"
)

func TestMergeDescriptionSectionsPreservesUnrelatedContentAndReplacesTargetSection(t *testing.T) {
	description := map[string]any{
		"type":    "doc",
		"version": 1,
		"content": []any{
			adfHeading("背景", 2),
			adfParagraph("现有背景"),
			adfHeading("问题分支", 2),
			adfParagraph("main"),
			adfHeading("其它信息", 2),
			adfParagraph("必须保留"),
		},
	}

	merged, err := mergeDescriptionSections(description, map[string]string{
		"问题分支": "develop",
		"修复分支": "release-v3.31",
	})
	if err != nil {
		t.Fatalf("mergeDescriptionSections error = %v", err)
	}
	text := plainText(merged)
	for _, want := range []string{"背景", "现有背景", "问题分支", "develop", "其它信息", "必须保留", "修复分支", "release-v3.31"} {
		if !strings.Contains(text, want) {
			t.Fatalf("merged description missing %q: %#v", want, merged)
		}
	}
	if strings.Contains(text, "main") {
		t.Fatalf("old problem branch content was not replaced: %s", text)
	}
}

func TestMergeDescriptionSectionsRejectsDuplicateTargetHeading(t *testing.T) {
	description := map[string]any{
		"type":    "doc",
		"version": 1,
		"content": []any{
			adfHeading("问题分支", 2),
			adfParagraph("develop"),
			adfHeading("问题分支", 2),
			adfParagraph("release-v3.31"),
		},
	}

	_, err := mergeDescriptionSections(description, map[string]string{"问题分支": "main"})
	if err == nil || !strings.Contains(err.Error(), "duplicate description section") {
		t.Fatalf("error = %v, want duplicate section error", err)
	}
}

func TestMergeDescriptionSectionsRejectsUnsupportedDescription(t *testing.T) {
	_, err := mergeDescriptionSections("plain text description", map[string]string{"问题分支": "develop"})
	if err == nil || !strings.Contains(err.Error(), "unsupported jira description") {
		t.Fatalf("error = %v, want unsupported description error", err)
	}
}

func TestMergeDescriptionSectionsDoesNotTreatColonParagraphAsSectionBoundary(t *testing.T) {
	description := map[string]any{
		"type":    "doc",
		"version": 1,
		"content": []any{
			adfHeading("问题现象", 2),
			adfParagraph("触发条件："),
			adfParagraph("旧的触发条件"),
			adfHeading("验收标准", 2),
			adfParagraph("必须保留"),
		},
	}

	merged, err := mergeDescriptionSections(description, map[string]string{"问题现象": "新的问题现象"})
	if err != nil {
		t.Fatalf("mergeDescriptionSections error = %v", err)
	}
	text := plainText(merged)
	if strings.Contains(text, "旧的触发条件") || strings.Contains(text, "触发条件：") {
		t.Fatalf("old target section content was not fully replaced: %s", text)
	}
	for _, want := range []string{"新的问题现象", "验收标准", "必须保留"} {
		if !strings.Contains(text, want) {
			t.Fatalf("merged description missing %q: %s", want, text)
		}
	}
}
