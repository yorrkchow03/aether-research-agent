"""Sharing must preserve curated assets and exclude private state and secrets."""
import hashlib
import json
from pathlib import Path
import runpy
from tempfile import TemporaryDirectory
import unittest

ROOT = Path(__file__).resolve().parents[1]
export_source = runpy.run_path(str(ROOT / 'scripts/dev/export_source.py'))['export_source']


class SourceExportTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / 'project'
        self.root.mkdir()
        (self.root / 'README.md').write_text('Synthetic export fixture only.\n')
        self.output = Path(self.temp.name) / 'export'

    def write(self, name, data):
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)

    def test_curated_figures_round_trip_but_unreviewed_images_do_not(self):
        image = b'\x89PNG\r\nsynthetic transport fixture, not an actual image'
        self.write('docs/architecture/figures/aether-agent-framework-zh.png', image)
        self.write('docs/architecture/figures/aether-agent-framework-zh.svg', b'<svg/>')
        self.write('docs/architecture/figures/private-screenshot.png', b'not-public')
        export_source(self.root, self.output)
        manifest = json.loads((self.output / 'SOURCE_MANIFEST.json').read_text())['files']
        asset = 'docs/architecture/figures/aether-agent-framework-zh.png'
        self.assertEqual((self.output / asset).read_bytes(), image)
        self.assertEqual(manifest[asset], hashlib.sha256(image).hexdigest())
        self.assertNotIn('docs/architecture/figures/private-screenshot.png', manifest)

    def test_private_state_and_real_env_are_not_exported(self):
        for name in ('.env', 'runtime/ledger.json', 'data/prices.json',
                     'reports/private.md', 'local/coursework/proposal.md'):
            self.write(name, b'ARK_API_KEY=\n' if name == '.env' else b'private fixture')
        self.write('.env.example', b'ARK_API_KEY=\n')
        export_source(self.root, self.output)
        self.assertEqual(set(json.loads((self.output / 'SOURCE_MANIFEST.json').read_text())['files']),
                         {'README.md', '.env.example'})

    def test_asset_secret_detection_is_atomic(self):
        secret = 'synthetic-' + 'private-credential-for-export-test'
        (self.root / '.env').write_text('ARK_API_KEY=' + secret + '\n')
        self.write('docs/architecture/figures/aether-agent-framework-zh.png', secret.encode())
        with self.assertRaisesRegex(ValueError, 'contents withheld'):
            export_source(self.root, self.output)
        self.assertFalse(self.output.exists())

    def test_source_symlink_is_rejected_before_copy(self):
        outside = Path(self.temp.name) / 'unrelated.py'
        outside.write_text('private fixture')
        (self.root / 'src').mkdir()
        (self.root / 'src/linked.py').symlink_to(outside)
        with self.assertRaisesRegex(ValueError, 'Symlink'):
            export_source(self.root, self.output)
        self.assertFalse(self.output.exists())

    def test_top_level_symlink_is_also_rejected(self):
        (self.root/'README.md').unlink()
        outside=Path(self.temp.name)/'external.md';outside.write_text('external fixture')
        (self.root/'README.md').symlink_to(outside)
        with self.assertRaisesRegex(ValueError,'Symlink'):export_source(self.root,self.output)

    def test_documentation_index_is_shared(self):
        self.write('docs/README.md',b'# Synthetic documentation index\n')
        export_source(self.root,self.output)
        self.assertTrue((self.output/'docs/README.md').is_file())

    def test_typesafe_and_github_token_patterns_are_refused(self):
        for marker in ('apikey_'+'a'*32+'_'+'b'*64,'ghp_'+'c'*40,'github_pat_'+'d'*60):
            self.write('src/leak.py',marker.encode())
            with self.subTest(marker=marker[:7]),self.assertRaisesRegex(ValueError,'contents withheld'):
                export_source(self.root,self.output)
            self.assertFalse(self.output.exists())


if __name__ == '__main__':
    unittest.main()
