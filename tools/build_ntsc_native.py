"""Build separate optional workers from verified upstream sources (Zig 0.13.0).

Does not change the pinned upstream tree or read a body backup. The added
manifest accepts only stock 2.01 as input and the exact combined output.
"""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import struct
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from macfl23.engine import verify
from macfl23 import ntsc


def array(name, data):
    return f'static const u8 {name}[]={{' + ','.join(map(str, data)) + '};\n'


def validate_elf(raw, name, entries):
    import io
    from elftools.elf.elffile import ELFFile
    elf = ELFFile(io.BytesIO(raw))
    if elf['e_machine'] != 'EM_AARCH64' or elf['e_type'] != 'ET_DYN':
        raise ValueError('Unexpected native module type')
    dynamic = elf.get_section_by_name('.dynamic')
    if any(tag.entry.d_tag == 'DT_NEEDED' for tag in dynamic.iter_tags()):
        raise ValueError('Native module has shared-library dependencies')
    symbols = elf.get_section_by_name('.dynsym')
    if any(s.name and s['st_shndx'] == 'SHN_UNDEF' for s in symbols.iter_symbols()):
        raise ValueError('Native module has unresolved symbols')
    relocations = [r for section in elf.iter_sections() if section['sh_type'] == 'SHT_RELA'
                   for r in section.iter_relocations()]
    if any(r['r_info_type'] != 1027 for r in relocations):
        raise ValueError('Unexpected relocation type')
    expected = 2 * len(entries) if name in ('installer', 'upgrade') else 0
    if len(relocations) != expected:
        raise ValueError(f'Unexpected relocation count for {name}: {len(relocations)}; expected {expected}')
    if name in ('installer', 'upgrade'):
        def read_va(address, size):
            for segment in elf.iter_segments():
                if (segment['p_type'] == 'PT_LOAD' and segment['p_vaddr'] <= address
                        and address + size <= segment['p_vaddr'] + segment['p_filesz']):
                    start = segment['p_offset'] + address - segment['p_vaddr']
                    return raw[start:start + size]
            raise ValueError('Window data is not backed by an ELF load segment')
        ordered = sorted(relocations, key=lambda r: r['r_offset'])
        for i, (offset, chip, before, after) in enumerate(entries):
            a, b = ordered[i * 2:i * 2 + 2]
            if b['r_offset'] != a['r_offset'] + 8:
                raise ValueError('Unexpected window pointer layout')
            if struct.unpack('<QII', read_va(a['r_offset'] - 16, 16)) != (offset, len(before), chip):
                raise ValueError('Compiled window metadata differs from exact profile')
            if read_va(a['r_addend'], len(before)) != before or read_va(b['r_addend'], len(after)) != after:
                raise ValueError('Compiled window bytes differ from exact profile')
    return {'machine': 'AArch64', 'type': 'ET_DYN', 'needed_libraries': [],
            'undefined_symbols': [], 'relative_relocations': len(relocations)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--zig', default='zig')
    args = parser.parse_args()
    version = subprocess.check_output([args.zig, 'version'], text=True).strip()
    if version != '0.13.0':
        parser.error('Use the pinned Zig 0.13.0 compiler')
    base = verify()
    patches = json.loads((base / 'assets/patches.json').read_text())
    entries, text = [], ''
    # Keep the pinned worker's CP-then-AP write/rollback ordering.
    for chip in (1, 0):
        program = patches[chip]
        for window in program['writes']:
            entries.append((int(window['file_offset'], 16), chip,
                            bytes.fromhex(window['before_hex']), bytes.fromhex(window['after_hex'])))
    entries.extend((offset, 0, before, ntsc.AFTER) for offset, before in ntsc.PATCHES)
    if len(entries) != 42:
        raise ValueError('Unexpected profile window count')
    for chip in (0, 1):
        selected = sorted(e for e in entries if e[1] == chip)
        if any(a[0] + len(a[2]) > b[0] for a, b in zip(selected, selected[1:])):
            raise ValueError('Overlapping fixed windows')
    for i, (_, _, before, after) in enumerate(entries):
        if not before or len(before) != len(after) or len(before) > 4096:
            raise ValueError('Unexpected fixed window length')
        text += array('old_' + str(i), before) + array('new_' + str(i), after)
    text += 'static const struct Window windows[]={' + ','.join(
        f'{{{offset}UL,{len(before)},{chip},old_{i},new_{i}}}'
        for i, (offset, chip, before, after) in enumerate(entries)) + '};\n'
    text += 'static const u32 window_count=42;\n'
    text += 'static const u64 file_sizes[]={99960744UL,18090880UL};\n'
    for name, hashes in [('original_hashes', [
            '2ea02f7ecbc5bb917316fe8ef4ce5ffd87eb66efaae78ba2ce11420552df539b',
            'f5a477588698ef965748903b8959a39ce4f59ffd4aee4d4234ef1670f56a1428']),
            ('final_hashes', [ntsc.AP_COMBINED, ntsc.CP_FL23])]:
        text += f'static const u8 {name}[2][32]={{' + ','.join(
            '{' + ','.join(map(str, bytes.fromhex(value))) + '}' for value in hashes) + '};\n'
    runtime = (base / 'native/runtime.c').read_text()
    substitutions = {
        'data[6][2][128]': 'data[8][2][128]',
        'offsets[6]={0x3528804,0x4b2ed19,0x59892b8,0x5989370,0x4b4a5e4,0x4b3b544}':
            'offsets[8]={0x3528804,0x4b2ed19,0x59892b8,0x5989370,0x4b4a5e4,0x4b3b544,0x20617b0,0x2061d14}',
        'lengths[6]={112,2,40,40,12,12}': 'lengths[8]={112,2,40,40,12,12,4,4}',
        'runtime.count=6': 'runtime.count=8', 'i<6': 'i<8',
    }
    for before, after in substitutions.items():
        if runtime.count(before) != 1:
            raise ValueError('Upstream runtime source does not match expected template')
        runtime = runtime.replace(before, after)
    flags = ['cc', '-target', 'aarch64-linux-gnu', '-shared', '-fPIC', '-O2', '-g0',
             '-ffreestanding', '-fno-builtin', '-fno-stack-protector',
             '-fno-unwind-tables', '-fno-asynchronous-unwind-tables', '-nostdlib',
             '-Wl,--no-undefined', '-Wl,-Bsymbolic', '-Wl,-z,noexecstack', '-Wl,--strip-all']
    manifest = {'format': 1, 'features': ntsc.FEATURES, 'compiler': 'Zig ' + version,
                'status': 'offline_only_not_camera_validated', 'files': {}, 'elf': {}}
    with tempfile.TemporaryDirectory(prefix='a7c2-ntsc-build-') as directory:
        directory = Path(directory)
        (directory / 'combined_manifest.h').write_text(text)
        (directory / 'runtime.c').write_text(runtime)
        for name, source in [('installer', base / 'native/installer.c'),
                             ('runtime', directory / 'runtime.c')]:
            output = directory / (name + '.so')
            command = [args.zig, *flags, '-I', str(directory), '-I', str(base / 'native')]
            if name == 'installer':
                command += ['-DPATCH_MANIFEST="combined_manifest.h"']
            subprocess.run([*command, '-o', str(output), str(source)], check=True)
            raw = output.read_bytes()
            asset_name = 'ntsc-' + name + '.so'
            manifest['elf'][asset_name] = validate_elf(raw, name, entries)
            manifest['files'][asset_name] = hashlib.sha256(raw).hexdigest()
            (ntsc.ASSETS / asset_name).write_bytes(raw)
    (ntsc.ASSETS / 'ntsc-manifest.json').write_text(json.dumps(manifest, indent=2) + '\n')
    print(json.dumps(manifest, indent=2))


if __name__ == '__main__':
    main()
