"""Explicit, one-shot FL23 -> integrated upgrade; preserve the source workspace."""
import json
from pathlib import Path
from datetime import datetime
import re

from . import integrated, ntsc
from .session import app, core, MacSession
from .workflow import MacJob, private_new_directory, write_new

BACKUPS = {'appFw.so.bin', 'cpapp-b.bin.bin', 'backup.private.bin',
           'backup-stored.private.bin', 'unit-02a40001.bin', 'unit-02a4000d.bin',
           'ap-02cf1287.bin', 'ap-02cf1289.bin'}
ATTEMPT = 'upgrade-attempt.private.json'


def prepare_upgrade(source_path, output, disable_ntsc_warning=False):
    """Offline only: verify old per-body inputs and create a separate workspace."""
    old, new = Path(source_path).resolve(), Path(output).resolve()
    if old == new or old in new.parents or new in old.parents:
        raise ValueError('升级目录与原目录不能相同或互相包含')
    source = MacJob(source_path)
    state_raw = source.state_path.read_bytes()
    pre = json.loads(state_raw)
    if set(pre.get('backup_files', {})) != BACKUPS:
        raise ValueError('升级需要完整的八项原始机身备份')
    source.validate_candidates()
    if (integrated.enabled(source.state) or ntsc.enabled(source.state) or source.state.get('phase') != 'verified_after_cold_start'
            or source.state.get('version') != '2.01'):
        raise ValueError('仅支持已完成冷启动验证的原 FL2/FL3 工作目录；不支持旧 NTSC 组合或未知阶段')
    identity = source.state.get('identity', {})
    if identity.get('model') != core.MODEL or not re.fullmatch('[0-9a-f]{64}', identity.get('serial_sha256', '')):
        raise ValueError('源机身身份无效')
    selected = integrated.COMBINED if disable_ntsc_warning else integrated.BASE
    selected.assets()
    job = MacJob(output)
    job.state = {'format': 1, 'model': core.MODEL, 'version': '2.01',
                 'features': list(selected.FEATURES), 'identity': dict(identity),
                 'backup_files': dict(source.state['backup_files']), 'backup_copies': 1,
                 'backup_copy': str(job.root / 'original'), 'journal': [],
                 'upgrade_source_state_sha256': core.sha(state_raw),
                 'upgrade_source_hashes': list(core.FINAL)}
    originals = {name: (source.root / 'original' / name).read_bytes() for name in BACKUPS}
    candidates = {name: job.patch_program(originals[name + '.bin'], chip)
                  for chip, name in enumerate(core.PROGRAMS)}
    _, merged = source.units()
    candidates.update({'unit-%08x.bin' % key: raw for key, raw in merged.items()})
    if source.state_path.read_bytes() != state_raw:
        raise ValueError('源工作目录在离线准备期间变化，停止')
    if any(core.sha(raw) != pre['backup_files'][name] for name, raw in originals.items()):
        raise ValueError('源备份在离线准备期间变化，停止')
    private_new_directory(job.root)
    for name in ('original', 'candidate', 'evidence'):
        (job.root / name).mkdir(mode=0o700)
    job.save('preparing_upgrade')
    for name, raw in originals.items():
        write_new(job.root / 'original' / name, raw)
    for name, raw in candidates.items():
        write_new(job.root / 'candidate' / name, raw)
    job.validate_candidates()
    if source.state_path.read_bytes() != state_raw:
        raise ValueError('源状态发生变化；新目录不放行升级')
    job.save('upgrade_prepared')
    print('离线升级目录已准备；原目录未改动，未连接相机。')
    print('目标 AP SHA-256：' + selected.AP_FINAL)
    return job


