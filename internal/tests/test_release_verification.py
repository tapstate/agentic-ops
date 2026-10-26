"""真实 Git/测试子进程覆盖发布证据与合并收尾；只替换外部 uv/GitHub 边界。"""
from __future__ import annotations
import json
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

from internal.release.verification import Verification, CHECKS, epoch, merged, premerge

ROOT = Path(__file__).resolve().parents[2]
COMMON = ROOT / 'internal/release/lib/release-common.sh'


class ReleaseVerificationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='ao-release-test-')
        self.addCleanup(self.temp.cleanup)
        self.area = Path(self.temp.name)
        self.root = self.area / 'repo'
        self.root.mkdir()
        self.env = {k: v for k, v in os.environ.items() if not k.startswith('GIT_')}
        self.env.pop('AGENTIC_OPS_RELEASE_WORKFLOW_TEST_RUNNING', None)
        # 嵌套发布验收必须使用夹具自己的根与操作锁，不能继承外层快照定位。
        self.env.pop('AGENTIC_OPS_RELEASE_SNAPSHOT', None)
        self.env.pop('AO_CONNECTOR_MAVEN_TEST', None)
        self.env.pop('AO_JAR_MAVEN_TEST', None)
        self.git('init', '-q', '-b', 'main')
        self.git('config', 'user.email', 'fixture@example.invalid')
        self.git('config', 'user.name', 'Release Fixture')
        self.write('.gitignore', '.local/\n__pycache__/\n')
        self.write('contracts/station-state-compatibility.json', '{"station_state_epoch":31}')
        self.write('internal/pyproject.toml', '# fixture')
        self.write('internal/uv.lock', '# fixture')
        for name, script in CHECKS.items():
            self.write(script, f'#!/bin/sh\necho {name}\necho {name} >> "$RELEASE_TEST_COUNTER"\n[ "${{RELEASE_TEST_FAIL:-}}" != {name} ]\n')
        self.git('add', '.')
        self.git('commit', '-qm', 'baseline')
        self.base = self.git('rev-parse', 'HEAD')
        self.git('checkout', '-qb', 'develop')
        self.write('product.txt', 'candidate\n')
        self.git('add', '.')
        self.git('commit', '-qm', 'candidate')
        self.head = self.git('rev-parse', 'HEAD')
        self.git('update-ref', 'refs/remotes/origin/main', self.base)
        self.uv = self.area / 'uv'
        self.uv.write_text('#!' + sys.executable + '\n' + '''import os,pathlib,sys
if sys.argv[1:] == ['--version']: print('uv fixture 1')
elif sys.argv[1:3] == ['python','find']: print(sys.executable)
elif sys.argv[1] == 'run':
 import subprocess
 sys.exit(subprocess.call([sys.executable]+sys.argv[sys.argv.index('python')+1:]))
elif sys.argv[1] == 'sync':
 p=pathlib.Path(os.environ['UV_PROJECT_ENVIRONMENT'])/'bin/python'
 p.parent.mkdir(parents=True,exist_ok=True)
 import shlex
 p.write_text('#!/bin/sh\\nexec '+shlex.quote(sys.executable)+' "$@"\\n');p.chmod(0o700)
 print('locked fixture environment prepared')
else: sys.exit(2)
''')
        self.uv.chmod(0o700)
        self.counter = self.area / 'count'
        self.env.update(AGENTIC_OPS_UV=str(self.uv), RELEASE_TEST_COUNTER=str(self.counter),
                        PYTHONPATH=str(ROOT), PYTHONDONTWRITEBYTECODE='1')
        self.environment = patch.dict(os.environ, self.env, clear=True)
        self.environment.start()
        self.addCleanup(self.environment.stop)

    def write(self, path, value):
        p = self.root / path
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(value)

    def git(self, *args, root=None):
        return subprocess.check_output(['git', '-C', str(root or self.root), *args],
                                       env=self.env, stderr=subprocess.DEVNULL, text=True).strip()

    def verifier(self, head=None, identity='v1.4'):
        return Verification(self.root, self.base, head or self.head, identity)

    def count(self):
        return len(self.counter.read_text().splitlines()) if self.counter.exists() else 0

    def shell(self, body, check=True):
        result = subprocess.run(['bash', '-c', 'set -euo pipefail\nsource ' + shlex.quote(str(COMMON.parent / 'development-workflow.sh')) + '\nsource ' + shlex.quote(str(COMMON)) + '\n' + body],
                                cwd=self.root, env=self.env, capture_output=True, text=True)
        if check and result.returncode:
            self.fail(result.stdout + result.stderr)
        return result

    def merge(self, first=None, second=None, tree=None):
        value = self.git('commit-tree', tree or self.git('rev-parse', self.head + '^{tree}'),
                         '-p', first or self.base, '-p', second or self.head, '-m', 'merge')
        self.git('update-ref', 'refs/remotes/origin/main', value)
        return value

    def remote(self, main, develop=None):
        bare = self.area / 'remote.git'
        subprocess.run(['git', 'init', '-q', '--bare', str(bare)], check=True, env=self.env)
        self.git('remote', 'add', 'origin', str(bare))
        self.git('push', '-q', 'origin', main + ':refs/heads/main', (develop or self.head) + ':refs/heads/develop')
        return bare

    def fake_gh(self, commit):
        gh = self.area / 'gh'
        body = f'<!-- agentic-ops-fixed-head:{self.head} -->\n<!-- agentic-ops-release:v1.4:{self.base} -->'
        gh.write_text('#!' + sys.executable + '\n' +
                      'import sys\na=sys.argv\n' +
                      f"body={body!r}\n" +
                      "if a[1]=='api': print('main' if a[-1]=='.default_branch' else 'true')\n" +
                      "elif '--json' in a and a[a.index('--json')+1]=='body': print(body)\n" +
                      f"elif a[a.index('--json')+1]=='baseRefName,headRefOid': print('main\\t{self.head}')\n" +
                      f"else: print('38\\thttps://github.com/tapstate/agentic-ops/pull/38\\tMERGED\\tmain\\t{self.head}\\t{commit}')\n")
        gh.chmod(0o700)
        self.env['AGENTIC_OPS_GH_BIN'] = str(gh)
        os.environ['AGENTIC_OPS_GH_BIN'] = str(gh)

    def test_one_independent_run_reused_by_every_consumer(self):
        v = self.verifier()
        first = v.execute('ensure')
        self.assertEqual(4, self.count())
        for mode in ('ensure', 'check', 'check'):
            value = v.execute(mode)
            self.assertTrue(value['reused'])
            self.assertEqual(first['run_id'], value['run_id'])
        self.assertEqual(4, self.count())
        self.assertEqual('release-verification/v1', first['schema_version'])

    def test_check_missing_never_runs_tests(self):
        with self.assertRaisesRegex(ValueError, '缺少匹配'):
            self.verifier().execute('check')
        self.assertEqual(0, self.count())

    def test_explicit_failure_revokes_previous_pass(self):
        v = self.verifier()
        v.execute('ensure')
        with patch.dict(os.environ, RELEASE_TEST_FAIL='python_runtime'):
            with self.assertRaisesRegex(ValueError, '验收失败'):
                v.execute('verify')
        with self.assertRaises(ValueError):
            v.execute('check')
        self.assertEqual('failed', json.loads(v.path.read_text())['status'])
        self.assertEqual(5, self.count())

    def test_invalid_explicit_reverify_also_revokes_old_pass(self):
        v = self.verifier()
        v.execute('ensure')
        with patch.dict(os.environ, AGENTIC_OPS_RELEASE_WORKFLOW_TEST_RUNNING='1'):
            with self.assertRaises(ValueError):
                v.execute('verify')
        with self.assertRaises(ValueError):
            v.execute('check')

    def test_corrupt_partial_development_and_changed_environment_rejected(self):
        v = self.verifier()
        record = v.execute('ensure')
        for change in ({'purpose': 'development'}, {'schema_version': 6}, {'checks': record['checks'][:3]},
                       {'status': 'cancelled'}, {'run_id': None}, {'binding': dict(record['binding'], verifier='different')}):
            v.path.write_text(json.dumps(dict(record, **change)))
            with self.assertRaises(ValueError):
                v.execute('check')
        v.path.write_text(json.dumps(record))
        self.uv.write_text(self.uv.read_text().replace('uv fixture 1', 'uv fixture 2'))
        with self.assertRaises(ValueError):
            v.execute('check')
        self.assertEqual(4, self.count())

    def test_candidate_and_identity_do_not_share_records(self):
        self.verifier().execute('ensure')
        self.write('product.txt', 'new\n')
        self.git('add', '.')
        self.git('commit', '-qm', 'new')
        for v in (self.verifier(self.git('rev-parse', 'HEAD')), self.verifier(identity='AO-262')):
            with self.assertRaises(ValueError):
                v.execute('check')
        self.assertEqual(4, self.count())

    def test_maven_and_nested_skip_flags_cannot_pass(self):
        for name in ('AO_CONNECTOR_MAVEN_TEST', 'AO_JAR_MAVEN_TEST', 'AGENTIC_OPS_RELEASE_WORKFLOW_TEST_RUNNING'):
            with patch.dict(os.environ, {name: '1'}):
                with self.assertRaises(ValueError):
                    self.verifier().execute('ensure')
        self.assertEqual(0, self.count())

    def test_dirty_user_files_do_not_enter_fixed_candidate(self):
        self.write('product.txt', 'user work\n')
        self.write('untracked.txt', 'keep\n')
        self.verifier().execute('ensure')
        self.assertEqual('user work\n', (self.root / 'product.txt').read_text())
        self.assertTrue((self.root / 'untracked.txt').exists())

    def test_symlink_record_path_rejected(self):
        (self.root / '.local').symlink_to(self.area, target_is_directory=True)
        with self.assertRaises(ValueError):
            self.verifier()

    def test_merge_requires_exact_parents_and_tree(self):
        commit = self.merge()
        merged(self.root, self.base, self.head, commit)
        wrong = self.merge(first=self.head, second=self.base)
        with self.assertRaises(ValueError):
            merged(self.root, self.base, self.head, wrong)
        wrong = self.merge(tree=self.git('rev-parse', self.base + '^{tree}'))
        with self.assertRaises(ValueError):
            merged(self.root, self.base, self.head, wrong)
        with self.assertRaises(ValueError):
            merged(self.root, self.base, self.head, self.head)

    def test_conflict_free_advanced_main_does_not_reuse_candidate(self):
        self.git('checkout', '-q', 'main')
        self.write('parallel.txt', 'parallel')
        self.git('add', '.')
        self.git('commit', '-qm', 'parallel')
        advanced = self.git('rev-parse', 'HEAD')
        tree = self.git('merge-tree', '--write-tree', advanced, self.head)
        commit = self.merge(first=advanced, tree=tree)
        with self.assertRaises(ValueError):
            merged(self.root, self.base, self.head, commit)
        with self.assertRaises(ValueError):
            premerge(self.root, advanced, self.head)

    def test_epoch_uses_both_git_objects_not_dirty_worktree(self):
        self.write('contracts/station-state-compatibility.json', '{"station_state_epoch":32}')
        self.assertEqual(31, epoch(self.root, self.head))
        self.git('add', '.')
        self.git('commit', '-qm', 'epoch')
        self.assertEqual(32, epoch(self.root, 'HEAD'))

    def test_tag_idempotency_and_conflict_are_real_remote_facts(self):
        commit = self.merge()
        bare = self.remote(commit)
        call = f'release_create_and_push_version_tag "$PWD" v1.4 {commit}'
        self.shell(call)
        ref = self.git('rev-parse', 'refs/tags/v1.4', root=bare)
        self.shell(call)
        self.assertEqual(ref, self.git('rev-parse', 'refs/tags/v1.4', root=bare))
        self.assertEqual(commit, self.git('rev-parse', 'v1.4^{}', root=bare))
        self.assertNotEqual(0, self.shell(f'release_create_and_push_version_tag "$PWD" v1.4 {self.head}', check=False).returncode)

    def test_develop_parallel_commit_preserved_and_reported(self):
        commit = self.merge()
        self.write('parallel.txt', 'new development')
        self.git('add', '.')
        self.git('commit', '-qm', 'parallel develop')
        parallel = self.git('rev-parse', 'HEAD')
        bare = self.remote(commit, parallel)
        result = self.shell(f'release_sync_develop_to_main "$PWD" {commit}\necho "$RELEASE_DEVELOP_SYNC"')
        self.assertEqual('pending_integration', result.stdout.strip())
        self.assertEqual(parallel, self.git('rev-parse', 'develop', root=bare))

    def test_sync_preserves_dirty_worktree_and_does_not_follow_new_main(self):
        commit = self.merge()
        bare = self.remote(commit)
        self.write('product.txt', 'user work')
        result = self.shell(f'release_sync_develop_to_main "$PWD" {commit}\necho "$RELEASE_DEVELOP_SYNC"')
        self.assertEqual('local_pending', result.stdout.strip())
        self.assertEqual(commit, self.git('rev-parse', 'develop', root=bare))
        self.assertEqual('user work', (self.root / 'product.txt').read_text())

    def test_recovery_plan_and_execute_reuse_evidence_without_source_branch(self):
        self.verifier().execute('ensure')
        commit = self.merge()
        bare = self.remote(commit)
        self.fake_gh(commit)
        setup = 'RELEASE_IDENTITY=v1.4\n'
        plan = self.shell(setup + 'release_recover_merged_candidate "$PWD" v1.4 38 false "" soft', check=False)
        self.assertNotEqual(0, plan.returncode)
        lines = [json.loads(x) for x in plan.stderr.splitlines() if x.startswith('{')]
        self.assertEqual('release_recovery_confirmation_required', lines[-1]['code'])
        digest = lines[-1]['required_human_action'].split()[-1]
        self.assertEqual(4, self.count())
        self.git('checkout', '-q', '--detach', self.head)
        self.git('branch', '-D', 'develop')
        result = self.shell(setup + f'release_recover_merged_candidate "$PWD" v1.4 38 true {digest} soft')
        data = json.loads(result.stdout.splitlines()[-1])
        self.assertEqual('published', data['delivery_status'])
        self.assertEqual('step-result/v2', data['schema_version'])
        self.assertIn('next_step', data)
        self.assertEqual('local_pending', data['develop_sync_status'])
        self.assertEqual(commit, self.git('rev-parse', 'v1.4^{}', root=bare))
        self.assertEqual(4, self.count())


    def hotfix_fixture(self, trust=True, cross_epoch=False):
        """基线运行真实 Story Gate，测试命令是提交在夹具中的独立小脚本。"""
        from internal.tests.test_story_gate import STORY_BODY, POLICY
        from internal.story_gate.service import StoryGateService
        self.git('checkout', '-q', 'main')
        for directory in ('internal/story_gate', 'internal/bin', 'internal/release'):
            shutil.copytree(ROOT / directory, self.root / directory, dirs_exist_ok=True,
                            ignore=shutil.ignore_patterns('__pycache__'))
        shutil.copyfile(ROOT / 'internal/test_selection.py', self.root / 'internal/test_selection.py')
        self.write('.agentic-ops-source', 'source\n')
        self.write('AGENTS.md', '# Fixture\n')
        self.write('docs/strategy/project-goals.md', '# Fixture\n')
        self.write('docs/user-stories/v1/int-001.md', STORY_BODY.format(story_id='INT-001'))
        registry = '''schema_version: 1
story_categories: [internal, product]
stories:
  - story_id: INT-001
    category: internal
    title: 发布夹具
    document: docs/user-stories/v1/int-001.md
    protected_paths: ['**']
    acceptance_checks: [python_runtime, resource_contracts, product_install_boundary, release_workflow]
    evidence_requirements: [发布测试]
'''
        self.write('internal/story_gate/stories.yaml', registry)
        self.write('internal/story_gate/review-policy.yaml', POLICY)
        for script in CHECKS.values():
            (self.root / script).chmod(0o755)
        self.git('add', '.')
        self.git('commit', '-qm', 'real gate baseline')
        self.base = self.git('rev-parse', 'HEAD')
        self.git('checkout', '-B', 'develop', self.base)
        self.write('internal/story_gate/stories.yaml' if trust else 'product.txt', registry + '\n# changed\n' if trust else 'changed')
        if cross_epoch:
            self.write('contracts/station-state-compatibility.json', '{"station_state_epoch":32}')
        self.git('add', '.')
        self.git('commit', '-qm', 'hotfix candidate')
        self.head = self.git('rev-parse', 'HEAD')
        self.git('update-ref', 'refs/remotes/origin/main', self.base)
        service = StoryGateService(self.root)
        result = service.verify('range', base=self.base, head=self.head, scope='full')
        service.approve('range', result['impact_id'], 'user-confirmation:AO-262:commit:' + self.head,
                        base=self.base, head=self.head)
        self.counter.write_text('')  # 独立统计发布执行，不把真实开发验收算作发布验收。
        bare = self.remote(self.base)
        gh = self.area / 'hotfix-gh'
        state = self.area / 'pr.json'
        body = self.area / 'pr-body'
        gh.write_text('#!' + sys.executable + '\n' + f'''import json,pathlib,sys
state=pathlib.Path({str(state)!r});body=pathlib.Path({str(body)!r});a=sys.argv[1:]
if a[:2]==['pr','list']:
 if state.exists():
  d=json.loads(state.read_text());print('38\\thttps://github.com/tapstate/agentic-ops/pull/38\\t'+d['state']+'\\t'+d['head']+'\\t'+d.get('merge','-'))
elif a[:2]==['pr','create']:
 body.write_text(pathlib.Path(a[a.index('--body-file')+1]).read_text())
 state.write_text(json.dumps(dict(state='OPEN',head={self.head!r})))
 print('https://github.com/tapstate/agentic-ops/pull/38')
elif a[:2]==['pr','view']:
 if a[a.index('--json')+1]=='body': print(body.read_text())
 elif a[a.index('--json')+1]=='baseRefName,headRefOid': print('main\\t{self.head}')
 else: print('38')
else: sys.exit(2)
''')
        gh.chmod(0o700)
        self.env['AGENTIC_OPS_GH_BIN'] = str(gh)
        os.environ['AGENTIC_OPS_GH_BIN'] = str(gh)
        return bare, state

    def hotfix_call(self):
        return self.shell('source ' + shlex.quote(str(ROOT / 'internal/release/hotfix.sh')) +
                          '\nrelease_hotfix_candidate "$PWD" AO-262', check=False)

    def test_same_epoch_trust_root_hotfix_waits_for_pr_then_finishes_without_tag(self):
        bare, state = self.hotfix_fixture()
        first = self.hotfix_call()
        self.assertEqual(2, first.returncode, first.stderr)
        self.assertEqual('waiting_for_manual_merge', json.loads(first.stdout.splitlines()[-1])['status'])
        self.assertEqual(self.base, self.git('rev-parse', 'main', root=bare))
        self.assertEqual(4, self.count())
        commit = self.merge()
        self.git('push', '-q', 'origin', commit + ':refs/heads/main')
        state.write_text(json.dumps(dict(state='MERGED', head=self.head, merge=commit)))
        self.git('push', '-q', 'origin', ':refs/heads/codex/AO-262/fix-main')
        pr_gh = self.env['AGENTIC_OPS_GH_BIN']
        self.governance_gh()
        governance = self.env['AGENTIC_OPS_GH_BIN']
        router = self.area / 'hotfix-router-gh'
        router.write_text('#!' + sys.executable + '\n' + f'''import os,sys
args=sys.argv[1:]
path={governance!r} if args[0]=='api' or ('--json' in args and args[args.index('--json')+1]=='author') else {pr_gh!r}
os.execv(path,[path]+args)
''')
        router.chmod(0o700)
        self.env['AGENTIC_OPS_GH_BIN'] = str(router)
        good_rules, good_reviews = self.ruleset.read_text(), self.reviews.read_text()
        rules = json.loads(good_rules)
        rules['enforcement'] = 'disabled'
        self.ruleset.write_text(json.dumps(rules))
        self.assertIn('release_main_protection_required', self.hotfix_call().stderr)
        self.ruleset.write_text(good_rules)
        self.reviews.write_text('[[]]')
        self.assertIn('release_independent_approval_required', self.hotfix_call().stderr)
        self.assertNotEqual(commit, self.git('rev-parse', 'develop', root=bare))
        self.reviews.write_text(good_reviews)
        second = self.hotfix_call()
        self.assertEqual(0, second.returncode, second.stderr)
        data = json.loads(second.stdout.splitlines()[-1])
        self.assertEqual('published', data['delivery_status'])
        self.assertEqual('step-result/v2', data['schema_version'])
        self.assertIn('next_step', data)
        self.assertEqual('', data['tag'])
        self.assertEqual('', self.git('tag', root=bare))
        self.assertEqual(commit, self.git('rev-parse', 'develop', root=bare))
        self.assertEqual(4, self.count())

    def test_cross_epoch_hotfix_preserves_independent_review(self):
        bare, _ = self.hotfix_fixture(cross_epoch=True)
        result = self.hotfix_call()
        self.assertNotEqual(0, result.returncode)
        self.assertIn('waiting_for_manual_merge', result.stdout)
        self.assertEqual(4, self.count())
        self.assertEqual(self.base, self.git('rev-parse', 'main', root=bare))

    def test_same_epoch_ordinary_hotfix_atomically_publishes_without_pr(self):
        bare, state = self.hotfix_fixture(trust=False)
        result = self.hotfix_call()
        self.assertEqual(0, result.returncode, result.stderr)
        data = json.loads(result.stdout.splitlines()[-1])
        self.assertEqual('published', data['delivery_status'])
        self.assertEqual('step-result/v2', data['schema_version'])
        self.assertIn('next_step', data)
        self.assertFalse(state.exists())
        self.assertEqual(data['merge_commit'], data['main_commit'])
        self.assertFalse(data['branch_created'])
        self.assertFalse(data['jira_interaction'])
        self.assertTrue(data['changed'])
        self.assertEqual(4, self.count())
        self.assertEqual(self.git('rev-parse', 'main', root=bare), self.git('rev-parse', 'develop', root=bare))
        self.assertEqual('', self.git('tag', root=bare))

    def test_conflicting_pr_marker_cannot_fall_back_to_legacy_version(self):
        commit = self.merge()
        self.fake_gh(commit)
        gh = Path(self.env['AGENTIC_OPS_GH_BIN'])
        gh.write_text(gh.read_text().replace(self.base, 'f' * 40).replace("body=", "body='- 版本基线：`v1.4`\\n'+"))
        result = self.shell(f'RELEASE_BASE_COMMIT={self.base}\nrelease_validate_pr_binding url v1.4 {self.head}', check=False)
        self.assertNotEqual(0, result.returncode)
        self.assertIn('release_pr_binding_invalid', result.stderr)

    def test_tag_push_lost_response_is_read_back_without_rewriting(self):
        commit = self.merge()
        bare = self.remote(commit)
        result = self.shell('git() { if [[ " $* " == *" push "* ]]; then command git "$@"; return 1; else command git "$@"; fi; }\n'
                            + f'release_create_and_push_version_tag "$PWD" v1.4 {commit}')
        self.assertEqual(0, result.returncode)
        self.assertEqual(commit, self.git('rev-parse', 'v1.4^{}', root=bare))

    def test_inspect_reports_delivery_separately_without_running_tests(self):
        self.verifier().execute('ensure')
        commit = self.merge()
        self.remote(commit)
        self.fake_gh(commit)
        self.shell(f'release_create_and_push_version_tag "$PWD" v1.4 {commit}')
        result = self.shell('release_inspect_state "$PWD" v1.4 38')
        data = json.loads(result.stdout)
        self.assertEqual('published', data['delivery_status'])
        self.assertEqual('pending_fast_forward', data['develop_sync_status'])
        self.assertEqual(4, self.count())

    def test_cancelled_explicit_run_revokes_pass_and_reaps_children(self):
        script = next(iter(CHECKS.values()))
        self.write(script, (self.root / script).read_text() + '\nif [ -n "${RELEASE_TEST_WAIT:-}" ]; then touch "$RELEASE_TEST_WAIT"; sleep 30; fi\n')
        self.git('add', '.')
        self.git('commit', '-qm', 'cancellable candidate')
        self.head = self.git('rev-parse', 'HEAD')
        v = self.verifier()
        v.execute('ensure')
        marker = self.area / 'started'
        env = dict(self.env, RELEASE_TEST_WAIT=str(marker))
        process = subprocess.Popen([sys.executable, '-m', 'internal.release.verification', 'verify',
                                    '--root', str(self.root), '--base', self.base, '--head', self.head,
                                    '--identity', 'v1.4'], env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        try:
            deadline = time.monotonic() + 10
            while not marker.exists() and process.poll() is None and time.monotonic() < deadline:
                time.sleep(0.05)
            self.assertTrue(marker.exists())
            process.terminate()
            process.communicate(timeout=10)
            self.assertNotEqual(0, process.returncode)
            self.assertEqual('failed', json.loads(v.path.read_text())['status'])
            with self.assertRaises(ValueError):
                v.execute('check')
        finally:
            if process.poll() is None:
                process.kill()
                process.communicate()


    def test_evidence_reuses_in_clean_clone_without_original_logs(self):
        v = self.verifier()
        record = v.execute('ensure')
        bare = self.remote(self.base)
        clone = self.area / 'clean-clone'
        subprocess.run(['git', 'clone', '-q', '--no-checkout', str(bare), str(clone)], env=self.env, check=True)
        self.git('checkout', '-q', '--detach', self.head, root=clone)
        other = Verification(clone, self.base, self.head, 'v1.4')
        other.path.parent.mkdir(parents=True)
        shutil.copyfile(v.path, other.path)
        self.assertEqual(record['run_id'], other.execute('check')['run_id'])
        self.assertEqual(4, self.count())

    def test_audit_failure_after_tag_retries_only_missing_finish(self):
        self.verifier().execute('ensure')
        commit = self.merge()
        bare = self.remote(commit)
        audit = self.root / '.local/release' / f'release-v1.4-{self.head}.json'
        outside = self.area / 'keep'
        outside.write_text('user data')
        audit.symlink_to(outside)
        setup = f'RELEASE_IDENTITY=v1.4\nRELEASE_BASE_COMMIT={self.base}\nrelease_run_full_verification "$PWD" {self.head} check\n'
        call = setup + f'release_finish "$PWD" v1.4 {self.head} {commit} soft release_publish'
        result = self.shell(call, check=False)
        self.assertNotEqual(0, result.returncode)
        self.assertEqual(commit, self.git('rev-parse', 'v1.4^{}', root=bare))
        tag = self.git('rev-parse', 'v1.4', root=bare)
        audit.unlink()
        result = self.shell(call)
        self.assertEqual('published', json.loads(result.stdout.splitlines()[-1])['delivery_status'])
        self.assertEqual(tag, self.git('rev-parse', 'v1.4', root=bare))
        self.assertEqual('user data', outside.read_text())
        self.assertEqual(4, self.count())


    def test_actual_pr_target_and_head_override_matching_body_markers(self):
        commit = self.merge()
        self.fake_gh(commit)
        gh = Path(self.env['AGENTIC_OPS_GH_BIN'])
        original = gh.read_text()
        for target, head in [('develop', self.head), ('main', self.base)]:
            gh.write_text(original.replace(f"print('main\\t{self.head}')", f"print('{target}\\t{head}')"))
            result = self.shell(f'RELEASE_BASE_COMMIT={self.base}\nrelease_validate_pr_binding url v1.4 {self.head}', check=False)
            self.assertNotEqual(0, result.returncode)
            self.assertIn('release_pr_binding_invalid', result.stderr)

    def test_formal_trust_review_keeps_jira_and_reuses_release_evidence(self):
        self.hotfix_fixture()
        automatic = self.shell(f'release_verify_story_gate "$PWD" origin/main {self.head} false ""', check=False)
        self.assertIn('release_story_gate_trust_root_changed', automatic.stderr)
        self.assertEqual(0, self.count())
        self.verifier().execute('ensure')
        result = self.shell(f'RELEASE_IDENTITY=v1.4\nRELEASE_JIRA_ID=AO-262\nRELEASE_BASE_COMMIT={self.base}\n'
                            + f'release_submit_review "$PWD" tapstate/agentic-ops v1.4 release/v1.4 {self.head} hard true')
        self.assertEqual(0, result.returncode)
        body = (self.area / 'pr-body').read_text()
        self.assertIn('- Jira 任务：`AO-262`', body)
        self.assertIn(f'<!-- agentic-ops-release:v1.4:{self.base} -->', body)
        self.assertEqual(4, self.count())


    def test_ordinary_candidate_can_choose_manual_review_without_auto_merge(self):
        bare, state = self.hotfix_fixture(trust=False)
        self.verifier().execute('ensure')
        self.shell(f'RELEASE_IDENTITY=v1.4\nRELEASE_BASE_COMMIT={self.base}\n'
                   + f'release_submit_review "$PWD" tapstate/agentic-ops v1.4 release/v1.4 {self.head} hard true')
        self.assertEqual('OPEN', json.loads(state.read_text())['state'])
        self.assertEqual(self.base, self.git('rev-parse', 'main', root=bare))
        self.assertEqual(4, self.count())

    def test_local_candidate_replacement_retains_old_commit_and_refuses_remote_review(self):
        bare, state = self.hotfix_fixture()
        self.git('branch', 'release/v1.4', self.base)
        gh = self.area / 'candidate-gh'
        gh.write_text('#!/bin/sh\nprintf "%s\\n" "${TEST_PR_COUNT:-0}"\n')
        gh.chmod(0o700)
        self.env['AGENTIC_OPS_GH_BIN'] = str(gh)
        command = f'RELEASE_REPLACE_CANDIDATE={self.base}\nrelease_prepare_fixed_branch "$PWD" release/v1.4 {self.head}'
        missing = self.shell(f'release_prepare_fixed_branch "$PWD" release/v1.4 {self.head}', check=False)
        self.assertIn('release_fixed_branch_conflict', missing.stderr)
        self.env['TEST_PR_COUNT'] = '1'
        refused = self.shell(command, check=False)
        self.assertIn('release_candidate_review_exists', refused.stderr)
        self.assertEqual(self.base, self.git('rev-parse', 'release/v1.4'))
        self.env['TEST_PR_COUNT'] = '0'
        self.git('push', '-q', 'origin', 'release/v1.4')
        refused = self.shell(command, check=False)
        self.assertIn('release_fixed_branch_conflict', refused.stderr)
        self.git('push', '-q', 'origin', ':release/v1.4')
        worktree = self.area / 'candidate-worktree'
        self.git('worktree', 'add', '-q', str(worktree), 'release/v1.4')
        refused = self.shell(command, check=False)
        self.assertIn('release_candidate_checked_out', refused.stderr)
        self.git('worktree', 'remove', str(worktree))
        self.shell(command)
        self.assertEqual(self.head, self.git('rev-parse', 'release/v1.4'))
        self.assertEqual(self.base, self.git('rev-parse', 'codex/superseded-release-v1.4-' + self.base))
        self.assertEqual(0, self.count())

    def test_candidate_replacement_rejects_change_during_remote_check(self):
        self.remote(self.base)
        self.git('branch', 'release/v1.4', self.base)
        intervening = self.git('commit-tree', self.head + '^{tree}', '-p', self.head, '-m', 'other session')
        gh = self.area / 'concurrent-gh'
        gh.write_text('#!/bin/sh\ngit -C ' + shlex.quote(str(self.root)) +
                      ' update-ref refs/heads/release/v1.4 ' + intervening + '\nprintf "0\\n"\n')
        gh.chmod(0o700)
        self.env['AGENTIC_OPS_GH_BIN'] = str(gh)
        result = self.shell(f'RELEASE_REPLACE_CANDIDATE={self.base}\n'
                            f'release_prepare_fixed_branch "$PWD" release/v1.4 {self.head}', check=False)
        self.assertNotEqual(0, result.returncode)
        self.assertEqual(intervening, self.git('rev-parse', 'release/v1.4'))
        self.assertEqual('', self.git('for-each-ref', '--format=%(refname)', 'refs/heads/codex/superseded-'))

    def test_prepare_and_hard_review_do_not_query_or_configure_github_settings(self):
        bare, state = self.hotfix_fixture(cross_epoch=False)
        shutil.rmtree(self.root / '.local/story-gate')
        unrelated = self.area / 'unrelated-history'
        unrelated.mkdir()
        (self.root / '.local/story-gate').symlink_to(unrelated, target_is_directory=True)
        # 正式版本不消费开发台账，甚至不应沿无关链接读取或写入。
        # 本地准备没有范围验收或人工批准历史，也应执行自己的独立验收。
        # 隔离远端使用真实 bare 仓库；仅映射传输身份，不替换发布判定。
        bin_dir = self.area / 'bin'
        bin_dir.mkdir()
        git_bin = shutil.which('git')
        wrapper = bin_dir / 'git'
        wrapper.write_text('#!' + sys.executable + '\n' + f'''import os,sys
args=sys.argv[1:]
if args[-3:]==['config','--get-all','remote.origin.url'] or ('remote' in args and 'get-url' in args):
 print('https://github.com/tapstate/agentic-ops.git')
else: os.execv({git_bin!r},[{git_bin!r}]+args)
''')
        wrapper.chmod(0o700)
        self.env['PATH'] = str(bin_dir) + os.pathsep + self.env['PATH']
        # 此边界只支持 PR 调用，任何治理 API/配置请求均失败。
        for stage in ('prepare', 'submit-for-review', 'publish', 'recover'):
            rejected = subprocess.run(['bash', 'internal/release/release.sh', stage, '--version', 'v1.4', '--configure-workflow'],
                                      cwd=self.root, env=self.env, capture_output=True, text=True)
            self.assertIn('release_configuration_separate', rejected.stderr)
            self.assertFalse(state.exists())
        self.git('branch', 'release/v1.4', self.base)
        conflict = subprocess.run(['bash', 'internal/release/release.sh', 'prepare', '--version', 'v1.4', '--jira', 'AO-262'],
                                  cwd=self.root, env=self.env, capture_output=True, text=True)
        self.assertIn('release_fixed_branch_conflict', conflict.stderr)
        self.assertEqual(0, self.count())
        self.git('branch', '-D', 'release/v1.4')
        result = subprocess.run(['bash', 'internal/release/release.sh', 'prepare', '--version', 'v1.4', '--jira', 'AO-262'],
                                cwd=self.root, env=self.env, capture_output=True, text=True)
        self.assertEqual(0, result.returncode, result.stdout + result.stderr)
        data = json.loads(result.stdout.splitlines()[-1])
        self.assertEqual('pending', data['delivery_checks'])
        self.assertEqual(self.head, self.git('rev-parse', 'release/v1.4'))
        self.assertEqual('', self.git('branch', '--list', 'release/v1.4', root=bare))
        self.assertEqual(4, self.count())
        inspected = subprocess.run(['bash', 'internal/release/release.sh', 'inspect', '--version', 'v1.4'],
                                   cwd=self.root, env=self.env, capture_output=True, text=True)
        self.assertEqual(0, inspected.returncode, inspected.stderr)
        self.assertIn('submit-for-review', json.loads(inspected.stdout)['next_command'])
        self.assertEqual(4, self.count())
        verified = subprocess.run(['bash', 'internal/release/release.sh', 'verify', '--version', 'v1.4'],
                                  cwd=self.root, env=self.env, capture_output=True, text=True)
        self.assertEqual(0, verified.returncode, verified.stdout + verified.stderr)
        self.assertEqual(8, self.count())
        blocked = subprocess.run(['bash', 'internal/release/release.sh', 'submit-for-review', '--version', 'v1.4'],
                                 cwd=self.root, env=self.env, capture_output=True, text=True)
        self.assertNotEqual(0, blocked.returncode)
        self.assertIn('release_confirmation_required', blocked.stderr)
        self.assertFalse(state.exists())
        self.assertEqual('', self.git('branch', '--list', 'release/v1.4', root=bare))
        record = self.verifier().path
        saved = record.read_text()
        record.unlink()
        missing = subprocess.run(['bash', 'internal/release/release.sh', 'submit-for-review', '--version', 'v1.4', '--confirm-release'],
                                 cwd=self.root, env=self.env, capture_output=True, text=True)
        self.assertIn('release_verification_required', missing.stderr)
        self.assertFalse(state.exists())
        self.assertEqual(8, self.count())
        record.write_text(saved)
        result = subprocess.run(['bash', 'internal/release/release.sh', 'submit-for-review', '--version', 'v1.4', '--jira', 'AO-262', '--confirm-release'],
                                cwd=self.root, env=self.env, capture_output=True, text=True)
        self.assertEqual(0, result.returncode, result.stdout + result.stderr)
        self.assertEqual('hard', json.loads(result.stdout.splitlines()[-1])['protection_mode'])
        self.assertEqual('OPEN', json.loads(state.read_text())['state'])
        self.assertEqual(self.base, self.git('rev-parse', 'main', root=bare))
        self.assertEqual('', self.git('tag', root=bare))
        self.assertEqual([], list(unrelated.iterdir()))
        self.assertEqual(8, self.count())

    def governance_gh(self):
        self.ruleset = self.area / 'ruleset.json'
        self.reviews = self.area / 'reviews.json'
        self.ruleset.write_text(json.dumps(dict(id=7, name='company-main', target='branch', enforcement='active',
            bypass_actors=[], conditions=dict(ref_name=dict(include=['refs/heads/main'], exclude=[])),
            rules=[dict(type='deletion'), dict(type='non_fast_forward'), dict(type='pull_request', parameters=dict(
                allowed_merge_methods=['merge'], required_approving_review_count=2, require_code_owner_review=True,
                dismiss_stale_reviews_on_push=True, require_last_push_approval=True, required_review_thread_resolution=True))])))
        self.reviews.write_text(json.dumps([[dict(id=1, user=dict(login='reviewer', type='User'), state='APPROVED', commit_id=self.head, submitted_at='2026-10-25')]]))
        gh = self.area / 'governance-gh'
        gh.write_text('#!' + sys.executable + '\n' + f'''import json,subprocess,sys,pathlib
args=sys.argv[1:]
if args[:2]==['pr','view']: data={{'author':{{'login':'author'}}}}
elif args[0]=='api':
 endpoint=args[1]
 if '/reviews?' in endpoint: data=json.loads(pathlib.Path({str(self.reviews)!r}).read_text())
 elif '/rulesets/' in endpoint: data=json.loads(pathlib.Path({str(self.ruleset)!r}).read_text())
 elif '/rulesets?' in endpoint: data=[json.loads(pathlib.Path({str(self.ruleset)!r}).read_text())]
 else: data={{'default_branch':'main','allow_merge_commit':True,'allow_auto_merge':False}}
else: sys.exit(90)
if any(x in args for x in ['PATCH','PUT','POST']): sys.exit(91)
sys.exit(subprocess.run(['jq','-r',args[args.index('--jq')+1]],input=json.dumps(data),text=True).returncode)
''')
        gh.chmod(0o700)
        self.env['AGENTIC_OPS_GH_BIN'] = str(gh)

    def test_delivery_accepts_stronger_renamed_ruleset_but_auto_merge_is_separate(self):
        self.governance_gh()
        self.shell('workflow_check_delivery tapstate/agentic-ops hard')
        result = self.shell('workflow_check_auto_merge tapstate/agentic-ops', check=False)
        self.assertIn('release_auto_merge_required', result.stderr)
        value = json.loads(self.ruleset.read_text())
        for field in ('enforcement', 'bypass_actors'):
            changed = dict(value, **{field: 'disabled' if field == 'enforcement' else [dict(actor_id=1)]})
            self.ruleset.write_text(json.dumps(changed))
            result = self.shell('workflow_check_delivery tapstate/agentic-ops hard', check=False)
            self.assertIn('release_main_protection_required', result.stderr)

    def test_independent_approval_requires_current_head_other_human_and_latest_decision(self):
        self.governance_gh()
        call = f'release_check_independent_approval 38 {self.head}'
        self.shell(call)
        review = json.loads(self.reviews.read_text())[0][0]
        for change in (dict(commit_id=self.base), dict(user=dict(login='author', type='User')),
                       dict(user=dict(login='bot', type='Bot')), dict(state='DISMISSED')):
            self.reviews.write_text(json.dumps([[dict(review, **change)]]))
            self.assertIn('release_independent_approval_required', self.shell(call, check=False).stderr)
        self.reviews.write_text(json.dumps([[review], [dict(review, id=2, user=dict(login='second', type='User'), state='CHANGES_REQUESTED')]]))
        self.assertIn('release_independent_approval_required', self.shell(call, check=False).stderr)
        self.reviews.write_text(json.dumps([[review], [dict(review, id=2, state='CHANGES_REQUESTED')]]))
        self.assertIn('release_independent_approval_required', self.shell(call, check=False).stderr)
        self.reviews.write_text(json.dumps([[review], [dict(review, id=2, state='CHANGES_REQUESTED'), dict(review, id=3), dict(review, id=4, state='COMMENTED')]]))
        self.shell(call)


    def test_hard_recovery_checks_protection_and_approval_before_tag_without_auto_merge(self):
        self.write('.githooks/pre-commit', '# trust-root candidate\n')
        self.git('add', '.')
        self.git('commit', '-qm', 'trust-root')
        self.head = self.git('rev-parse', 'HEAD')
        self.verifier().execute('ensure')
        commit = self.merge()
        bare = self.remote(commit)
        self.fake_gh(commit)
        pr_gh = self.env['AGENTIC_OPS_GH_BIN']
        self.governance_gh()
        governance = self.env['AGENTIC_OPS_GH_BIN']
        router = self.area / 'router-gh'
        router.write_text('#!' + sys.executable + '\n' + f'''import os,sys
args=sys.argv[1:]
path={governance!r} if args[0]=='api' or ('--json' in args and args[args.index('--json')+1]=='author') else {pr_gh!r}
os.execv(path,[path]+args)
''')
        router.chmod(0o700)
        self.env['AGENTIC_OPS_GH_BIN'] = str(router)
        setup = 'RELEASE_IDENTITY=v1.4\n'
        call = 'release_recover_merged_candidate "$PWD" v1.4 38 false "" hard'
        good_rules = self.ruleset.read_text()
        rules = json.loads(good_rules)
        rules['enforcement'] = 'disabled'
        self.ruleset.write_text(json.dumps(rules))
        self.assertIn('release_main_protection_required', self.shell(setup + call, check=False).stderr)
        self.assertEqual('', self.git('tag', root=bare))
        self.assertIn('release_main_protection_required', self.shell(setup + call.replace('hard', 'soft'), check=False).stderr)
        self.ruleset.write_text(good_rules)
        good_reviews = self.reviews.read_text()
        self.reviews.write_text('[[]]')
        self.assertIn('release_independent_approval_required', self.shell(setup + call, check=False).stderr)
        self.assertEqual('', self.git('tag', root=bare))
        self.reviews.write_text(good_reviews)
        plan = self.shell(setup + call, check=False)
        lines = [json.loads(x) for x in plan.stderr.splitlines() if x.startswith('{')]
        self.assertEqual('release_recovery_confirmation_required', lines[-1]['code'])
        command = lines[-1]['required_human_action']
        self.assertNotIn('--allow-soft-gate', command)
        digest = command.split()[-1]
        self.shell(setup + f'release_recover_merged_candidate "$PWD" v1.4 38 true {digest} hard')
        self.assertEqual(commit, self.git('rev-parse', 'v1.4^{}', root=bare))
        self.assertEqual(4, self.count())

    def test_snapshot_verifier_ignores_changed_original_worktree_modules(self):
        self.hotfix_fixture(trust=False)
        self.write('internal/__init__.py', '')
        self.git('add', '.')
        self.git('commit', '-qm', 'snapshot package')
        snapshot = self.area / 'implementation'
        self.git('worktree', 'add', '--detach', str(snapshot), 'HEAD')
        # 固定快照形成后，原目录中的同名包/模块变化不得参与解析。
        self.write('internal/release/verification.py', 'raise RuntimeError("mutable verifier loaded")\n')
        self.write('internal/story_gate/__init__.py', 'raise RuntimeError("mutable dependency loaded")\n')
        result = self.shell('source ' + shlex.quote(str(snapshot / 'internal/release/lib/release-common.sh'))
                            + f'\nrelease_verification epoch --root "$PWD" --base {self.base} --head {self.head}'
                            + '\nprintf "cwd=%s\\n" "$PWD"', check=False)
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertEqual(['same', 'cwd=' + str(self.root.resolve())], result.stdout.splitlines())
        self.assertIn('mutable verifier', (self.root / 'internal/release/verification.py').read_text())

    def test_direct_hotfix_retry_keeps_local_pending_until_real_sync(self):
        bare, _ = self.hotfix_fixture(trust=False)
        self.write('product.txt', 'user changes')
        first = self.hotfix_call()
        self.assertEqual(0, first.returncode, first.stderr)
        commit = self.git('rev-parse', 'main', root=bare)
        for result in (first, self.hotfix_call()):
            self.assertEqual(0, result.returncode, result.stderr)
            data = json.loads(result.stdout.splitlines()[-1])
            self.assertEqual('completed_with_pending_sync', data['status'])
            self.assertEqual('local_pending', data['develop_sync_status'])
            self.assertEqual(self.head, data['head'])
        self.assertEqual('user changes', (self.root / 'product.txt').read_text())
        self.assertEqual(self.head, self.git('rev-parse', 'develop'))
        self.write('product.txt', self.git('show', self.head + ':product.txt'))
        final = self.hotfix_call()
        self.assertEqual(0, final.returncode, final.stderr)
        self.assertEqual('synced', json.loads(final.stdout.splitlines()[-1])['develop_sync_status'])
        self.assertEqual(commit, self.git('rev-parse', 'develop'))
        self.assertEqual(commit, self.git('rev-parse', 'main', root=bare))
        self.assertEqual(4, self.count())

    def test_direct_hotfix_retry_repairs_missing_audit_without_new_merge(self):
        bare, _ = self.hotfix_fixture(trust=False)
        audit = self.root / '.local/release' / f'release-AO-262-{self.head}.json'
        audit.mkdir(parents=True)
        first = self.hotfix_call()
        self.assertNotEqual(0, first.returncode)
        self.assertIn('release_audit_path_unsafe', first.stderr)
        commit = self.git('rev-parse', 'main', root=bare)
        self.assertNotEqual(self.base, commit)
        audit.rmdir()
        second = self.hotfix_call()
        self.assertEqual(0, second.returncode, second.stderr)
        self.assertTrue(audit.is_file())
        data = json.loads(audit.read_text())
        self.assertEqual(self.head, data['head'])
        self.assertEqual(commit, data['merge_commit'])
        self.assertEqual(commit, self.git('rev-parse', 'main', root=bare))
        self.assertEqual(4, self.count())

    def test_direct_hotfix_retry_does_not_publish_later_develop_commits(self):
        bare, _ = self.hotfix_fixture(trust=False)
        first = self.hotfix_call()
        self.assertEqual(0, first.returncode, first.stderr)
        commit = self.git('rev-parse', 'main', root=bare)
        self.write('later.txt', 'not part of this hotfix')
        self.git('add', '.')
        self.git('commit', '-qm', 'later development')
        later = self.git('rev-parse', 'HEAD')
        self.git('push', '-q', 'origin', 'develop')
        second = self.hotfix_call()
        self.assertEqual(0, second.returncode, second.stderr)
        data = json.loads(second.stdout.splitlines()[-1])
        self.assertEqual(self.head, data['head'])
        self.assertEqual(commit, data['merge_commit'])
        self.assertEqual(commit, self.git('rev-parse', 'main', root=bare))
        self.assertEqual(later, self.git('rev-parse', 'develop', root=bare))
        self.assertEqual(4, self.count())


if __name__ == '__main__':
    unittest.main()
