"""Per-body backup preparation; upstream install/restore and native modules."""
import os
from pathlib import Path

from .session import app, core, MacSession


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
    def prepare(self, second=None):
        core.assets()
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
                      'backup_copies': 2 if second else 1, 'backup_files': {}, 'journal': []}
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
                candidate = core.patch_program(raw, chip)
                if core.patch_program(candidate, chip, True) != raw:
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
            candidate = core.patch_program(original, chip)
            if (self.root / 'candidate' / name).read_bytes() != candidate:
                raise ValueError('候选程序不匹配')
            if core.patch_program(candidate, chip, True) != original:
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
        if action in ('install', 'restore'):
            phase = 'settings_verified' if action == 'install' else 'restoring_programs'
            if self.state['phase'] != phase or (last.get('module'), last.get('mode')) != ('wbi', 1):
                raise ValueError('Write completion not verified')
            expected = core.FINAL if action == 'install' else core.ORIGINAL
            for chip, name in enumerate(core.PROGRAMS):
                files = sorted((self.root / 'readback').glob('*-' + name))
                if not files or core.sha(files[-1].read_bytes()) != expected[chip]:
                    raise ValueError('Full-file evidence mismatch')
            if not any(row['step'] == 'settings_storage_verified' and row.get('restore') == (action == 'restore')
                       for row in journal):
                raise ValueError('Missing saved-settings evidence')
            return ('installed' if action == 'install' else 'restored') + '_pending_cold_start_usb_unconfirmed'
        if action == 'verify':
            if last.get('module') != 'runtime':
                raise ValueError('Runtime evidence missing')
            paths = sorted((self.root / 'evidence').glob('*-runtime.bin'))
            runtime = core.parse_native('runtime', paths[-1].read_bytes())
            candidate = (self.root / 'candidate' / 'appFw.so').read_bytes()
            for span in runtime['spans']:
                expected = candidate[span['offset']:span['offset']+span['length']].hex()
                if span['memory'] != expected or span['disk'] != expected:
                    raise ValueError('Runtime evidence mismatch')
            return 'verified_after_cold_start_usb_unconfirmed'
        raise ValueError('Unsupported completion review')
