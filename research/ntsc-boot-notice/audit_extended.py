"""Offline NTSC review through the native screen-selection call.

This extends the earlier gate-only experiment. External predicates are explicit
fixtures; their combinations are not proof of reachability on real hardware.
No camera APIs or firmware writer are used.
"""
import argparse
import hashlib
import itertools
import json
from pathlib import Path
import struct

from unicorn import (Uc, UC_ARCH_ARM64, UC_MODE_ARM, UC_HOOK_CODE,
                     UC_HOOK_MEM_WRITE, UC_PROT_READ, UC_PROT_EXEC)
from unicorn import arm64_const as regs

GATE_PATCHES = [(0x20617B0, bytes.fromhex('1f00016b')),
                (0x2061D14, bytes.fromhex('3f00006b'))]
AFTER = bytes.fromhex('3f00206a')
FL23 = '1aaeb8fbe0297950cab454d498d1a5ba874f91542aa2737aae27a73971e324d7'
COMBINED = '31b7336d39e211bdd9d2cb51d17e831a81703b86b9fab81bd40c97081fd4e8c9'
MR_COMBINED = 'f75abf49555190549898fcd8d96562e5884898addbc970023642a7bbe5896ddd'


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def audit_comparison_instructions():
    comparisons = 0
    for _, original in GATE_PATCHES:
        for current, default, nzcv in itertools.product(range(2), range(2), range(16)):
            for instruction, zero in [(original, current == default),
                                      (AFTER, current & ~default == 0)]:
                emu = Uc(UC_ARCH_ARM64, UC_MODE_ARM)
                emu.mem_map(0x1000, 0x1000, UC_PROT_READ | UC_PROT_EXEC)
                emu.mem_write(0x1000, instruction)
                for i in range(31):
                    emu.reg_write(getattr(regs, 'UC_ARM64_REG_X' + str(i)), 0xA7000000 + i)
                emu.reg_write(regs.UC_ARM64_REG_X0, 0xDEADBEEF00000000 | default)
                emu.reg_write(regs.UC_ARM64_REG_X1, 0xAABBCCDD00000000 | current)
                emu.reg_write(regs.UC_ARM64_REG_SP, 0x70004000)
                emu.reg_write(regs.UC_ARM64_REG_NZCV, nzcv << 28)
                before_regs = tuple(emu.reg_read(getattr(regs, 'UC_ARM64_REG_X' + str(i)))
                                    for i in range(31))
                emu.emu_start(0x1000, 0x1004, count=1)
                assert bool(emu.reg_read(regs.UC_ARM64_REG_NZCV) & (1 << 30)) == zero
                assert before_regs == tuple(emu.reg_read(getattr(regs, 'UC_ARM64_REG_X' + str(i)))
                                            for i in range(31))
                assert emu.reg_read(regs.UC_ARM64_REG_SP) == 0x70004000
            comparisons += 1
    return comparisons


def execute(raw, path, current, default, reset, boot_type, display,
            pending, other_id, predicate_a, predicate_b, selector_b,
            predicate_c, region=1):
    uc = Uc(UC_ARCH_ARM64, UC_MODE_ARM)
    uc.mem_map(0x2060000, 0x3000, UC_PROT_READ | UC_PROT_EXEC)
    uc.mem_write(0x2060000, raw[0x2060000:0x2063000])
    uc.mem_map(0x70000000, 0x10000)
    fp = 0x70004000
    for i in range(29):
        uc.reg_write(getattr(regs, 'UC_ARM64_REG_X' + str(i)), 0xA7000000 + i)
    uc.reg_write(regs.UC_ARM64_REG_X29, fp)
    uc.reg_write(regs.UC_ARM64_REG_SP, fp)
    for offset, value in [(0xA8, region), (0xAC, current), (0xB8, default),
                          (0xA4, reset), (0x98, boot_type)]:
        uc.mem_write(fp + offset, struct.pack('<I', value))
    for register, value in [(19, fp + 0xE0), (20, fp + 0xD0),
                            (21, 0x70008000), (23, 0), (25, other_id), (28, pending)]:
        uc.reg_write(getattr(regs, 'UC_ARM64_REG_X' + str(register)), value)
    # Calls are identified by their actual BL destination, not by an outcome.
    display_pointer, selector_pointer = 0x70009000, 0x70009010
    uc.mem_write(display_pointer, struct.pack('<I', display))
    uc.mem_write(selector_pointer, struct.pack('<I', selector_b))
    fixtures = {
        0x2E06CC0: None, 0x2E068CC: None,  # lock/unlock
        0x251DD4C: None, 0x251DD5C: None,  # model access scope
        0x1660700: display_pointer,
        0x1660718: selector_pointer,
        0x205F250: predicate_a,
        0x205F1EC: predicate_b,
        0x25AA880: predicate_c,
    }
    result, trace = {}, []

    def on_write(emu, access, address, size, value, data):
        raise AssertionError('Native selection window unexpectedly wrote memory')

    def on_code(emu, pc, size, data):
        trace.append(pc)
        word = struct.unpack_from('<I', raw, pc)[0]
        if word & 0xFC000000 != 0x94000000:
            return
        displacement = word & 0x3FFFFFF
        displacement = (displacement ^ 0x2000000) - 0x2000000
        target = pc + displacement * 4
        if target == 0x205F144:
            result.update(message_id=emu.reg_read(regs.UC_ARM64_REG_X2),
                          mode=emu.reg_read(regs.UC_ARM64_REG_X1))
            emu.emu_stop()
            return
        if target not in fixtures:
            raise AssertionError('Unexpected external call: ' + hex(target))
        value = fixtures[target]
        if value is not None:
            emu.reg_write(regs.UC_ARM64_REG_X0, value)
        emu.reg_write(regs.UC_ARM64_REG_PC, pc + 4)

    uc.hook_add(UC_HOOK_CODE, on_code)
    uc.hook_add(UC_HOOK_MEM_WRITE, on_write)
    uc.emu_start(0x206179C if path == 'first' else 0x2061CF8, 0x2062200, count=300)
    assert result and uc.reg_read(regs.UC_ARM64_REG_SP) == fp
    registers = tuple(uc.reg_read(getattr(regs, 'UC_ARM64_REG_X' + str(i))) for i in range(31))
    return result, trace, registers


