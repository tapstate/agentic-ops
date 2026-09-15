"""测试诊断计时；不改变 unittest 选择、结果或跳过语义。"""
import time
import subprocess
import unittest
from unittest import mock


class TimedTestResult(unittest.TextTestResult):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.timings = []

    def startTest(self, test):
        self.started = time.monotonic()
        super().startTest(test)

    def stopTest(self, test):
        self.timings.append((time.monotonic() - self.started, test.id()))
        super().stopTest(test)

    def stopTestRun(self):
        super().stopTestRun()
        self.stream.writeln("\n逐用例耗时（含准备与清理，秒）：")
        for elapsed, name in sorted(self.timings, reverse=True):
            self.stream.writeln("  %.3f %s" % (elapsed, name))


class TimedTestRunner(unittest.TextTestRunner):
    resultclass = TimedTestResult

    def run(self, test):
        original = subprocess.Popen
        git_calls = 0

        def counted(command, *args, **kwargs):
            nonlocal git_calls
            if isinstance(command, (list, tuple)) and command and command[0] == "git":
                git_calls += 1
            return original(command, *args, **kwargs)

        with mock.patch.object(subprocess, "Popen", side_effect=counted):
            result = super().run(test)
        self.stream.writeln("Git 调用次数：%s" % git_calls)
        return result
