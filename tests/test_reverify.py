"""Only an audited old-memory verify failure can permit another read check."""
import copy
import contextlib
import io
import json
import struct
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace as NS
import unittest
from unittest.mock import MagicMock, patch
from macfl23 import cli, integrated, session as sm, workflow


def legacy_fixture(root, explicit_features=False):
    for sub in ('original', 'candidate', 'evidence'):(root/sub).mkdir()
    (root/'original/appFw.so.bin').write_bytes(b'ssss')
    (root/'candidate/appFw.so').write_bytes(b'llll')
    path=root/'evidence/20261019-100001-000000-runtime.bin'
    path.write_bytes(b'legacy report')
    runtime={'spans':[{'offset':0,'length':2,'memory':b'ss'.hex(),'disk':b'll'.hex()}], 'image_mode':'0\n'}
    job=workflow.MacJob(root)
    job.state={'format':1,'model':workflow.core.MODEL,'version':'2.01','identity':{},
               'phase':'needs_review','failed_action':'verify','failed_during':'installed_pending_cold_start',
               'last_error':'ValueError: 執行期仍有舊資料；請確認已關機並取出電池後再開機',
               'journal':[{'step':'host_full_file_readback_verified','hashes':workflow.core.FINAL},
                   {'step':'native_about_to_run','module':'runtime','mode':None,'time':'2026-10-19T10:00:00'},
                   {'step':'native_verified','module':'runtime','mode':None,'result':copy.deepcopy(runtime)},
                   {'step':'mac_usb_session_exit','time':'2026-10-19T10:00:02',
                    'result':{'service_exit_request':'sent','normal_usb_returned':True,'errors':[]}}]}
    if explicit_features:job.state['features']=['fl23']
    return job,runtime,path


