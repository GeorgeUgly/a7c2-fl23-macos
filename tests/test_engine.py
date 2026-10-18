import io
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import zipfile
from macfl23 import engine


class EngineTests(unittest.TestCase):
    def archive(self, name='package/test.py', content=b'fixture'):
        out = io.BytesIO()
        with zipfile.ZipFile(out, 'w') as z:
            z.writestr(name, content)
        raw = out.getvalue()
        return raw, {'sha256': engine.digest(raw), 'files': {name: engine.digest(content)}}

    def test_changed_archive_rejected_before_extraction(self):
        raw, meta = self.archive()
        with tempfile.TemporaryDirectory() as t:
            with self.assertRaises(ValueError):
                engine.unpack(raw + b'changed', Path(t), meta)
            self.assertEqual(list(Path(t).iterdir()), [])

    def test_traversal_rejected_even_with_matching_hash(self):
        raw, meta = self.archive('../escape')
        with tempfile.TemporaryDirectory() as t:
            with self.assertRaises(ValueError):
                engine.unpack(raw, Path(t), meta)

    def test_extra_executable_file_rejected_by_inventory(self):
        raw, meta = self.archive()
        with tempfile.TemporaryDirectory() as t, patch.object(engine, 'lock', return_value=meta):
            root = Path(t)
            engine.unpack(raw, root, meta)
            engine.verify(root)
            (root / 'extra.py').write_text('unexpected')
            with self.assertRaises(ValueError):
                engine.verify(root)

    def test_changed_member_rejected(self):
        raw, meta = self.archive()
        meta['files']['package/test.py'] = '0' * 64
        with tempfile.TemporaryDirectory() as t:
            with self.assertRaises(ValueError):
                engine.unpack(raw, Path(t), meta)

    def test_symlink_tree_rejected(self):
        with tempfile.TemporaryDirectory() as t:
            root = Path(t)
            (root / 'link').symlink_to('/tmp')
            with self.assertRaises(ValueError):
                engine.verify(root)
