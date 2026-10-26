"""标准库产品入口屏障；稳定 flock 文件不删除，不承担任务或平台规则。"""
import contextlib
import errno
import fcntl
import json
import os
from pathlib import Path
import runpy
import stat
import subprocess
import sys

LEASE_ENV = 'AO_PRODUCT_LEASE'
LOCK_NAME = '.local/product-access.lock'


def _tokens():
    raw = os.environ.get(LEASE_ENV)
    if not raw:
        return []
    value = json.loads(raw)
    values = value if isinstance(value, list) else [value]
    if not all(isinstance(item, dict) for item in values):
        raise ValueError('产品租约交接材料无效')
    return values


def _token(root):
    return next((item for item in _tokens() if item.get('root') == str(root)), None)


def _publish(token):
    os.environ[LEASE_ENV] = json.dumps([item for item in _tokens() if item.get('root') != token['root']] + [token])


class Busy(ValueError):
    pass


def _identity(fd, path):
    if path.parent.is_symlink():
        raise ValueError('产品租约目录身份变化')
    opened, current = os.fstat(fd), os.stat(path, follow_symlinks=False)
    if (not stat.S_ISREG(current.st_mode) or opened.st_nlink != 1
            or (opened.st_dev, opened.st_ino) != (current.st_dev, current.st_ino)):
        raise ValueError('产品租约文件身份变化；保留现场，不删除或替换锁文件')


def _ancestor(pid):
    """仅验证本机进程亲缘；不是身份认证，不接受单独环境变量作为租约。"""
    if pid in (os.getpid(), os.getppid()):
        return True
    current = os.getppid()
    for _ in range(64):
        if current == pid:
            return True
        if current <= 1:
            return False
        if sys.platform.startswith('linux'):
            try:
                # 只读取已知父链，不扫描进程；精简 Linux 无需额外安装 procps。
                current = int(Path('/proc/%s/stat' % current).read_text().rsplit(')', 1)[1].split()[1])
            except (OSError, ValueError, IndexError):
                return False
            continue
        value = subprocess.run(['ps', '-o', 'ppid=', '-p', str(current)],
                               capture_output=True, text=True, timeout=5)
        if value.returncode or not value.stdout.strip().isdigit():
            return False
        current = int(value.stdout.strip())
    return False


def inherited(root, mode):
    token = _token(root)
    if not token:
        return None
    try:
        fd = token['fd']
        if type(fd) is not int or fd < 3:
            raise ValueError('产品租约描述符无效')
        try:
            os.fstat(fd)
        except OSError as error:
            if error.errno == errno.EBADF:
                return None  # subprocess 默认关闭描述符；重新取得自己的租约。
            raise
        path = root / LOCK_NAME
        _identity(fd, path)
        if token['mode'] not in ('shared', 'exclusive') or not _ancestor(token['owner']):
            raise ValueError('产品租约没有有效的父进程持有者')
        if mode == 'exclusive' and token['mode'] != 'exclusive':
            raise Busy('当前调用或会话持有共享租约；禁止原地升级，请退出后由启动侧更新')
        # 独立描述符必须观察到父租约；随后同一继承描述符必须可重取原模式。
        # 环境变量配上一个未加锁的 fd 不能通过此核验。
        probe = os.open(str(path), os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0))
        try:
            probe_mode = fcntl.LOCK_SH if token['mode'] == 'exclusive' else fcntl.LOCK_EX
            try:
                fcntl.flock(probe, probe_mode | fcntl.LOCK_NB)
            except BlockingIOError:
                pass
            else:
                raise ValueError('继承的产品租约未持锁')
        finally:
            os.close(probe)
        fcntl.flock(fd, (fcntl.LOCK_EX if token['mode'] == 'exclusive' else fcntl.LOCK_SH) | fcntl.LOCK_NB)
        return fd
    except (KeyError, TypeError, json.JSONDecodeError) as error:
        raise ValueError('产品租约交接材料无效') from error


