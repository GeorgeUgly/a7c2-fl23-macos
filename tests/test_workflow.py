import contextlib
import io
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from macfl23 import workflow as w
from macfl23 import cli, integrated


class WorkflowTests(unittest.TestCase):
    def fixture(self):
        units = {k: bytes(n) for k, n in zip(w.core.CP_KEYS, (8192, 28672))}
        records = [dict((k, SimpleNamespace(data=b'\0')) for k in w.core.AP_KEYS),
                   dict((k, SimpleNamespace(data=data)) for k, data in units.items())]
        session = MagicMock()
        session.__enter__.return_value = session
        session.identity = {'model': w.core.MODEL, 'serial_sha256': 'offline-fixture'}
        session.call.side_effect = lambda fn, *args: fn(*args)
        session.camera.getBackupPresetData.side_effect = lambda live: b'live' if live else b'stored'
        session.camera.readBackup.return_value = b'\0'
        session.cp_read.side_effect = lambda key: units[key]
        session.read.side_effect = lambda path: b'AP' if path.endswith('appFw.so') else b'CP'
        return session, records

    def test_complete_backup_is_duplicated_before_native_preflight(self):
        session, records = self.fixture()
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            job = w.MacJob(root / 'work')
            def native(session, name, mode=None):
                self.assertEqual(len(job.state['backup_files']), 8)
                for file, sha in job.state['backup_files'].items():
                    first = (job.root / 'original' / file).read_bytes()
                    self.assertEqual(first, (root / 'second' / file).read_bytes())
                    self.assertEqual(w.core.sha(first), sha)
            with patch.object(w, 'MacSession', return_value=session), \
                 patch.object(w.core, 'backup_records', return_value=records), \
                 patch.object(w.core, 'ORIGINAL', [w.core.sha(b'AP'), w.core.sha(b'CP')]), \
                 patch.object(w.core, 'patch_program', side_effect=lambda raw, chip, reverse=False: raw), \
                 patch.object(integrated.Profile, 'patch_ap', side_effect=lambda raw, reverse=False: raw), \
                 patch.object(job, 'module', side_effect=native) as module, \
                 contextlib.redirect_stdout(io.StringIO()):
                job.prepare(root / 'second')
            self.assertEqual(job.state['phase'], 'prepared')
            self.assertEqual([c.args[1:] for c in module.call_args_list],
                             [('probe',), ('installer', 0), ('wbi', 0)])
            session.cp_write.assert_not_called()
            session.ap_write.assert_not_called()
            session.camera.saveBackup.assert_not_called()

    def test_unknown_original_keeps_backups_but_never_runs_native_module(self):
        session, records = self.fixture()
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            job = w.MacJob(root / 'work')
            with patch.object(w, 'MacSession', return_value=session), \
                 patch.object(w.core, 'backup_records', return_value=records), \
                 patch.object(job, 'module') as module, contextlib.redirect_stdout(io.StringIO()):
                with self.assertRaises(ValueError):
                    job.prepare(root / 'second')
            module.assert_not_called()
            self.assertEqual((root / 'second' / 'cpapp-b.bin.bin').read_bytes(), b'CP')
            self.assertNotEqual(job.state['phase'], 'prepared')

    def test_existing_backup_never_overwritten(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            second = root / 'second'
            second.mkdir()
            marker = second / 'keep'
            marker.write_text('keep')
            with patch.object(w, 'MacSession') as factory:
                with self.assertRaises(ValueError):
                    w.MacJob(root / 'work').prepare(second)
                factory.assert_not_called()
            self.assertEqual(marker.read_text(), 'keep')

    def test_default_preparation_creates_only_one_backup_location(self):
        session, records = self.fixture()
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            job = w.MacJob(root / 'work')
            with patch.object(w, 'MacSession', return_value=session), \
                 patch.object(w.core, 'backup_records', return_value=records), \
                 patch.object(w.core, 'ORIGINAL', [w.core.sha(b'AP'), w.core.sha(b'CP')]), \
                 patch.object(w.core, 'patch_program', side_effect=lambda raw, chip, reverse=False: raw), \
                 patch.object(integrated.Profile, 'patch_ap', side_effect=lambda raw, reverse=False: raw), \
                 patch.object(job, 'module'), contextlib.redirect_stdout(io.StringIO()):
                job.prepare()
                job.load()
            self.assertEqual([p.resolve() for p in root.iterdir()], [job.root])
            self.assertEqual(job.state['backup_copies'], 1)
            self.assertEqual(job.state['backup_copy'], str(job.root / 'original'))
            self.assertEqual(len(list((job.root / 'original').iterdir())), 8)
            self.assertEqual(job.state['phase'], 'prepared')

    def test_unknown_phase_cannot_resume_install(self):
        args = SimpleNamespace(action='install')
        job = MagicMock(state={'phase': 'needs_review'})
        with self.assertRaises(ValueError):
            cli.check_phase(args, job)
        job.save.assert_not_called()

    def test_low_battery_rejected_before_usb_or_camera_lock(self):
        args = SimpleNamespace(action='install', battery_percent=49)
        with patch.object(cli.platform, 'system', return_value='Darwin'), \
             patch.object(cli.os, 'geteuid', return_value=0), \
             patch.object(cli, 'camera_lock') as lock:
            with self.assertRaises(ValueError):
                cli.run_camera(args)
            lock.assert_not_called()

    def test_cold_start_confirmation_required_before_usb(self):
        args = SimpleNamespace(action='verify', cold_start_confirmed=False)
        with patch.object(cli.platform, 'system', return_value='Darwin'), \
             patch.object(cli.os, 'geteuid', return_value=0), \
             patch.object(cli, 'camera_lock') as lock:
            with self.assertRaises(ValueError):
                cli.run_camera(args)
            lock.assert_not_called()

    def test_private_data_inside_repository_rejected(self):
        with self.assertRaises(ValueError):
            cli.private_path(cli.ROOT / 'workspaces' / 'body')

    def test_partial_writes_never_get_completed_timeout_classification(self):
        job = w.MacJob('/unused/offline-workspace')
        job.state = {'phase': 'writing_programs', 'journal': [
            {'step': 'native_verified', 'module': 'wbi', 'mode': 0},
            {'step': 'mac_usb_session_exit', 'result': {'service_exit_request': 'sent', 'errors': ['timeout']}}]}
        with self.assertRaises(ValueError):
            job.confirm_completed_body('install')

    def test_successful_preflight_with_only_exit_timeout_stays_distinct(self):
        job = w.MacJob('/unused/offline-workspace')
        job.state = {'phase': 'preflight_verified', 'journal': [
            {'step': 'native_verified', 'module': 'wbi', 'mode': 0},
            {'step': 'mac_usb_session_exit', 'result': {'service_exit_request': 'sent', 'errors': ['timeout']}}]}
        with patch.object(job, 'load'):
            self.assertEqual(job.confirm_completed_body('prepare'), 'prepared_usb_unconfirmed')

    def test_service_stop_failure_is_not_completed(self):
        job = w.MacJob('/unused/offline-workspace')
        job.state = {'phase': 'preflight_verified', 'journal': [
            {'step': 'native_verified', 'module': 'wbi', 'mode': 0},
            {'step': 'mac_usb_session_exit', 'result': {'errors': ['stop failed']}}]}
        with self.assertRaises(ValueError):
            job.confirm_completed_body('prepare')

    def test_cli_distinguishes_completed_exit_timeout_from_unknown_io(self):
        from macfl23.session import NormalReturnTimeout
        for error, completed in [(NormalReturnTimeout('exit timeout'), True),
                                 (TimeoutError('unknown write'), False)]:
            with self.subTest(completed=completed), tempfile.TemporaryDirectory() as temp:
                root = Path(temp)
                root.chmod(0o700)
                args = SimpleNamespace(action='install', workspace=root, battery_percent=80)
                job = MagicMock(root=root, state={'phase': 'prepared', 'journal': []})
                def save(phase, **values):
                    job.state.update(values, phase=phase)
                def install(battery):
                    job.state['phase'] = 'settings_verified' if completed else 'writing_programs'
                    raise error
                job.save.side_effect = save
                job.install.side_effect = install
                job.confirm_completed_body.return_value = 'installed_pending_cold_start_usb_unconfirmed'
                with patch.object(cli.platform, 'system', return_value='Darwin'), \
                     patch.object(cli.os, 'geteuid', return_value=0), \
                     patch.object(cli, 'camera_lock', return_value=contextlib.nullcontext()), \
                     patch.object(cli, 'return_ownership'), \
                     patch.object(cli.subprocess, 'Popen'), \
                     patch.object(w, 'MacJob', return_value=job), \
                     contextlib.redirect_stdout(io.StringIO()):
                    if completed:
                        self.assertEqual(cli.run_camera(args), 2)
                        self.assertEqual(job.state['phase'], 'installed_pending_cold_start_usb_unconfirmed')
                    else:
                        with self.assertRaises(TimeoutError):
                            cli.run_camera(args)
                        self.assertEqual(job.state['phase'], 'needs_review')
                        job.confirm_completed_body.assert_not_called()
                job.install.assert_called_once_with(80)
