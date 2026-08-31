"""无状态解析 Git push 选项与唯一 refspec，不执行授权决策。"""
from __future__ import annotations


def parse_push(arguments, config):
    positionals = []
    options_safe = True
    index = 0
    value_options = set(config["git_push_unsafe_value_options"])
    while index < len(arguments):
        item = arguments[index]
        if item == "--":
            positionals.extend(arguments[index + 1:])
            break
        option = item.split("=", 1)[0]
        if item in value_options:
            options_safe = False
            index += 2
            continue
        if option in value_options and "=" in item:
            options_safe = False
        elif item.startswith("-"):
            options_safe = options_safe and _flag_allowed(item, config)
        else:
            positionals.append(item)
        index += 1

    remote = positionals[0] if positionals else None
    refspec = _split_refspec(positionals[1:])
    target = {}
    if refspec:
        source, destination = refspec
        target = {
            "push_source_ref": source,
            "push_destination_ref": destination,
            "push_target_branch": _target_branch(destination),
        }
    safe = remote == "origin" and options_safe and bool(refspec)
    return target if safe else {}, safe


def _flag_allowed(item, config):
    if item in config["git_push_allowed_flags"] or item.startswith("--force-with-lease="):
        return True
    return len(item) > 1 and item.startswith("-") and not item.startswith("--") and all(
        flag in config["git_push_allowed_short_flags"] for flag in item[1:]
    )


def _split_refspec(refs):
    if len(refs) != 1:
        return None
    refspec = refs[0].lstrip("+")
    if not refspec or refspec.count(":") > 1 or "*" in refspec or refspec.startswith("^"):
        return None
    if ":" in refspec:
        source, destination = refspec.split(":", 1)
        if not source or not destination:
            return None
    else:
        if refspec in ("HEAD", "@"):
            return None
        source = destination = refspec
    return source, _destination_ref(destination)


def _destination_ref(destination):
    return destination if destination.startswith("refs/") else "refs/heads/" + destination


def _target_branch(destination):
    prefix = "refs/heads/"
    return destination[len(prefix):] if destination.startswith(prefix) else destination
