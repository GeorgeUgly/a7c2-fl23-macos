import contextlib
import io
import json
from pathlib import Path
import struct
import tempfile
import unittest
from unittest.mock import patch

from macfl23 import cli, ntsc
from macfl23 import workflow as w


class NtscTests(unittest.TestCase):
    def test_prepare_persists_opt_in_before_preflight(self):
        from test_workflow import WorkflowTests
        session, records = WorkflowTests().fixture()
        with tempfile.TemporaryDirectory() as directory:
            job = w.MacJob(Path(directory) / 'work')
            def native(session, name, mode=None):
                self.assertEqual(job.state['features'], ntsc.FEATURES)
                self.assertEqual(len(job.state['backup_files']), 8)
            with patch.object(w, 'MacSession', return_value=session), \
                 patch.object(w.core, 'backup_records', return_value=records), \
                 patch.object(w.core, 'ORIGINAL', [w.core.sha(b'AP'), w.core.sha(b'CP')]), \
                 patch.object(w.core, 'patch_program', side_effect=lambda raw, chip, reverse=False: raw), \
                 patch.object(ntsc, 'patch_ap', side_effect=lambda raw, reverse=False: raw), \
                 patch.object(job, 'module', side_effect=native), \
                 contextlib.redirect_stdout(io.StringIO()):
                job.prepare(disable_ntsc_warning=True)
                job.validate_candidates()
            state = json.loads(job.state_path.read_text())
            self.assertEqual(state['features'], ntsc.FEATURES)
            self.assertEqual(state['phase'], 'prepared')
            session.cp_write.assert_not_called()
            session.ap_write.assert_not_called()

    def test_feature_choice_is_opt_in_and_only_on_prepare(self):
        default = cli.parser().parse_args(['prepare', '--workspace', '/private/work'])
        combined = cli.parser().parse_args([
            'prepare', '--workspace', '/private/work', '--disable-ntsc-warning'])
        self.assertFalse(default.disable_ntsc_warning)
        self.assertTrue(combined.disable_ntsc_warning)
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            cli.parser().parse_args(['install', '--workspace', '/private/work',
                                     '--battery-percent', '80', '--disable-ntsc-warning'])

    def test_old_workspaces_default_to_fl23_unknown_choices_stop(self):
        self.assertFalse(ntsc.enabled({}))
        self.assertFalse(ntsc.enabled({'features': ['fl23']}))
        self.assertTrue(ntsc.enabled({'features': ntsc.FEATURES}))
        for value in [[], ['no_ntsc_notice'], ['fl23', 'unknown'], 'fl23', None]:
            with self.subTest(value=value), self.assertRaises(ValueError):
                ntsc.enabled({'features': value})

    def test_wrong_firmware_and_already_patched_input_rejected(self):
        for reverse in (False, True):
            with self.assertRaises(ValueError):
                ntsc.patch_ap(b'unsupported firmware', reverse)

    def test_optional_assets_are_pinned_and_tampering_rejected(self):
        manifest = ntsc.assets()
        self.assertEqual(len(manifest['files']), 2)
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / 'ntsc-manifest.json').write_text(json.dumps(manifest))
            for name in manifest['files']:
                (root / name).write_bytes((ntsc.ASSETS / name).read_bytes())
            path = root / 'ntsc-installer.so'
            path.write_bytes(path.read_bytes() + b'changed')
            with patch.object(ntsc, 'ASSETS', root), self.assertRaises(ValueError):
                ntsc.assets()

    def report(self, mode, writes):
        raw = bytearray(240)
        struct.pack_into('<8sIIIIqqqqqqqQQQQ', raw, 0,
                         b'A7IRP001', 1, mode, 9, writes,
                         0, 0, 0, 0, 0, 0, 1234, 1, 2, 99960744, 18090880)
        final = [ntsc.AP_COMBINED, ntsc.CP_FL23]
        before = final if mode == 2 else w.core.ORIGINAL
        after = w.core.ORIGINAL if mode == 2 else final
        raw[112:176] = bytes.fromhex(''.join(before))
        if mode:
            raw[176:240] = bytes.fromhex(''.join(after))
        return raw

    def test_native_reports_require_combination_hashes_and_exact_write_count(self):
        for mode in (0, 1, 2):
            raw = self.report(mode, 0 if mode == 0 else 42)
            parsed = ntsc.parse_native(w.core, 'installer', raw, mode)
            self.assertEqual(parsed['window_writes'], 0 if mode == 0 else 42)
            if mode:
                raw[176:208] = bytes.fromhex(w.core.FINAL[0])
                with self.assertRaises(ValueError):
                    ntsc.parse_native(w.core, 'installer', raw, mode)
        for count in (0, 40, 41, 43):
            with self.assertRaises(ValueError):
                ntsc.parse_native(w.core, 'installer', self.report(1, count), 1)

    def test_runtime_report_includes_both_notice_windows(self):
        raw = bytearray(2344)
        struct.pack_into('<8sIIqqqQII', raw, 0, b'A7RUN001', 1, 8, 0, 0, 1234, 0x100000, 16, 0)
        for i in (6, 7):
            raw[56 + i * 256:60 + i * 256] = ntsc.AFTER
            raw[184 + i * 256:188 + i * 256] = ntsc.AFTER
        raw[2104:2106] = b'0\n'
        result = ntsc.parse_native(w.core, 'runtime', raw)
        self.assertEqual([(s['offset'], s['length']) for s in result['spans']], ntsc.SPANS)
        self.assertEqual([s['memory'] for s in result['spans'][6:]], [ntsc.AFTER.hex()] * 2)
        self.assertEqual(result['image_mode'], '0\n')
        with self.assertRaises(ValueError):
            ntsc.parse_native(w.core, 'runtime', bytes(1832))

    def test_full_file_readback_uses_saved_selection_and_stock_restore_target(self):
        job = w.MacJob('/private/offline-test')
        for features, expected in [(['fl23'], w.core.FINAL),
                                   (ntsc.FEATURES, [ntsc.AP_COMBINED, ntsc.CP_FL23])]:
            job.state = {'features': features}
            with patch.object(w.app.Job, 'files') as files:
                job.files('session', w.core.FINAL)
                files.assert_called_once_with('session', expected)
            with patch.object(w.app.Job, 'files') as files:
                job.files('session', w.core.ORIGINAL)
                files.assert_called_once_with('session', w.core.ORIGINAL)

    def test_native_dispatch_defaults_to_upstream_and_opt_in_uses_own_workers(self):
        from macfl23 import ntsc_module
        job = w.MacJob('/private/offline-test')
        job.state = {}
        with patch.object(w.app.Job, 'module') as upstream, patch.object(ntsc_module, 'run') as optional:
            job.module('session', 'installer', 1)
            upstream.assert_called_once_with('session', 'installer', 1)
            optional.assert_not_called()
        job.state = {'features': ntsc.FEATURES}
        with patch.object(w.app.Job, 'module') as upstream, patch.object(ntsc_module, 'run') as optional:
            job.module('session', 'installer', 1)
            optional.assert_called_once_with(job, 'session', w.core, 'installer', 1)
            upstream.assert_not_called()
        with patch.object(w.app.Job, 'module') as upstream:
            job.module('session', 'wbi', 1)
            upstream.assert_called_once_with('session', 'wbi', 1)


if __name__ == '__main__':
    unittest.main()
