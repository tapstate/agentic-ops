"""产品 Git 子进程的确定性环境；不执行命令或修改当前进程环境。"""
import os


def git_environment(read_only=False):
    environment = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
    environment.update(GIT_TERMINAL_PROMPT="0", GIT_NO_REPLACE_OBJECTS="1", GIT_NO_LAZY_FETCH="1")
    if read_only:
        environment["GIT_OPTIONAL_LOCKS"] = "0"
    return environment
