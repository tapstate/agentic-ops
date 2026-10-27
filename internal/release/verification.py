"""代码验收复用与隔离补验；交付身份和合并条件由发布入口检查。"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import uuid

from internal.story_gate import evidence
from bootstrap import python_runtime
from internal.story_gate.io import atomic_write_json
from internal.story_gate.locking import TaskLock
from internal.story_gate.model import FULL_ACCEPTANCE_CHECKS

CHECKS = dict(zip(FULL_ACCEPTANCE_CHECKS, (
    'internal/tests/test_runtime.sh', 'internal/tests/test_resources.sh',
    'tests/test_install.sh', 'internal/tests/test_release.sh',
)))
SCHEMA = 'release-verification/v1'


def git(root, *args):
    return evidence.git(root, *args).decode().strip()


def safe_path(root, relative):
    path = Path(root)
    for part in Path(relative).parts:
        if part in ('..', '.') or Path(relative).is_absolute():
            raise ValueError('发布证据路径越界')
        path = path / part
        if path.is_symlink() or (path.exists() and not (path.is_file() or path.is_dir())):
            raise ValueError('发布证据路径含符号链接或特殊文件')
    return path


def premerge(root, base, head):
    base = git(root, 'rev-parse', base + '^{commit}')
    head = git(root, 'rev-parse', head + '^{commit}')
    git(root, 'merge-base', '--is-ancestor', base, head)
    if git(root, 'merge-tree', '--write-tree', base, head).splitlines()[0] != git(root, 'rev-parse', head + '^{tree}'):
        raise ValueError('预合并结果不是候选树，请先集成 main 并重新审查候选')
    return base, head


def merged(root, base, head, commit, main='origin/main'):
    parents = git(root, 'show', '-s', '--format=%P', commit).split()
    if parents != [base, head]:
        raise ValueError('实际 Merge commit 的两个父提交与确认基线/候选不一致')
    if git(root, 'rev-parse', commit + '^{tree}') != git(root, 'rev-parse', head + '^{tree}'):
        raise ValueError('实际合并树不同于已验收候选，停止发布并重新评估范围')
    git(root, 'merge-base', '--is-ancestor', commit, main)


def epoch(root, ref):
    value = json.loads(git(root, 'show', ref + ':contracts/station-state-compatibility.json'))['station_state_epoch']
    if type(value) is not int or value < 1:
        raise ValueError('无效的 station_state_epoch')
    return value


def verifier_digest(root):
    paths = ['internal/release', 'bootstrap/python_runtime.py', 'internal/python-environment.sh', 'internal/story_gate/evidence.py',
             'internal/story_gate/model.py', 'internal/story_gate/io.py',
             'internal/story_gate/locking.py', 'internal/test_selection.py']
    items = []
    for name in paths:
        p = Path(root) / name
        files = p.rglob('*') if p.is_dir() else [p]
        for f in files:
            if f.is_file() and '__pycache__' not in f.parts and f.suffix in ('.py', '.sh'):
                if f.is_symlink():
                    raise ValueError('发布验证器不能是符号链接')
                items.append((str(f.relative_to(root)), hashlib.sha256(f.read_bytes()).hexdigest()))
    return evidence.digest(sorted(items))


def record_time(value):
    """按实际 UTC 时刻比较；损坏时间不能参与通过结果复用。"""
    try:
        result = datetime.fromisoformat(value['verified_at'].replace('Z', '+00:00'))
        return result.astimezone(timezone.utc) if result.tzinfo else None
    except (KeyError, TypeError, AttributeError, ValueError, OverflowError):
        return None


def run_command(command, root, env, log):
    """取消时回收整个测试进程组，不能留下继续写证据的子进程。"""
    process = subprocess.Popen(command, cwd=root, env=env, stdout=log, stderr=subprocess.STDOUT,
                               start_new_session=True)
    try:
        return process.wait()
    except BaseException:
        try:
            os.killpg(process.pid, signal.SIGTERM)
            process.wait(timeout=5)
        except (ProcessLookupError, subprocess.TimeoutExpired):
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            process.wait()
        raise


class Verification:
    def __init__(self, root, base, head, identity):
        self.root = Path(root).resolve()
        self.base = base
        self.head = git(self.root, 'rev-parse', head + '^{commit}')
        self.tree = evidence.candidate_tree(self.root, 'range', self.head)
        if not re.fullmatch(r'(?:v[0-9]+\.[0-9]+|[A-Z][A-Z0-9]+-[1-9][0-9]*)', identity):
            raise ValueError('无效发布版本或 Jira 编号')
        self.identity = identity
        self.key = self.tree
        self.path = safe_path(self.root, '.local/release/verification-' + self.key + '.json')

    def binding(self, snapshot, uv):
        # 测试跳过开关不能产生或消费完整四项证据。
        if os.environ.get('AGENTIC_OPS_RELEASE_WORKFLOW_TEST_RUNNING', '0') != '0':
            raise ValueError('发布验收不能启用嵌套测试跳过开关')
        internal_python = python_runtime.test_python(self.root)
        old = os.environ.get('AGENTIC_OPS_INTERNAL_TEST_PYTHON')
        os.environ['AGENTIC_OPS_INTERNAL_TEST_PYTHON'] = internal_python
        try:
            environment = evidence.environment(snapshot)
        finally:
            if old is None:
                os.environ.pop('AGENTIC_OPS_INTERNAL_TEST_PYTHON', None)
            else:
                os.environ['AGENTIC_OPS_INTERNAL_TEST_PYTHON'] = old
        return dict(repository='tapstate/agentic-ops', tree=self.tree,
                    verifier=verifier_digest(Path(__file__).resolve().parents[2]),
                    contract=evidence.contract_digest(), environment=environment,
                    uv=subprocess.check_output([uv, '--version'], text=True).strip())

    def read(self, binding):
        try:
            value = json.loads(self.path.read_text())
        except (OSError, ValueError):
            return None
        if (not isinstance(value, dict) or value.get('schema_version') != SCHEMA or value.get('purpose') != 'release'
                or value.get('status') != 'passed' or value.get('binding') != binding
                or not isinstance(value.get('run_id'), str)
                or not re.fullmatch(r'[0-9a-f]{32}', value['run_id'])
                or record_time(value) is None):
            return None
        checks = value.get('checks')
        if not isinstance(checks, list) or len(checks) != len(CHECKS):
            return None
        for name, check in zip(CHECKS, checks):
            if (not isinstance(check, dict) or check.get('check_id') != name
                    or type(check.get('exit_code')) is not int or check['exit_code'] != 0
                    or type(check.get('bytes')) is not int or check['bytes'] <= 0
                    or not isinstance(check.get('log_sha256'), str)
                    or not re.fullmatch(r'[0-9a-f]{64}', check['log_sha256'])):
                return None
        return value

    def read_development(self, binding):
        """直接读取 v6 完整验收，不复制通过状态或审批到另一份台账。"""
        directory = safe_path(self.root, '.local/story-gate/evidence')
        records = []
        for path in directory.glob('*.json'):
            safe_path(self.root, str(path.relative_to(self.root)))
            try:
                value = json.loads(path.read_text())
            except (OSError, ValueError):
                continue
            if (isinstance(value, dict) and isinstance(value.get('binding'), dict)
                    and value['binding'].get('candidate_tree') == self.tree):
                records.append(value)
        if not records:
            return None
        # 同树后续失败或不完整记录不能退回更早通过结果。
        value = max(records, key=lambda item: (record_time(item) or datetime.max.replace(tzinfo=timezone.utc),
                    not evidence.full_acceptance_matches(item, self.tree, binding['environment'])))
        return value

    def execute(self, mode):
        uv = os.environ.get('AGENTIC_OPS_UV') or shutil.which('uv')
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(self.path.parent, 0o700)
        # 与开发验收共用现役锁，读取期间不能并发重验撤销正在消费的结果。
        lock = safe_path(self.root, '.local/story-gate/runs/.verification.lock')
        with TaskLock(lock), tempfile.TemporaryDirectory(prefix='ao-release-check-') as temp:
            if mode == 'verify':
                atomic_write_json(self.path, dict(schema_version=SCHEMA, status='running',
                                                  verified_at=datetime.now(timezone.utc).isoformat()))
            if not uv:
                raise ValueError('缺少 uv，无法核验发布工具环境')
            snapshot = Path(temp) / 'candidate'
            subprocess.run(['git', '-C', str(self.root), 'worktree', 'add', '--detach', str(snapshot), self.head],
                           env=evidence.git_environment(), check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            try:
                binding = self.binding(snapshot, uv)
                previous = self.read(binding)
                if mode != 'verify':
                    development = self.read_development(binding)
                    try:
                        local = json.loads(self.path.read_text())
                    except (OSError, ValueError):
                        local = {} if not self.path.exists() else {'verified_at': '~'}
                    local_time = record_time(local) if self.path.exists() else datetime.min.replace(tzinfo=timezone.utc)
                    development_time = record_time(development)
                    if development:
                        valid = (development_time is not None and evidence.full_acceptance_matches(
                            development, self.tree, binding['environment']))
                        if development_time is None or (local_time is not None and development_time > local_time):
                            previous = development if valid else None
                        elif development_time == local_time and not valid:
                            previous = None
                if previous and mode != 'verify':
                    return dict(previous, reused=True)
                if mode == 'check':
                    raise ValueError('缺少匹配的完整四项验收证据；请执行 prepare 补验，verify 用于明确重验')
                record = dict(schema_version=SCHEMA, purpose='release', status='running', binding=binding,
                              run_id=uuid.uuid4().hex, checks=[], verified_at=datetime.now(timezone.utc).isoformat())
                atomic_write_json(self.path, record)
                os.chmod(self.path, 0o600)
                try:
                    self.run(snapshot, uv, record)
                    if self.binding(snapshot, uv) != binding:
                        raise ValueError('发布验证环境或验证器在执行期间变化')
                    if git(snapshot, 'status', '--porcelain'):
                        raise ValueError('发布测试修改了固定候选材料')
                    record.update(status='passed', verified_at=datetime.now(timezone.utc).isoformat())
                    atomic_write_json(self.path, record)
                except BaseException:
                    record.update(status='failed', verified_at=datetime.now(timezone.utc).isoformat())
                    atomic_write_json(self.path, record)
                    raise
                return dict(record, reused=False)
            finally:
                subprocess.run(['git', '-C', str(self.root), 'worktree', 'remove', '--force', str(snapshot)],
                               env=evidence.git_environment(), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    def run(self, snapshot, uv, record):
        log_dir = safe_path(self.root, '.local/release/runs/' + record['run_id'])
        log_dir.mkdir(parents=True, mode=0o700)
        env = evidence.git_environment()
        env.update(UV_CACHE_DIR=str(snapshot / '.local/cache/uv'),
                   UV_PROJECT_ENVIRONMENT=str(snapshot / '.local/venv/internal'),
                   AGENTIC_OPS_INTERNAL_TEST_PYTHON=str(snapshot / '.local/venv/internal/bin/python'),
                   AGENTIC_OPS_TEST_PYTHON=python_runtime.test_python(self.root),
                   PYTHONPATH=str(snapshot), PYTHONDONTWRITEBYTECODE='1')
        with (log_dir / 'setup.log').open('wb') as log:
            os.chmod(log.name, 0o600)
            if run_command([uv, 'sync', '--locked', '--project', 'internal', '--python', env['AGENTIC_OPS_TEST_PYTHON']], snapshot, env, log):
                raise ValueError('发布隔离环境准备失败：' + str(log.name))
        for name, script in CHECKS.items():
            path = log_dir / (name + '.log')
            print('发布验收：' + name + '，日志：' + str(path), file=sys.stderr, flush=True)
            with path.open('wb') as log:
                os.chmod(path, 0o600)
                code = run_command(['bash', script], snapshot, env, log)
            data = path.read_bytes()
            record['checks'].append(dict(check_id=name, exit_code=code, bytes=len(data),
                                         log_sha256=hashlib.sha256(data).hexdigest()))
            if code:
                raise ValueError('发布验收失败：' + name + '，日志：' + str(path))


def main():
    p = argparse.ArgumentParser()
    p.add_argument('mode', choices=['ensure', 'check', 'verify', 'merged', 'epoch'])
    p.add_argument('--root', required=True)
    p.add_argument('--base', required=True)
    p.add_argument('--head', required=True)
    p.add_argument('--identity', default='')
    p.add_argument('--merge')
    a = p.parse_args()
    if a.mode == 'epoch':
        print('same' if epoch(a.root, a.base) == epoch(a.root, a.head) else 'changed')
    elif a.mode == 'merged':
        merged(a.root, a.base, a.head, a.merge)
    else:
        result = Verification(a.root, a.base, a.head, a.identity).execute(a.mode)
        print(result['verified_at'])


if __name__ == '__main__':
    signal.signal(signal.SIGTERM, lambda *_: (_ for _ in ()).throw(KeyboardInterrupt()))
    try:
        main()
    except (ValueError, OSError, subprocess.SubprocessError) as error:
        print(str(error), file=sys.stderr)
        sys.exit(1)
