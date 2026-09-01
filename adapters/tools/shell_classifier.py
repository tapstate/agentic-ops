"""无状态解析 Shell、Git、gh 与受控 Workflow CLI 语法。"""
from __future__ import annotations

import json
import re
import shlex
from pathlib import Path

from adapters.tools.git_push_syntax import parse_push
from adapters.tools.shell_syntax import DYNAMIC, normalize_shell_call

CONFIG = json.loads((Path(__file__).resolve().parent / "mcp-operations.json").read_text(encoding="utf-8"))
SHELL, UNKNOWN = CONFIG["shell"], "unknown_external_write"
GITHUB_OPS = {"create_pr", "update_pr", "fix_pr_comments", "pr_merge", "release", "create_repository"}
GIT_HELP_FLAGS = {"-a", "--all", "--dry-run", "--no-verify", "-q", "--quiet", "-v", "--verbose"}
GH_HELP_FLAGS = {"--draft", "--web", "--fill", "--fill-first", "--fill-verbose"}
PY_VALUE_OPTIONS = {"-W", "-X", "--check-hash-based-pycs"}
PY_FLAGS = set("-b -B -d -E -h --help -i -I -O -OO -P -q -s -S -u -v -V --version -x "
               "--help-env --help-xoptions --help-all".split())
GIT_KNOWN = set(SHELL["git_readonly_redirects"]) | set(SHELL["git_simple_operations"]) | {
    "branch", "clone", "commit", "fetch", "filter-branch", "filter-repo", "pull", "push",
    "rebase", "replace", "reset", "tag", "worktree",
}


def classify_bash_call(command):
    operations, aliases = [], set()
    target_fields = ("issue_key", "workspace", "git_cwd", "push_source_ref", "push_destination_ref",
                     "push_target_branch", "repository"); targets = {key: [] for key in target_fields}
    for command_tokens, reliable in (calls := normalize_shell_call(command, SHELL)):
        controlled_alias = _controlled_alias_call(command_tokens, aliases)
        current, current_target = _dispatch(command_tokens) if reliable and command_tokens else ([], {})
        if (
            not reliable and not _readonly_rg_composition(command)
            and (controlled_alias or _controlled_hint(command_tokens, len(calls) == 1, command))
            and UNKNOWN not in current
        ):
            current.append(UNKNOWN)
        operations.extend(current)
        for field, value in current_target.items():
            targets[field].append(value)
    target, ambiguous = {"branch_relevant": True}, False
    for field, values in targets.items():
        unique = set(values)
        if values and all(values) and len(unique) == 1:
            target[field] = unique.pop()
        elif field in ("issue_key", "workspace") and len(unique) == 1:
            continue
        elif field == "repository" and len(values) == 1 and values[0] == "":
            continue
        elif values:
            ambiguous = True
    if ambiguous and UNKNOWN not in operations:
        operations.append(UNKNOWN)
    return operations, target


def _readonly_rg_composition(command):
    try:
        lexer = shlex.shlex(command, posix=True, punctuation_chars="|&;(){}<>\r\n")
        lexer.whitespace, lexer.whitespace_split, lexer.commenters = " \t", True, ""
        tokens = list(lexer)
    except ValueError:
        return False
    # Shell 参数不能含 NUL；仅将独立的管道和 && token 转为分段符。
    segments = [
        segment.split("\0")
        for segment in "\0".join(tokens).replace("\0|\0", "\1").replace("\0&&\0", "\1").split("\1")
    ]
    return len(segments) > 1 and all(
        segment and segment[0] == "rg" and not any(
            token in ("--pre", "(", ")", "{", "}", "<", ">") or token.startswith("--pre=")
            or any(marker in token for marker in ("$", "*", "?", "`"))
            or all(char in "|&;(){}<>" for char in token) for token in segment[1:]
        ) for segment in segments
    )


def _controlled_hint(tokens, single_segment, command):
    markers = re.compile(r"(?:^|[^A-Za-z0-9_.-])(git|gh|agenticops)(?:$|[^A-Za-z0-9_.-])")
    return not (
        (executable := Path(str(tokens[0])).name if tokens else "") and single_segment
        and not any(marker in command for marker in DYNAMIC)
        and not any(str(token).lstrip("0123456789").startswith((">", "<")) for token in tokens[1:])
        and (
            executable in {"cat", "file", "grep", "head", "ls", "rg", "stat", "tail"}
            and (executable != "rg" or not any(str(token) == "--pre" or str(token).startswith("--pre=")
                                               for token in tokens[1:]))
            or executable == "sed" and len(tokens) >= 4 and tokens[1] == "-n"
            and re.fullmatch(r"\d+(?:,\d+)?p", tokens[2])
            and all(str(token) and not str(token).startswith("-") for token in tokens[3:])
        )
    ) and (
        executable in ("git", "gh", "agenticops", "rg")
        or any(
            markers.search(str(token)) or str(token).replace("\\", "/").endswith(
                ("workflow/task.py", "workflow/repository_worktree.py")
            ) for token in tokens
        )
    )


