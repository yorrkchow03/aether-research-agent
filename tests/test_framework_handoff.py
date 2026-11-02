"""The teammate entry point runs without a credential or live model."""
import json
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory
import unittest


class FrameworkHandoffTests(unittest.TestCase):
    def test_offline_handoff_probe(self):
        root = Path(__file__).resolve().parents[1]
        with TemporaryDirectory() as directory:
            output = Path(directory) / 'receipt.json'
            result = subprocess.run([sys.executable, str(root / 'scripts/dev/check_framework.py'),
                '--output', str(output)], cwd=directory, capture_output=True, text=True, timeout=60)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            receipt = json.loads(output.read_text())
            self.assertTrue(receipt['fresh_empty_state'])
            self.assertTrue(receipt['demo']['blocked_before_plan'])
            self.assertTrue(receipt['demo']['checkpoint_saved'])
            self.assertEqual(receipt['external_model_calls'], 0)
            self.assertFalse(receipt['user_credentials_loaded'])
            self.assertEqual(len(receipt['current_business_tools']), 12)


if __name__ == '__main__':
    unittest.main()
