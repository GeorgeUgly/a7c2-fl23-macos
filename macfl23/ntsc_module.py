"""Optional module staging, adapted from the pinned upstream app.Job.module.

Same temporary names, request ABI, readback and cleanup rules; separate exact
asset fingerprints and report parser. No upstream files/constants are changed.
"""
from datetime import datetime
import struct
from . import ntsc


def run(job, session, core, name, mode=None):
    if name not in ('installer', 'runtime'):
        raise ValueError('Unsupported optional module')
    if (name == 'installer' and mode not in (0, 1, 2)) or (name == 'runtime' and mode is not None):
        raise ValueError('Unsupported optional module mode')
    ntsc.assets()
    blob = (ntsc.ASSETS / ('ntsc-' + name + '.so')).read_bytes()
    result = 'a7c2.install.result' if name == 'installer' else 'a7c2.runtime.result'
    names = ['libJit.so', 'a7c2-preview-staged.so', result]
    names.append('a7c2.install.request' if name == 'installer' else 'a7c2.probe.result')
    for item in names:
        if session.read('/tmp/jiritsu/' + item, True) is not None:
            raise RuntimeError('存在先前临时文件；保留状态，不重试：' + item)
    if session.read('/tmp/jiritsu/../../etc/ld.so.preload', True):
        raise RuntimeError('发现既有预载配置，停止')
    created = []
    try:
        created.append('a7c2-preview-staged.so')
        session.temp(created[-1], blob)
        if session.read('/tmp/jiritsu/' + created[-1]) != blob:
            raise RuntimeError('可选模块临时文件读回不符')
        if name == 'installer':
            request = struct.pack('<8sII', b'A7INS001', mode, 0)
            created.append('a7c2.install.request')
            session.temp(created[-1], request)
            if session.read('/tmp/jiritsu/' + created[-1]) != request:
                raise RuntimeError('可选安装器请求读回不符')
        job.audit('native_about_to_run', module=name, mode=mode, profile=ntsc.FEATURES)
        created.extend(['libJit.so', result])
        if name == 'runtime':
            created.append('a7c2.probe.result')
        session.temp('libJit.so', blob)
        raw = session.read('/tmp/jiritsu/' + result)
        dest = job.root / 'evidence' / f'{datetime.now():%Y%m%d-%H%M%S-%f}-{name}.bin'
        dest.parent.mkdir(exist_ok=True)
        dest.write_bytes(raw)
        parsed = ntsc.parse_native(core, name, raw, mode)
        job.audit('native_verified', module=name, mode=mode, result=parsed, profile=ntsc.FEATURES)
        return parsed
    finally:
        for item in reversed(list(dict.fromkeys(created))):
            if not session.healthy:
                job.audit('temporary_cleanup_pending', name=item)
                continue
            if session.read('/tmp/jiritsu/' + item, True) is not None:
                session.temp(item, remove=True)
            if session.read('/tmp/jiritsu/' + item, True) is not None:
                raise RuntimeError('临时文件清理未完成')
