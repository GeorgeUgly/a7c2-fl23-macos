"""Offline A7C II 2.01 NTSC boot-notice experiment. No camera I/O.

Executes the real local boot-condition instruction windows in Unicorn.
External mutex/getter calls are explicit harness substitutes, not device tests.
"""
import argparse
from pathlib import Path
import hashlib
import itertools
import json
import struct

from capstone import Cs, CS_ARCH_ARM64, CS_MODE_LITTLE_ENDIAN
from elftools.elf.elffile import ELFFile
from unicorn import Uc, UC_ARCH_ARM64, UC_MODE_ARM, UC_HOOK_CODE, UC_HOOK_MEM_WRITE
from unicorn.arm64_const import (
    UC_ARM64_REG_PC, UC_ARM64_REG_SP, UC_ARM64_REG_X0, UC_ARM64_REG_X2,
    UC_ARM64_REG_X19, UC_ARM64_REG_X20, UC_ARM64_REG_X21,
    UC_ARM64_REG_X23, UC_ARM64_REG_X25, UC_ARM64_REG_X28,
    UC_ARM64_REG_X29,
)

PATCHES = [
    (0x20617b0, bytes.fromhex('1f00016b')),
    (0x2061d14, bytes.fromhex('3f00006b')),
]
REPLACEMENT = bytes.fromhex('3f00206a')  # bics wzr, w1, w0
DECODER = Cs(CS_ARCH_ARM64, CS_MODE_LITTLE_ENDIAN)


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def dump(raw, start, end):
    return '\n'.join(f'{i.address:08x} {i.mnemonic:10} {i.op_str}'
                     for i in DECODER.disasm(raw[start:end], start)) + '\n'


def experiment(raw, path, dst, current, default, reset, boot_type, display, other):
    """Run original instructions from the gate until its native continuation.

    Does not execute full boot or draw a screen. 'mode_notice' identifies the
    native downstream ID; 'continue' reaches the original fallback entry.
    """
    uc = Uc(UC_ARCH_ARM64, UC_MODE_ARM)
    uc.mem_map(0x2060000, 0x3000)
    uc.mem_write(0x2060000, raw[0x2060000:0x2063000])
    uc.mem_map(0x70000000, 0x10000)
    fp = 0x70004000
    uc.reg_write(UC_ARM64_REG_X29, fp)
    uc.reg_write(UC_ARM64_REG_SP, fp)
    # Values of the real locals established before these condition windows.
    for offset, value in [(0xa8, dst), (0xac, current), (0xb8, default),
                          (0xa4, reset), (0x98, boot_type)]:
        uc.mem_write(fp + offset, struct.pack('<I', value))
    uc.reg_write(UC_ARM64_REG_X19, fp + 0xe0)
    uc.reg_write(UC_ARM64_REG_X20, fp + 0xd0)
    uc.reg_write(UC_ARM64_REG_X21, 0x70008000)
    uc.reg_write(UC_ARM64_REG_X23, other)
    uc.reg_write(UC_ARM64_REG_X25, 0x391)
    uc.reg_write(UC_ARM64_REG_X28, other)
    display_ptr = 0x70009000
    uc.mem_write(display_ptr, struct.pack('<I', display))
    # These calls are replaced explicitly: lock, property getter, unlock.
    calls = {0x20617c8: None, 0x20617cc: display_ptr, 0x20617d8: None,
             0x2061d20: None, 0x2061d24: display_ptr, 0x2061d30: None}
    fallback = 0x206189c if path == 'first_gate' else 0x2061dd0
    # 0x2061568 / 0x2061a74 feed the original generic boot-screen selector.
    commits = {0x2061568, 0x2061a74}
    outcome = {}
    writes = []

    def on_write(emu, access, address, size, value, data):
        writes.append((address, size))

    def on_code(emu, address, size, data):
        if address == fallback:
            outcome.update(kind='continue', pc=hex(address))
            emu.emu_stop()
        elif address in commits:
            outcome.update(kind='selection', id=emu.reg_read(UC_ARM64_REG_X2),
                           pc=hex(address))
            emu.emu_stop()
        elif address in (0x20617f0, 0x2061d48):
            outcome.update(kind='invalid_enum_assertion', pc=hex(address))
            emu.emu_stop()
        elif address in calls:
            value = calls[address]
            if value is not None:
                emu.reg_write(UC_ARM64_REG_X0, value)
            emu.reg_write(UC_ARM64_REG_PC, address + 4)

    uc.hook_add(UC_HOOK_CODE, on_code)
    uc.hook_add(UC_HOOK_MEM_WRITE, on_write)
    uc.emu_start(0x206179c if path == 'first_gate' else 0x2061cf8,
                 0x2062200, count=200)
    if not outcome:
        raise AssertionError('No recognized native continuation')
    if writes:
        raise AssertionError('Condition window unexpectedly wrote memory')
    return outcome


