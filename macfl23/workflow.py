"""Per-body backup preparation; upstream install/restore and native modules."""
import os
from pathlib import Path

from .session import app, core, MacSession
from . import ntsc, integrated


def private_new_directory(path):
    path = Path(path).absolute()
    if path.exists() or path.is_symlink():
        raise ValueError('必须使用尚不存在的新目录：' + str(path))
    path.mkdir(mode=0o700, parents=False)
    return path


def write_new(path, raw):
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(descriptor, 'wb') as stream:
        stream.write(raw)
        stream.flush()
        os.fsync(stream.fileno())
    if path.read_bytes() != raw:
        raise RuntimeError('备份写回核验失败：' + path.name)


class MacJob(app.Job):
    def load(self):
        super().load()
        if integrated.enabled(self.state):
            integrated.select(self.state).assets()
        elif ntsc.enabled(self.state):
            ntsc.assets()
        return self

    def final_hashes(self):
        if integrated.enabled(self.state):
            return [integrated.select(self.state).AP_FINAL, integrated.CP_FL23]
        return [ntsc.AP_COMBINED, ntsc.CP_FL23] if ntsc.enabled(self.state) else core.FINAL

    def patch_program(self, raw, chip, reverse=False):
        if chip == 0 and integrated.enabled(self.state):
            if reverse:
                return core.patch_program(integrated.select(self.state).patch_ap(raw, True), chip, True)
            return integrated.select(self.state).patch_ap(core.patch_program(raw, chip))
        if chip == 0 and ntsc.enabled(self.state):
            if reverse:
                return core.patch_program(ntsc.patch_ap(raw, True), chip, True)
            return ntsc.patch_ap(core.patch_program(raw, chip))
        return core.patch_program(raw, chip, reverse)

    def module(self, session, name, mode=None):
        if integrated.enabled(self.state) and name in ('installer', 'upgrade', 'runtime'):
            from .ntsc_module import run
            return run(self, session, core, name, mode, profile=integrated.select(self.state))
        if ntsc.enabled(self.state) and name in ('installer', 'runtime'):
            from .ntsc_module import run
            return run(self, session, core, name, mode)
        return super().module(session, name, mode)

    def files(self, session, expected):
        if expected == core.FINAL:
            expected = self.final_hashes()
        return super().files(session, expected)

    def review_failed_verify_before_cold_start(self):
        """Permit only a new read-only verify, preserving the failed evidence."""
        from datetime import datetime
        from .finish import require, evidence_file
        state = self.state
        require(integrated.enabled(state) and
                (state.get('phase'), state.get('failed_action'), state.get('failed_during')) ==
                ('needs_review', 'verify', 'installed_pending_cold_start') and
                state.get('last_error') == 'ValueError: 运行状态与所选组合不符；保留状态，检查冷启动结果',
                '不符合未冷启动时只读验证失败的专项阶段')
        rows = state.get('journal', [])
        starts = [i for i,r in enumerate(rows) if r.get('step') == 'host_full_file_readback_verified']
        require(bool(starts), '缺少前次完整程序读回')
        tail = rows[starts[-1]:]
        require([r.get('step') for r in tail] == ['host_full_file_readback_verified', 'native_about_to_run',
                'native_verified', 'mac_usb_session_exit'] and tail[0].get('hashes') == self.final_hashes(),
                '前次只读验证步骤不匹配')
        require(all((r.get('module'),r.get('mode')) == ('runtime',None) for r in tail[1:3]) and
                tail[-1].get('result') == {'service_exit_request':'sent','normal_usb_returned':True,'errors':[]},
                '前次验证不是正常返回的只读运行检查')
        path = evidence_file(self.root, 'runtime.bin', datetime.fromisoformat(tail[1]['time']),
                             datetime.fromisoformat(tail[-1]['time']))
        runtime = integrated.select(state).parse_native(core,'runtime',path.read_bytes())
        require(runtime == tail[2].get('result'), '前次运行报告与审计不符')
        old = core.patch_program((self.root/'original/appFw.so.bin').read_bytes(),0)
        new = (self.root/'candidate/appFw.so').read_bytes()
        stale = 0
        for span in runtime['spans']:
            start,n = span['offset'],span['length']
            old_hex,new_hex = old[start:start+n].hex(),new[start:start+n].hex()
            require(span['disk'] == new_hex and span['memory'] in (old_hex,new_hex),
                    '前次运行报告含未知程序内容')
            stale += span['memory'] != new_hex
        require(stale > 0, '前次报告并非已知旧内容仍在内存')
        return {'stale_regions':stale,'previous_session_returned_normally':True,'operation':'read_only_verify'}

    def verify(self):
        self.load()
        if integrated.enabled(self.state) and self.state['phase'] in (
                'restored_pending_cold_start', 'restored_pending_cold_start_usb_unconfirmed',
                'restored_verified_after_cold_start', 'restored_verified_after_cold_start_usb_unconfirmed'):
            return self.verify_restored()
        if not integrated.enabled(self.state) and not ntsc.enabled(self.state):
            return super().verify()
        with MacSession(self.state['identity']) as session:
            self.files(session, self.final_hashes())
            if integrated.enabled(self.state):
                self.verify_saved_targets(session)
            else:
                current = core.backup_records(session.call(session.camera.getBackupPresetData, True))
                _, new = self.units()
                if any(current[1][k].data != v for k, v in new.items()):
                    raise ValueError('重启后 CP 参数不符')
                if any(current[0][k].data != bytes([10 + i]) for i, k in enumerate(core.AP_KEYS)):
                    raise ValueError('重启后 AP 绑定不符')
            runtime = self.module(session, 'runtime')
            candidate = self.patch_program((self.root / 'original/appFw.so.bin').read_bytes(), 0)
            for span in runtime['spans']:
                expected = candidate[span['offset']:span['offset'] + span['length']].hex()
                if span['memory'] != expected or span['disk'] != expected:
                    raise ValueError('运行状态与所选组合不符；保留状态，检查冷启动结果')
        self.audit('post_restart_files_and_targets_verified', features=self.state['features'])
        print(f'组合程序、参数和{len(runtime["spans"])}处运行区域核验通过；NTSC 提示、菜单、拍摄及回放仍需实机确认。', flush=True)

    def verify_saved_targets(self, session, restore=False):
        records = [core.backup_records(session.call(session.camera.getBackupPresetData, live))
                   for live in (True, False)]
        original = core.backup_records((self.root / 'original/backup.private.bin').read_bytes())
        originals, merged = self.units()
        desired_ap = dict.fromkeys(core.AP_KEYS, b'\0') if restore else dict(zip(core.AP_KEYS, (b'\x0a', b'\x0b')))
        for chip, targets in ((0, desired_ap), (1, originals if restore else merged)):
            for key, desired in targets.items():
                direct = session.cp_read(key) if chip else session.call(session.camera.readBackup, key)
                if (direct != desired or any(record[chip][key].data != desired or
                        record[chip][key].attr != original[chip][key].attr for record in records)):
                    raise ValueError('冷启动后当前、保存或直接读取的目标设置/属性不符')

    def verify_restored(self):
        """Integrated-profile recovery: check stock files, saved targets and memory."""
        with MacSession(self.state['identity']) as session:
            self.files(session, core.ORIGINAL)
            self.verify_saved_targets(session, restore=True)
            runtime = self.module(session, 'runtime')
            stock = (self.root / 'original/appFw.so.bin').read_bytes()
            for span in runtime['spans']:
                expected = stock[span['offset']:span['offset'] + span['length']].hex()
                if span['memory'] != expected or span['disk'] != expected:
                    raise ValueError('恢复冷启动后运行内存或磁盘不是原版')
        self.audit('stock_post_restart_files_settings_runtime_verified')
        print(f'原版完整程序、当前/保存设置和{len(runtime["spans"])}处运行区域核验通过；菜单、拍摄和回放仍需确认。', flush=True)

    def prepare(self, second=None, disable_ntsc_warning=False):
        core.assets()
        selected = integrated.COMBINED if disable_ntsc_warning else integrated.BASE
        selected.assets()
        second = Path(second).absolute() if second else None
        if second and (second == self.root or second in self.root.parents or self.root in second.parents):
            raise ValueError('两份备份目录不能相同或互相包含')
        for path in [self.root] + ([second] if second else []):
            if path.exists() or path.is_symlink() or not path.parent.is_dir():
                raise ValueError('工作目录和第二备份目录必须尚不存在，且父目录已存在')
        private_new_directory(self.root)
        if second:
            private_new_directory(second)
        for name in ('original', 'candidate', 'evidence'):
            (self.root / name).mkdir(mode=0o700)
        # The unchanged upstream loader expects backup_copy. With one copy it
        # points to original itself; it does not create a duplicate or imply
        # redundancy. backup_copies records the actual number of locations.
        self.state = {'format': 1, 'model': core.MODEL, 'version': core.VERSION,
                      'phase': 'preparing', 'backup_copy': str(second or self.root / 'original'),
                      'backup_copies': 2 if second else 1, 'backup_files': {}, 'journal': [],
                      'features': list(selected.FEATURES)}
        self.save('preparing')

        def backup(name, raw):
            for parent in [self.root / 'original'] + ([second] if second else []):
                write_new(parent / name, raw)
            self.state['backup_files'][name] = core.sha(raw)
            self.audit('backup_verified', copies=self.state['backup_copies'], name=name, bytes=len(raw), sha256=core.sha(raw))

        with MacSession() as session:
            self.state['identity'] = session.identity
            self.save('reading_backups')
            live = session.call(session.camera.getBackupPresetData, True)
            backup('backup.private.bin', live)
            records = core.backup_records(live)
            stored_raw = session.call(session.camera.getBackupPresetData, False)
            backup('backup-stored.private.bin', stored_raw)
            stored = core.backup_records(stored_raw)
            for chip, name, path in [(1, core.PROGRAMS[1], '/cp/cpapp-b.bin'),
                                     (0, core.PROGRAMS[0], '/usr/lib/appFw.so')]:
                print('读取并备份完整程序：' + name, flush=True)
                raw = session.read('/tmp/jiritsu/../..' + path)
                backup(name + '.bin', raw)
                if core.sha(raw) != core.ORIGINAL[chip]:
                    raise ValueError('程序不匹配支持的原版 2.01；保留读取结果，停止')
                candidate = self.patch_program(raw, chip)
                if self.patch_program(candidate, chip, True) != raw:
                    raise ValueError('候选反向还原核验失败')
                write_new(self.root / 'candidate' / name, candidate)
            for key in core.CP_KEYS:
                raw = session.cp_read(key)
                backup('unit-%08x.bin' % key, raw)
                if raw != records[1][key].data or raw != stored[1][key].data:
                    raise ValueError('CP 直接读回、当前设置与保存设置不一致')
            for key in core.AP_KEYS:
                raw = session.call(session.camera.readBackup, key)
                backup('ap-%08x.bin' % key, raw)
                if raw != b'\0' or records[0][key].data != raw or stored[0][key].data != raw:
                    raise ValueError('AP 保留槽不是空白，停止')
            _, merged = self.units()
            for key, raw in merged.items():
                write_new(self.root / 'candidate' / ('unit-%08x.bin' % key), raw)
            self.save('backups_verified')
            print('备份及候选已核验。开始机内诊断（上传临时模块，不写固件或保存设置）。', flush=True)
            self.module(session, 'probe')
            self.module(session, 'installer', 0)
            self.module(session, 'wbi', 0)
            self.save('preflight_verified')
        self.save('prepared')

    def validate_candidates(self):
        self.load()
        for chip, name in enumerate(core.PROGRAMS):
            original = (self.root / 'original' / (name + '.bin')).read_bytes()
            candidate = self.patch_program(original, chip)
            if (self.root / 'candidate' / name).read_bytes() != candidate:
                raise ValueError('候选程序不匹配')
            if self.patch_program(candidate, chip, True) != original:
                raise ValueError('候选无法精确还原')
        _, merged = self.units()
        for key, raw in merged.items():
            if (self.root / 'candidate' / ('unit-%08x.bin' % key)).read_bytes() != raw:
                raise ValueError('机身参数候选不匹配')

    def confirm_completed_body(self, action):
        """Called ONLY for typed exit timeout after the action body succeeded.

        This is not a general recovery entry point and never sends camera I/O.
        The last successful action phase and audit sequence must agree.
        """
        journal = self.state['journal']
        verified = [row for row in journal if row['step'] == 'native_verified']
        if not journal or journal[-1]['step'] != 'mac_usb_session_exit':
            raise ValueError('Missing session exit evidence')
        exit_result = journal[-1]['result']
        if exit_result.get('service_exit_request') != 'sent' or len(exit_result.get('errors', [])) != 1:
            raise ValueError('Service exit was not confirmed')
        last = verified[-1] if verified else {}
        if action == 'prepare':
            if self.state['phase'] != 'preflight_verified' or (last.get('module'), last.get('mode')) != ('wbi', 0):
                raise ValueError('Preflight not complete')
            self.load()
            return 'prepared_usb_unconfirmed'
        if action in ('install', 'restore', 'upgrade'):
            phase = {'install': 'settings_verified', 'restore': 'restoring_programs',
                     'upgrade': 'upgrade_settings_verified'}[action]
            if self.state['phase'] != phase or (last.get('module'), last.get('mode')) != ('wbi', 1):
                raise ValueError('Write completion not verified')
            expected = self.final_hashes() if action != 'restore' else core.ORIGINAL
            for chip, name in enumerate(core.PROGRAMS):
                files = sorted((self.root / 'readback').glob('*-' + name))
                if not files or core.sha(files[-1].read_bytes()) != expected[chip]:
                    raise ValueError('Full-file evidence mismatch')
            if action == 'upgrade':
                preserved = any(row['step'] == 'upgrade_settings_unchanged_verified' for row in journal)
            else:
                preserved = any(row['step'] == 'settings_storage_verified' and row.get('restore') == (action == 'restore')
                                for row in journal)
            if not preserved:
                raise ValueError('Missing saved-settings evidence')
            return ('restored' if action == 'restore' else 'installed') + '_pending_cold_start_usb_unconfirmed'
        if action == 'verify':
            if last.get('module') != 'runtime':
                raise ValueError('Runtime evidence missing')
            paths = sorted((self.root / 'evidence').glob('*-runtime.bin'))
            raw = paths[-1].read_bytes()
            runtime = (integrated.select(self.state).parse_native(core, 'runtime', raw) if integrated.enabled(self.state)
                       else ntsc.parse_native(core, 'runtime', raw) if ntsc.enabled(self.state)
                       else core.parse_native('runtime', raw))
            restored = integrated.enabled(self.state) and self.state['phase'].startswith('restored_')
            candidate = ((self.root / 'original/appFw.so.bin') if restored
                         else (self.root / 'candidate/appFw.so')).read_bytes()
            for span in runtime['spans']:
                expected = candidate[span['offset']:span['offset']+span['length']].hex()
                if span['memory'] != expected or span['disk'] != expected:
                    raise ValueError('Runtime evidence mismatch')
            return ('restored_verified_after_cold_start_usb_unconfirmed' if restored
                    else 'verified_after_cold_start_usb_unconfirmed')
        raise ValueError('Unsupported completion review')