@contextlib.contextmanager
def lease(root, mode='shared'):
    root = Path(root).resolve()
    reused = inherited(root, mode)
    if reused is not None:
        previous = os.environ[LEASE_ENV]
        token = dict(_token(root))
        token['owner'] = os.getpid()
        _publish(token)
        try:
            yield reused
            _identity(reused, root / LOCK_NAME)
        finally:
            os.environ[LEASE_ENV] = previous
        return
    path = root / LOCK_NAME
    if path.parent.is_symlink():
        raise ValueError('产品本地状态目录不能是链接')
    if not path.parent.exists():
        path.parent.mkdir(mode=0o700, exist_ok=True)
    flags = os.O_RDONLY if mode == 'shared' and path.exists() else os.O_CREAT | os.O_RDWR
    fd = os.open(str(path), flags | getattr(os, 'O_NOFOLLOW', 0), 0o600)
    previous = os.environ.get(LEASE_ENV)
    try:
        _identity(fd, path)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB if mode == 'exclusive' else fcntl.LOCK_SH)
        except BlockingIOError as error:
            raise Busy('产品调用或 Agent 会话仍在使用安装，暂不切换版本') from error
        _identity(fd, path)
        _publish({'root': str(root), 'fd': fd, 'mode': mode, 'owner': os.getpid()})
        yield fd
        _identity(fd, path)
    finally:
        if previous is None:
            os.environ.pop(LEASE_ENV, None)
        else:
            os.environ[LEASE_ENV] = previous
        os.close(fd)  # 不主动 LOCK_UN：继承该描述符的子进程仍须受保护。


def child_fds(root):
    fd = inherited(Path(root).resolve(), 'shared')
    return () if fd is None else (fd,)


def child_environment(root, environment=None):
    environment = dict(os.environ if environment is None else environment)
    token = _token(Path(root).resolve())
    if token is None:
        environment.pop(LEASE_ENV, None)
    else:
        environment[LEASE_ENV] = json.dumps(token)
    return environment


def _option(args, name, default):
    if name in args and args.index(name) + 1 < len(args):
        return args[args.index(name) + 1]
    for value in args:
        if value.startswith(name + '='):
            return value.split('=', 1)[1]
    return default


def _root(script):
    for parent in Path(script).resolve().parents:
        if (parent / 'bootstrap/product_access.py').is_file():
            return parent
    raise ValueError('无法定位产品入口根目录')


def _mode(script, args):
    name = Path(script).name
    switch = (name in ('update.sh', 'rollback.sh', 'setup.sh', 'installation_update.py')
              or name == 'product_state.py' and any(value in ('write', 'update-ref') for value in args)
              or name == 'agenticops' and args and args[0] in ('update', 'rollback', 'recover-install', 'setup'))
    return 'exclusive' if switch and '--check' not in args else 'shared'


def _run_product(root, command, capture=False):
    result = subprocess.run(command, pass_fds=child_fds(root),
                            stdout=subprocess.PIPE if capture else None, text=True, env=child_environment(root))
    if result.returncode:
        if result.returncode == 3:
            raise Busy('共享安装仍忙碌，本次不切换；可继续旧版、协调停写后重试或选择独立安装')
        raise ValueError('更新准备或核验未完成（退出码=%s）；保留现场，不创建新任务' % result.returncode)
    return json.loads(result.stdout) if capture else None


