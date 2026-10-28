"""运行基线、业务环境隔离和可重建环境的真实解释器回归。"""
import io
import tarfile
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from bootstrap import python_runtime as runtime


class PythonRuntimeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='ao-python-runtime-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()

    def test_product_environment_is_standard_library_and_reusable(self):
        selected = runtime.prepare(self.root, sys.executable)
        self.assertEqual(selected, runtime.prepare(self.root, sys.executable))
        result = subprocess.check_output([selected, '-I', '-c',
            'import sys,importlib.util,json;print(json.dumps([sys.version_info[:2],importlib.util.find_spec("yaml") is None]))'], text=True)
        self.assertEqual([[3, 12], True], json.loads(result))
        (self.root / '.local/venv/product/bin/python').unlink()
        with self.assertRaises(ValueError):
            runtime.prepare(self.root, sys.executable)

    def test_explicit_invalid_python_does_not_fallback(self):
        with mock.patch.dict(os.environ, {'AGENTIC_OPS_PYTHON': '/missing/python'}):
            with self.assertRaises(ValueError):
                runtime.select(self.root)
        with mock.patch.dict(os.environ, {'AGENTIC_OPS_TEST_PYTHON': '/missing/test-python'}):
            with self.assertRaises(ValueError):
                runtime.test_python(ROOT)

    def test_runtime_directory_symlink_rejected(self):
        elsewhere = self.root / 'elsewhere'
        elsewhere.mkdir()
        (self.root / '.local').symlink_to(elsewhere, target_is_directory=True)
        with self.assertRaises(ValueError):
            runtime.prepare(self.root, sys.executable)
        self.assertEqual([], list(elsewhere.iterdir()))

    def test_wrong_version_direct_entry_stops_before_state_write(self):
        old = '/usr/bin/python3'
        if not Path(old).exists():
            self.skipTest('没有外部旧 Python，完整旧安装场景另验')
        version = subprocess.check_output([old, '-c', 'import sys;print(sys.version_info[:2])'], text=True).strip()
        if version == '(3, 12)':
            self.skipTest('外部解释器也是3.12')
        result = subprocess.run([old, str(ROOT / 'bootstrap/product_state.py'),
                                 '--product-root', str(self.root), 'write', '--mode', 'installed',
                                 '--repository', 'fixture', '--branch', 'main', '--current-ref', 'fixture'],
                                capture_output=True, text=True)
        self.assertNotEqual(0, result.returncode)
        self.assertIn('3.12', result.stderr)
        self.assertFalse((self.root / '.local/product.json').exists())

    def test_product_shell_preserves_business_path_and_python_variables(self):
        product = self.root / 'product'
        (product / 'bootstrap').mkdir(parents=True)
        for name in ('python_runtime.py', 'product_access.py'):
            shutil.copyfile(ROOT / 'bootstrap' / name, product / 'bootstrap' / name)
        selected = runtime.prepare(product, sys.executable)
        business = self.root / 'business-bin'
        business.mkdir()
        command = business / 'python3'
        command.write_text('#!/bin/sh\necho business-python\n')
        command.chmod(0o755)
        script = product / 'agenticops'
        script.write_text('#!/bin/sh\n# AO_PRODUCT_SHELL_BEGIN\nunused\n# AO_PRODUCT_SHELL_END\n'
                          'python3 -c \'import sys; print("product",sys.version_info[:2])\'\n'
                          'exec bash -c \'python3; printf "%s\\n" "$PYTHONPATH"\'\n')
        env = dict(os.environ, PATH=str(business) + os.pathsep + os.environ['PATH'], PYTHONPATH='business-modules')
        env.pop('AGENTIC_OPS_PYTHON', None)
        code = 'import runpy,sys; a=runpy.run_path(sys.argv[1]);raise SystemExit(a["shell_entry"](sys.argv[2],[]))'
        result = subprocess.run([selected, '-I', '-c', code, str(product / 'bootstrap/product_access.py'), str(script)],
                                env=env, capture_output=True, text=True)
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertEqual(['product (3, 12)', 'business-python', 'business-modules'], result.stdout.splitlines())


    def test_real_old_install_upgrade_and_rollback_preserves_task(self):
        old_python = '/usr/bin/python3'
        if not Path(old_python).exists() or subprocess.check_output(
                [old_python, '-c', 'import sys;print(sys.version_info[:2])'], text=True).strip() != '(3, 9)':
            self.skipTest('真实3.9旧安装测试需要Python 3.9')
        baseline = '178283a0a05a6762fe23dae26a7f08b7f81c9f37'
        available = subprocess.run(['git', '-C', str(ROOT), 'cat-file', '-e', baseline], capture_output=True)
        if available.returncode:
            self.skipTest('隔离仓库缺少已发布旧基线178283a')
        seed, install, station = (self.root / name for name in ('seed', 'install', 'station'))
        seed.mkdir()
        paths = ('bootstrap', 'contracts', 'workflow', 'gate', 'projects', 'policies', 'adapters', 'skills', 'docs', 'agenticops', '.gitignore')
        archive = subprocess.check_output(['git', '-C', str(ROOT), 'archive', baseline, *paths])
        with tarfile.open(fileobj=io.BytesIO(archive)) as source:
            source.extractall(seed, filter='data')
        old_bin = self.root / 'old-bin'
        old_bin.mkdir()
        (old_bin / 'python3').symlink_to(old_python)
        env = dict(os.environ, PATH=str(old_bin) + os.pathsep + os.environ['PATH'])
        for key in ('AGENTIC_OPS_PYTHON', 'AO_PRODUCT_PYTHON', 'AO_PRODUCT_LEASE', 'AGENTIC_OPS_TEST_PYTHON', 'PYTHONPATH'):
            env.pop(key, None)
        def run(*args, check=True, use_env=None):
            result = subprocess.run(args, env=use_env or env, capture_output=True, text=True, timeout=90)
            if check:
                self.assertEqual(0, result.returncode, result.stdout + result.stderr)
            return result
        run('git', '-C', str(seed), 'init', '-q', '-b', 'channel')
        run('git', '-C', str(seed), 'config', 'user.name', 'Fixture')
        run('git', '-C', str(seed), 'config', 'user.email', 'fixture@example.invalid')
        run('git', '-C', str(seed), 'add', '.')
        run('git', '-C', str(seed), 'commit', '-qm', 'published old product')
        original = run('git', '-C', str(seed), 'rev-parse', 'HEAD').stdout.strip()
        run('bash', str(seed / 'bootstrap/install.sh'), '--install-home', str(install), '--repository', str(seed), '--branch', 'channel')
        run(old_python, str(install / 'bootstrap/product_state.py'), '--product-root', str(install), 'read')
        run(str(install / 'agenticops'), 'station', 'init', '--station', str(station), '--project', 'tapdata', '--agent', 'codex')
        # 用旧版任务模块生成原run；不手改升级器的状态或证据。
        code = ('import sys;sys.path.insert(0,sys.argv[1]);from workflow import task_store as s;'
                'task={"issue_key":"TAP-123","run_id":s.new_run_id("TAP-123"),"task_class":"technical_task",'
                '"stage":"waiting_takeover","outcome":"in_progress","facts":{},"history":[],"pending":None,'
                '"engineering_baseline":{"status":"resolving"},"task_repositories":{},"terminal_proof":None,"archive_ref":None};'
                's.compare_and_set(sys.argv[2],0,task)')
        run(old_python, '-c', code, str(install), str(station))
        (station / 'source/retained.txt').write_text('source work')
        (station / 'config/retained.txt').write_text('configuration')
        preserved = {p: p.read_bytes() for p in (station / '.agenticops/current-task.json', station / 'source/retained.txt', station / 'config/retained.txt')}
        for name in ('bootstrap', 'contracts'):
            shutil.copytree(ROOT / name, seed / name, dirs_exist_ok=True, ignore=shutil.ignore_patterns('__pycache__'))
        shutil.copy2(ROOT / 'agenticops', seed / 'agenticops')
        run('git', '-C', str(seed), 'add', '.')
        run('git', '-C', str(seed), 'commit', '-qm', 'python 3.12 candidate')
        target = run('git', '-C', str(seed), 'rev-parse', 'HEAD').stdout.strip()
        args = (sys.executable, str(seed / 'bootstrap/installation_update.py'), '--product-root', str(install),
                '--mode', 'update', '--to-ref', target, '--expected-current-ref', original)
        bad_env = dict(env, AGENTIC_OPS_PYTHON='/missing/python')
        failed = run(*args, '--check', use_env=bad_env, check=False)
        self.assertNotEqual(0, failed.returncode)
        self.assertEqual(original, run('git', '-C', str(install), 'rev-parse', 'HEAD').stdout.strip())
        good_env = dict(env, AGENTIC_OPS_PYTHON=sys.executable)
        run(*args, '--check', use_env=good_env)
        self.assertFalse((install / '.local/venv/product').exists())
        run(*args, '--writers-stopped', use_env=good_env)
        self.assertTrue((install / '.local/venv/product/bin/python').exists())
        self.assertEqual(target, run('git', '-C', str(install), 'rev-parse', 'HEAD').stdout.strip())
        run(str(install / 'agenticops'), 'station', 'repair', '--station', str(station), use_env=good_env)
        run(str(install / 'agenticops'), 'station', 'doctor', '--station', str(station), use_env=good_env)
        self.assertEqual(preserved, {p: p.read_bytes() for p in preserved})
        run(str(install / 'agenticops'), 'rollback', use_env=good_env)
        self.assertEqual(original, run('git', '-C', str(install), 'rev-parse', 'HEAD').stdout.strip())
        run(str(install / 'agenticops'), 'version')
        self.assertEqual(preserved, {p: p.read_bytes() for p in preserved})


if __name__ == '__main__':
    unittest.main()