def main():
    if not __debug__:
        raise RuntimeError('Do not disable verification assertions')
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', required=True, type=Path)
    parser.add_argument('--mr-combined', required=True, type=Path)
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args()
    assert not args.output.exists() and not args.output.is_symlink()
    before = args.source.read_bytes()
    assert sha(before) == FL23
    changed = bytearray(before)
    for offset, expected in GATE_PATCHES:
        assert before[offset:offset + 4] == expected
        changed[offset:offset + 4] = AFTER
    after = bytes(changed)
    assert sha(after) == COMBINED
    mr = args.mr_combined.read_bytes()
    assert sha(mr) == MR_COMBINED
    assert mr[0x2060000:0x2063000] == after[0x2060000:0x2063000]
    flag_comparisons = audit_comparison_instructions()
    counts = {'total': 0, 'unchanged': 0, 'ntsc_selection_changed': 0,
              'other_selection_changed': 0, 'matches_native_fallback_reference': 0}
    cases, examples = [], []
    # These pairs are taken from assignments before the gates. The earlier
    # branch excludes predicate_b=true with selector_b!=0, so omit that pair.
    pending_pairs = [(0, 0x4B7), (0, 0x36C), (1, 0x390), (1, 0x391)]
    predicate_b_pairs = [(0, 0), (0, 1), (1, 0)]
    for path, current, default, reset, boot_type, display, pending_pair, predicate_a, b_pair, predicate_c in itertools.product(
        ('first', 'second'), range(2), range(2), range(2), (0, 3), range(2),
        pending_pairs, range(2), predicate_b_pairs, range(2)):
        pending, other_id = pending_pair
        predicate_b, selector_b = b_pair
        params = (path, current, default, reset, boot_type, display,
                  pending, other_id, predicate_a, predicate_b, selector_b, predicate_c)
        old, old_trace, old_regs = execute(before, *params)
        new, new_trace, new_regs = execute(after, *params)
        bypass = current == 0 and default == 1 and (
            (path == 'first' and reset == 0) or (path == 'second' and boot_type == 3))
        if bypass:
            reference, _, _ = execute(before, *params, region=0)
            assert new == reference
            counts['matches_native_fallback_reference'] += 1
        else:
            assert old == new and old_trace == new_trace and old_regs == new_regs
        if old == new:
            counts['unchanged'] += 1
        elif old['message_id'] == 0x36B:
            assert bypass and new['message_id'] != 0x36B
            counts['ntsc_selection_changed'] += 1
        else:
            assert bypass
            counts['other_selection_changed'] += 1
        counts['total'] += 1
        row = {'path': path, 'current': current, 'default': default, 'reset': reset,
               'boot_type': boot_type, 'display': display, 'pending': pending,
               'other_id': hex(other_id), 'predicate_205f250': predicate_a,
               'predicate_205f1ec': predicate_b, 'selector_1660718': selector_b,
               'predicate_25aa880': predicate_c, 'before': old, 'after': new}
        cases.append(row)
        if old != new and old['message_id'] != 0x36B and len(examples) < 8:
            examples.append(row)
    assert counts['other_selection_changed'] > 0
    assert sha(args.source.read_bytes()) == FL23
    assert sha(args.mr_combined.read_bytes()) == MR_COMBINED
    report = {
        'status': 'offline_review_requires_prompt_priority_caveat',
        'camera_io': False, 'installed': False,
        'source_ap_sha256': FL23, 'combined_ap_sha256': COMBINED,
        'mr_combined_ap_sha256': MR_COMBINED,
        'mr_combined_boot_window_identical': True,
        'firmware_patch_changed_by_this_review': False,
        'counts': counts,
        'outside_target_gate_cases_have_identical_instruction_trace_and_gprs': True,
        'outside_target_gate_cases': counts['total'] - counts['matches_native_fallback_reference'],
        'isolated_instruction_flag_comparisons': flag_comparisons,
        'isolated_comparisons_preserve_all_gprs_and_stack': True,
        'native_window_memory_writes': 0,
        'external_predicate_fixtures': ['0x205f250', '0x205f1ec', '0x25aa880'],
        'examples_of_other_selection_changes': examples,
        'conclusion': 'The patch bypasses the NTSC condition, not just its displayed text. Entering the normal fallback can change message IDs other than 0x36b in fixture combinations; their actual UI text is not identified.',
        'limitations': [
            'No complete boot, actual rendering, persisted settings, USB or capture were executed.',
            'External predicates and getters are fixtures; complete real-device reachability is not established.',
            'The extended selection matrix initializes the later mode selector x23 to zero; the earlier gate-only matrix covered both selector branches.',
            'Message IDs other than NTSC/PAL are intentionally left unnamed without decoded UI resources.',
        ],
    }
    args.output.mkdir(mode=0o700)
    for name, value in [('review.json', report), ('extended-cases.json', cases)]:
        target = args.output / name
        with target.open('x') as stream:
            json.dump(value, stream, indent=2)
            stream.write('\n')
        target.chmod(0o600)
    print(json.dumps(report, indent=2))


if __name__ == '__main__':
    main()
