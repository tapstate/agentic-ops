#!/usr/bin/env python3
"""Shared assets: scope, ownership, legacy manifests and installed lifecycle."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'bootstrap'))
from skill_wiring import (shared_skill_sources, maintenance_skill_sources,
                          unique_skill_sources, expected_artifacts, refresh_wiring,
                          check_wiring)
from station_paths import StationDirectory
import render
from agent_registry import discover


def skill(root, name):
    path = root / name
    path.mkdir(parents=True)
    (path / 'SKILL.md').write_text('---\nname: %s\ndescription: test skill\n---\n' % name)
    return path


class SharedSkillsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.product = self.root / 'product'
        self.product.mkdir()
        shutil.copytree(ROOT / 'adapters', self.product / 'adapters')
        self.maintenance = skill(self.product / 'skills', 'maintenance-test')
        self.shared = skill(self.product / 'skills/shared', 'shared-test')

    def test_selection_collision_and_unsafe_sources(self):
        self.assertEqual(maintenance_skill_sources(self.product), [self.maintenance])
        self.assertEqual(shared_skill_sources(self.product), [self.shared])
        duplicate = skill(self.product / 'projects/test/skills', 'shared-test')
        with self.assertRaisesRegex(ValueError, '冲突'):
            unique_skill_sources([self.shared], [duplicate])
        shutil.rmtree(self.product / 'skills/shared')
        with self.assertRaisesRegex(ValueError, 'update'):
            shared_skill_sources(self.product)
        (self.product / 'skills/shared').symlink_to(self.root, target_is_directory=True)
        with self.assertRaises(ValueError):
            shared_skill_sources(self.product)

    def test_legacy_manifest_refresh_and_safe_removal(self):
        artifacts = expected_artifacts(self.product)
        legacy = {p: a for p, a in artifacts.items() if p.endswith('/maintenance-test')}
        with StationDirectory(self.product) as tree:
            refresh_wiring(self.product, legacy, tree)
            refresh_wiring(self.product, artifacts, tree)
            check_wiring(self.product, artifacts, tree)
            for path in artifacts:
                if path.endswith('/shared-test'):
                    self.assertEqual((self.product / path).resolve(), self.shared)
        # Changed link must survive failed cleanup, including other valid links.
        link = self.product / '.agents/skills/shared-test'
        link.unlink()
        link.symlink_to(self.maintenance)
        with StationDirectory(self.product) as tree:
            with self.assertRaisesRegex(ValueError, '漂移'):
                refresh_wiring(self.product, legacy, tree)
        self.assertTrue((self.product / '.claude/skills/shared-test').is_symlink())
        link.unlink()
        link.symlink_to(artifacts['.agents/skills/shared-test']['target'])
        with StationDirectory(self.product) as tree:
            refresh_wiring(self.product, legacy, tree)
        with StationDirectory(self.product) as tree:
            self.assertFalse(link.is_symlink())
            check_wiring(self.product, legacy, tree)

    def test_project_selection_and_owned_drift(self):
        skill(self.product / 'projects/demo/skills', 'project-test')
        station = self.root / 'station'
        station.mkdir()
        manifests = discover(self.product)
        artifacts, _ = render.expected_artifacts(self.product, station, 'demo', ['codex'], manifests)
        path = '.agents/skills/shared-test'
        self.assertIn(path, artifacts)
        self.assertIn('.agents/skills/project-test', artifacts)
        self.assertNotIn('.agents/skills/maintenance-test', artifacts)
        self.assertEqual((station / path).parent.joinpath(artifacts[path]['target']).resolve(), self.shared)
        link = station / path
        link.parent.mkdir(parents=True)
        link.symlink_to(self.maintenance)
        owned = {path: artifacts[path]}
        with StationDirectory(station) as tree:
            with self.assertRaisesRegex(ValueError, '漂移'):
                render.assert_artifact_ownership(station, owned, {path: artifacts[path]}, tree)
        self.assertEqual(link.resolve(), self.maintenance)
        link.unlink()
        link.symlink_to(artifacts[path]['target'])
        with StationDirectory(station) as tree:
            render.remove_stale_artifacts(station, owned, set(), tree)
        self.assertFalse(link.is_symlink())
        skill(self.product / 'projects/demo/skills', 'shared-test')
        with self.assertRaisesRegex(ValueError, '冲突'):
            render.expected_artifacts(self.product, station, 'demo', ['codex'], manifests)

    def test_unmanaged_file_is_preserved(self):
        target = self.product / '.agents/skills/shared-test'
        target.parent.mkdir(parents=True)
        target.write_text('user content')
        with StationDirectory(self.product) as tree:
            with self.assertRaises(ValueError):
                refresh_wiring(self.product, expected_artifacts(self.product), tree)
        self.assertEqual(target.read_text(), 'user content')


class InstalledScopeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.source = self.root / 'source'
        self.source.mkdir()
        self.home = self.root / 'home'
        self.home.mkdir()
        self.env = dict(os.environ, HOME=str(self.home), PYTHONDONTWRITEBYTECODE='1')
        for name in ('bootstrap', 'contracts', 'workflow', 'gate', 'policies'):
            shutil.copytree(ROOT / name, self.source / name)
        self.git(self.source, 'init', '-q', '-b', 'develop')
        self.git(self.source, 'config', 'user.email', 'test@example.test')
        self.git(self.source, 'config', 'user.name', 'Test')
        (self.source / '.gitignore').write_text('.local/\n__pycache__/\n')
        self.git(self.source, 'add', '.')
        self.git(self.source, 'commit', '-qm', 'old scope')
        self.old = self.git(self.source, 'rev-parse', 'HEAD').strip()
        self.install = self.root / 'install'
        self.git(self.root, 'clone', '-q', '--no-checkout', str(self.source), str(self.install))
        self.git(self.install, 'sparse-checkout', 'init', '--cone')
        self.git(self.install, 'sparse-checkout', 'set', 'bootstrap', 'contracts', 'workflow', 'gate', 'policies')
        self.git(self.install, 'checkout', '-q', 'develop')
        self.run_tool('product_state.py', '--product-root', str(self.install), 'write',
                      '--mode', 'installed', '--repository', str(self.source),
                      '--branch', 'develop', '--current-ref', self.old,
                      '--source-pool', str(self.root / 'pool'))
        self.run_tool('station_registry.py', '--product-root', str(self.install), 'initialize')
        skill(self.source / 'skills/shared', 'shared-test')
        skill(self.source / 'skills', 'private-maintenance')
        self.git(self.source, 'add', '.')
        self.git(self.source, 'commit', '-qm', 'shared scope')
        self.new = self.git(self.source, 'rev-parse', 'HEAD').strip()

    def call(self, args, cwd=None):
        result = subprocess.run(args, cwd=cwd, env=self.env, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        return result.stdout

    def git(self, root, *args):
        return self.call(['git', '-C', str(root), *args])

    def run_tool(self, name, *args):
        return self.call([sys.executable, str(self.install / 'bootstrap' / name), *args])

    def lifecycle(self, name):
        env = self.env.copy()
        env['AGENTIC_OPS_HOME'] = str(self.install)
        result = subprocess.run(['bash', str(self.install / 'bootstrap' / name)],
                                env=env, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def state(self):
        return json.loads((self.install / '.local/product.json').read_text())

    def test_update_repeat_and_rollback_preserve_scope_and_pointer(self):
        self.lifecycle('update.sh')
        self.assertTrue((self.install / 'skills/shared/shared-test/SKILL.md').is_file())
        self.assertFalse((self.install / 'skills/private-maintenance').exists())
        self.assertEqual(self.state()['previous_ref'], self.old)
        self.lifecycle('update.sh')
        self.assertEqual(self.state()['previous_ref'], self.old)
        self.lifecycle('rollback.sh')
        self.assertEqual(self.git(self.install, 'rev-parse', 'HEAD').strip(), self.old)
        self.assertFalse((self.install / 'skills/shared').exists())
        self.lifecycle('rollback.sh')
        self.assertTrue((self.install / 'skills/shared/shared-test/SKILL.md').is_file())
        for path in ('.codex/skills', '.agents/skills', '.claude/skills'):
            self.assertFalse((self.home / path).exists())

    def test_old_updater_scope_can_be_recovered_without_losing_rollback(self):
        # An old updater advances HEAD/state while retaining the previous sparse set.
        self.git(self.install, 'pull', '--ff-only')
        self.run_tool('product_state.py', '--product-root', str(self.install), 'update-ref',
                      '--current-ref', self.new, '--previous-ref', self.old)
        self.assertFalse((self.install / 'skills/shared').exists())
        with self.assertRaisesRegex(ValueError, 'update'):
            shared_skill_sources(self.install)
        self.lifecycle('update.sh')
        self.assertEqual(self.state()['previous_ref'], self.old)
        self.assertTrue((self.install / 'skills/shared/shared-test/SKILL.md').is_file())


if __name__ == '__main__':
    unittest.main()
