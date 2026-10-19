"""Offline guards for the distinct, explicitly reviewed upgrade stop."""
import copy
import contextlib
import json
import io
from pathlib import Path
import struct
import tempfile
from types import SimpleNamespace as NS
import unittest
from unittest.mock import MagicMock, patch

from macfl23 import finish_upgrade as f, integrated, cli, session as sm


def records():
    return [{f.KEY: NS(attr=2, data=(120).to_bytes(4, 'little')), 3: NS(attr=2, data=b'keep')}, {}]


class FinishUpgradeTests(unittest.TestCase):
    def test_time_bounded_exception_requires_explicit_scope_and_preserves_historical_rule(self):
        before=records()
        for delta,elapsed,allowed in ((660,658,True),(1200,1201,True),(660,400,False),
                                     (661,660,False),(-60,60,False),(60,-1,False),(86460,86460,False)):
            with self.subTest(delta=delta,elapsed=elapsed):
                after=copy.deepcopy(before)
                after[0][f.KEY].data=(120+delta).to_bytes(4,'little')
                if allowed:
                    self.assertEqual(f.compare_records(before,after,live=True,elapsed_seconds=elapsed),delta)
                else:
                    with self.assertRaises(ValueError):f.compare_records(before,after,live=True,elapsed_seconds=elapsed)
                if delta!=60:
                    with self.assertRaises(ValueError):f.compare_records(before,after,live=True)
                    with self.assertRaises(ValueError):f.compare_records(before,after,live=True,historical=True,elapsed_seconds=elapsed)
                with self.assertRaises(ValueError):f.compare_records(before,after,elapsed_seconds=elapsed)
        after=copy.deepcopy(before);after[0][f.KEY].data=(780).to_bytes(4,'little')
        for elapsed in (-1,86401,float('nan')):
            with self.assertRaises(ValueError):f.compare_records(before,before,live=True,elapsed_seconds=elapsed)
        for problem in ('attr','length','other','missing'):
            bad=copy.deepcopy(after)
            if problem=='attr':bad[0][f.KEY].attr=8
            if problem=='length':bad[0][f.KEY].data+=b'\0'
            if problem=='other':bad[0][3].data=b'other'
            if problem=='missing':del bad[0][3]
            with self.assertRaises(ValueError):f.compare_records(before,bad,live=True,elapsed_seconds=658)

    def test_cli_keeps_time_field_exception_opt_in(self):
        argv=['finish-upgrade','--workspace','/private/offline','--battery-percent','91','--accept-reviewed-upgrade-finish']
        for flag in (False,True):
            args=cli.parser().parse_args(argv+(['--accept-unconfirmed-live-time-field'] if flag else []))
            with patch.object(cli.platform,'system',return_value='Darwin'),patch.object(cli.os,'geteuid',return_value=0), \
                 patch.object(cli,'private_path',return_value=Path('/private/offline')), \
                 patch.object(f,'run_finish_upgrade') as run:
                cli.run_camera(args)
                run.assert_called_once_with(Path('/private/offline'),accept_live_time_field=flag)

    def test_live_exception_is_scoped_and_saved_settings_must_be_exact(self):
        before=records();after=copy.deepcopy(before)
        after[0][f.KEY].data=(180).to_bytes(4,'little')
        self.assertEqual(f.compare_records(before,after,live=True,historical=True),60)
        with self.assertRaises(ValueError):f.compare_records(before,after)
        for delta in (-60,0,59,61,120,3600,3660):
            changed=copy.deepcopy(before);changed[0][f.KEY].data=(120+delta).to_bytes(4,'little')
            if delta == 0:f.compare_records(before,changed,live=True)
            else:
                with self.assertRaises(ValueError):f.compare_records(before,changed,live=True)
            with self.assertRaises(ValueError):f.compare_records(before,changed,live=True,historical=True)
        after[0][3].data=b'other'
        with self.assertRaises(ValueError):f.compare_records(before,after,live=True,historical=True)
        self.assertFalse(f.core.runtime_counter(0,f.KEY,before[0][f.KEY],after[0][f.KEY]))

    def test_guard_uses_new_runtime_and_cannot_upload_upgrade_or_installer(self):
        raw=MagicMock();session=f.SnapshotSession(raw,integrated.COMBINED)
        for name in ('integrated-installer.so','integrated-upgrade.so'):
            with self.assertRaises(ValueError):
                session.temp('libJit.so',(integrated.ASSETS/name).read_bytes())
        with self.assertRaises(ValueError):session.cp_write(1,b'bad')
        with self.assertRaises(ValueError):session.ap_write(1,b'bad')
        with self.assertRaises(ValueError):session.call(raw.camera.saveBackup,0x02cf)
        with self.assertRaises(ValueError):session.temp('a7c2.install.request',b'bad')
        session.temp('libJit.so',(integrated.ASSETS/'integrated-runtime.so').read_bytes())
        session.wbi_mode=0
        with self.assertRaises(ValueError):session.temp('a7c2.wbi.request',struct.pack('<8sII',b'A7WBI001',1,0))

    def test_no_wbi_before_fresh_check_and_marker_precedes_wbi_write(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);source=MagicMock(root=root,state={'features':integrated.FEATURES,'phase':'needs_review'})
            job=f.SnapshotJob(root,source,records(),records())
            with self.assertRaises(ValueError):job.complete_snapshot(MagicMock(),{})
            self.assertFalse((root/f.MARKER).exists())
            job.wbi_permitted=job.wbi_preflight_verified=True
            def run(session,name,mode):
                self.assertTrue((root/f.MARKER).is_file())
                source.save.assert_called_with('finishing_upgrade_snapshot')
                self.assertEqual((name,mode),('wbi',1))
            with patch.object(job,'module',side_effect=run),patch.object(job,'audit'):
                job.complete_snapshot(MagicMock(),{'reviewed':True})
            self.assertTrue(job.wbi_completed)
            with self.assertRaises(FileExistsError):job.complete_snapshot(MagicMock(),{})

    def test_cli_requires_explicit_new_flag_and_real_battery_before_operation(self):
        args=cli.parser().parse_args(['finish-upgrade','--workspace','/private/offline','--battery-percent','80'])
        with patch.object(cli.platform,'system',return_value='Darwin'),patch.object(cli.os,'geteuid',return_value=0), \
             patch.object(cli,'private_path',return_value=Path('/private/offline')), \
             patch.object(f,'run_finish_upgrade') as run:
            with self.assertRaises(ValueError):cli.run_camera(args)
            args.accept_reviewed_upgrade_finish=True;args.battery_percent=49
            with self.assertRaises(ValueError):cli.run_camera(args)
            run.assert_not_called()

    def test_original_attempt_and_wrong_phase_are_rejected_before_usb(self):
        for bad in ('features','phase','marker'):
            with tempfile.TemporaryDirectory() as d:
                root=Path(d)
                source=MagicMock(root=root)
                source.state={'features':integrated.FEATURES,'phase':'needs_review','failed_action':'upgrade',
                              'failed_during':'upgrade_programs_written','last_error':next(iter(f.ERRORS))}
                (root/'upgrade-attempt.private.json').write_text('original')
                if bad=='features':source.state['features']=['fl23']
                if bad=='phase':source.state['failed_during']='writing_upgrade_programs'
                if bad=='marker':(root/f.MARKER).write_text('preserve')
                with self.assertRaises(ValueError):f.review_history(source)

    def test_current_gate_rejects_mismatches_before_wbi(self):
        for problem in ('none','files','targets','stored','live','mode','backend','memory','disk','regions'):
            with self.subTest(problem=problem), tempfile.TemporaryDirectory() as d:
                root=Path(d)
                for name in ('original','candidate','evidence'):(root/name).mkdir()
                old,new=b'oooooo',b'nnnnnn'
                (root/'original/appFw.so.bin').write_bytes(old)
                (root/'candidate/appFw.so').write_bytes(new)
                profile=NS(AP_FINAL='ap',CP_FL23='cp',SPANS=[(0,2),(2,2)])
                source=MagicMock(root=root,state={'features':integrated.FEATURES,'phase':'needs_review'})
                job=f.SnapshotJob(root,source,records(),records());job.profile=profile
                session=MagicMock()
                session.call.side_effect=[b'live',b'stored']
                fresh=[records(),records()]
                if problem=='stored':fresh[1][0][3].data=b'changed'
                if problem=='live':fresh[0][0][f.KEY].data=(240).to_bytes(4,'little')
                if problem=='targets':source.verify_saved_targets.side_effect=ValueError('targets')
                raw=bytearray(56+256*2+240);at=56+256*2
                raw[at+16:at+22]=b'*wbi4\n' if problem!='backend' else b'wrong!'
                (root/'evidence/offline-runtime.bin').write_bytes(raw)
                runtime={'image_mode':'0' if problem=='mode' else '2','spans':[
                    {'offset':0,'length':2,'memory':old[:2].hex(),'disk':new[:2].hex()},
                    {'offset':2,'length':2,'memory':new[2:4].hex(),'disk':new[2:4].hex()}]}
                if problem in ('memory','disk'):runtime['spans'][0][problem]=b'xx'.hex()
                if problem=='regions':runtime['spans'].pop()
                with patch.object(f.app.Job,'files',side_effect=ValueError('files') if problem=='files' else None), \
                     patch.object(f.core,'backup_records',side_effect=fresh), \
                     patch.object(f.core,'patch_program',return_value=old), \
                     patch.object(job,'module',side_effect=[runtime,{}]) as module:
                    if problem=='none':
                        job.check_current(session)
                        self.assertTrue(job.wbi_preflight_verified)
                        self.assertEqual(module.call_args.args[1:],('wbi',0))
                    else:
                        with self.assertRaises(ValueError):job.check_current(session)
                        self.assertFalse(job.wbi_preflight_verified)
                        self.assertFalse(any(c.args[1]=='wbi' for c in module.call_args_list))

    def test_completion_states_preserve_failed_precheck_and_block_unknown_write_retry(self):
        for outcome in ('success','timeout','write_failure','precheck_failure'):
            with self.subTest(outcome=outcome),tempfile.TemporaryDirectory() as d:
                root=Path(d);source=f.MacJob(root)
                source.state={'phase':'needs_review','failed_action':'upgrade','failed_during':'upgrade_programs_written',
                              'journal':[],'identity':{'model':f.core.MODEL,'serial_sha256':'a'*64},
                              'features':integrated.FEATURES}
                source.save('needs_review');before=source.state_path.read_bytes()
                factory=MagicMock()
                def exit_session(*args):
                    factory.audit({'service_exit_request':'sent','normal_usb_returned':outcome!='timeout',
                                   'errors':['normal USB timeout'] if outcome=='timeout' else []})
                    if outcome=='timeout':raise sm.NormalReturnTimeout('normal USB timeout')
                    return False
                factory.return_value.__exit__.side_effect=exit_session
                def check(job,session):
                    if outcome=='precheck_failure':raise ValueError('current settings differ')
                    job.wbi_permitted=job.wbi_preflight_verified=True
                def module(job,session,name,mode):
                    self.assertEqual((name,mode),('wbi',1))
                    self.assertTrue((root/f.MARKER).exists())
                    self.assertEqual(json.loads(source.state_path.read_text())['phase'],'finishing_upgrade_snapshot')
                    if outcome=='write_failure':raise RuntimeError('unknown WBI result')
                with patch.object(f,'MacJob',return_value=source), \
                     patch.object(f,'review_history',return_value=({},records(),records())), \
                     patch.object(sm,'MacSession',factory),patch.object(cli,'camera_lock',contextlib.nullcontext), \
                     patch('subprocess.Popen',return_value=MagicMock()), \
                     patch.object(f.SnapshotJob,'check_current',check),patch.object(f.SnapshotJob,'module',module), \
                     contextlib.redirect_stdout(io.StringIO()):result=f.run_finish_upgrade(root)
                saved=json.loads(source.state_path.read_text())
                self.assertEqual(result,{'success':0,'timeout':2,'write_failure':1,'precheck_failure':1}[outcome])
                self.assertEqual(next(root.glob('upgrade-finish-*/source-state.private.json')).read_bytes(),before)
                if outcome=='precheck_failure':
                    self.assertEqual(source.state_path.read_bytes(),before)
                    self.assertFalse((root/f.MARKER).exists())
                elif outcome=='write_failure':
                    self.assertEqual((saved['phase'],saved['failed_action']),('needs_review','finish-upgrade'))
                    self.assertTrue((root/f.MARKER).exists())
                else:
                    self.assertEqual(saved['phase'],'installed_pending_cold_start'+('_usb_unconfirmed' if outcome=='timeout' else ''))


if __name__=='__main__':unittest.main()
