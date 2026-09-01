"""无状态切分 Shell 片段，并判断命令包装是否可可靠归一化。"""
from __future__ import annotations

import getopt
import os
import re
import shlex

ASSIGNMENT = re.compile(r"[A-Za-z_][A-Za-z0-9_]*=.*", re.S)
DYNAMIC = ("$(", "`", "<(", ">(")
CONTROL_WORDS = {
    "!", "{", "}", "case", "do", "done", "elif", "else", "esac", "fi", "for",
    "function", "if", "in", "select", "then", "time", "until", "while",
}
UNSAFE_WRAPPERS = {
    ".", "bash", "builtin", "dash", "eval", "exec", "fish", "ksh", "nice", "nohup",
    "setsid", "sh", "source", "su", "sudo", "timeout", "xargs", "zsh",
}


def normalize_shell_call(command, config):
    """返回 `(tokens, reliable)`；无法静态归一化的片段标记为不可靠。"""
    lexer = shlex.shlex(command, posix=True, punctuation_chars="|&;()<>\r\n")
    lexer.whitespace = " \t"
    lexer.whitespace_split = True
    lexer.commenters = ""
    try:
        tokens, parsed = list(lexer), True
    except ValueError:
        tokens, parsed = command.split(), False
    normalized = []
    command_tokens = []
    for token in tokens + [";"]:
        if token and all(character in "|&;\r\n" for character in token):
            if command_tokens:
                unwrapped_tokens, unwrapped = _unwrap_command(command_tokens, config)
                if not unwrapped or not unwrapped_tokens and all(map(ASSIGNMENT.fullmatch, command_tokens)):
                    unwrapped_tokens = command_tokens
                segment = " ".join(command_tokens)
                reliable = parsed and unwrapped and not _unsupported(segment, unwrapped_tokens)
                normalized.append((unwrapped_tokens, reliable))
            command_tokens = []
            continue
        command_tokens.append(token)
    return normalized


def _dangerous_name(name, config):
    return name in config["dangerous_environment_names"] or any(
        name.startswith(prefix) for prefix in config["dangerous_environment_prefixes"]
    )


def _strip_assignments(tokens, config, index=0):
    while index < len(tokens) and ASSIGNMENT.fullmatch(tokens[index]):
        name, value = tokens[index].split("=", 1)
        if _dangerous_name(name, config) or any(marker in value for marker in DYNAMIC):
            return index, False
        index += 1
    return index, True


def _unwrap_env(arguments, config):
    try:
        options, remaining = getopt.getopt(
            arguments,
            "i0vu:C:S:a:",
            (
                "ignore-environment",
                "null",
                "debug",
                "unset=",
                "chdir=",
                "split-string=",
                "argv0=",
            ),
        )
    except getopt.GetoptError:
        return [], False
    denied = {"-i", "--ignore-environment", "-C", "--chdir", "-S", "--split-string"}
    if any(option in denied for option, _ in options):
        return [], False
    if any(
        option in ("-u", "--unset") and _dangerous_name(value, config)
        for option, value in options
    ):
        return [], False
    index, reliable = _strip_assignments(remaining, config)
    return (remaining[index:], True) if reliable else ([], False)


def _unwrap_command(tokens, config):
    current = list(tokens)
    while current:
        index, reliable = _strip_assignments(current, config)
        if not reliable:
            return [], False
        current = current[index:]
        if not current:
            return [], True
        executable = os.path.basename(current[0])
        if executable == "env":
            current, reliable = _unwrap_env(current[1:], config)
        elif executable == "command":
            try:
                options, current = getopt.getopt(current[1:], "pVv")
            except getopt.GetoptError:
                return [], False
            if any(option in ("-v", "-V") for option, _ in options):
                return [], True
            reliable = True
        else:
            return current, True
        if not reliable:
            return [], False
    return [], True


def _controlled_token(token):
    words = token.strip().split(maxsplit=1)
    if not words:
        return False
    normalized = words[0].replace("\\", "/")
    executable = os.path.basename(normalized)
    return (
        executable in {"agenticops", "gh", "git"}
        or executable.startswith("python")
        or normalized.endswith(("/workflow/task.py", "/workflow/repository_worktree.py"))
    )


def _unsupported(segment, tokens):
    if not tokens:
        return False
    executable = os.path.basename(tokens[0])
    grouped = any(token in ("(", ")") for token in tokens) or segment.startswith("{") or segment.endswith("}")
    control = executable in CONTROL_WORDS or executable.endswith("()")
    wrapper = executable in UNSAFE_WRAPPERS
    unknown_wrapper = not _controlled_token(tokens[0]) and any(
        _controlled_token(token) or token != "." and os.path.basename(token) in UNSAFE_WRAPPERS
        for token in tokens[1:]
    )
    dynamic = any(marker in segment for marker in DYNAMIC) or "$" in tokens[0]
    return grouped or control or wrapper or unknown_wrapper or dynamic or any(
        token in ("<", ">") for token in tokens
    ) or executable == "rg" and any(token == "--pre" or token.startswith("--pre=") for token in tokens[1:])
