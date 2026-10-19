"""Opt-in, exact-source NTSC boot-notice profile; no USB operations here."""
import hashlib
import json
from pathlib import Path
import struct

FEATURES = ['fl23', 'no_ntsc_notice']
AP_FL23 = '1aaeb8fbe0297950cab454d498d1a5ba874f91542aa2737aae27a73971e324d7'
AP_COMBINED = '31b7336d39e211bdd9d2cb51d17e831a81703b86b9fab81bd40c97081fd4e8c9'
CP_FL23 = '60a9de1a0636c7f6b5b98607a4d25eeb48634e25294f632743a6d256e583b370'
PATCHES = ((0x20617b0, bytes.fromhex('1f00016b')),
           (0x2061d14, bytes.fromhex('3f00006b')))
AFTER = bytes.fromhex('3f00206a')
SPANS = [(0x3528804, 112), (0x4b2ed19, 2), (0x59892b8, 40),
         (0x5989370, 40), (0x4b4a5e4, 12), (0x4b3b544, 12),
         (0x20617b0, 4), (0x2061d14, 4)]
ASSETS = Path(__file__).resolve().parent / 'assets'
PREFIX = 'ntsc-'


def enabled(state):
    from .integrated import FEATURES as INTEGRATED_FEATURES, BASE
    features = state.get('features', ['fl23'])
    if features not in (['fl23'], FEATURES, BASE.FEATURES, INTEGRATED_FEATURES):
        raise ValueError('未知功能组合；不会操作相机')
    return features in (FEATURES, INTEGRATED_FEATURES)


def patch_ap(raw, reverse=False):
    source, target = (AP_COMBINED, AP_FL23) if reverse else (AP_FL23, AP_COMBINED)
    if hashlib.sha256(raw).hexdigest() != source:
        raise ValueError('NTSC 可选补丁的完整 AP 指纹不匹配')
    result = bytearray(raw)
    for offset, before in PATCHES:
        old, new = (AFTER, before) if reverse else (before, AFTER)
        if raw[offset:offset + 4] != old:
            raise ValueError('NTSC 可选补丁窗口不匹配')
        result[offset:offset + 4] = new
    if hashlib.sha256(result).hexdigest() != target:
        raise ValueError('NTSC 可选补丁结果指纹不匹配')
    return bytes(result)


def assets():
    manifest = json.loads((ASSETS / 'ntsc-manifest.json').read_text())
    if set(manifest['files']) != {'ntsc-installer.so', 'ntsc-runtime.so'}:
        raise ValueError('NTSC 模块清单不匹配')
    for name, expected in manifest['files'].items():
        if hashlib.sha256((ASSETS / name).read_bytes()).hexdigest() != expected:
            raise ValueError('NTSC 模块指纹不匹配：' + name)
    return manifest


def parse_native(core, name, raw, mode=None):
    if name == 'installer':
        if len(raw) != 240:
            raise ValueError('可选安装器报告长度不符')
        values = struct.unpack_from('<8sIIIIqqqqqqqQQQQ', raw)
        if (values[:3] != (b'A7IRP001', 1, mode) or values[3] != 9
                or any(values[i] for i in [5, 6, 7, 8, 9, 10])
                or values[4] != (0 if mode == 0 else 42)):
            raise ValueError('可选安装器未完整成功；保留状态，不重试')
        final = [AP_COMBINED, CP_FL23]
        before = [raw[112:144].hex(), raw[144:176].hex()]
        after = [raw[176:208].hex(), raw[208:240].hex()]
        if before != (final if mode == 2 else core.ORIGINAL):
            raise ValueError('可选安装器写入前完整指纹不符')
        if mode and after != (core.ORIGINAL if mode == 2 else final):
            raise ValueError('可选安装器写入后完整指纹不符')
        return {'mode': mode, 'window_writes': values[4], 'before': before,
                'after': after, 'readonly_verified': True}
    if name == 'runtime':
        if len(raw) != 2344:
            raise ValueError('可选运行状态报告长度不符')
        values = struct.unpack_from('<8sIIqqqQII', raw)
        if (values[:3] != (b'A7RUN001', 1, 8) or values[3] or values[4]
                or values[7] != 16 or values[8]):
            raise ValueError('可选运行状态身份或结果不符')
        spans = [{'offset': offset, 'length': length,
                  'memory': raw[56 + i * 256:56 + i * 256 + length].hex(),
                  'disk': raw[184 + i * 256:184 + i * 256 + length].hex()}
                 for i, (offset, length) in enumerate(SPANS)]
        return {'spans': spans,
                'image_mode': raw[2104:2120].split(b'\0')[0].decode('ascii', 'replace')}
    raise ValueError('未知可选模块')
