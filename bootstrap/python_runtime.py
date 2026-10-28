"""AgenticOps 的 Python 3.12 选择与可重建运行环境；不改变业务进程环境。"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

VERSION = (3, 12)


def require_current():
    if sys.version_info[:2] != VERSION:
        raise ValueError('AgenticOps 需要 Python 3.12.x；请使用产品入口或明确的 3.12 解释器')


def inspect(executable):
    path = shutil.which(str(executable)) if not os.path.isabs(str(executable)) else str(executable)
    if not path or not os.access(path, os.X_OK):
        raise ValueError('Python 解释器不可执行：%s' % executable)
    env = dict(os.environ)
    for key in ('PYTHONHOME', 'PYTHONPATH'):
        env.pop(key, None)
    result = subprocess.run([path, '-I', '-c',
        'import json,sys; print(json.dumps([list(sys.version_info[:3]),sys._base_executable]))'],
        env=env, capture_output=True, text=True, timeout=20)
    try:
        version, base = json.loads(result.stdout)
        if result.returncode or tuple(version[:2]) != VERSION:
            raise ValueError()
    except (ValueError, TypeError):
        raise ValueError('AgenticOps 需要 Python 3.12.x，所选解释器不符合要求：%s' % path)
    return os.path.abspath(path), base


def select(root, explicit=None):
    # 显式配置失效必须报错，不能悄悄寻找另一版本。
    explicit = explicit or os.environ.get('AGENTIC_OPS_PYTHON')
    if explicit:
        return inspect(explicit)[0]
    root = Path(root)
    candidates = [root / '.local/venv/product/bin/python',
                  root / '.local/venv/internal/bin/python']
    for path in candidates:
        if path.exists():
            return inspect(path)[0]
    if sys.version_info[:2] == VERSION:
        return inspect(sys._base_executable)[0]
    named = shutil.which('python3.12')
    if named:
        return inspect(named)[0]
    uv = shutil.which('uv')
    if uv:
        result = subprocess.run([uv, 'python', 'find', '--no-python-downloads', '3.12'],
                                capture_output=True, text=True, timeout=20)
        if result.returncode == 0:
            return inspect(result.stdout.strip())[0]
    raise ValueError('缺少 Python 3.12.x；先准备解释器，或设置 AGENTIC_OPS_PYTHON 为其绝对路径；未切换产品版本')


def prepare(root, executable=None):
    root = Path(root).resolve()
    target = root / '.local/venv/product'
    for path in (root / '.local', root / '.local/venv', target):
        if path.is_symlink():
            raise ValueError('产品 Python 环境目录不能是符号链接：%s' % path)
    selected = select(root, executable)
    _, base = inspect(selected)
    if target.exists():
        # 损坏环境保留现场，不覆盖或删除；独立解释器仍可用于恢复。
        current, current_base = inspect(target / 'bin/python')
        if Path(base).resolve() != Path(current_base).resolve():
            raise ValueError('产品 Python 基础解释器已变化；先保全并显式重建运行环境')
        return current
    target.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run([base, '-I', '-m', 'venv', '--without-pip', str(target)], check=True)
    return inspect(target / 'bin/python')[0]


def test_python(root):
    return select(root, os.environ.get('AGENTIC_OPS_TEST_PYTHON'))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('operation', choices=('select', 'prepare', 'test'))
    parser.add_argument('--root', required=True)
    args = parser.parse_args()
    try:
        print({'select': select, 'prepare': prepare, 'test': test_python}[args.operation](args.root))
    except (ValueError, OSError, subprocess.SubprocessError) as error:
        print('[AgenticOps][Python][失败] %s' % error, file=sys.stderr)
        return 2
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
