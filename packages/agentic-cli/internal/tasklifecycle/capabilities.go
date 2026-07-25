package tasklifecycle

import (
	"context"
	"strings"
)

func resolveCapability(request Request, taskClass string) (LifecycleCapability, Result) {
	if request.Process == "empty" {
		return emptyTaskCapability{}, Result{}
	}
	if taskClass == "bug_fix" {
		return defectFixCapability{}, Result{}
	}
	return nil, Result{
		OK:                  false,
		Code:                "capability_mapping_gap",
		Message:             "当前任务分类没有可用接管能力",
		RequiredHumanAction: "请维护能力映射，或显式使用 --process empty 跑通空处理",
		CurrentStage:        "capability_resolution",
		NextAction:          "ask_owner",
		HumanGate:           true,
	}
}

type emptyTaskCapability struct{}

func (emptyTaskCapability) ID() string {
	return "empty_task_v1"
}

func (emptyTaskCapability) Process(ctx context.Context, task TaskContext) CapabilityResult {
	return CapabilityResult{
		OK:                    true,
		CurrentStage:          "completed",
		NextAction:            "task_audit_submitted",
		CurrentAgentIDCleared: true,
		AuditTarget:           "local_event",
		AuditSubmitted:        true,
		AuditReference:        task.IssueKey + ":empty_task_v1",
	}
}

type defectFixCapability struct{}

func (defectFixCapability) ID() string {
	return "defect_fix_v1"
}

func (defectFixCapability) Process(ctx context.Context, task TaskContext) CapabilityResult {
	complexity := defectComplexity(task.Labels)
	if complexity == "complex" {
		return CapabilityResult{
			OK:                  false,
			Code:                "design_impact_assessment_required",
			Message:             "复杂缺陷需要先评估设计影响",
			RequiredHumanAction: "请研发负责人确认输入输出、接口或跨模块影响后再继续",
			CurrentStage:        "design_impact_review",
			NextAction:          "ask_owner",
			DefectComplexity:    complexity,
			HumanGate:           true,
		}
	}
	return CapabilityResult{
		OK:               true,
		CurrentStage:     "implementation",
		NextAction:       "start_defect_fix",
		DefectComplexity: complexity,
		AuditSubmitted:   false,
	}
}

func defectComplexity(labels []string) string {
	for _, label := range labels {
		normalized := strings.ToLower(strings.TrimSpace(label))
		switch normalized {
		case "defect:simple", "simple-defect", "simple_bug", "simple-bug":
			return "simple"
		case "defect:complex", "complex-defect", "complex_bug", "complex-bug":
			return "complex"
		case "defect:normal", "normal-defect", "normal_bug", "normal-bug":
			return "normal"
		}
	}
	return "normal"
}
