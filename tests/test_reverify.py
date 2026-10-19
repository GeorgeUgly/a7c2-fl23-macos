"""Only an audited old-memory verify failure can permit another read check."""
import copy
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace as NS
import unittest
from unittest.mock import patch
from macfl23 import integrated, workflow


class ReverifyTests(unittest.TestCase):
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
