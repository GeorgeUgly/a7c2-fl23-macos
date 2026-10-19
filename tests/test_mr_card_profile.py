"""The research candidate must never become an implicit device upgrade."""
import contextlib
import importlib.util
import io
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from macfl23 import cli, ntsc

SPEC = importlib.util.spec_from_file_location(
    'mr_card_patch_profile', Path(__file__).resolve().parents[1] /
    'research/mr-card-compat/patch_profile.py')
PROFILE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(PROFILE)
BUILDER = Path(__file__).resolve().parents[1] / 'research/mr-card-compat/build_and_verify.py'


class MrCardProfileTests(unittest.TestCase):
    def test_unknown_and_truncated_firmware_rejected_in_both_directions(self):
        # Actual accepted firmware is exercised by the offline native verifier;
        # host tests never replace or relax the source/output fingerprints.
        for value in (b'', b'unsupported firmware', b'\0' * 1024):
            for reverse in (False, True):
                with self.subTest(size=len(value), reverse=reverse), self.assertRaises(ValueError):
                    PROFILE.patch_ap(value, reverse=reverse)

    def test_research_profile_is_not_a_production_feature(self):
        self.assertFalse(ntsc.enabled({}))
        self.assertFalse(cli.parser().parse_args(['prepare', '--workspace', '/private/work']).disable_ntsc_warning)
        with self.assertRaises(ValueError):
            ntsc.enabled({'features': ['fl23', 'mr_card_compat']})
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            cli.parser().parse_args(['install', '--workspace', '/private/work',
                                     '--battery-percent', '80', '--mr-card-compat'])

    def test_offline_builder_refuses_existing_output_without_touching_it(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / 'source'
            source.write_bytes(b'unsupported firmware')
            marker = root / 'keep.txt'
            marker.write_text('preserve')
            result = subprocess.run([sys.executable, str(BUILDER), '--source', str(source),
                                     '--output', str(root)], capture_output=True, text=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn('new directory', result.stderr)
            self.assertEqual(marker.read_text(), 'preserve')
            self.assertEqual(source.read_bytes(), b'unsupported firmware')

    def test_output_symlinks_and_existing_files_are_never_followed(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / 'source'
            source.write_bytes(b'keep source')
            link = root / 'link'
            dangling = root / 'dangling'
            link.symlink_to(source)
            dangling.symlink_to(root / 'absent')
            for output in (source, link, dangling):
                with self.subTest(output=output.name):
                    result = subprocess.run([
                        sys.executable, str(BUILDER), '--source', str(source),
                        '--output', str(output)], capture_output=True, text=True)
                    self.assertNotEqual(result.returncode, 0)
                    self.assertIn('new directory', result.stderr)
                    self.assertEqual(source.read_bytes(), b'keep source')
            self.assertFalse((root / 'absent').exists())

    def test_optimized_python_cannot_disable_verification(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / 'source'
            source.write_bytes(b'keep source')
            output = root / 'new'
            result = subprocess.run([
                sys.executable, '-O', str(BUILDER), '--source', str(source),
                '--output', str(output)], capture_output=True, text=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn('Do not disable', result.stderr)
            self.assertFalse(output.exists())
            self.assertEqual(source.read_bytes(), b'keep source')

    def test_unknown_source_never_creates_a_candidate_directory(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / 'source'
            source.write_bytes(b'unknown firmware')
            output = root / 'new'
            result = subprocess.run([
                sys.executable, str(BUILDER), '--source', str(source),
                '--output', str(output)], capture_output=True, text=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn('Unsupported full AP fingerprint', result.stderr)
            self.assertFalse(output.exists())
            self.assertEqual(source.read_bytes(), b'unknown firmware')


if __name__ == '__main__':
    unittest.main()
