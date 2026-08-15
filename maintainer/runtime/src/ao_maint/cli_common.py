from __future__ import annotations

import argparse


class ArgumentParserError(Exception):
    pass


class JsonArgumentParser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        raise ArgumentParserError(message)