def _controlled_alias_call(tokens, aliases):
    candidates = tokens[1:] if tokens and tokens[0] in ("export", "readonly") else tokens
    assignments = [re.fullmatch(r"([A-Za-z_][A-Za-z0-9_]*)=(.*)", item, re.S) for item in candidates]
    if assignments and all(assignments):
        aliases.difference_update(assignment.group(1) for assignment in assignments)
        aliases.update(assignment.group(1) for assignment in assignments
                       if Path(assignment.group(2)).name in ("git", "gh", "agenticops"))
        return False
    variable = re.fullmatch(r"\$(?:{(\w+)}|(\w+))", tokens[0]) if tokens else None
    return bool(variable and (variable.group(1) or variable.group(2)) in aliases)


def _dispatch(tokens):
    executable = Path(tokens[0]).name
    if executable == "git":
        return _git(tokens[1:])
    if executable == "gh":
        return _gh(tokens[1:])
    executor, _ = _executor_spec(executable)
    if executor and executor != "python":
        return [], {}
    if executable == "agenticops" and "workspace" in tokens and "purge" in tokens:
        return (["manage_repository_worktree", UNKNOWN], {}) if len(
            workspaces := _argument_values(tokens, "--workspace")
        ) > 1 or any(item.split("=", 1)[0] not in ("--all", "--yes", "--workspace")
                     for item in tokens if item.startswith("-")) else (
            ["manage_repository_worktree"], {"workspace": workspaces[0] if workspaces else ""})
    return _workflow(tokens)


def _executor_spec(executable):
    return next(
        ((executor, specification) for executor, specification in SHELL["code_executors"].items()
         for name in specification["names"] if re.fullmatch(
             re.escape(name) + (r"(?:\d+(?:\.\d+)*)?" if specification["versioned"] else ""), executable)),
        (None, None),
    )


def _workflow(tokens):
    executor = _executor_spec(executable := Path(tokens[0]).name)[0]
    index = 0
    if executor == "python":
        index = 1
        while index < len(tokens):
            item = tokens[index]
            if item == "-m" or item.startswith("-m"):
                offset = 2 if item == "-m" else 1
                module = tokens[index + 1] if item == "-m" and index + 1 < len(tokens) else item[2:]
                script = SHELL["workflow_modules"].get(module)
                return _workflow_action(script, tokens[index + offset:]) if script else ([], {})
            if item == "-c" or item.startswith("-c"):
                return [], {}
            if item in PY_VALUE_OPTIONS:
                index += 2
                continue
            if item == "--":
                index += 1
                break
            if item in PY_FLAGS or item.startswith(("-W", "-X", "--check-hash-based-pycs=")):
                index += 1
                continue
            break
    if index >= len(tokens):
        return [], {}
    normalized = tokens[index].replace("\\", "/")
    script = next((name for name in ("task.py", "repository_worktree.py")
                   if normalized == "workflow/" + name or normalized.endswith("/workflow/" + name)), None)
    if executor == "python" and not script:
        return [], {}
    return _workflow_action(script, tokens[index + 1:])


def _workflow_action(script, arguments):
    if not script or any(item in ("-h", "--help") for item in arguments):
        return [], {}
    if script == "repository_worktree.py":
        operation = "manage_repository_worktree" if any(action in arguments
                                                          for action in ("prepare", "cleanup")) else None
    elif "purge" in arguments:
        operation = "delete_task_state"
    elif "repository" in arguments and "prepare" in arguments:
        operation = (
            "manage_repository_worktree"
            if "--reuse-existing-branch" in arguments
            else "prepare_task_repository"
        )
    elif "repository" in arguments and "cleanup" in arguments:
        operation = "manage_repository_worktree"
    else:
        operation = None
    if not operation:
        return [], {}
    bindings = {"issue_key": _argument_values(arguments, "--issue-key"),
                "workspace": _argument_values(arguments, "--dir")}
    if any(len(values) > 1 for values in bindings.values()):
        return [operation, UNKNOWN], {}
    target = {field: values[0] if values else "" for field, values in bindings.items()}
    target["issue_key"] = target["issue_key"].upper()
    return [operation], target


def _argument_values(arguments, name):
    values = [arguments[index + 1] for index, item in enumerate(arguments[:-1]) if item == name]
    values.extend(item.split("=", 1)[1] for item in arguments if item.startswith(name + "="))
    return values