class UpgradeSession:
    """Allow only the exact upgrade/runtime/WBI workers and settings reads."""
    TEMP = {'libJit.so', 'a7c2-preview-staged.so', 'a7c2.install.request', 'a7c2.install.result',
            'a7c2.runtime.result', 'a7c2.probe.result', 'a7c2.wbi.request', 'a7c2.wbi.result'}

    def __init__(self, session, selected=integrated.COMBINED):
        self._session = session
        self.camera = session.camera
        self.blobs = {(integrated.ASSETS / (selected.PREFIX + name + '.so')).read_bytes()
                      for name in ('upgrade', 'runtime')}
        self.blobs.add((core.BASE / 'assets/wbi.so').read_bytes())

    @property
    def healthy(self):
        return self._session.healthy

    def read(self, path, missing=False):
        allowed = {'/tmp/jiritsu/' + name for name in self.TEMP} | {
            '/tmp/jiritsu/../../usr/lib/appFw.so', '/tmp/jiritsu/../../cp/cpapp-b.bin',
            '/tmp/jiritsu/../../etc/ld.so.preload'}
        if path not in allowed:
            raise ValueError('升级不允许此读取路径')
        return self._session.read(path, missing)

    def call(self, method, *args):
        if not ((method == self.camera.getBackupPresetData and args in ((True,), (False,)))
                or (method == self.camera.readBackup and len(args) == 1 and args[0] in core.AP_KEYS)):
            raise ValueError('升级禁止写入或保存拍摄设置')
        return self._session.call(method, *args)

    def cp_read(self, key):
        if key not in core.CP_KEYS:
            raise ValueError('升级只读既有 FL2/FL3 色彩参数')
        return self._session.cp_read(key)

    def temp(self, name, data=None, remove=False):
        import struct
        if name not in self.TEMP:
            raise ValueError('升级临时文件名不允许')
        if not remove:
            if name in ('libJit.so', 'a7c2-preview-staged.so'):
                if data not in self.blobs:
                    raise ValueError('升级仅允许运行固定 upgrade/runtime/WBI 模块')
            elif name in ('a7c2.install.request', 'a7c2.wbi.request'):
                magic = b'A7INS001' if name == 'a7c2.install.request' else b'A7WBI001'
                if data not in {struct.pack('<8sII', magic, mode, 0) for mode in (0, 1)}:
                    raise ValueError('升级只允许校验或正向写入模式')
            else:
                raise ValueError('升级不允许伪造结果文件')
        return self._session.temp(name, data, remove)

    def cp_write(self, *args):
        raise ValueError('升级禁止写入 CP 设置')

    def ap_write(self, *args):
        raise ValueError('升级禁止写入 AP 设置')


class UpgradeJob(MacJob):
    def module(self, session, name, mode=None):
        if (name not in ('upgrade', 'runtime', 'wbi')
                or (name == 'runtime' and mode is not None)
                or (name != 'runtime' and mode not in (0, 1))):
            raise ValueError('本升级入口不允许完整安装器、设置保存或其他模式')
        return super().module(session, name, mode)

    def snapshot(self, session, label):
        records = []
        for live, suffix in ((True, 'live'), (False, 'stored')):
            raw = session.call(session.camera.getBackupPresetData, live)
            write_new(self.root / 'evidence' / f'{datetime.now():%Y%m%d-%H%M%S-%f}-{label}-{suffix}.private.bin', raw)
            records.append(core.backup_records(raw))
        original = core.backup_records((self.root / 'original/backup.private.bin').read_bytes())
        _, merged = self.units()
        for chip, targets in ((0, dict(zip(core.AP_KEYS, (b'\x0a', b'\x0b')))), (1, merged)):
            for key, desired in targets.items():
                now, saved = records[0][chip][key], records[1][chip][key]
                direct = session.cp_read(key) if chip else session.call(session.camera.readBackup, key)
                if (now.data != desired or saved.data != desired or direct != desired
                        or now.attr != saved.attr or now.attr != original[chip][key].attr):
                    raise ValueError('升级前后 FL2/FL3 当前/保存/直接读取的参数或属性不匹配')
        return records

    def upgrade(self, battery):
        self.validate_candidates()
        if (self.state.get('phase') != 'upgrade_prepared' or not integrated.enabled(self.state)
                or self.state.get('upgrade_source_hashes') != core.FINAL
                or (self.root / ATTEMPT).exists()):
            raise ValueError('升级状态未知或已有写入尝试；不允许重跑')
        if not 50 <= battery <= 100:
            raise ValueError('相机实际电量必须至少50%')
        with MacSession(self.state['identity']) as raw_session:
            session = UpgradeSession(raw_session, integrated.select(self.state))
            # Bypass MacJob.files' final-profile dispatch for the source check.
            app.Job.files(self, session, list(core.FINAL))
            before = self.snapshot(session, 'upgrade-before')
            runtime = self.module(session, 'runtime')
            baseline = core.patch_program((self.root / 'original/appFw.so.bin').read_bytes(), 0)
            for span in runtime['spans']:
                expected = baseline[span['offset']:span['offset'] + span['length']].hex()
                if span['memory'] != expected or span['disk'] != expected:
                    raise ValueError('升级前运行内存或磁盘不是预期的原 FL2/FL3；停止')
            self.module(session, 'upgrade', 0)
            self.module(session, 'wbi', 0)
            write_new(self.root / ATTEMPT, b'{"status":"attempt_started","do_not_retry":true}\n')
            self.save('writing_upgrade_programs')
            self.module(session, 'upgrade', 1)
            self.save('upgrade_programs_written')
            self.files(session, self.final_hashes())
            after = self.snapshot(session, 'upgrade-after')
            preservation = [core.verify_preservation(a, b, {}) for a, b in zip(before, after)]
            self.audit('upgrade_settings_unchanged_verified', live=preservation[0], stored=preservation[1])
            self.save('upgrade_settings_verified')
            self.module(session, 'wbi', 1)
        self.save('installed_pending_cold_start')
        print('所选版本的 AP 窗口升级及完整程序读回完成；未重写或保存拍摄设置。请按文档冷启动，再运行 verify。')
