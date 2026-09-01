package agenticops

# AgenticOps v1 门禁判定（OPA 路径）。
# 与 gate/engine.py 语义一致，策略数据同源于 policies/operations.json（作为 data 加载）。
#
# input: {
#   "operation": "git_push",
#   "context": {"branch": "...", "origin": "...", "push_source_ref": "...",
#                "push_destination_ref": "...", "push_target_branch": "...",
#                "branch_relevant": true},
#   "authorization": {...}  # .agenticops/tasks/<issue-key>/authorization.json，可为空对象
# }
# 输出 data.agenticops.result 包含 decision、operation、reason、reason_code，
# 需要人工处理时还包含 required_action。

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

auth_missing_bindings contains b if {
	some b in scope.required_bindings
	auth[b] in {"", null, false, 0}
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

auth_problem contains msg if {
	auth.scope
	some binding in scope.required_bindings
	binding != "repositories"
	auth[binding]
	not is_string(auth[binding])
	msg := sprintf("授权绑定字段类型错误：%v", [binding])
}

auth_problem contains "授权 repositories 必须是非空列表" if {
	auth.scope
	not is_array(auth.repositories)
}

auth_problem contains "授权 repositories 必须是非空列表" if {
	auth.scope
	is_array(auth.repositories)
	count(auth.repositories) == 0
}

auth_problem contains msg if {
	auth.scope
	is_array(auth.repositories)
	some i, repository in auth.repositories
	not is_object(repository)
	msg := sprintf("授权 repositories[%v] 不是对象", [i])
}

auth_problem contains msg if {
	auth.scope
	is_array(auth.repositories)
	some i, repository in auth.repositories
	is_object(repository)
	some binding in scope.repository_bindings
	not repository[binding]
	msg := sprintf("授权 repositories[%v] 缺少绑定字段：%v", [i, binding])
}

auth_problem contains msg if {
	auth.scope
	is_array(auth.repositories)
	some i, repository in auth.repositories
	is_object(repository)
	some binding in scope.repository_bindings
	repository[binding] in {"", null, false, 0}
	msg := sprintf("授权 repositories[%v] 缺少绑定字段：%v", [i, binding])
}

auth_problem contains msg if {
	auth.scope
	is_array(auth.repositories)
	some i, repository in auth.repositories
	is_object(repository)
	some binding in scope.repository_bindings
	repository[binding]
	not is_string(repository[binding])
	msg := sprintf("授权 repositories[%v] 绑定字段类型错误：%v", [i, binding])
}

auth_problem contains msg if {
	auth.scope
	is_array(auth.repositories)
	some i, left in auth.repositories
	some j, right in auth.repositories
	i < j
	is_object(left)
	is_object(right)
	left.repository
	left.repository == right.repository
	msg := sprintf("授权仓库重复：%v", [left.repository])
}

matching_repositories := [repository |
	some repository in auth.repositories
	is_object(repository)
	repository.repository == input.context.origin
]

auth_problem contains msg if {
	auth.scope
	is_string(input.context.repository_fact_error)
	input.context.repository_fact_error != ""
	msg := input.context.repository_fact_error
}

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

push_repository_matched if {
	input.operation == "git_push"
	count(matching_repositories) == 1
}

push_authorized_endpoint_trusted if {
	input.operation == "git_push"
	count(matching_repositories) == 1
	repository := matching_repositories[0]
	is_string(repository.authorized_endpoint)
	repository.authorized_endpoint != ""
	is_string(input.context.raw_origin_endpoint)
	input.context.raw_origin_endpoint != ""
	is_string(input.context.fetch_origin_endpoint)
	input.context.fetch_origin_endpoint != ""
	is_string(input.context.push_origin_endpoint)
	input.context.push_origin_endpoint != ""
	repository.authorized_endpoint == input.context.raw_origin_endpoint
	repository.authorized_endpoint == input.context.fetch_origin_endpoint
	repository.authorized_endpoint == input.context.push_origin_endpoint
}

push_repository_untrusted if {
	input.operation == "git_push"
	is_string(input.context.repository_fact_error)
	input.context.repository_fact_error != ""
}

push_repository_untrusted if {
	input.operation == "git_push"
	input.context.authorization_state in {"loaded", "invalid"}
	not push_authorized_endpoint_trusted
}

push_refspec_trusted if {
	input.operation == "git_push"
	count(matching_repositories) == 1
	repository := matching_repositories[0]
	repository.work_branch
	input.context.push_destination_ref == sprintf("refs/heads/%v", [repository.work_branch])
	input.context.push_source_ref == "HEAD"
}

push_refspec_trusted if {
	input.operation == "git_push"
	count(matching_repositories) == 1
	repository := matching_repositories[0]
	repository.work_branch
	input.context.push_destination_ref == sprintf("refs/heads/%v", [repository.work_branch])
	input.context.push_source_ref == repository.work_branch
}

push_refspec_trusted if {
	input.operation == "git_push"
	count(matching_repositories) == 1
	repository := matching_repositories[0]
	repository.work_branch
	input.context.push_destination_ref == sprintf("refs/heads/%v", [repository.work_branch])
	input.context.push_source_ref == input.context.push_destination_ref
}

push_refspec_untrusted if {
	input.operation == "git_push"
	input.context.push_refspec_required
	count(matching_repositories) == 1
	not push_refspec_trusted
}

# ---- 判定 -----------------------------------------------------------------

result := {"decision": "ask", "operation": input.operation, "reason": "受控操作存在包装、目标或参数歧义，无法可靠生成标准请求", "reason_code": "unknown_external_write", "required_action": "请研发工程师核对并执行原命令；Agent 不得拆分、改写或换工具重试。Tool Adapter 更新后，研发工程师可明确要求原样重放一次；再次拒绝则停止。"} if {
	input.operation == "unknown_external_write"
} else := {"decision": "ask", "operation": input.operation, "reason": "未知标准操作，需人工确认并补充操作契约", "reason_code": "unknown_operation", "required_action": "请研发工程师确认本次操作；维护者应补充标准操作契约后再重试。"} if {
	level == "unlisted"
} else := {"decision": "allow", "operation": input.operation, "reason": "自由操作，无需门禁", "reason_code": "operation_free"} if {
	level == "free"
} else := {"decision": "deny", "operation": input.operation, "reason": sprintf("禁止 Agent 执行的不可逆操作（%v）；如确有必要由人工在自己的终端执行", [input.operation]), "reason_code": "forbidden_operation", "required_action": "Agent 必须停止；如确有必要，请研发工程师在自己的终端执行。"} if {
	level == "forbidden"
} else := {"decision": "deny", "operation": "untrusted_push_repository", "reason": "Git 实际 origin 信任链缺失、不唯一或与任务授权 endpoint 不一致", "reason_code": "untrusted_push_repository", "required_action": "Agent 必须停止推送；请修复 origin URL 信任链或重新签发包含可信 endpoint 的授权后重试。"} if {
	push_repository_untrusted
} else := {"decision": "deny", "operation": "unauthorized_push_refspec", "reason": "push source/destination 未严格绑定授权工作分支", "reason_code": "unauthorized_push_refspec", "required_action": "Agent 必须停止推送；只允许从 HEAD 或授权工作分支推送到同名 heads ref。"} if {
	push_refspec_untrusted
} else := {"decision": "deny", "operation": "protected_branch_push", "reason": sprintf("目标分支 %v 是保护分支，禁止 Agent 直接推送", [push_branch]), "reason_code": "protected_branch_push", "required_action": "Agent 必须停止直接推送；请通过受保护的审查与合入流程处理。"} if {
	pushing_protected
} else := {"decision": "ask", "operation": input.operation, "reason": "受控仓库准备必须显式指定 Jira 任务号", "reason_code": "issue_key_required", "required_action": "请使用 workflow/task.py repository prepare --issue-key <KEY> 重试。"} if {
	level == "controlled"
	not input.context.issue_key
} else := {"decision": "ask", "operation": input.operation, "reason": sprintf("Jira 任务 %v 不是当前工作空间中的 active 任务", [input.context.issue_key]), "reason_code": "no_active_task", "required_action": "请先接管或恢复对应任务，再使用显式 --issue-key 重试；Agent 停止仓库准备及其依赖步骤。"} if {
	level == "controlled"
	input.context.issue_key
	input.context.task_resolution == "no_active_task"
} else := {"decision": "ask", "operation": input.operation, "reason": "当前执行上下文无法唯一解析 active 任务", "reason_code": "ambiguous_active_task", "required_action": "请先接管或恢复对应任务，再使用显式 --issue-key 重试；Agent 停止仓库准备及其依赖步骤。"} if {
	level == "controlled"
	input.context.issue_key
	input.context.task_resolution == "ambiguous_active_task"
} else := {"decision": "allow", "operation": input.operation, "reason": sprintf("active 任务 %v 的受控 Source Pool 与 linked worktree 准备可自动执行", [input.context.issue_key]), "reason_code": "controlled_prepare_allowed"} if {
	level == "controlled"
	input.context.issue_key
	input.context.task_resolution == "resolved"
} else := {"decision": "ask", "operation": input.operation, "reason": "高风险操作永不被任务授权覆盖，每次都需要人工单独确认", "reason_code": "excluded_operation", "required_action": "请研发工程师在自己的终端执行原命令，完成后回复“继续”；Agent 不得重试该命令。"} if {
	level == "excluded"
} else := {"decision": "ask", "operation": input.operation, "reason": "当前操作无法匹配 active 任务", "reason_code": "no_active_task", "required_action": "请先接管任务或消除 active 任务歧义；Agent 在恢复前停止该操作及其依赖步骤。"} if {
	level == "gated"
	input.context.task_resolution == "no_active_task"
} else := {"decision": "ask", "operation": input.operation, "reason": "当前操作匹配到多个 active 任务", "reason_code": "ambiguous_active_task", "required_action": "请先接管任务或消除 active 任务歧义；Agent 在恢复前停止该操作及其依赖步骤。"} if {
	level == "gated"
	input.context.task_resolution == "ambiguous_active_task"
} else := {"decision": "ask", "operation": input.operation, "reason": "active 任务尚未签发 task_execution 授权", "reason_code": "authorization_missing", "required_action": "请完成方案确认并签发 task_execution 授权；Agent 在授权前停止该操作及其依赖步骤。"} if {
	level == "gated"
	input.context.task_resolution == "resolved"
	input.context.authorization_state == "missing"
} else := {"decision": "allow", "operation": input.operation, "reason": sprintf("已由任务授权覆盖：%v，仓库 %v（计划 %v）", [auth.issue_key, input.context.origin, auth.approved_plan_version]), "reason_code": "task_authorization_covered"} if {
	level == "gated"
	auth_valid
	covered
} else := {"decision": "ask", "operation": input.operation, "reason": "操作不在 task_execution 授权覆盖清单内", "reason_code": "operation_not_covered", "required_action": "请研发工程师在自己的终端执行原命令，完成后回复“继续”；Agent 不得重试该命令。"} if {
	level == "gated"
	auth_valid
} else := {"decision": "ask", "operation": input.operation, "reason": sprintf("task_execution 授权无效：%v", [concat("；", auth_problem)]), "reason_code": "authorization_invalid", "required_action": "请修复或重新签发有效授权；Agent 在授权恢复前停止该操作及其依赖步骤。"}
