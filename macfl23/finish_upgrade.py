"""Explicit WBI-only completion for an audited, saved-settings-unchanged upgrade.

This is not a firmware retry or a global counter exception. Historical +60,
unchanged saved settings, exact native reports and a fresh current check are
required. The meaning of the reviewed live field is still unconfirmed.
"""
from datetime import datetime
import json
from pathlib import Path

from . import integrated
from .session import app, core
from .workflow import MacJob, write_new
from .finish import FinishSession, FinishJob, require, evidence_file

KEY = 0x02770015
MARKER = 'upgrade-finish-attempt.private.json'
ERRORS = {'ValueError: 發現額外設定變動：chip0/0x2770015，請保存紀錄後檢查',
          'ValueError: 发现额外设置变动：chip0/0x2770015，请保存记录后检查'}


def compare_records(before, after, live=False, historical=False, elapsed_seconds=None):
    """Saved records must be exact. Scope live review to the observed field."""
    if live and not historical and elapsed_seconds is not None:
        require(0 <= elapsed_seconds <= 86400, '专项时间参考无效或超过24小时')
    reviewed = None
    for chip in (0, 1):
        require(set(before[chip]) == set(after[chip]), '审查键集合发生变化')
        for key, old in before[chip].items():
            new = after[chip][key]
            if old.data == new.data and old.attr == new.attr:
                continue
            if live and core.runtime_counter(chip, key, old, new):
                continue
            if live and chip == 0 and key == KEY:
                require(old.attr == new.attr == 2 and len(old.data) == len(new.data) == 4,
                        '审查字段属性或长度不匹配')
                delta = int.from_bytes(new.data, 'little') - int.from_bytes(old.data, 'little')
                if historical or elapsed_seconds is None:
                    require(delta == 60, '审查字段变化不是固定+60；不扩大审查例外')
                else:
                    require(delta > 0 and delta % 60 == 0
                            and abs(delta - elapsed_seconds) <= 120,
                            '专项字段增量与经过时间不符；不执行快照收尾')
                reviewed = delta
                continue
            raise ValueError('存在未放行的设置变化；不执行快照收尾')
    if historical:
        require(reviewed == 60, '历史证据没有唯一的固定+60差异')
    return reviewed


def review_history(source):
    source.validate_candidates()
    state = source.state
    identity = state.get('identity', {})
    require(identity.get('model') == core.MODEL and len(identity.get('serial_sha256', '')) == 64,
            '原始机身身份无效')
    require(integrated.enabled(state), '仅适用于两类新版的追加升级停止点')
    require((state.get('phase'), state.get('failed_action'), state.get('failed_during')) ==
            ('needs_review', 'upgrade', 'upgrade_programs_written'), '失败阶段不匹配')
    require(state.get('last_error') in ERRORS, '失败错误不匹配')
    require((source.root / 'upgrade-attempt.private.json').is_file(), '缺少原升级单次标记')
    require(not (source.root / MARKER).exists(), '已有快照收尾尝试；不允许重试')
    for previous in source.root.glob('upgrade-finish-*'):
        if not previous.is_dir():
            continue
        summary = json.loads((previous / 'finish-summary.json').read_text())
        require(summary.get('wbi_write_attempted') is False and summary.get('wbi_completed') is False
                and summary.get('source_state_unchanged') is True and summary.get('normal_usb_returned') is True,
                '此前收尾检查结果未知或已尝试WBI；不继续')
    rows = state.get('journal', [])
    starts = [i for i, r in enumerate(rows) if r.get('step') == 'native_about_to_run'
              and (r.get('module'), r.get('mode')) == ('upgrade', 1)]
    require(len(starts) == 1, '升级写入开始记录不唯一')
    index = starts[0]
    tail = rows[index:]
    require([(r.get('module'), r.get('mode')) for r in tail if r.get('step') == 'native_about_to_run']
            == [('upgrade', 1)], '写入后存在其他原生操作尝试')
    verified = [r for r in tail if r.get('step') == 'native_verified']
    profile = integrated.select(state)
    require(len(verified) == 1 and (verified[0].get('module'), verified[0].get('mode')) == ('upgrade', 1),
            '缺少唯一升级完成报告')
    require(any(r.get('step') == 'host_full_file_readback_verified'
                and r.get('hashes') == [profile.AP_FINAL, profile.CP_FL23] for r in tail), '缺少完整目标读回记录')
    require(tail[-1].get('step') == 'mac_usb_session_exit' and
            tail[-1].get('result') == {'service_exit_request': 'sent', 'normal_usb_returned': True, 'errors': []},
            '原失败会话没有正常退出并返回MSC')
    require(not any(r.get('step') in ('temporary_cleanup_pending', 'upgrade_settings_unchanged_verified') for r in rows),
            '原证据与此停止点不一致')
    require(not any(r.get('step') == 'native_about_to_run' and r.get('module') == 'wbi' and r.get('mode') == 1 for r in rows),
            '已有WBI写入尝试')
    begin = datetime.fromisoformat(rows[0]['time'])
    end = datetime.fromisoformat(tail[-1]['time'])
    native = evidence_file(source.root, 'upgrade.bin', datetime.fromisoformat(tail[0]['time']), end)
    parsed = profile.parse_native(core, 'upgrade', native.read_bytes(), 1)
    require(parsed == verified[0].get('result'), '二进制升级报告与审计不符')
    paths = [evidence_file(source.root, label + '.private.bin', begin, end) for label in
             ('upgrade-before-live', 'upgrade-before-stored', 'upgrade-after-live', 'upgrade-after-stored')]
    records = [core.backup_records(p.read_bytes()) for p in paths]
    compare_records(records[0], records[2], live=True, historical=True)
    compare_records(records[1], records[3])
    # Direct/live/stored target checks completed in snapshot() before failure;
    # independently recheck their archived current and saved values here.
    original = core.backup_records((source.root / 'original/backup.private.bin').read_bytes())
    _, merged = source.units()
    for chip, targets in ((0, dict(zip(core.AP_KEYS, (b'\x0a', b'\x0b')))), (1, merged)):
        for key, value in targets.items():
            require(all(rec[chip][key].data == value and rec[chip][key].attr == original[chip][key].attr
                        for rec in records), '历史四项目标值或属性不符')
    # Actual readback files, not only JSON claims, must bind both old and new.
    for chip, name in enumerate(core.PROGRAMS):
        reads = sorted((source.root / 'readback').glob('*-' + name))
        require(len(reads) == 2, '原升级完整读回文件不唯一')
        require(core.sha(reads[0].read_bytes()) == core.FINAL[chip] and
                core.sha(reads[1].read_bytes()) == source.final_hashes()[chip], '原升级完整读回指纹不符')
    reviewed = {'scoped_live_key': '0x02770015', 'historical_delta': 60,
                'live_reference_time': datetime.strptime(paths[2].name[:22], '%Y%m%d-%H%M%S-%f').isoformat(),
                'saved_settings_unchanged': True, 'no_wbi_write_attempt': True,
                'evidence_hashes': {p.name: core.sha(p.read_bytes()) for p in [native, *paths]}}
    return reviewed, records[2], records[3]


