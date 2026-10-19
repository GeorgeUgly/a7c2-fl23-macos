"""Integrated profile and one-shot upgrade guards; no camera access."""
import contextlib
import io
from pathlib import Path
import struct
import tempfile
import unittest
from unittest.mock import MagicMock, patch
from types import SimpleNamespace

from macfl23 import integrated as p, ntsc, cli, workflow as w
from macfl23 import upgrade as u


class IntegratedTests(unittest.TestCase):
    def report(self, name, mode):
        raw = bytearray(240)
        struct.pack_into('<8sIIIIqqqqqqqQQQQ', raw, 0,
                         b'A7IRP001', 1, mode, 9, 0 if mode == 0 else 43 if name == 'installer' else 3,
                         0, 0, 0, 0, 0, 0, 1234, 1, 2, 99960744, 18090880)
        source = w.core.ORIGINAL if name == 'installer' else w.core.FINAL
        final = [p.AP_FINAL, p.CP_FL23]
        raw[112:176] = bytes.fromhex(''.join(final if mode == 2 else source))
        if mode:
            raw[176:240] = bytes.fromhex(''.join(source if mode == 2 else final))
        return raw

    def test_native_upgrade_and_stock_restore_require_distinct_full_hashes(self):
        for name in ('upgrade', 'installer'):
            for mode in (0, 1, 2):
                raw = self.report(name, mode)
                p.parse_native(w.core, name, raw, mode)
                raw[112:144] = bytes.fromhex(ntsc.AP_COMBINED)
                with self.assertRaises(ValueError):
                    p.parse_native(w.core, name, raw, mode)
        for count in (0, 2, 4, 40, 42, 44):
            for name in ('upgrade', 'installer'):
                raw = self.report(name, 1)
                struct.pack_into('<I', raw, 20, count)
                with self.assertRaises(ValueError):
                    p.parse_native(w.core, name, raw, 1)

    def test_native_report_errors_and_sizes_never_pass(self):
        for offset, fmt, value in ((24, '<q', -5), (32, '<q', -5), (48, '<q', -5),
                                   (96, '<Q', 99960745), (108, '<I', 1)):
            raw = self.report('upgrade', 1)
            struct.pack_into(fmt, raw, offset, value)
            with self.assertRaises(ValueError):
                p.parse_native(w.core, 'upgrade', raw, 1)

    def test_runtime_checks_nine_spans_including_mr_and_narrow_ntsc(self):
        raw = bytearray(2600)
        struct.pack_into('<8sIIqqqQII', raw, 0, b'A7RUN001', 1, 9, 0, 0, 123, 0x100000, 16, 0)
        for i, (offset, length) in enumerate(p.SPANS):
            value = bytes([i]) * length
            raw[56 + i*256:56 + i*256 + length] = value
            raw[184 + i*256:184 + i*256 + length] = value
        raw[2360:2362] = b'2\n'
        parsed = p.parse_native(w.core, 'runtime', raw)
        self.assertEqual([(s['offset'], s['length']) for s in parsed['spans']], p.SPANS)
        self.assertIn((0x4b2dd02, 2), p.SPANS)
        self.assertNotIn((0x20617b0, 4), p.SPANS)
        self.assertEqual(parsed['image_mode'], '2\n')
        with self.assertRaises(ValueError):
            p.parse_native(w.core, 'runtime', raw[:2344])

    def test_integrated_state_routes_to_its_own_workers_and_blocks_legacy_diagnostics(self):
        from macfl23 import ntsc_module
        self.assertTrue(ntsc.enabled({'features': p.FEATURES}))
        job = w.MacJob('/private/offline-integrated')
        job.state = {'features': p.FEATURES}
        self.assertEqual(job.final_hashes(), [p.AP_FINAL, p.CP_FL23])
        with patch.object(ntsc_module, 'run') as run:
            job.module('session', 'installer', 2)
            run.assert_called_once_with(job, 'session', w.core, 'installer', 2, profile=p.COMBINED)
        with patch.object(w.app.Job, 'files') as files:
            job.files('session', w.core.ORIGINAL)
            files.assert_called_once_with('session', w.core.ORIGINAL)
        for reverse in (False, True):
            with self.assertRaises(ValueError):
                p.patch_ap(b'unknown', reverse)

    def test_upgrade_guard_forbids_setting_writes_full_installer_and_reverse_requests(self):
        import json
        session = MagicMock()
        guarded = u.UpgradeSession(session)
        for action in (lambda: guarded.cp_write(1,b'x'), lambda: guarded.ap_write(1,b'x'),
                       lambda: guarded.call(session.camera.saveBackup,0x02cf),
                       lambda: guarded.temp('libJit.so',(p.ASSETS/'integrated-installer.so').read_bytes()),
                       lambda: guarded.temp('a7c2.install.request',struct.pack('<8sII',b'A7INS001',2,0)),
                       lambda: guarded.temp('a7c2.install.result',b'fake'),
                       lambda: guarded.read('/unapproved')):
            with self.assertRaises(ValueError):action()
        session.temp.assert_not_called()
        session.call.assert_not_called()
        job = u.UpgradeJob('/private/offline')
        with self.assertRaises(ValueError):job.module(guarded,'installer',1)

    def test_upgrade_requires_battery_cold_start_and_explicit_experimental_flag_before_lock(self):
        for changes in ({'battery_percent':49}, {'cold_start_confirmed':False},
                        {'accept_experimental_upgrade':False}):
            args=SimpleNamespace(action='upgrade',battery_percent=80,cold_start_confirmed=True,
                                 accept_experimental_upgrade=True)
            for name,value in changes.items():setattr(args,name,value)
            with patch.object(cli.platform,'system',return_value='Darwin'), \
                 patch.object(cli.os,'geteuid',return_value=0), patch.object(cli,'camera_lock') as lock:
                with self.assertRaises(ValueError):cli.run_camera(args)
                lock.assert_not_called()

    def simulate(self, failure=None):
        with tempfile.TemporaryDirectory() as directory:
            job=u.UpgradeJob(directory)
            (job.root/'original').mkdir();(job.root/'evidence').mkdir()
            (job.root/'original/appFw.so.bin').write_bytes(b'stock')
            job.state={'phase':'upgrade_prepared','features':p.FEATURES,'identity':{},
                       'upgrade_source_hashes':w.core.FINAL,'journal':[]}
            session=MagicMock();session.__enter__.return_value=session
            events=[]
            def native(s,name,mode=None):
                events.append((name,mode))
                if failure=='wbi_after' and (name,mode)==('wbi',1):raise TimeoutError('unknown WBI')
                return {'spans':[{'offset':0,'length':2,'memory':'4150','disk': '4241' if failure=='runtime' else '4150'}]}
            empty=[{},{}]
            with patch.object(job,'validate_candidates'), patch.object(u,'MacSession',return_value=session), \
                 patch.object(u,'UpgradeSession',side_effect=lambda s,*a:s), \
                 patch.object(u.app.Job,'files',side_effect=ValueError('wrong AP') if failure=='source_hash' else None) as source_files, \
                 patch.object(job,'files',side_effect=ValueError('wrong postwrite AP') if failure=='posthash' else None) as after_files, \
                 patch.object(job,'snapshot',return_value=[empty,empty]), \
                 patch.object(w.core,'verify_preservation',side_effect=ValueError('unexpected setting') if failure=='settings' else None,return_value={}), \
                 patch.object(job,'module',side_effect=native), \
                 patch.object(w.core,'patch_program',return_value=b'AP'),contextlib.redirect_stdout(io.StringIO()):
                if failure:
                    with self.assertRaises((ValueError,TimeoutError)):job.upgrade(80)
                else:
                    job.upgrade(80)
                    self.assertEqual(job.state['phase'],'installed_pending_cold_start')
                    source_files.assert_called_once_with(job,session,w.core.FINAL)
                    after_files.assert_called_once_with(session,[p.AP_FINAL,p.CP_FL23])
                if failure in ('runtime','source_hash'):
                    self.assertFalse((job.root/u.ATTEMPT).exists())
                    self.assertNotIn(('upgrade',1),events)
                    self.assertNotIn(('wbi',1),events)
                else:
                    self.assertTrue((job.root/u.ATTEMPT).exists())
                    self.assertIn(('upgrade',1),events)
                if failure in ('settings','posthash'):
                    self.assertNotIn(('wbi',1),events)
                session.cp_write.assert_not_called();session.ap_write.assert_not_called()
                session.camera.saveBackup.assert_not_called()
            return events

    def test_upgrade_checks_old_source_before_writing_and_keeps_settings(self):
        self.assertEqual(self.simulate(), [('runtime',None),('upgrade',0),('wbi',0),('upgrade',1),('wbi',1)])

    def test_wrong_source_or_runtime_never_writes(self):
        for failure in ('source_hash','runtime'):self.simulate(failure)

    def test_unknown_wbi_result_keeps_attempt_marker(self):
        self.simulate('wbi_after')

    def test_postwrite_hash_or_setting_failure_never_drops_snapshot(self):
        for failure in ('posthash','settings'):self.simulate(failure)

    def test_existing_attempt_rejected_before_camera(self):
        with tempfile.TemporaryDirectory() as directory:
            job=u.UpgradeJob(directory)
            job.state={'phase':'upgrade_prepared','features':p.FEATURES,'upgrade_source_hashes':w.core.FINAL}
            (job.root/u.ATTEMPT).write_text('preserve')
            with patch.object(job,'validate_candidates'),patch.object(u,'MacSession') as camera:
                with self.assertRaises(ValueError):job.upgrade(80)
                camera.assert_not_called()
            self.assertEqual((job.root/u.ATTEMPT).read_text(),'preserve')

    def test_prepare_upgrade_is_offline_command_and_cannot_be_repeated_install(self):
        args=cli.parser().parse_args(['prepare-upgrade','--source-workspace','/private/source',
                                      '--workspace','/private/new'])
        self.assertEqual(args.action,'prepare-upgrade')
        job=MagicMock(state={'phase':'upgrade_prepared'})
        with self.assertRaises(ValueError):cli.check_phase(SimpleNamespace(action='install'),job)
        cli.check_phase(SimpleNamespace(action='upgrade'),job)

    def test_both_new_install_choices_persist_before_preflight(self):
        from test_workflow import WorkflowTests
        for option, selected in ((False,p.BASE),(True,p.COMBINED)):
            session, records=WorkflowTests().fixture()
            with tempfile.TemporaryDirectory() as directory:
                job=w.MacJob(Path(directory)/'work')
                def native(s,name,mode=None):
                    self.assertEqual(job.state['features'],selected.FEATURES)
                    self.assertEqual(len(job.state['backup_files']),8)
                with patch.object(w,'MacSession',return_value=session), \
                     patch.object(w.core,'backup_records',return_value=records), \
                     patch.object(w.core,'ORIGINAL',[w.core.sha(b'AP'),w.core.sha(b'CP')]), \
                     patch.object(w.core,'patch_program',side_effect=lambda raw,chip,reverse=False:raw), \
                     patch.object(p.Profile,'patch_ap',side_effect=lambda raw,reverse=False:raw), \
                     patch.object(job,'module',side_effect=native),contextlib.redirect_stdout(io.StringIO()):
                    job.prepare(disable_ntsc_warning=option)
                    job.validate_candidates()
                self.assertEqual(job.state['phase'],'prepared')

    def test_plain_fl23_has_mr_fix_seven_spans_and_41_window_installer(self):
        job=w.MacJob('/private/offline-base');job.state={'features':p.BASE.FEATURES}
        self.assertFalse(ntsc.enabled(job.state))
        self.assertEqual(job.final_hashes(),[p.BASE.AP_FINAL,p.CP_FL23])
        self.assertEqual(p.BASE.PATCHES,[p.MR])
        self.assertEqual(len(p.BASE.SPANS),7)
        for name,count in (('installer',41),('upgrade',1)):
            raw=self.report(name,1)
            struct.pack_into('<I',raw,20,count)
            raw[176:208]=bytes.fromhex(p.BASE.AP_FINAL)
            p.BASE.parse_native(w.core,name,raw,1)
            raw[176:208]=bytes.fromhex(p.COMBINED.AP_FINAL)
            with self.assertRaises(ValueError):p.BASE.parse_native(w.core,name,raw,1)

    def test_restored_new_profiles_verify_stock_instead_of_candidate(self):
        for selected in (p.BASE,p.COMBINED):
            job=w.MacJob('/private/offline-restore')
            job.state={'phase':'restored_pending_cold_start','features':selected.FEATURES,'identity':{}}
            with patch.object(job,'load'),patch.object(job,'verify_restored') as restored:
                job.verify();restored.assert_called_once_with()
            cli.check_phase(SimpleNamespace(action='verify'),MagicMock(state=job.state))

    def test_upgrade_preparation_cannot_nest_inside_original_directory(self):
        for a,b in (('/private/a','/private/a'),('/private/a','/private/a/b'),('/private/a/b','/private/a')):
            with patch.object(u,'MacJob') as job:
                with self.assertRaises(ValueError):u.prepare_upgrade(a,b)
                job.assert_not_called()

    def test_cold_start_checks_saved_direct_and_original_target_attributes(self):
        from copy import deepcopy
        for restore in (False, True):
            cp={k:b'original' for k in w.core.CP_KEYS};merged={k:b'new' for k in w.core.CP_KEYS}
            desired=cp if restore else merged
            ap=dict(zip(w.core.AP_KEYS,(b'\0',b'\0') if restore else (b'\x0a',b'\x0b')))
            good=[{k:SimpleNamespace(data=v,attr=2) for k,v in ap.items()},
                  {k:SimpleNamespace(data=v,attr=2) for k,v in desired.items()}]
            for fault in (None,'stored','direct','attribute'):
                saved=deepcopy(good)
                if fault=='stored':saved[1][w.core.CP_KEYS[0]].data=b'wrong'
                if fault=='attribute':saved[0][w.core.AP_KEYS[0]].attr=3
                session=MagicMock()
                session.call.side_effect=lambda fn,*args:ap[args[0]] if fn==session.camera.readBackup else b'records'
                session.cp_read.side_effect=lambda key:b'wrong' if fault=='direct' else desired[key]
                with tempfile.TemporaryDirectory() as directory:
                    job=w.MacJob(directory);(job.root/'original').mkdir()
                    (job.root/'original/backup.private.bin').write_bytes(b'original')
                    with patch.object(job,'units',return_value=(cp,merged)), \
                         patch.object(w.core,'backup_records',side_effect=[good,saved,good]):
                        if fault:
                            with self.assertRaises(ValueError):job.verify_saved_targets(session,restore)
                        else:job.verify_saved_targets(session,restore)


if __name__=='__main__':unittest.main()
