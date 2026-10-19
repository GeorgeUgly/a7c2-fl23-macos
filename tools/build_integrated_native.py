"""Build exact, isolated integrated workers from pinned upstream sources."""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from macfl23 import integrated as profile
from macfl23.engine import verify
from build_ntsc_native import array, validate_elf


def entries_for(base, upgrade=False, selected=profile.COMBINED):
    entries = []
    if not upgrade:
        patches = json.loads((base / 'assets/patches.json').read_text())
        for chip in (1, 0):
            for window in patches[chip]['writes']:
                entries.append((int(window['file_offset'], 16), chip,
                                bytes.fromhex(window['before_hex']), bytes.fromhex(window['after_hex'])))
    entries += [(offset, 0, before, after) for offset, before, after in selected.PATCHES]
    if len(entries) != len(selected.PATCHES) + (0 if upgrade else 40):
        raise ValueError('Unexpected integrated window count')
    for chip in (0, 1):
        chosen = sorted(e for e in entries if e[1] == chip)
        if any(a[0] + len(a[2]) > b[0] for a, b in zip(chosen, chosen[1:])):
            raise ValueError('Overlapping integrated windows')
    return entries


def render_manifest(entries, upgrade=False, selected=profile.COMBINED):
    text = ''
    for i, (_, _, before, after) in enumerate(entries):
        if not before or len(before) != len(after) or len(before) > 4096:
            raise ValueError('Unexpected integrated window length')
        text += array('old_' + str(i), before) + array('new_' + str(i), after)
    text += 'static const struct Window windows[] __attribute__((used,retain))={' + ','.join(
        f'{{{offset}UL,{len(before)},{chip},old_{i},new_{i}}}'
        for i, (offset, chip, before, after) in enumerate(entries)) + '};\n'
    text += f'static const u32 window_count={len(entries)};\n'
    text += 'static const u64 file_sizes[]={99960744UL,18090880UL};\n'
    originals = [profile.AP_FL23, profile.CP_FL23] if upgrade else [
        '2ea02f7ecbc5bb917316fe8ef4ce5ffd87eb66efaae78ba2ce11420552df539b',
        'f5a477588698ef965748903b8959a39ce4f59ffd4aee4d4234ef1670f56a1428']
    for name, hashes in [('original_hashes', originals), ('final_hashes', [selected.AP_FINAL, profile.CP_FL23])]:
        text += f'static const u8 {name}[2][32]={{' + ','.join(
            '{' + ','.join(map(str, bytes.fromhex(value))) + '}' for value in hashes) + '};\n'
    return text


def runtime_source(base, selected=profile.COMBINED):
    text = (base / 'native/runtime.c').read_text()
    changes = {
        'data[6][2][128]': f'data[{len(selected.SPANS)}][2][128]',
        'offsets[6]={0x3528804,0x4b2ed19,0x59892b8,0x5989370,0x4b4a5e4,0x4b3b544}':
            f'offsets[{len(selected.SPANS)}]={{' + ','.join(hex(o) for o, _ in selected.SPANS) + '}',
        'lengths[6]={112,2,40,40,12,12}':
            f'lengths[{len(selected.SPANS)}]={{' + ','.join(str(n) for _, n in selected.SPANS) + '}',
        'runtime.count=6': f'runtime.count={len(selected.SPANS)}', 'i<6': f'i<{len(selected.SPANS)}',
    }
    for old, new in changes.items():
        if text.count(old) != 1:
            raise ValueError('Pinned runtime template differs')
        text = text.replace(old, new)
    return text


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--zig', required=True)
    args = parser.parse_args()
    if subprocess.check_output([args.zig, 'version'], text=True).strip() != '0.13.0':
        parser.error('Use Zig 0.13.0')
    base = verify()
    for selected in (profile.BASE, profile.COMBINED):
        manifest = {'format': 1, 'features': selected.FEATURES, 'compiler': 'Zig 0.13.0',
                    'status': 'offline_verified_hardware_unverified', 'files': {}, 'elf': {}}
        outputs = {}
        flags = ['cc', '-target', 'aarch64-linux-gnu', '-shared', '-fPIC', '-O2', '-g0',
                 '-ffreestanding', '-fno-builtin', '-fno-stack-protector', '-fno-unwind-tables',
                 '-fno-asynchronous-unwind-tables', '-nostdlib', '-Wl,--no-undefined',
                 '-Wl,-Bsymbolic', '-Wl,-z,noexecstack', '-Wl,--strip-all']
        for name in ('installer', 'upgrade', 'runtime'):
            entries = entries_for(base, upgrade=name == 'upgrade', selected=selected)
            builds = []
            for attempt in range(2):
                with tempfile.TemporaryDirectory(prefix='a7c2-integrated-build-') as directory:
                    directory = Path(directory)
                    (directory / 'integrated_manifest.h').write_text(render_manifest(entries, name == 'upgrade', selected))
                    (directory / 'runtime.c').write_text(runtime_source(base, selected))
                    source = directory / 'runtime.c' if name == 'runtime' else base / 'native/installer.c'
                    output = directory / 'module.so'
                    command = [args.zig, *flags, '-I', str(directory), '-I', str(base / 'native')]
                    if name != 'runtime':
                        command += ['-DPATCH_MANIFEST="integrated_manifest.h"']
                    subprocess.run([*command, '-o', str(output), str(source)], check=True)
                    builds.append(output.read_bytes())
            if builds[0] != builds[1]:
                raise ValueError('Repeated builds differ: ' + name)
            raw = builds[0]
            asset_name = selected.PREFIX + name + '.so'
            manifest['elf'][asset_name] = validate_elf(raw, name, entries)
            manifest['files'][asset_name] = hashlib.sha256(raw).hexdigest()
            outputs[asset_name] = raw
        manifest['repeat_build_identical'] = True
        for name, raw in outputs.items():
            (profile.ASSETS / name).write_bytes(raw)
        (profile.ASSETS / (selected.PREFIX + 'manifest.json')).write_text(json.dumps(manifest, indent=2) + '\n')
        print(json.dumps(manifest, indent=2))


if __name__ == '__main__':
    main()