class SnapshotSession(FinishSession):
    def __init__(self, session, profile):
        super().__init__(session)
        self._runtime = (profile.ASSETS / (profile.PREFIX + 'runtime.so')).read_bytes()


class SnapshotJob(FinishJob):
    def __init__(self, root, source, live, stored):
        super().__init__(root, source)
        self.profile = integrated.select(source.state)
        self.last_live, self.last_stored = live, stored
        self.live_time_reference = None
        self.live_field_delta = None

    def module(self, session, name, mode=None):
        if name == 'runtime':
            require(mode is None and not self.write_started, '快照写入后不再启动运行读取器')
            from .ntsc_module import run
            return run(self, session, core, name, profile=self.profile)
        return super().module(session, name, mode)

    def check_current(self, session):
        app.Job.files(self, session, [self.profile.AP_FINAL, self.profile.CP_FL23])
        self.source.verify_saved_targets(session)
        fresh = []
        for live, label in ((True, 'live'), (False, 'stored')):
            raw = session.call(session.camera.getBackupPresetData, live)
            write_new(self.root / (label + '.private.bin'), raw)
            fresh.append(core.backup_records(raw))
        elapsed = None if self.live_time_reference is None else (datetime.now() - self.live_time_reference).total_seconds()
        self.live_field_delta = compare_records(self.last_live, fresh[0], live=True, elapsed_seconds=elapsed)
        compare_records(self.last_stored, fresh[1])
        self.audit('fresh_saved_settings_exact_and_live_field_reviewed', delta=self.live_field_delta,
                   elapsed_seconds=elapsed, field_semantics='unconfirmed')
        runtime = self.module(session, 'runtime')
        require(len(runtime['spans']) == len(self.profile.SPANS) and
                {s['offset']: s['length'] for s in runtime['spans']} == dict(self.profile.SPANS),
                '当前运行区域清单不符')
        files = sorted((self.root / 'evidence').glob('*-runtime.bin'))
        raw = files[-1].read_bytes()
        at = 56 + 256 * len(self.profile.SPANS)
        require(runtime['image_mode'].strip() == '2' and
                raw[at + 16:at + 48].split(b'\0')[0] == b'*wbi4\n', '当前启动快照状态不符')
        old = core.patch_program((self.source.root / 'original/appFw.so.bin').read_bytes(), 0)
        new = (self.source.root / 'candidate/appFw.so').read_bytes()
        for span in runtime['spans']:
            offset, n = span['offset'], span['length']
            require(span['disk'] == new[offset:offset+n].hex(), '当前磁盘运行窗口不符')
            require(span['memory'] in (old[offset:offset+n].hex(), new[offset:offset+n].hex()),
                    '当前内存既非已知旧版也非目标窗口')
        self.wbi_permitted = True
        self.module(session, 'wbi', 0)
        self.wbi_preflight_verified = True

    def complete_snapshot(self, session, reviewed):
        require(self.wbi_permitted and self.wbi_preflight_verified, '当前检查未完成')
        write_new(self.source.root / MARKER, json.dumps(reviewed).encode())
        self.write_started = True
        self.source.audit('reviewed_upgrade_finish_about_to_change_wbi', review=reviewed, evidence_directory=self.root.name)
        self.source.save('finishing_upgrade_snapshot')
        self.module(session, 'wbi', 1)
        self.wbi_completed = True
        self.audit('reviewed_upgrade_wbi_finish_completed')