def _git_subcommand(arguments):
    index = 0
    while index < len(arguments):
        item = arguments[index]
        if item in ("-C", "-c", "--git-dir", "--work-tree", "--namespace"):
            index += 2
        elif item.startswith("-"):
            index += 1
        else:
            return item, arguments[index + 1:]
    return "", []


def _git(arguments):
    git_cwds = [arguments[index + 1] for index, item in enumerate(arguments[:-1]) if item == "-C"]
    git_cwd = git_cwds[0] if len(git_cwds) == 1 else ""
    subcommand, rest = _git_subcommand(arguments)
    help_call = any(
        item in ("-h", "--help")
        and all(
            not previous.startswith("-") or previous in GIT_HELP_FLAGS
            for previous in arguments[:index]
        )
        for index, item in enumerate(arguments)
    )
    if help_call:
        operations = []
    elif subcommand == "push":
        forced = any(
            item in ("--force", "-f", "--force-with-lease")
            or item.startswith(("--force-with-lease=", "+"))
            or item.startswith("-") and not item.startswith("--") and "f" in item[1:]
            for item in rest
        )
        operations = ["force_push" if forced else "git_push"]
    elif subcommand == "commit":
        operations = ["history_rewrite" if "--amend" in rest else "git_commit"]
    elif subcommand in SHELL["git_simple_operations"]:
        operations = [SHELL["git_simple_operations"][subcommand]]
    elif subcommand in ("clone", "fetch", "pull", "worktree"):
        operations = ["manage_repository_worktree"]
    elif subcommand == "branch" and any(item in ("-d", "-D", "--delete") for item in rest):
        operations = ["manage_repository_worktree"]
    elif subcommand == "tag":
        operations = ([] if not rest or all(item.startswith("-l") or item == "--list" for item in rest)
                      else ["git_tag"])
    elif subcommand in ("filter-branch", "filter-repo", "replace") or (subcommand == "reset" and "--hard" in rest):
        operations = ["history_rewrite"]
    else:
        operations = []
    readonly = subcommand in SHELL["git_readonly_redirects"] or (subcommand == "tag" and not operations)
    if subcommand and subcommand not in GIT_KNOWN and UNKNOWN not in operations:
        operations.append(UNKNOWN)
    elif subcommand and not operations and not readonly and not help_call:
        operations.append(UNKNOWN)
    execution_modified = any(
        item == "-c"
        or item.startswith("-c") and item != "-c"
        or item in ("--config-env", "--exec-path")
        or item.startswith(("--config-env=", "--exec-path="))
        for item in arguments
    )
    if operations and execution_modified and UNKNOWN not in operations:
        operations.append(UNKNOWN)
    redirects = ("-C", "--git-dir", "--work-tree", "--namespace")
    redirected = any(
        item in redirects
        or any(item.startswith(prefix + "=") for prefix in redirects[1:])
        or item.startswith("-C") and item != "-C"
        for item in arguments
    )
    if redirected and not git_cwd and subcommand not in SHELL["git_readonly_redirects"] and UNKNOWN not in operations:
        operations.append(UNKNOWN)
    target = {"git_cwd": git_cwd} if git_cwd else {}
    if subcommand == "push":
        push_target, push_safe = parse_push(rest, SHELL)
        target.update(push_target)
        if not push_safe and UNKNOWN not in operations:
            operations.append(UNKNOWN)
    return operations, target


def _gh(arguments):
    help_call = any(
        item in ("-h", "--help")
        and all(
            not previous.startswith("-") or previous in GH_HELP_FLAGS
            for previous in arguments[:index]
        )
        for index, item in enumerate(arguments)
    )
    if help_call or len(arguments) < 2:
        return [], {}
    group, action = arguments[:2]
    if group == "pr":
        operation = SHELL["gh_pr_operations"].get(action)
    elif group == "release" and action in ("create", "edit", "delete", "upload"):
        operation = "release"
    elif group == "repo" and action in ("create", "fork", "delete"):
        operation = "create_repository"
    else:
        joined = " ".join(arguments)
        writes_api = re.search(r"(-X|--method)\s+(POST|PUT|PATCH|DELETE)", joined, re.I)
        operation = (UNKNOWN if group == "api" and (writes_api or "-f " in joined or "--field" in joined)
                     else None)
    if not operation:
        return [], {}
    repositories = ([arguments[index + 1] for index, item in enumerate(arguments[:-1]) if item in ("-R", "--repo")]
                    + [item.split("=", 1)[1] for item in arguments if item.startswith("--repo=")]
                    + [item[2:] for item in arguments if item.startswith("-R") and item != "-R"])
    repository = (repositories[0] if repositories and repositories[0] and len(set(repositories)) == 1
                  else "" if not repositories else None)
    target = {"repository": repository} if operation in GITHUB_OPS else {}
    return [operation], target
