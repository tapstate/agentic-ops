from __future__ import annotations

import argparse


class ArgumentParserError(Exception):
    pass


class HelpRequested(Exception):
    """argparse 解析到 -h/--help 时抛出，携带对应层级解析器的帮助文本。

    argparse 默认在 print_help 后 sys.exit(0)，会破坏 JSON 输出协议；
    覆写 print_help 改为抛本异常，由 CLI 层转成 JSON help 输出。
    """

    def __init__(self, usage: str) -> None:
        super().__init__(usage)
        self.usage = usage


class JsonArgumentParser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        raise ArgumentParserError(message)

    def print_help(self, file=None) -> None:
        raise HelpRequested(self.format_help())