def before_task(root, args, startup=False):
    """启动侧编排只加载标准库；每次产品子调用自行持锁，不原地升级。"""
    boundary = args.index('--') if '--' in args else len(args)
    own, forwarded = args[:boundary], args[boundary:]
    station = _option(own, '--station' if startup else '--dir', os.getcwd())
    current_ref = _option(own, '--update-current-ref', None)
    target_ref = _option(own, '--update-target-ref', None)
    if bool(current_ref) != bool(target_ref):
        raise ValueError('继续旧版须同时绑定 --update-current-ref 与 --update-target-ref 完整 SHA')
    stopped = startup and '--writers-stopped' in own
    apply_update = startup and '--apply-update' in own
    expected_stations = _option(own, '--expected-stations', None) if startup else None
    if apply_update and (not current_ref or not stopped or expected_stations is None):
        raise ValueError('应用更新须明确原/目标 SHA，并核验停写：--apply-update --writers-stopped --update-current-ref <原SHA> --update-target-ref <目标SHA> --expected-stations <登记工位JSON>')
    cleaned = []
    index = 0
    while index < len(own):
        value = own[index]
        if value in ('--update-current-ref', '--update-target-ref') or startup and value == '--expected-stations':
            if index + 1 == len(own):
                raise ValueError('--update-current-ref 缺少完整 SHA')
            index += 2
            continue
        if value.startswith(('--update-current-ref=', '--update-target-ref=')) or startup and value.startswith('--expected-stations=') or startup and value in ('--writers-stopped', '--apply-update'):
            index += 1
            continue
        cleaned.append(value); index += 1
    command = [sys.executable, str(root / 'bootstrap/task_update_check.py'), '--station', station]
    if startup:
        command += ['--check-resume']
    if current_ref and not apply_update:
        command += ['--current-ref', current_ref, '--target-ref', target_ref]
    packet = _run_product(root, command, capture=True)
    if packet.get('state_requires_repair'):
        print('[AgenticOps][任务数据] 会话可启动以分析修补；任务推进仍须原功能复查：' + packet['state_requires_repair'], file=sys.stderr)
    status = packet['status']
    if apply_update and (packet.get('original_ref') != current_ref or packet.get('target_ref') != target_ref):
        raise ValueError('更新确认与当前原/目标提交不一致；重新检查并确认，不沿用旧决定')
    if apply_update and json.loads(expected_stations) != packet.get('stations'):
        raise ValueError('登记工位范围与更新确认不一致；重新展示影响范围并确认')
    if status == 'invalid_local_state':
        raise ValueError(packet['reason'])
    if status in ('check_unavailable', 'target_unknown'):
        print('[AgenticOps][更新检查] 未能确认可用更新，沿可信当前版本继续：' + packet['reason'], file=sys.stderr)
    if status == 'update_available':
        if packet['compatible'] and stopped and apply_update:
            command = [sys.executable, str(root / 'bootstrap/installation_update.py'), '--product-root', str(root),
                       '--to-ref', packet['target_ref'], '--expected-current-ref', packet['original_ref'],
                       '--automatic', '--writers-stopped', '--expected-stations', json.dumps(packet['stations'])]
            _run_product(root, command)
            packet['original_ref'] = packet['target_ref']
            _run_product(root, ['bash', str(root / 'agenticops'), 'station', 'repair', '--station', station])
            _run_product(root, ['bash', str(root / 'agenticops'), 'station', 'doctor', '--station', station])
        else:
            print(json.dumps(packet, ensure_ascii=False, indent=2), file=sys.stderr)
            if not packet['compatible']:
                guidance = '旧模式目标不兼容，须由原版退出并 purge，或选择独立安装'
            else:
                guidance = '研发确认此原/目标 SHA 及共享工位影响后，核实停写，加 --apply-update --writers-stopped 并同时传入上述原/目标 SHA 与 --expected-stations 的登记工位 JSON 更新；也可选择独立安装'
            raise Busy('发现更新；可用 --update-current-ref %s --update-target-ref %s 继续当前版本处理。%s；本次未修改任务或创建 run' % (packet['original_ref'], packet['target_ref'], guidance))
    return cleaned + forwarded, packet


def _git_head(root):
    # 此处尚未加载产品模块；保持与 workflow.git_environment 的只读约定一致。
    environment = {key: value for key, value in os.environ.items() if not key.startswith('GIT_')}
    environment.update(GIT_TERMINAL_PROMPT='0', GIT_NO_REPLACE_OBJECTS='1',
                       GIT_NO_LAZY_FETCH='1', GIT_OPTIONAL_LOCKS='0')
    result = subprocess.run(['git', '-C', str(root), 'rev-parse', 'HEAD'],
                            capture_output=True, text=True, timeout=30, env=environment)
    if result.returncode:
        raise ValueError('无法核验产品 HEAD；保留现场，不加载产品代码')
    return result.stdout.strip()


def _verify_preflight(root, packet):
    if not packet or 'original_ref' not in packet:
        return
    document = json.loads((root / '.local/product.json').read_text())
    head = _git_head(root)
    if document['current_ref'] != packet['original_ref'] or head != packet['original_ref']:
        raise ValueError('检查后产品版本变化；重新检查，不使用旧会话或旧选择创建任务')
    binding = json.loads((Path(packet['station']) / '.agenticops/station.json').read_text())
    if binding['station_id'] != packet['station_id'] or Path(binding['product_root']).resolve() != root:
        raise ValueError('检查后工位绑定变化')


def _fresh_loader():
    captured = globals().get('_AO_LOADER_SOURCE')
    if captured is None:
        return None
    source = Path(__file__).read_bytes()
    if source == captured:
        return None
    namespace = {'__file__': __file__, '_AO_LOADER_SOURCE': source}
    exec(compile(source, __file__, 'exec'), namespace)
    return namespace


def _verify_installation(root):
    path = root / '.local/product.json'
    if not path.exists():
        return  # 初次安装写入产品记录之前，仍由安装入口核验目标目录。
    document = json.loads(path.read_text())
    if document.get('mode') != 'installed':
        return
    head = _git_head(root)
    token = _token(root)
    if token['mode'] != 'exclusive' and head != document.get('current_ref'):
        raise ValueError('加载产品代码前发现 HEAD/安装记录不一致；保留现场并恢复原更新')
    # -B 只禁止写缓存，不能禁止读取同秒、同大小源码遗留的旧 pyc。
    # 以完整提交隔离读取路径，且本入口不生成字节码缓存。
    sys.dont_write_bytecode = True
    sys.pycache_prefix = str(root / '.local/product-bytecode' / head)