def run_finish_upgrade(workspace, accept_live_time_field=False):
    import os
    import subprocess
    import tempfile
    import traceback
    from .cli import camera_lock, return_ownership, save_failure
    from .session import MacSession, NormalReturnTimeout

    with camera_lock():
        source = MacJob(workspace)
        reviewed, live, stored = review_history(source)
        reviewed.update(live_field_policy='elapsed_time_bounded_unconfirmed' if accept_live_time_field else 'exact_60',
                        field_semantics='unconfirmed')
        original_state = source.state_path.read_bytes()
        previous_umask, previous_audit = os.umask(0o077), MacSession.audit
        job = wake = None
        status, result, exit_report = 'incomplete', 1, {}
        try:
            root = Path(tempfile.mkdtemp(prefix='upgrade-finish-', dir=workspace))
            write_new(root / 'source-state.private.json', original_state)
            job = SnapshotJob(root, source, live, stored)
            if accept_live_time_field:
                job.live_time_reference = datetime.fromisoformat(reviewed['live_reference_time'])
            def audit(row):
                exit_report.update(row)
                job.audit('mac_usb_session_exit', result=row)
            MacSession.audit = audit
            wake = subprocess.Popen(['/usr/bin/caffeinate', '-i', '-s', '-w', str(os.getpid())])
            print('实验性升级收尾：仅在历史+60及保存设置完全未变的审查后执行WBI，不重刷、不保存设置。', flush=True)
            with MacSession(source.state['identity']) as session:
                guarded = SnapshotSession(session, job.profile)
                job.check_current(guarded)
                reviewed['fresh_live_field_delta'] = job.live_field_delta
                job.complete_snapshot(guarded, reviewed)
            require(job.wbi_completed and exit_report.get('service_exit_request') == 'sent'
                    and exit_report.get('normal_usb_returned') is True and exit_report.get('errors') == [],
                    '收尾或普通USB返回未确认')
            status, result = 'installed_pending_cold_start', 0
        except NormalReturnTimeout:
            if (job is not None and job.wbi_completed and exit_report.get('service_exit_request') == 'sent'
                    and len(exit_report.get('errors', [])) == 1):
                status, result = 'installed_pending_cold_start_usb_unconfirmed', 2
            elif job is not None and job.write_started:
                save_failure(source, 'finish-upgrade', RuntimeError('未知收尾结果'))
        except BaseException as exc:
            if job is not None:
                (job.root / 'error.private.txt').write_text(traceback.format_exc())
                if job.write_started:
                    save_failure(source, 'finish-upgrade', exc)
            print('STOPPED: ' + str(exc), flush=True)
        finally:
            MacSession.audit = previous_audit
            try:
                if wake is not None:
                    wake.terminate();wake.wait(timeout=5)
                if job is not None:
                    if result in (0, 2):
                        source.audit('reviewed_upgrade_finish_completed', evidence_directory=job.root.name)
                        source.save(status, failed_action=None, failed_during=None, last_error=None,
                                    normal_usb_returned=exit_report.get('normal_usb_returned') is True,
                                    functional_test='manual_check_required')
                    core.save_json(job.root / 'finish-summary.json', {
                        'status': status, 'wbi_completed': job.wbi_completed,
                        'wbi_write_attempted': job.write_started, 'firmware_or_settings_writes': False,
                        'normal_usb_returned': exit_report.get('normal_usb_returned') is True,
                        'source_state_unchanged': source.state_path.read_bytes() == original_state})
                    print('RESULT: ' + status, flush=True)
            finally:
                return_ownership([workspace]);os.umask(previous_umask)
        return result
