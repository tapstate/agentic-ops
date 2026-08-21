"""源码克隆流式执行的测试：无超时语义、进度转发、停滞提示与失败信息。

对应 workspace_init/service.py 的 `_run_git_streaming`：clone 不设超时，
大仓库 + 慢网络下无限等待，stderr 实时转发；仅当 stderr 持续无输出超过
stall_warn_interval 时输出停滞提示（不终止进程）。
"""

from __future__ import annotations

import io
import os
import subprocess
import tempfile
import time
import unittest
from contextlib import redirect_stderr
from pathlib import Path
from unittest import mock

from ao_work.workspace_init.service import WorkspaceInitializer

CONTINUOUS_PROGRESS = """#!/bin/sh
i=0
while [ $i -lt 25 ]; do
  printf 'Receiving objects: %d%%\\r' "$i" >&2
  i=$((i + 1))
  sleep 0.1
done
printf '\\n' >&2
exit 0
"""

STALL_THEN_RESUME = """#!/bin/sh
echo 'Cloning into ...' >&2
sleep 0.8
printf 'Receiving objects: 100%%\\r' >&2
sleep 0.1
exit 0
"""

FAIL_WITH_STDERR = """#!/bin/sh
echo 'fatal: could not read Username for https://github.com: terminal prompts disabled' >&2
exit 128
"""


class WorkspaceCloneStreamingTests(unittest.TestCase):
    def make_initializer(self, root: Path) -> WorkspaceInitializer:
        workspace = root / "workspace"
        workspace.mkdir()
        return WorkspaceInitializer(workspace, root / "install")

    def install_fake_git(self, root: Path, script: str) -> None:
        fake_bin = root / "bin"
        fake_bin.mkdir()
        git = fake_bin / "git"
        git.write_text(script, encoding="utf-8")
        git.chmod(0o755)

    def run_streaming(
        self, root: Path, script: str, *, stall_warn_interval: float
    ) -> tuple[subprocess.CompletedProcess[str], float, str]:
        self.install_fake_git(root, script)
        initializer = self.make_initializer(root)
        stderr = io.StringIO()
        started = time.monotonic()
        with (
            redirect_stderr(stderr),
            mock.patch.dict(
                os.environ,
                {"PATH": f"{root / 'bin'}:{os.environ['PATH']}"},
            ),
        ):
            result = initializer._run_git_streaming(
                ["clone", "git@github.com:tapdata/tapdata.git", str(root / "dest")],
                stall_warn_interval=stall_warn_interval,
            )
        elapsed = time.monotonic() - started
        return result, elapsed, stderr.getvalue()

    def test_continuous_progress_never_times_out(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            result, elapsed, stderr = self.run_streaming(
                root, CONTINUOUS_PROGRESS, stall_warn_interval=0.5
            )
            self.assertEqual(0, result.returncode)
            # 全程持续输出（0.1s 一次）持续约 2.5s；若实现仍带硬超时，
            # 会在 0.5s 被杀，elapsed 不可能超过 2 秒。
            self.assertGreaterEqual(elapsed, 2.0)
            self.assertIn("Receiving objects", result.stderr)
            self.assertIn("Receiving objects", stderr)
            self.assertNotIn("无新进度输出", stderr)

    def test_stall_warns_but_does_not_kill_and_resumes(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            result, elapsed, stderr = self.run_streaming(
                root, STALL_THEN_RESUME, stall_warn_interval=0.3
            )
            # 停滞 0.8s 后恢复输出：只提示、不杀进程（returncode 0 且总耗时 > 0.8s）
            self.assertEqual(0, result.returncode)
            self.assertGreaterEqual(elapsed, 0.7)
            self.assertIn("无新进度输出", stderr)
            self.assertIn("Ctrl+C 中断", stderr)
            self.assertIn("Cloning into", stderr)

    def test_git_failure_preserves_returncode_and_stderr(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            result, _, _ = self.run_streaming(
                root, FAIL_WITH_STDERR, stall_warn_interval=30.0
            )
            self.assertEqual(128, result.returncode)
            self.assertIn("fatal: could not read Username", result.stderr)


if __name__ == "__main__":
    unittest.main()
