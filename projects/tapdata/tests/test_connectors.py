import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import types
import unittest
from unittest.mock import patch
import zipfile

SCRIPTS = Path(__file__).resolve().parents[1] / 'scripts'
sys.path.insert(0, str(SCRIPTS))
spec = importlib.util.spec_from_file_location('connectors', SCRIPTS / 'connectors.py')
c = importlib.util.module_from_spec(spec)
spec.loader.exec_module(c)
from workflow.environment_config import Store


class ConnectorsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name).resolve()
        self.repo = self.root / 'source/tapdata/tapdata-connectors'
        self.repo.mkdir(parents=True)
        entry = self.repo.parent/'tapdata-connectors-enterprise/build/build.sh'
        entry.parent.mkdir(parents=True)
        entry.write_text('#!/bin/bash\n')
        self.environment = types.SimpleNamespace(station=self.root, source=self.root/'source',
                                                 store=Store(self.root), identity='a'*32, snapshot=lambda: [{'head':'current'}])
        self.specification = {'properties': {'id':'dummy','name':'Dummy'}}

    def tearDown(self):
        self.tmp.cleanup()

    def module(self, path='connectors/dummy-connector'):
        module = self.repo/path
        resources = module/'src/main/resources'
        resources.mkdir(parents=True)
        (module/'pom.xml').write_text('<project><artifactId>dummy-connector</artifactId></project>')
        (resources/'spec.json').write_text(json.dumps(self.specification))
        return module

    def jar(self, module, name='dummy.jar'):
        target=module/'target';target.mkdir(exist_ok=True)
        file=target/name
        with zipfile.ZipFile(file,'w') as jar:
            jar.writestr('spec.json',json.dumps(self.specification))
            jar.writestr('other.json','not valid json')
        return file

    def test_discovers_native_spec_and_ignores_generated_pom(self):
        self.module();self.module('target/copy')
        self.assertEqual(c.modules(self.repo),{'connectors/dummy-connector':'dummy-connector'})

    def test_selects_multiple_and_deduplicates_aliases(self):
        available={'a':'dummy-connector','b':'mysql-connector'}
        self.assertEqual(c.select(available,['Dummy','dummy-connector','MySQL']),['a','b'])

    def test_unknown_or_ambiguous_selection(self):
        for names,available in [(['unknown'],{'a':'dummy-connector'}),(['dummy'],{'a':'dummy-connector','b':'dummy-connector'}),([],{})]:
            with self.assertRaises(c.EnvironmentError): c.select(available,names)

    def test_jar_selection_rejects_old_ambiguous_artifacts(self):
        module=self.module();file=self.jar(module)
        self.jar(module,'original-dummy.jar');self.jar(module,'dummy-sources.jar')
        self.assertEqual(c.jar_for(module),file)
        self.jar(module,'old-dummy.jar')
        with self.assertRaises(c.EnvironmentError): c.jar_for(module)

    def test_selects_full_assembly_from_pom_instead_of_plain_jar(self):
        module=self.module()
        (module/'pom.xml').write_text('<project><artifactId>dummy-connector</artifactId><version>1.0</version>'
            '<build><plugins><plugin><artifactId>maven-assembly-plugin</artifactId><configuration>'
            '<finalName>${connector.file.name}</finalName></configuration></plugin></plugins></build></project>')
        self.jar(module,'dummy-connector-1.0.jar');full=self.jar(module,'dummy-connector-v1.0-123.jar')
        self.assertEqual(c.jar_for(module),full)

    def native_result(self, argv, cwd, log, env=None):
        self.assertEqual(env['JAVA_HOME'], '/java')
        self.assertIn('--local-repository', argv)
        output = Path(argv[argv.index('--output-dir')+1]);output.mkdir(parents=True)
        module = argv[argv.index('--connector')+1].split(':',1)[1]
        jar = output/'dummy.jar';jar.write_bytes(b'connector')
        c.write_json(output/'build-manifest.json', {'complete':True, 'kind':'connector',
            'artifacts':[{'repository':self.repo.name,'module':module,'file':jar.name}],
            'files':{jar.name:c.sha(jar)}})
        return 0

    def test_build_records_only_selected_artifacts_and_does_not_register(self):
        args=types.SimpleNamespace(maven='/mvn',java_home='/java',local_repository='/repo',profile=[],skip_tests=False)
        with patch.object(c,'execute',side_effect=self.native_result) as execute:
            record=c.build(self.environment,self.repo,['connectors/dummy-connector'],args)
        self.assertEqual(execute.call_count,1)
        self.assertTrue(c.read_json(record)['built'])
        self.assertEqual(c.read_json(record)['artifacts'][0]['sha256'],c.sha(record.parent/'dummy.jar'))

    def test_build_failure_does_not_mark_artifacts_built(self):
        args=types.SimpleNamespace(maven='/mvn',java_home='/java',local_repository='/repo',profile=[],skip_tests=False)
        with patch.object(c,'execute',return_value=1):
            with self.assertRaises(c.EnvironmentError):c.build(self.environment,self.repo,['connectors/dummy'],args)
        record=next((self.root/'runtime/tapdata-connectors').glob('*/build.json'))
        self.assertFalse(c.read_json(record).get('built',False))

    def test_passes_verified_native_protoc_parameter(self):
        protoc=self.root/'protoc';protoc.write_text('tool');protoc.chmod(0o700)
        args=types.SimpleNamespace(maven='/mvn',java_home='/java',local_repository='/repo',profile=[],skip_tests=False,protoc_command=str(protoc))
        def run(argv,cwd,log,env=None):
            self.assertEqual(argv[argv.index('--protoc-command')+1], str(protoc))
            return self.native_result(argv,cwd,log,env)
        with patch.object(c,'execute',side_effect=run):c.build(self.environment,self.repo,['connectors/dummy-connector'],args)
        args.protoc_command='relative/protoc'
        with patch.object(c,'execute') as execute:
            with self.assertRaises(c.EnvironmentError):c.build(self.environment,self.repo,['connectors/dummy-connector'],args)
            execute.assert_not_called()

    def test_missing_business_entry_does_not_run_old_build_logic(self):
        (self.repo.parent/'tapdata-connectors-enterprise/build/build.sh').unlink()
        args=types.SimpleNamespace(maven='/mvn',java_home='/java',local_repository='/repo',profile=[],skip_tests=False)
        with patch.object(c,'execute') as execute:
            with self.assertRaisesRegex(c.EnvironmentError, 'develop'):
                c.build(self.environment,self.repo,['connectors/dummy-connector'],args)
            execute.assert_not_called()

    def test_modified_native_artifact_is_rejected(self):
        args=types.SimpleNamespace(maven='/mvn',java_home='/java',local_repository='/repo',profile=[],skip_tests=False)
        def run(argv,cwd,log,env=None):
            self.native_result(argv,cwd,log,env)
            (Path(argv[argv.index('--output-dir')+1])/'dummy.jar').write_bytes(b'changed')
            return 0
        with patch.object(c,'execute',side_effect=run):
            with self.assertRaisesRegex(c.EnvironmentError, '校验失败'):
                c.build(self.environment,self.repo,['connectors/dummy-connector'],args)

    def test_safe_target_address(self):
        self.assertEqual(c.url('https://tm.example/'),'https://tm.example')
        for value in ['file:///tmp/x','http://user:pw@host','http://host/?access_token=x','http://host/api','http://host/#x']:
            with self.assertRaises(c.EnvironmentError):c.url(value)

    def test_inactive_environment_does_not_deploy(self):
        self.environment.config=lambda name:(name,{})
        self.environment.state=lambda:None
        with self.assertRaises(c.EnvironmentError): c.target_url(self.environment,types.SimpleNamespace(env='local',tm_url=None))

    def test_argfile_quotes_and_rejects_newlines(self):
        self.assertEqual(c.argfile(['hello world','a"b','c\\d']),'"hello world"\n"a\\"b"\n"c\\\\d"\n')
        with self.assertRaises(c.EnvironmentError):c.argfile(['secret\n--tm evil'])

    def registration(self, modules=None):
        chosen=modules or ['connectors/dummy-connector']
        directory=self.root/'runtime/tapdata-connectors/build';directory.mkdir(parents=True)
        artifacts=[]
        for i,module in enumerate(chosen):
            file=directory/('artifact%s.jar'%i);file.write_bytes(b'original')
            artifacts.append({'module':module,'file':file.name,'sha256':c.sha(file)})
        record=directory/'build.json'
        c.write_json(record,{'built':True,'station_id':self.environment.identity,'repository':'tapdata/tapdata-connectors',
                             'source':self.environment.snapshot(),'artifacts':artifacts})
        secrets=self.root/'config/shared/secrets';secrets.mkdir(parents=True)
        credentials=secrets/'credentials.json';credentials.write_text('{"password":"private-value"}');credentials.chmod(0o600)
        java_home=self.root/'java';(java_home/'bin').mkdir(parents=True);(java_home/'bin/java').write_text('')
        cli=self.root/'pdk.jar';cli.write_bytes(b'cli')
        args=types.SimpleNamespace(env=None,tm_url='http://127.0.0.1:13030',credentials='shared/secrets/credentials.json',pdk_cli=str(cli),java_home=str(java_home),latest=True)
        return chosen,record,args

    def test_register_uses_private_arguments_and_preserves_original_jar(self):
        chosen,record,args=self.registration()
        def run(argv,cwd,log,env=None):
            self.assertNotIn('private-value',' '.join(argv))
            arg_path=Path(argv[-1][1:]);self.assertEqual(arg_path.stat().st_mode&0o077,0)
            self.assertIn('private-value',arg_path.read_text())
            upload=next(record.parent.glob('registration-*/*/*.jar'));upload.write_bytes(b'encrypted')
            return 0
        with patch.object(c,'execute',side_effect=run):result=c.register(self.environment,self.repo,chosen,record,args)
        self.assertTrue(result['results'][0]['submitted']);self.assertFalse(result['results'][0]['verified'])
        self.assertEqual((record.parent/'artifact0.jar').read_bytes(),b'original')
        self.assertEqual(list(record.parent.glob('.pdk-args-*')),[])
        self.assertNotIn('private-value',Path(result['receipt']).read_text())

    def test_register_stops_batch_on_failure(self):
        chosen,record,args=self.registration(['first','second'])
        with patch.object(c,'execute',return_value=1) as execute:
            with self.assertRaises(c.EnvironmentError):c.register(self.environment,self.repo,chosen,record,args)
        self.assertEqual(execute.call_count,1)
        self.assertEqual(list(record.parent.glob('.pdk-args-*')),[])

    def test_named_environment_reads_user_admin_password(self):
        chosen,record,args=self.registration()
        args.env='local-amd64';args.credentials=None
        path=self.root/'config/environments/local-amd64/secrets/admin-password'
        path.parent.mkdir(parents=True);path.write_text('user password\n');path.chmod(0o600)
        def run(argv,cwd,log,env=None):
            arguments=Path(argv[-1][1:]).read_text()
            self.assertIn('"user password"',arguments)
            self.assertNotIn('user password',' '.join(argv))
            return 0
        with patch.object(c,'target_url',return_value='http://127.0.0.1:13030'), patch.object(c,'execute',side_effect=run):
            result=c.register(self.environment,self.repo,chosen,record,args)
        self.assertTrue(result['results'][0]['submitted'])

    def test_changed_source_or_tampered_jar_never_uploads(self):
        chosen,record,args=self.registration()
        with patch.object(c,'execute') as execute:
            self.environment.snapshot=lambda:[]
            with self.assertRaises(c.EnvironmentError):c.register(self.environment,self.repo,chosen,record,args)
            self.environment.snapshot=lambda:[{'head':'current'}]
            (record.parent/'artifact0.jar').write_bytes(b'changed')
            with self.assertRaises(c.EnvironmentError):c.register(self.environment,self.repo,chosen,record,args)
            execute.assert_not_called()

    def test_credentials_permissions_fail_before_upload(self):
        chosen,record,args=self.registration();(self.root/'config/shared/secrets/credentials.json').chmod(0o644)
        with patch.object(c,'execute') as execute:
            with self.assertRaises(c.EnvironmentError):c.register(self.environment,self.repo,chosen,record,args)
            execute.assert_not_called()


if __name__=='__main__':unittest.main()