def main():
    if not __debug__:
        raise RuntimeError('Do not run firmware analysis with Python optimization enabled')
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--workspace', required=True, type=Path,
                        help='Private existing workspace containing original/ and candidate/')
    parser.add_argument('--output', required=True, type=Path,
                        help='Private output directory outside this repository')
    args = parser.parse_args()
    repo = Path(__file__).resolve().parents[2]
    work, output = args.workspace.expanduser().resolve(), args.output.expanduser().resolve()
    if output == repo or repo in output.parents:
        parser.error('Keep generated firmware and reports outside the repository')
    output.mkdir(mode=0o700, parents=False, exist_ok=True)
    if output.stat().st_mode & 0o077:
        parser.error('Output directory must be private (0700)')
    sources = {
        'stock_ap': (work / 'original/appFw.so.bin',
            '2ea02f7ecbc5bb917316fe8ef4ce5ffd87eb66efaae78ba2ce11420552df539b'),
        'fl23_ap': (work / 'candidate/appFw.so',
            '1aaeb8fbe0297950cab454d498d1a5ba874f91542aa2737aae27a73971e324d7'),
        'fl23_cp': (work / 'candidate/cpapp-b.bin',
            '60a9de1a0636c7f6b5b98607a4d25eeb48634e25294f632743a6d256e583b370'),
    }
    inputs = {}
    for name, (path, expected) in sources.items():
        raw = path.read_bytes()
        assert digest(raw) == expected, 'Unexpected input fingerprint: ' + name
        inputs[name] = raw
    with sources['fl23_ap'][0].open('rb') as stream:
        elf = ELFFile(stream)
        assert elf['e_machine'] == 'EM_AARCH64'
        for offset, before in PATCHES:
            sections = [s for s in elf.iter_sections()
                        if s['sh_addr'] <= offset < s['sh_addr'] + s['sh_size']]
            assert len(sections) == 1
            section = sections[0]
            assert section.name == '.text'
            assert section['sh_addr'] == section['sh_offset']
    instruction = list(DECODER.disasm(REPLACEMENT, 0))
    assert len(instruction) == 1
    assert instruction[0].mnemonic == 'bics'
    assert instruction[0].op_str == 'wzr, w1, w0'
    patched = {}
    windows = [(0x2060dcc, 0x20613dc), (0x206179c, 0x20618e0),
               (0x2061cf8, 0x2061df0), (0x35aab18, 0x35aade4),
               (0x2d8e2e0, 0x2d8e32c), (0x35ab884, 0x35ab96c)]
    for name in ('stock_ap', 'fl23_ap'):
        raw = inputs[name]
        modified = bytearray(raw)
        for offset, before in PATCHES:
            assert raw[offset:offset + 4] == before
            modified[offset:offset + 4] = REPLACEMENT
        patched[name] = bytes(modified)
        changed = [i for i, (a, b) in enumerate(zip(raw, modified)) if a != b]
        allowed = {i for off, _ in PATCHES for i in range(off, off + 4)}
        assert set(changed) <= allowed and len(changed) > 0
        for index, (start, end) in enumerate(windows):
            (output / f'{name}-window{index}-before.asm.txt').write_text(dump(raw, start, end))
            (output / f'{name}-window{index}-after.asm.txt').write_text(dump(modified, start, end))

    cases = []
    counts = {'total': 0, 'ntsc_notice_removed': 0, 'ntsc_gate_bypassed_without_visible_notice': 0, 'unchanged': 0,
              'pal_notice_preserved': 0, 'invalid_model_enums': 0}
    for source in patched:
        for path in ('first_gate', 'second_gate'):
            for dst, current, default, reset, boot_type, display, other in itertools.product(
                    range(2), range(2), range(2), range(2), range(4), range(2), range(2)):
                params = (path, dst, current, default, reset, boot_type, display, other)
                before = experiment(inputs[source], *params)
                after = experiment(patched[source], *params)
                is_ntsc = before.get('kind') == 'selection' and before.get('id') == 0x36b
                bypass_gate = (dst == 1 and current == 0 and default == 1 and
                    ((path == 'first_gate' and reset == 0) or
                     (path == 'second_gate' and boot_type == 3)))
                if bypass_gate:
                    assert current == 0 and default == 1
                    assert after['kind'] == 'continue'
                    key = ('ntsc_notice_removed' if is_ntsc else
                           'ntsc_gate_bypassed_without_visible_notice')
                    counts[key] += 1
                else:
                    assert before == after, (source, params, before, after)
                    counts['unchanged'] += 1
                    if before.get('id') == 0x373:
                        counts['pal_notice_preserved'] += 1
                counts['total'] += 1
                cases.append({'source': source, 'path': path, 'dst': dst,
                    'current': current, 'default': default, 'reset': reset,
                    'boot_type': boot_type, 'display': display, 'other': other,
                    'before': before, 'after': after})
    # Also retain the native invalid-current assertion when the PAL-like enum
    # carries bit 1: this is a spot check, not a promise about corrupted state.
    for source in patched:
        for path in ('first_gate', 'second_gate'):
            for current in (2, 3):
                params = (path, 1, current, 1, 0, 3, 1, 0)
                assert experiment(inputs[source], *params) == experiment(patched[source], *params)
                counts['invalid_model_enums'] += 1

    candidate_dir = output / 'candidate'
    candidate_dir.mkdir(mode=0o700, exist_ok=True)
    candidate = candidate_dir / 'appFw-fl23-no-ntsc-notice.EXPERIMENTAL.so'
    if candidate.exists():
        assert candidate.read_bytes() == patched['fl23_ap']
    else:
        candidate.write_bytes(patched['fl23_ap'])
        candidate.chmod(0o600)
    report = {
        'status': 'OFFLINE_EXPERIMENT_PASSED_NOT_CAMERA_VALIDATED',
        'camera_io': False, 'installed': False,
        'inputs': {name: {'bytes': len(raw), 'sha256': digest(raw)}
                   for name, raw in inputs.items()},
        'patches': [{'file_offset_and_va': hex(off), 'before': before.hex(),
                     'after': REPLACEMENT.hex(), 'after_instruction': 'bics wzr, w1, w0'}
                    for off, before in PATCHES],
        'anchors': {
            'boot_controller_log_va': '0x4121d51',
            'boot_controller_log': '%s%s(%d) <NPAL> dst:%08x, setting:%08x, default:%08x, nw_rst:%02x',
            'boot_controller_init_string_va': '0x4121e48',
            'default_system_getter_va': '0x35aacfc',
            'current_system_getter_va': '0x35aab18',
            'current_system_model_getter_va': '0x287abd8',
            'region_gate_model_getter_va': '0x2881ecc',
            'ntsc_selected_message_id': '0x36b',
            'pal_selected_message_id': '0x373',
            'no_secondary_screen_id': '0x4b7',
        },
        'meaning': 'For validated enum values NTSC=0 and PAL=1, Z is set when current & ~default == 0. Only a differing NTSC notice is bypassed; a differing PAL notice still follows the stock path.',
        'counts': counts,
        'candidate': {'path': str(candidate.relative_to(output)),
                      'bytes': len(patched['fl23_ap']), 'sha256': digest(patched['fl23_ap'])},
        'limitations': ['Only selected real condition windows were emulated.',
                       'External mutex and display-property calls used explicit harness substitutes.',
                       'No full boot, rendering, persisted settings, USB, or capture tests.',
                       'Message IDs are identified by their NTSC/PAL selection context; localized UI resources were not decoded.',
                       'This analysis script has no camera writer; the optional installation workflow is managed separately by macfl23.'],
    }
    for name, (path, expected) in sources.items():
        assert digest(path.read_bytes()) == expected, 'Source changed: ' + name
    report['sources_unchanged'] = True
    (output / 'verification.json').write_text(json.dumps(report, indent=2) + '\n')
    (output / 'condition-cases.json').write_text(json.dumps(cases, indent=2) + '\n')
    print(json.dumps(report, indent=2))


if __name__ == '__main__':
    main()
