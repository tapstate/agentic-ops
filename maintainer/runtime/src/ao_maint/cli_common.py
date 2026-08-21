from __future__ import annotations

import argparse


class ArgumentParserError(Exception):
    pass


class HelpRequested(Exception):
    def __init__(self, usage: str) -> None:
        super().__init__(usage)
        self.usage = usage


class JsonArgumentParser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        raise ArgumentParserError(message)

    def print_help(self, file: object | None = None) -> None:
        raise HelpRequested(self.format_help())
