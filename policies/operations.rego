package agenticops

# AgenticOps v1 门禁判定（OPA 路径）。
# 与 gate/engine.py 语义一致，策略数据同源于 policies/operations.json（作为 data 加载）。
#
# input: {
#   "operation": "git_push",
#   "context": {"branch": "...", "origin": "...", "push_target_branch": "...",
#                "branch_relevant": true},
#   "authorization": {...}  # .agenticops/tasks/<issue-key>/authorization.json，可为空对象
# }
# 输出 data.agenticops.result = {"decision": "...", "operation": "...", "reason": "..."}

import rego.v1

default_level := "unlisted"

op_meta := data.operations[input.operation]

level := op_meta.level if op_meta

level := default_level if not op_meta

scope := data.authorization_scopes.task_execution

# ---- 授权校验 -------------------------------------------------------------

auth := input.authorization

auth_missing_bindings contains b if {
	some b in scope.required_bindings
	not auth[b]
}

auth_problem contains "不存在任务执行授权" if not auth.scope

auth_problem contains "授权 scope 不是 task_execution" if {
	auth.scope
	auth.scope != "task_execution"
}

auth_problem contains msg if {
	auth.scope
	auth.status != "active"
	msg := sprintf("授权状态不是 active：%v", [auth.status])
}

auth_problem contains msg if {
	auth.scope
	count(auth_missing_bindings) > 0
	msg := sprintf("授权缺少绑定字段：%v", [concat(", ", auth_missing_bindings)])
}

auth_problem contains "授权 repositories 必须是非空列表" if {
	auth.scope
	not auth.repositories[0]
}

auth_problem contains msg if {
	auth.scope
	some i
	repository := auth.repositories[i]
	some binding in scope.repository_bindings
	not repository[binding]
	msg := sprintf("授权 repositories[%v] 缺少绑定字段：%v", [i, binding])
}

matching_repositories := [repository |
	some repository in auth.repositories
	repository.repository == input.context.origin
]

auth_problem contains msg if {
	auth.scope
	input.context.origin != ""
	count(matching_repositories) == 0
	msg := sprintf("仓库不匹配：当前 origin=%v，不在授权仓库集合中", [input.context.origin])
}

auth_problem contains msg if {
	auth.scope
	input.context.branch_relevant
	input.context.branch != ""
	count(matching_repositories) == 1
	repository := matching_repositories[0]
	input.context.branch != repository.work_branch
	msg := sprintf("分支不匹配：当前 %v，授权 work_branch=%v", [input.context.branch, repository.work_branch])
}

auth_problem contains "无法确定当前分支，门禁按保守处理" if {
	auth.scope
	input.context.branch_relevant
	input.context.branch == ""
	count(matching_repositories) == 1
}

auth_problem contains "授权已过期" if {
	auth.scope
	is_number(auth.expires_at_epoch)
	time.now_ns() / 1000000000 > auth.expires_at_epoch
}

auth_problem contains msg if {
	auth.scope
	input.context.issue_key
	input.context.issue_key != auth.issue_key
	msg := sprintf("Jira 任务不匹配：当前 %v，授权 issue_key=%v", [input.context.issue_key, auth.issue_key])
}

auth_valid if count(auth_problem) == 0

covered if input.operation in scope.covered_operations

# ---- 保护分支 -------------------------------------------------------------

push_branch := input.context.push_target_branch if input.context.push_target_branch

push_branch := input.context.branch if not input.context.push_target_branch

pushing_protected if {
	input.operation == "git_push"
	some pat in data.protected_branches
	glob.match(pat, ["/"], push_branch)
}

# ---- 判定 -----------------------------------------------------------------

result := {"decision": "ask", "operation": input.operation, "reason": "未识别的外部写操作，不在操作契约内，需人工确认（建议为其补充契约映射）"} if {
	input.operation == "unknown_external_write"
} else := {"decision": "ask", "operation": input.operation, "reason": "未知标准操作，需人工确认并补充操作契约"} if {
	level == "unlisted"
} else := {"decision": "allow", "operation": input.operation, "reason": "自由操作，无需门禁"} if {
	level == "free"
} else := {"decision": "deny", "operation": input.operation, "reason": sprintf("禁止 agent 执行的不可逆操作（%v）；如确有必要请指导员在自己的终端手工执行", [input.operation])} if {
	level == "forbidden"
} else := {"decision": "deny", "operation": "protected_branch_push", "reason": sprintf("目标分支 %v 是保护分支，禁止 agent 直接推送", [push_branch])} if {
	pushing_protected
} else := {"decision": "ask", "operation": input.operation, "reason": "高风险操作，永不被任务授权伞覆盖，每次都需要指导员单独确认"} if {
	level == "excluded"
} else := {"decision": "allow", "operation": input.operation, "reason": sprintf("已由任务授权伞覆盖：%v，仓库 %v（计划 %v）", [auth.issue_key, input.context.origin, auth.approved_plan_version])} if {
	level == "gated"
	auth_valid
	covered
} else := {"decision": "ask", "operation": input.operation, "reason": "操作有效但不在授权伞覆盖清单内，需人工确认"} if {
	level == "gated"
	auth_valid
} else := {"decision": "ask", "operation": input.operation, "reason": sprintf("需要人工确认。授权检查：%v", [concat("；", auth_problem)])}