class ReverifyTests(unittest.TestCase):
    def test_legacy_without_features_and_simplified_or_traditional_error_can_be_reviewed_offline(self):
        for explicit in (False,True):
            for usb_suffix in ('','_usb_unconfirmed'):
                for simplified in (False,True):
                    with TemporaryDirectory() as d:
                        job,runtime,_=legacy_fixture(Path(d),explicit)
                        job.state['failed_during']+=usb_suffix
                        if explicit:
                            runtime['image_mode']='2\n'
                            job.state['journal'][2]['result']=copy.deepcopy(runtime)
                        if simplified:
                            job.state['last_error']='ValueError: 执行期仍有旧资料；请确认已关机并取出电池后再开机'
                        job.save('needs_review');before=job.state_path.read_bytes()
                        with patch.object(workflow.core,'parse_native',return_value=runtime) as parse, \
                             patch.object(workflow.core,'patch_program') as patch_program,patch.object(sm,'MacSession') as camera:
                            review=job.review_failed_verify_before_cold_start()
                            self.assertEqual(review['profile'],'legacy_fl23')
                            self.assertEqual(review['stale_regions'],1)
                            parse.assert_called_once_with('runtime',b'legacy report')
                            patch_program.assert_not_called();camera.assert_not_called()
                        self.assertEqual(job.state_path.read_bytes(),before)

    def test_legacy_mismatch_write_failure_and_incomplete_exit_never_allow_reverify(self):
        for problem in ('write_action','write_phase','wrong_error','unknown_features','old_ntsc','unknown_memory',
                        'wrong_disk','no_stale','report_mismatch','usb','cleanup','extra_native','no_full_readback',
                        'hash','missing_report','duplicate_report','nested_without_review'):
            with self.subTest(problem=problem),TemporaryDirectory() as d:
                job,runtime,path=legacy_fixture(Path(d));rows=job.state['journal']
                if problem=='write_action':job.state['failed_action']='install'
                if problem=='write_phase':job.state['failed_during']='writing_settings'
                if problem=='wrong_error':job.state['last_error']='ValueError: 重開後 CP 參數不符'
                if problem=='unknown_features':job.state['features']=['unknown']
                if problem=='old_ntsc':job.state['features']=['fl23','no_ntsc_notice']
                if problem=='unknown_memory':runtime['spans'][0]['memory']=b'xx'.hex()
                if problem=='wrong_disk':runtime['spans'][0]['disk']=b'xx'.hex()
                if problem=='no_stale':runtime['spans'][0]['memory']=b'll'.hex()
                if problem in ('unknown_memory','wrong_disk','no_stale'):rows[2]['result']=copy.deepcopy(runtime)
                if problem=='report_mismatch':runtime['image_mode']='2\n'
                if problem=='usb':rows[-1]['result']['normal_usb_returned']=False
                if problem=='cleanup':rows.insert(3,{'step':'temporary_cleanup_pending'})
                if problem=='extra_native':rows.insert(3,{'step':'native_about_to_run','module':'wbi','mode':1})
                if problem=='no_full_readback':rows.pop(0)
                if problem=='hash':rows[0]['hashes']=workflow.core.ORIGINAL
                if problem=='missing_report':path.unlink()
                if problem=='duplicate_report':(path.parent/'20261019-100001-500000-runtime.bin').write_bytes(b'duplicate')
                if problem=='nested_without_review':job.state['failed_during']='needs_review'
                with patch.object(workflow.core,'parse_native',return_value=runtime), \
                     patch.object(sm,'MacSession') as camera, self.assertRaises(ValueError):
                    job.review_failed_verify_before_cold_start()
                camera.assert_not_called()

    def test_issue6_four_current_regions_and_two_old_menu_flags_are_eligible(self):
        with TemporaryDirectory() as d:
            root=Path(d);job,_,path=legacy_fixture(root)
            spans=integrated.ORIGINAL_SPANS
            for target,value in ((root/'original/appFw.so.bin',b's'),(root/'candidate/appFw.so',b'l')):
                with target.open('wb') as stream:
                    stream.truncate(max(offset+n for offset,n in spans))
                    for offset,n in spans:stream.seek(offset);stream.write(value*n)
            raw=bytearray(1832)
            struct.pack_into('<8sIIqqqQII',raw,0,b'A7RUN001',1,6,0,0,123,0x100000,16,0)
            for i,(_,n) in enumerate(spans):
                raw[56+i*256:56+i*256+n]=(b's' if i in (2,3) else b'l')*n
                raw[184+i*256:184+i*256+n]=b'l'*n
            raw[1592:1594]=b'0\n'
            path.write_bytes(raw)
            job.state['journal'][2]['result']=workflow.core.parse_native('runtime',raw)
            self.assertEqual(job.review_failed_verify_before_cold_start()['stale_regions'],2)
            path.write_bytes(raw[:-1])
            with self.assertRaises(ValueError):job.review_failed_verify_before_cold_start()

    def test_both_new_profiles_keep_their_existing_review_path(self):
        for profile in (integrated.BASE,integrated.COMBINED):
            with TemporaryDirectory() as d:
                job,runtime,_=legacy_fixture(Path(d))
                job.state.update(features=profile.FEATURES,
                    last_error='ValueError: 运行状态与所选组合不符；保留状态，检查冷启动结果')
                job.state['journal'][0]['hashes']=[profile.AP_FINAL,integrated.CP_FL23]
                with patch.object(integrated.Profile,'parse_native',return_value=runtime) as parser, \
                     patch.object(workflow.core,'patch_program',return_value=b'ssss'), \
                     patch.object(workflow.core,'parse_native') as legacy_parser:
                    self.assertEqual(job.review_failed_verify_before_cold_start()['profile'],'integrated')
                    parser.assert_called_once();legacy_parser.assert_not_called()

    def test_prior_controlled_review_supports_old_nested_failure_without_faking_phase(self):
        with TemporaryDirectory() as d:
            job,runtime,_=legacy_fixture(Path(d));job.state['failed_during']='needs_review'
            job.state['journal'].insert(0,{'step':'explicit_read_only_reverify_after_cold_start','review':{
                'operation':'read_only_verify','previous_session_returned_normally':True,'stale_regions':1}})
            with patch.object(workflow.core,'parse_native',return_value=runtime):
                self.assertEqual(job.review_failed_verify_before_cold_start()['profile'],'legacy_fl23')
            self.assertEqual(job.state['failed_during'],'needs_review')
            job.state['journal'].insert(1,{'step':'unrelated_operation'})
            with self.assertRaises(ValueError):job.review_failed_verify_before_cold_start()
            job.state['journal'].pop(1)
            job.state['journal'][0]['review']['previous_session_returned_normally']=False
            with self.assertRaises(ValueError):job.review_failed_verify_before_cold_start()

    def test_reverify_failure_keeps_original_failure_stage_and_records_latest_error(self):
        with TemporaryDirectory() as d:
            job,_,_=legacy_fixture(Path(d));job.save('needs_review')
            old=copy.deepcopy(job.state['journal'])
            cli.save_failure(job,'verify',ValueError('still old'))
            self.assertEqual(job.state['failed_during'],'installed_pending_cold_start')
            self.assertEqual(job.state['last_error'],'ValueError: still old')
            self.assertEqual(job.state['journal'],old)
            cli.save_failure(job,'restore',ValueError('not a verify'))
            self.assertEqual(job.state['failed_during'],'needs_review')
            self.assertEqual(job.state['failed_action'],'restore')

    def test_legacy_reverify_uses_existing_read_only_verify_and_clears_failure_only_on_success(self):
        for success in (False,True):
            with self.subTest(success=success),TemporaryDirectory() as d:
                root=Path(d);job,runtime,_=legacy_fixture(root);job.save('needs_review')
                args=cli.parser().parse_args(['verify','--workspace',d,'--cold-start-confirmed'])
                current=[{k:NS(data=bytes([10+i]),attr=2) for i,k in enumerate(workflow.core.AP_KEYS)},
                         {k:NS(data=b'cp',attr=2) for k in workflow.core.CP_KEYS}]
                new=copy.deepcopy(runtime)
                if success:new['spans'][0]['memory']=new['spans'][0]['disk']
                factory=MagicMock();session=factory.return_value.__enter__.return_value
                def exit_session(*a):
                    factory.audit({'service_exit_request':'sent','normal_usb_returned':True,'errors':[]})
                    return False
                factory.return_value.__exit__.side_effect=exit_session
                with patch.object(cli.platform,'system',return_value='Darwin'),patch.object(cli.os,'geteuid',return_value=0), \
                     patch.object(cli,'private_path',return_value=root),patch.object(cli,'camera_lock',contextlib.nullcontext), \
                     patch.object(cli,'return_ownership'),patch.object(workflow,'MacJob',return_value=job), \
                     patch.object(job,'validate_candidates'),patch.object(job,'load'),patch.object(job,'files') as files, \
                     patch.object(job,'units',return_value=({},dict.fromkeys(workflow.core.CP_KEYS,b'cp'))), \
                     patch.object(job,'module',return_value=new) as module, \
                     patch.object(workflow.core,'parse_native',return_value=runtime), \
                     patch.object(workflow.core,'patch_program',return_value=b'llll'), \
                     patch.object(workflow.core,'backup_records',return_value=current), \
                     patch.object(sm,'MacSession',factory),patch('subprocess.Popen',return_value=MagicMock()), \
                     contextlib.redirect_stdout(io.StringIO()):
                    if success:self.assertEqual(cli.run_camera(args),0)
                    else:
                        with self.assertRaises(ValueError):cli.run_camera(args)
                saved=json.loads(job.state_path.read_text())
                self.assertEqual(saved['phase'],'verified_after_cold_start' if success else 'needs_review')
                self.assertEqual(saved['failed_action'],None if success else 'verify')
                self.assertEqual(saved['failed_during'],None if success else 'installed_pending_cold_start')
                module.assert_called_once_with(session,'runtime')
                files.assert_called_once_with(session,workflow.core.FINAL)
                session.cp_write.assert_not_called();session.ap_write.assert_not_called()
                session.camera.saveBackup.assert_not_called()
                for action in ('install','restore','upgrade'):
                    blocked=MagicMock(state={'phase':'needs_review'})
                    with self.assertRaises(ValueError):cli.check_phase(NS(action=action),blocked)

    def test_only_known_old_memory_and_normal_read_only_exit_are_eligible(self):
        for problem in ('none','unknown_memory','wrong_disk','usb','action','phase','extra_native','report'):
            with self.subTest(problem=problem),TemporaryDirectory() as d:
                root=Path(d)
                for sub in ('original','candidate','evidence'):(root/sub).mkdir()
                (root/'original/appFw.so.bin').write_bytes(b'oooo')
                (root/'candidate/appFw.so').write_bytes(b'nnnn')
                (root/'evidence/20261019-100001-000000-runtime.bin').write_bytes(b'fixture')
                runtime={'spans':[{'offset':0,'length':2,'memory':b'oo'.hex(),'disk':b'nn'.hex()}], 'image_mode':'0\n'}
                journal=[{'step':'host_full_file_readback_verified','hashes':[integrated.AP_FINAL,integrated.CP_FL23]},
                         {'step':'native_about_to_run','module':'runtime','mode':None,'time':'2026-10-19T10:00:00'},
                         {'step':'native_verified','module':'runtime','mode':None,'result':copy.deepcopy(runtime)},
                         {'step':'mac_usb_session_exit','time':'2026-10-19T10:00:02','result':{
                             'service_exit_request':'sent','normal_usb_returned':True,'errors':[]}}]
                job=workflow.MacJob(root)
                job.state={'features':integrated.FEATURES,'phase':'needs_review','failed_action':'verify',
                           'failed_during':'installed_pending_cold_start',
                           'last_error':'ValueError: 运行状态与所选组合不符；保留状态，检查冷启动结果','journal':journal}
                if problem=='unknown_memory':runtime['spans'][0]['memory']=b'xx'.hex()
                if problem=='wrong_disk':runtime['spans'][0]['disk']=b'xx'.hex()
                if problem=='usb':journal[-1]['result']['normal_usb_returned']=False
                if problem=='action':job.state['failed_action']='upgrade'
                if problem=='phase':job.state['failed_during']='writing_settings'
                if problem=='extra_native':journal.insert(2,{'step':'native_about_to_run','module':'wbi','mode':1})
                if problem=='report':runtime['image_mode']='2\n'
                if problem in ('unknown_memory','wrong_disk'):journal[2]['result']=copy.deepcopy(runtime)
                with patch.object(integrated.Profile,'parse_native',return_value=runtime), \
                     patch.object(workflow.core,'patch_program',return_value=b'oooo'):
                    if problem=='none':
                        self.assertEqual(job.review_failed_verify_before_cold_start()['stale_regions'],1)
                        self.assertEqual(job.state['phase'],'needs_review')
                    else:
                        with self.assertRaises(ValueError):job.review_failed_verify_before_cold_start()
