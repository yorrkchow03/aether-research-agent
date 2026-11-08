"""First-run configuration uses synthetic keys and no provider requests."""
import importlib.util
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

PATH=Path(__file__).resolve().parents[1]/'scripts/setup_local.py'
SPEC=importlib.util.spec_from_file_location('aether_local_setup',PATH)
setup=importlib.util.module_from_spec(SPEC);SPEC.loader.exec_module(setup)

class LocalSetupTests(unittest.TestCase):
    def setUp(self):
        self.directory=tempfile.TemporaryDirectory();self.addCleanup(self.directory.cleanup)
        self.root=Path(self.directory.name);(self.root/'.env.example').write_text('ARK_API_KEY=\nAGENT_MODEL_MODE=ark_plan\n')

    def test_symlink_config_is_refused_without_changing_target(self):
        outside=self.root/'not-config';outside.write_text('KEEP')
        (self.root/'.env').symlink_to(outside)
        with patch.object(setup,'ROOT',self.root):
            with self.assertRaisesRegex(ValueError,'regular local'):
                setup.configure(no_input=True)
        self.assertEqual(outside.read_text(),'KEEP')

    def test_existing_key_keeps_content_and_becomes_private(self):
        config=self.root/'.env';config.write_text('ARK_API_KEY=synthetic-fixture-key\n');config.chmod(0o644)
        with patch.object(setup,'ROOT',self.root),patch.object(setup.getpass,'getpass',side_effect=AssertionError('No prompt for an existing key')):
            setup.configure()
        self.assertEqual(config.stat().st_mode&0o777,0o600)
        self.assertEqual(config.read_text(),'ARK_API_KEY=synthetic-fixture-key\n')

    def test_no_input_creates_only_empty_private_config_and_fails_clearly(self):
        with patch.object(setup,'ROOT',self.root):
            with self.assertRaisesRegex(ValueError,'Set ARK_API_KEY'):
                setup.configure(no_input=True)
        config=self.root/'.env'
        self.assertIn('ARK_API_KEY=\n',config.read_text())
        self.assertEqual(config.stat().st_mode&0o777,0o600)
