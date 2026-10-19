"""Automatic upgrade completion and strict settings guards; no device access."""
import contextlib
from copy import deepcopy
import io
import math
import tempfile
from types import SimpleNamespace as NS
import unittest
from unittest.mock import MagicMock, patch

from macfl23 import integrated, upgrade as u


def snapshots(delta=60):
    live = [{u.LIVE_TIME_KEY: NS(data=(1200).to_bytes(4, 'little'), attr=2),
             3: NS(data=b'keep', attr=2),
             0x01070000: NS(data=(10).to_bytes(8, 'little'), attr=64)},
            {4: NS(data=b'cp-keep', attr=2)}]
    before = [live, deepcopy(live)]
    after = deepcopy(before)
    after[0][0][u.LIVE_TIME_KEY].data = (1200 + delta).to_bytes(4, 'little')
    return before, after


class UpgradePreservationTests(unittest.TestCase):
    def test_observed_time_changes_pass_without_global_whitelist_or_input_mutation(self):
        for delta, elapsed in ((0, 48), (60, 48.561), (660, 658.082), (1080, 1070)):
            with self.subTest(delta=delta):
                before, after = snapshots(delta)
                original = deepcopy((before, after))
                result = u.verify_upgrade_preservation(before, after, elapsed)
                self.assertEqual((before, after), original)
                self.assertEqual(result[1], {'preserved_properties': 4, 'runtime_counters': []})
                if delta:
                    self.assertEqual(result[0]['reviewed_live_time_field']['delta'], delta)
                    self.assertFalse(u.core.runtime_counter(0, u.LIVE_TIME_KEY,
                                     before[0][0][u.LIVE_TIME_KEY], after[0][0][u.LIVE_TIME_KEY]))
                    with self.assertRaises(ValueError):u.core.verify_preservation(before[0], after[0], {})

    def test_bad_time_field_attributes_sizes_deltas_and_clock_are_rejected(self):
        for delta, elapsed in ((-60, 48), (59, 48), (61, 48), (660, 400),
                               (60, -1), (60, 86401), (60, math.nan), (60, math.inf)):
            before, after = snapshots(delta)
            with self.subTest(delta=delta, elapsed=elapsed), self.assertRaises(ValueError):
                u.verify_upgrade_preservation(before, after, elapsed)
        for problem in ('attr', 'length', 'missing', 'extra', 'other_live', 'cp_same_key'):
            before, after = snapshots()
            if problem == 'attr':after[0][0][u.LIVE_TIME_KEY].attr = 8
            if problem == 'length':after[0][0][u.LIVE_TIME_KEY].data += b'\0'
            if problem == 'missing':del after[0][0][u.LIVE_TIME_KEY]
            if problem == 'extra':after[0][0][99] = NS(data=b'new', attr=2)
            if problem == 'other_live':after[0][0][3].data = b'changed'
            if problem == 'cp_same_key':
                before[0][1][u.LIVE_TIME_KEY] = deepcopy(before[0][0][u.LIVE_TIME_KEY])
                after[0][1][u.LIVE_TIME_KEY] = deepcopy(after[0][0][u.LIVE_TIME_KEY])
            with self.subTest(problem=problem), self.assertRaises(ValueError):
                u.verify_upgrade_preservation(before, after, 48)

    def test_stored_records_are_exact_even_for_previously_allowed_counters(self):
        for problem in ('time', 'counter', 'attr', 'cp', 'missing', 'extra'):
            before, after = snapshots()
            if problem == 'time':after[1][0][u.LIVE_TIME_KEY].data = after[0][0][u.LIVE_TIME_KEY].data
            if problem == 'counter':
                after[1][0][0x01070000].data = (11).to_bytes(8, 'little')
                self.assertTrue(u.core.runtime_counter(0, 0x01070000,
                                before[1][0][0x01070000], after[1][0][0x01070000]))
            if problem == 'attr':after[1][0][3].attr = 8
            if problem == 'cp':after[1][1][4].data = b'changed'
            if problem == 'missing':del after[1][1][4]
            if problem == 'extra':after[1][0][99] = NS(data=b'new', attr=2)
            with self.subTest(problem=problem), self.assertRaises(ValueError):
                u.verify_upgrade_preservation(before, after, 48)

    def test_both_profiles_continue_once_to_wbi_only_after_all_settings_checks_pass(self):
        for profile in (integrated.BASE, integrated.COMBINED):
            for problem in (None, 'stored', 'live'):
                with self.subTest(profile=profile.PREFIX, problem=problem), tempfile.TemporaryDirectory() as d:
                    job = u.UpgradeJob(d)
                    (job.root / 'original').mkdir();(job.root / 'evidence').mkdir()
                    (job.root / 'original/appFw.so.bin').write_bytes(b'stock')
                    job.state = {'phase': 'upgrade_prepared', 'features': profile.FEATURES, 'identity': {},
                                 'upgrade_source_hashes': u.core.FINAL, 'journal': []}
                    before, after = snapshots()
                    if problem == 'stored':after[1][0][3].data = b'changed'
                    if problem == 'live':after[0][0][3].data = b'changed'
                    session = MagicMock();session.__enter__.return_value = session
                    events = []
                    def native(s, name, mode=None):
                        events.append((name, mode))
                        if (name, mode) == ('wbi', 1):
                            self.assertEqual(job.state['phase'], 'upgrade_settings_verified')
                            self.assertEqual(job.state['journal'][-1]['step'], 'upgrade_settings_unchanged_verified')
                            self.assertEqual(job.state['journal'][-1]['live']['reviewed_live_time_field']['delta'], 60)
                        return {'spans': [{'offset': 0, 'length': 2, 'memory': '4150', 'disk': '4150'}]}
                    with patch.object(job, 'validate_candidates'), patch.object(u, 'MacSession', return_value=session) as camera, \
                         patch.object(u, 'UpgradeSession', side_effect=lambda s,*a:s), \
                         patch.object(u.app.Job, 'files'), patch.object(job, 'files') as files, \
                         patch.object(job, 'snapshot', side_effect=[before, after]), \
                         patch.object(job, 'module', side_effect=native), \
                         patch.object(u.core, 'patch_program', return_value=b'AP'), \
                         patch.object(u, 'monotonic', side_effect=[100, 148.561]), contextlib.redirect_stdout(io.StringIO()):
                        if problem:
                            with self.assertRaises(ValueError):job.upgrade(91)
                            self.assertNotIn(('wbi', 1), events)
                        else:
                            job.upgrade(91)
                            self.assertEqual(job.state['phase'], 'installed_pending_cold_start')
                            self.assertEqual(events.count(('wbi', 1)), 1)
                            files.assert_called_once_with(session, [profile.AP_FINAL, profile.CP_FL23])
                        self.assertEqual(events.count(('upgrade', 1)), 1)
                        self.assertTrue((job.root / u.ATTEMPT).is_file())
                        camera.reset_mock()
                        with self.assertRaises(ValueError):job.upgrade(91)
                        camera.assert_not_called()
                        session.cp_write.assert_not_called();session.ap_write.assert_not_called()
                        session.camera.saveBackup.assert_not_called()
