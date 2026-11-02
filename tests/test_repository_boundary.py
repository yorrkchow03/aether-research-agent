"""Pre-push inspection must check staged bytes and actual shared link targets."""
from pathlib import Path
import runpy
import subprocess
from tempfile import TemporaryDirectory
import unittest

CHECK=runpy.run_path(str(Path(__file__).resolve().parents[1]/'scripts/dev/check_repository.py'))['check_repository']


class RepositoryBoundaryTests(unittest.TestCase):
    def setUp(self):
        tmp=TemporaryDirectory();self.addCleanup(tmp.cleanup);self.root=Path(tmp.name)
        for name,text in {
            'README.md':'# Synthetic source\n[Docs](docs/README.md)\n',
            'docs/README.md':'# Synthetic docs\n',
            'pyproject.toml':'[project]\nname="synthetic"\n',
            '.gitignore':'.env\n',
            '.env.example':'ARK_API_KEY=\nAETHER_CONTEXT_TRIGGER_TOKENS=48000\n',
            '.github/workflows/ci.yml':'name: Synthetic fixture\n',
        }.items():self.write(name,text)

    def write(self,name,text):
        p=self.root/name;p.parent.mkdir(parents=True,exist_ok=True);p.write_text(text)

    def git(self,*args):
        return subprocess.run(['git',*args],cwd=self.root,capture_output=True,check=True)

    def stage(self):
        self.git('init','--initial-branch=main');self.git('add','.')

    def test_empty_credentials_and_numeric_token_budget_are_valid(self):
        result=CHECK(self.root)
        self.assertEqual(result['status'],'passed');self.assertEqual(result['provider_calls'],0)

    def test_staged_secret_is_not_hidden_by_clean_working_copy(self):
        marker='ghp_'+'z'*40
        self.write('src/candidate.py',marker);self.stage()
        self.write('src/candidate.py','clean but not staged\n')
        result=CHECK(self.root)
        self.assertEqual(result['mode'],'git_index')
        self.assertTrue(any(i['check']=='credential_or_personal_path_pattern' for i in result['issues']))
        self.assertNotIn(marker,str(result))

    def test_private_index_file_is_refused_even_without_a_credential(self):
        self.write('data/private.json','{}');self.stage()
        result=CHECK(self.root)
        self.assertTrue(any(i=={'path':'data/private.json','check':'outside_source_boundary'} for i in result['issues']))

    def test_private_local_file_does_not_resolve_a_repository_link(self):
        self.write('README.md','# Synthetic\n[Not shared](data/private.md)\n')
        self.write('data/private.md','private synthetic fixture')
        result=CHECK(self.root)
        self.assertTrue(any(i['check']=='missing_relative_link' for i in result['issues']))

    def test_populated_template_credential_is_refused(self):
        self.write('.env.example','ARK_API_KEY=short-value\n')
        self.assertTrue(any(i['check']=='nonempty_template_credential' for i in CHECK(self.root)['issues']))

    def test_configured_secret_literals_are_checked_without_printing_them(self):
        marker='synthetic-owner-private-value'
        self.write('src/candidate.py',marker)
        result=CHECK(self.root,secret_values=[marker])
        self.assertEqual(result['status'],'failed');self.assertNotIn(marker,str(result))

    def test_root_data_ignore_rule_does_not_hide_collector_source(self):
        project=Path(__file__).resolve().parents[1]
        self.write('.gitignore',(project/'.gitignore').read_text())
        self.write('scripts/data/collector.py','# synthetic source\n')
        self.write('data/private.json','{}')
        self.git('init','--initial-branch=main')
        for name,ignored in [('scripts/data/collector.py',False),('data/private.json',True)]:
            result=subprocess.run(['git','check-ignore','--',name],cwd=self.root,capture_output=True)
            self.assertEqual(result.returncode==0,ignored)


if __name__=='__main__':unittest.main()
