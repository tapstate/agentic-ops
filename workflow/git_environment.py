"""产品 Git 子进程的确定性环境；不执行命令或修改当前进程环境。"""
import os


def git_environment(read_only=False):
    # 本地事实核验不得通过隐式下载把缺失材料变成“已准备”，也不得继承其它仓库上下文。
    # GIT_NO_LAZY_FETCH 只禁止缺失对象的按需获取，不禁止显式 fetch；安装生命周期
    # 必须先显式补齐对象再复用本环境。read_only 只控制可选锁，不是离线或安全沙箱。
    # 设计边界见 docs/architecture/agenticops-v1-architecture.md 第 7 节。
    environment = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
    environment.update(GIT_TERMINAL_PROMPT="0", GIT_NO_REPLACE_OBJECTS="1", GIT_NO_LAZY_FETCH="1")
    if read_only:
        environment["GIT_OPTIONAL_LOCKS"] = "0"
    return environment