def _verify_binding(root, script, args):
    own = args[:args.index('--')] if '--' in args else args
    default = os.getcwd() if (Path(script).parent.name == 'workflow' or '/projects/' in script
                              or Path(script).name == 'agenticops' and own[:1] == ['station']) else None
    station = _option(own, '--dir', _option(own, '--station', default))
    if station is None:
        return
    path = Path(station).resolve() / '.agenticops/station.json'
    if path.is_symlink() or path.parent.is_symlink():
        raise ValueError('工位绑定不能是链接')
    if path.exists():
        binding = json.loads(path.read_text())
        if not isinstance(binding.get('product_root'), str) or Path(binding['product_root']).resolve() != root:
            raise ValueError('入口产品根与工位绑定不一致；请从绑定的产品版本调用')


@contextlib.contextmanager
def _entry_lease(code_root, root, mode):
    with contextlib.ExitStack() as stack:
        if code_root != root:
            stack.enter_context(lease(code_root))
        fd = stack.enter_context(lease(root, mode))
        yield fd


def python_entry(script):
    script = str(Path(script).resolve())
    args = sys.argv[1:]
    code_root = root = _root(script)
    if Path(script).parent.name == 'bootstrap':
        root = Path(_option(args, '--product-root', _option(args, '--install-home', root))).resolve()
    try:
        packet = None
        if Path(script).name == 'task.py' and args and args[0] == 'takeover' and not any(v in args for v in ('--help', '-h')):
            args, packet = before_task(root, args)
            sys.argv = [script, *args]
        with _entry_lease(code_root, root, _mode(script, args)):
            fresh = _fresh_loader()
            if fresh is not None:
                return fresh['python_entry'](script)
            _verify_installation(root)
            _verify_binding(root, script, args)
            _verify_preflight(root, packet)
            # 加锁后读取脚本，不恢复等待前已加载的产品模块。
            runpy.run_path(script, run_name='__main__', init_globals={'_AO_PRODUCT_ENTRY': True})
        return 0
    except (ValueError, OSError) as error:
        print('[AgenticOps][产品租约][失败] %s' % error, file=sys.stderr)
        return 3 if isinstance(error, Busy) else 2


def shell_entry(script, args):
    script = str(Path(script).resolve())
    code_root = root = _root(script)
    if Path(script).name in ('update.sh', 'rollback.sh', 'setup.sh', 'station-init.sh'):
        # 与各脚本的 ${AGENTIC_OPS_HOME:-...} 一致，空值也使用默认目录。
        default_root = code_root if Path(script).name == 'setup.sh' else Path.home() / '.agentic-ops'
        root = Path(os.environ.get('AGENTIC_OPS_HOME') or default_root).resolve()
    try:
        packet = None
        own = args[:args.index('--')] if '--' in args else args
        if Path(script).name == 'agenticops' and own[:2] == ['station', 'start'] and not any(v in own for v in ('--help', '-h')):
            if '--non-interactive' not in own and (not sys.stdin.isatty() or not sys.stdout.isatty()):
                raise ValueError('交互启动需要终端；自动化调用请明确使用 --non-interactive，并传入 Agent 支持的非交互参数。')
            args, packet = before_task(root, args, startup=True)
        with _entry_lease(code_root, root, _mode(script, args)) as fd:
            fresh = _fresh_loader()
            if fresh is not None:
                return fresh['shell_entry'](script, args)
            _verify_installation(root)
            _verify_binding(root, script, args)
            _verify_preflight(root, packet)
            source = Path(script).read_text()
            begin = source.index('# AO_PRODUCT_SHELL_BEGIN')
            end = source.index('# AO_PRODUCT_SHELL_END', begin) + len('# AO_PRODUCT_SHELL_END')
            source = source[:begin] + source[end:]
            source = ('export AO_PRODUCT_LEASE="$(python3 -c \'import json,os,sys; '
                      'v=json.loads(os.environ["AO_PRODUCT_LEASE"]); v["owner"]=int(sys.argv[1]); '
                      'print(json.dumps(v))\' "$$")"\n') + source
            # 监督进程持续持锁；shell exec Agent 后仍等待整个前台会话退出。
            result = subprocess.run(['bash', '-c', source, script, *args], pass_fds=(fd,), env=child_environment(root))
            return result.returncode if result.returncode >= 0 else 128 - result.returncode
    except (ValueError, OSError) as error:
        print('[AgenticOps][产品租约][失败] %s' % error, file=sys.stderr)
        return 3 if isinstance(error, Busy) else 2
