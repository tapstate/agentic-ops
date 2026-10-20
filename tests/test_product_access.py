"""真实子进程屏障验证产品入口；不对使用中的安装执行切换。"""
import json
import os
from pathlib import Path
import select
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from bootstrap import product_access as access


class ProductAccessTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='ao-product-access-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        (self.root / 'bootstrap').mkdir()
        shutil.copyfile(ROOT / 'bootstrap/product_access.py', self.root / 'bootstrap/product_access.py')

    def process(self, code, *args, pass_fds=(), env=None):
        process = subprocess.Popen([sys.executable, '-u', '-c', code, str(self.root), *args],
                                   stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                                   pass_fds=pass_fds, env=env)
        self.addCleanup(lambda: process.poll() is None and process.kill())
        return process

    def test_shared_blocks_switch_and_releases_without_deleting_inode(self):
        code = 'import runpy,sys; a=runpy.run_path(sys.argv[1]+"/bootstrap/product_access.py");\ntry:\n with a["lease"](sys.argv[1],"exclusive"): print("acquired")\nexcept a["Busy"]: print("busy")'
        with access.lease(self.root):
            inode = (self.root / access.LOCK_NAME).stat().st_ino
            p = self.process(code)
            self.assertEqual(('busy\n', ''), p.communicate(timeout=10))
        p = self.process(code)
        self.assertEqual(('acquired\n', ''), p.communicate(timeout=10))
        self.assertEqual(inode, (self.root / access.LOCK_NAME).stat().st_ino)

    def test_shared_access_does_not_require_installation_write_permission(self):
        with access.lease(self.root):
            pass
        path = self.root / access.LOCK_NAME
        path.chmod(0o400)
        path.parent.chmod(0o500)
        try:
            with access.lease(self.root):
                self.assertEqual(0o400, path.stat().st_mode & 0o777)
        finally:
            path.parent.chmod(0o700)
            path.chmod(0o600)

    def test_waiter_loads_current_script_only_after_exclusive_release(self):
        script = self.root / 'reader.py'
        header = 'import runpy\nif not globals().get("_AO_PRODUCT_ENTRY"):\n raise SystemExit(runpy.run_path(__file__.replace("reader.py","bootstrap/product_access.py"))["python_entry"](__file__))\n'
        script.write_text(header + 'print("old")\n')
        # 管道在真正调用共享 flock 前发信号，证明旧入口已加载而产品正文尚未执行。
        ready_r, ready_w = os.pipe()
        self.addCleanup(os.close, ready_r)
        with access.lease(self.root, 'exclusive'):
            env = dict(os.environ); env.pop(access.LEASE_ENV, None)
            code = '''import os,runpy,sys,fcntl
original = fcntl.flock
def barrier(fd, mode):
 if mode == fcntl.LOCK_SH:
  os.write(int(sys.argv[2]), b"ready")
 return original(fd, mode)
fcntl.flock = barrier
runpy.run_path(sys.argv[1]+"/reader.py",run_name="__main__")'''
            p = self.process(code, str(ready_w), pass_fds=(ready_w,), env=env)
            os.close(ready_w)
            self.assertEqual(b'ready', os.read(ready_r, 5))
            self.assertEqual([], select.select([p.stdout], [], [], 0)[0])
            script.write_text(header + 'print("new")\n')
        self.assertEqual(('new\n', ''), p.communicate(timeout=10))

    def test_shared_cannot_upgrade_and_inherited_exclusive_does_not_deadlock(self):
        with access.lease(self.root):
            with self.assertRaises(access.Busy):
                with access.lease(self.root, 'exclusive'):
                    self.fail('upgraded')
        with access.lease(self.root, 'exclusive') as fd:
            p = self.process('import runpy,sys; a=runpy.run_path(sys.argv[1]+"/bootstrap/product_access.py");\nwith a["lease"](sys.argv[1],"shared"): print("nested")', pass_fds=(fd,))
            self.assertEqual(('nested\n', ''), p.communicate(timeout=10))

    def test_external_candidate_keeps_target_lease_while_locking_code_root(self):
        code_root = self.root / 'candidate'
        code_root.mkdir()
        with access.lease(self.root, 'exclusive') as fd:
            code = 'import runpy,sys; from pathlib import Path; a=runpy.run_path(sys.argv[1]+"/bootstrap/product_access.py");\nwith a["_entry_lease"](Path(sys.argv[1])/"candidate",Path(sys.argv[1]),"shared"):\n print("both-held")'
            process = self.process(code, pass_fds=(fd,))
            self.assertEqual(('both-held\n', ''), process.communicate(timeout=10))

    def test_mismatched_station_rejected_before_product_body(self):
        station = self.root / 'station'
        (station / '.agenticops').mkdir(parents=True)
        (station / '.agenticops/station.json').write_text(json.dumps({'product_root': str(self.root / 'other')}))
        (self.root / 'reader.py').write_text('print("unsafe-body")')
        process = self.process('import runpy,sys; a=runpy.run_path(sys.argv[1]+"/bootstrap/product_access.py"); raise SystemExit(a["python_entry"](sys.argv[1]+"/reader.py"))', '--station', str(station))
        out, err = process.communicate(timeout=10)
        self.assertEqual(2, process.returncode)
        self.assertNotIn('unsafe-body', out)
        self.assertIn('绑定不一致', err)

    def test_environment_without_held_descriptor_cannot_bypass(self):
        path = self.root / access.LOCK_NAME
        path.parent.mkdir(); path.touch()
        fd = os.open(path, os.O_RDWR)
        self.addCleanup(os.close, fd)
        env = dict(os.environ, AO_PRODUCT_LEASE=json.dumps({'root':str(self.root),'fd':fd,'owner':os.getpid(),'mode':'exclusive'}))
        p = self.process('import runpy,sys; a=runpy.run_path(sys.argv[1]+"/bootstrap/product_access.py");\nwith a["lease"](sys.argv[1],"exclusive"): print("unsafe")', pass_fds=(fd,), env=env)
        out, err = p.communicate(timeout=10)
        self.assertNotEqual(0, p.returncode); self.assertNotIn('unsafe',out)
        self.assertIn('未持锁', err)

    def test_lock_identity_replacement_rejected(self):
        with self.assertRaisesRegex(ValueError, '身份变化'):
            with access.lease(self.root):
                path = self.root / access.LOCK_NAME
                path.rename(path.with_suffix('.old')); path.touch()

    def test_kernel_releases_lease_after_process_killed(self):
        r, w = os.pipe()
        self.addCleanup(os.close, w)
        process = self.process('import runpy,sys,os; a=runpy.run_path(sys.argv[1]+"/bootstrap/product_access.py");\nwith a["lease"](sys.argv[1]):\n print("held",flush=True)\n os.read(int(sys.argv[2]),1)', str(r), pass_fds=(r,))
        os.close(r)
        self.assertEqual('held\n', process.stdout.readline())
        with self.assertRaises(access.Busy):
            with access.lease(self.root, 'exclusive'):
                self.fail('concurrent switch')
        process.kill(); process.communicate(timeout=10)
        with access.lease(self.root, 'exclusive'):
            self.assertTrue((self.root / access.LOCK_NAME).exists())

    def test_version_entry_ignores_stale_same_timestamp_bytecode(self):
        import py_compile
        source = self.root / 'snapshot.py'
        source.write_text('value = "old"\n')
        stamp = source.stat().st_mtime
        cache = self.root / '__pycache__' / ('snapshot.' + sys.implementation.cache_tag + '.pyc')
        cache.parent.mkdir()
        py_compile.compile(str(source), cfile=str(cache), doraise=True)
        source.write_text('value = "new"\n')
        os.utime(source, (stamp, stamp))
        for args in [('init','-q'), ('add','snapshot.py'), ('-c','user.name=Fixture','-c','user.email=f@example.test','commit','-qm','fixture')]:
            subprocess.run(['git','-C',str(self.root),*args],check=True,capture_output=True)
        head = subprocess.check_output(['git','-C',str(self.root),'rev-parse','HEAD'],text=True).strip()
        (self.root / '.local').mkdir()
        (self.root / '.local/product.json').write_text(json.dumps({'mode':'installed','current_ref':head}))
        p = self.process('import sys; sys.pycache_prefix=None; sys.path.insert(0,sys.argv[1]); import snapshot; print(snapshot.value)')
        self.assertEqual(('old\n',''),p.communicate(timeout=10))
        reader = self.root / 'reader.py'
        reader.write_text('import sys; from pathlib import Path; sys.path.insert(0,str(Path(__file__).parent)); import snapshot; print(snapshot.value)')
        p = self.process('import sys,runpy; sys.pycache_prefix=None; a=runpy.run_path(sys.argv[1]+"/bootstrap/product_access.py"); a["python_entry"](sys.argv[1]+"/reader.py")')
        self.assertEqual(('new\n',''),p.communicate(timeout=10))
        for environment in ({'GIT_CONFIG_COUNT': 'invalid'}, {'GIT_DIR': str(self.root / 'missing.git')}):
            with self.subTest(environment=environment):
                p = self.process('import sys,runpy; a=runpy.run_path(sys.argv[1]+"/bootstrap/product_access.py"); raise SystemExit(a["python_entry"](sys.argv[1]+"/reader.py"))', env=dict(os.environ, **environment))
                self.assertEqual(('new\n',''), p.communicate(timeout=10))
                self.assertEqual(0, p.returncode)

    def test_direct_lifecycle_scripts_lock_their_actual_target(self):
        home = self.root / 'home'
        home.mkdir()
        custom = self.root / 'custom-install'
        for script in ('rollback.sh', 'update.sh', 'setup.sh'):
            shutil.copyfile(ROOT / 'bootstrap' / script, self.root / 'bootstrap' / script)
            for setting in (None, '', str(custom)):
                target = custom if setting else self.root if script == 'setup.sh' else home / '.agentic-ops'
                target.mkdir(parents=True, exist_ok=True)
                with self.subTest(script=script, setting=setting), access.lease(target):
                    environment = dict(os.environ, HOME=str(home))
                    environment.pop(access.LEASE_ENV, None)
                    if setting is None:
                        environment.pop('AGENTIC_OPS_HOME', None)
                    else:
                        environment['AGENTIC_OPS_HOME'] = setting
                    result = subprocess.run(['bash', str(self.root / 'bootstrap' / script)],
                                            env=environment, capture_output=True, text=True, timeout=10)
                    self.assertEqual(3, result.returncode, result.stdout + result.stderr)
                    self.assertIn('暂不切换版本', result.stderr)
                    self.assertFalse((target / '.local/product.json').exists())

    def test_direct_station_init_waits_for_default_installation(self):
        home = self.root / 'home'
        home.mkdir()
        shutil.copyfile(ROOT / 'bootstrap/station-init.sh', self.root / 'bootstrap/station-init.sh')
        ready_r, ready_w = os.pipe()
        self.addCleanup(os.close, ready_r)
        environment = dict(os.environ, HOME=str(home), AGENTIC_OPS_HOME='')
        environment.pop(access.LEASE_ENV, None)
        (home / '.agentic-ops').mkdir()
        with access.lease(home / '.agentic-ops', 'exclusive'):
            code = '''import os,runpy,sys,fcntl
original = fcntl.flock
def barrier(fd, mode):
 if mode == fcntl.LOCK_SH and os.fstat(fd).st_ino == int(sys.argv[3]):
  os.write(int(sys.argv[2]), b"ready")
 return original(fd, mode)
fcntl.flock = barrier
a=runpy.run_path(sys.argv[1]+"/bootstrap/product_access.py")
raise SystemExit(a["shell_entry"](sys.argv[1]+"/bootstrap/station-init.sh",["--help"]))'''
            process = self.process(code, str(ready_w), str((home / '.agentic-ops' / access.LOCK_NAME).stat().st_ino),
                                   pass_fds=(ready_w,), env=environment)
            os.close(ready_w)
            self.assertTrue(select.select([ready_r], [], [], 10)[0], '入口未锁定默认安装')
            self.assertEqual(b'ready', os.read(ready_r, 5))
            self.assertEqual([], select.select([process.stdout], [], [], 0)[0])
        out, err = process.communicate(timeout=10)
        self.assertEqual(0, process.returncode, err)
        self.assertIn('用法：station-init.sh', out)

    def test_supported_python_entries_guard_before_product_import(self):
        import ast
        for directory in ('bootstrap','workflow','gate','projects/tapdata/scripts'):
            for path in (ROOT / directory).glob('*.py'):
                source = path.read_text()
                if '__main__' not in source or path.name == 'product_access.py':
                    continue
                tree = ast.parse(source)
                first = next(n for n in tree.body if not (isinstance(n, ast.Expr) and isinstance(n.value, ast.Constant) and isinstance(n.value.value,str)) and not (isinstance(n,ast.ImportFrom) and n.module=='__future__'))
                self.assertIsInstance(first, ast.If, str(path))
                self.assertIn('python_entry', ast.get_source_segment(source,first), str(path))


if __name__ == '__main__':
    unittest.main()
