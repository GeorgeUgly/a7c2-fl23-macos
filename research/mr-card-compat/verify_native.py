"""Execute the original AArch64 MR parameter validator in Unicorn, offline.

Only two reporting functions are substituted. Every parameter decision uses
the real dispatcher, capability tables and original checking instructions.
The valid model fixture is generated from native constraints, not body data.
"""
import io
import re
import struct

from capstone import Cs, CS_ARCH_ARM64, CS_MODE_LITTLE_ENDIAN
from unicorn import (Uc, UC_ARCH_ARM64, UC_MODE_ARM, UC_HOOK_CODE,
                     UC_HOOK_MEM_WRITE, UC_PROT_READ, UC_PROT_EXEC)
from unicorn import arm64_const as regs
from elftools.elf.elffile import ELFFile

from patch_profile import OFFSET, BEFORE, AFTER, sha256

DISPATCH = 0x28BBCC8
CHECK_START, CHECK_END = 0x2E2232C, 0x2E2E380
TABLE_FILE = 0x56B4118
MASK_VA = 0x4B2DCF8
LOOK_IDS = range(0x14E9, 0x151F)
MODEL_BYTES = 0x2074
STACK, MODEL, ERRORS, RETURN = 0x70000000, 0x71000000, 0x72000000, 0x73000000


def verify_elf_layout(raw):
    elf = ELFFile(io.BytesIO(raw))
    assert elf['e_machine'] == 'EM_AARCH64' and elf['e_type'] == 'ET_DYN'
    for va, expected_file, section_name in [
        (OFFSET, OFFSET, '.rodata'), (0x56C4118, TABLE_FILE, '.data.rel.ro'),
    ]:
        sections = [s for s in elf.iter_sections()
                    if s['sh_addr'] <= va < s['sh_addr'] + s['sh_size']]
        assert len(sections) == 1 and sections[0].name == section_name
        assert sections[0]['sh_offset'] + va - sections[0]['sh_addr'] == expected_file
    references = [r for section in elf.iter_sections() if section['sh_type'] == 'SHT_RELA'
                  for r in section.iter_relocations()
                  if MASK_VA <= r['r_addend'] < MASK_VA + 12]
    # Independent of the model-table search: no additional relocated readers
    # point at the mask or its interior bytes in this exact ELF.
    assert len(references) == 54
    assert {r['r_addend'] for r in references} == {MASK_VA}
    assert all(r['r_info_type'] == 1027 for r in references)
    expected = {0x56C4118 + pid * 24 + 8 for pid in LOOK_IDS}
    assert {r['r_offset'] for r in references} == expected


class NativeValidator:
    def __init__(self, raw):
        self.raw = raw
        self.emu = Uc(UC_ARCH_ARM64, UC_MODE_ARM)
        # Explicit VA/file mapping: .data.rel.ro has a 0x10000 delta.
        for va, offset, length in [
            (0x28BB000, 0x28BB000, 0x1000),
            (0x2E22000, 0x2E22000, 0xD000),
            (0x4B2A000, 0x4B2A000, 0x12000),
            (0x56C4000, 0x56B4000, 0x54000),
        ]:
            permissions = UC_PROT_READ | (UC_PROT_EXEC if va < 0x3700000 else 0)
            self.emu.mem_map(va, length, permissions)
            self.emu.mem_write(va, raw[offset:offset + length])
        for va, length in [(STACK, 0x10000), (MODEL, 0x3000),
                           (ERRORS, 0x4000), (RETURN, 0x1000),
                           (0x2E1B000, 0x1000), (0x3682000, 0x1000)]:
            self.emu.mem_map(va, length)
        decoder = Cs(CS_ARCH_ARM64, CS_MODE_LITTLE_ENDIAN)
        self.loads = {}
        for ins in decoder.disasm(raw[CHECK_START:CHECK_END], CHECK_START):
            if ins.mnemonic == 'ldr' and ins.op_str.startswith('w1, ['):
                match = re.fullmatch(r'w1, \[x(\d+)(?:, #(0x[\da-f]+|[0-9]+))?\]', ins.op_str)
                if not match:
                    raise ValueError('Unrecognized native scalar load')
                self.loads[ins.address] = (int(match[1]), int(match[2], 0) if match[2] else 0)
        self.seeding = False
        self.full = False
        self.seen, self.rejections = [], []
        self.last_source = None
        self.emu.hook_add(UC_HOOK_CODE, self.hook)
        self.emu.hook_add(UC_HOOK_MEM_WRITE, self.on_write)

    def on_write(self, emu, access, address, size, value, data):
        # Validating a fixture must never modify its input or the native tables.
        # Fixture construction uses host mem_write, not guest stores.
        if not STACK <= address < address + size <= STACK + 0x10000:
            raise ValueError('Native parameter validator wrote outside its stack')

    def entry(self, pid):
        return struct.unpack_from('<QQI', self.raw, TABLE_FILE + 24 * pid)

    def seed_value(self, pid):
        function, pointer, count = self.entry(pid)
        if function == 0x28BBAD4:
            return next(i for i in range(count) if self.raw[pointer + i])
        if function == 0x28BBAF4:
            return struct.unpack_from('<i', self.raw, pointer)[0] & 0xFFFFFFFF
        if function in (0x28BBB48, 0x28BBB60, 0x28BBB68):
            return 0
        raise ValueError('Unrecognized native fixture constraint')

    def hook(self, emu, pc, size, data):
        if self.full and pc in self.loads:
            register, offset = self.loads[pc]
            self.last_source = emu.reg_read(getattr(regs, 'UC_ARM64_REG_X' + str(register))) + offset
        if self.full and pc == DISPATCH:
            pid = emu.reg_read(regs.UC_ARM64_REG_W0)
            value = emu.reg_read(regs.UC_ARM64_REG_W1)
            address = self.last_source
            if address is None or not MODEL <= address <= MODEL + MODEL_BYTES - 4:
                raise ValueError('Parameter load escaped the model fixture')
            self.seen.append((pid, address - MODEL, value))
            if self.seeding:
                value = self.seed_value(pid)
                emu.mem_write(address, struct.pack('<I', value))
                emu.reg_write(regs.UC_ARM64_REG_W1, value)
        elif pc in (0x2E1B068, 0x368297C):
            if pc == 0x368297C:
                self.rejections.append((emu.reg_read(regs.UC_ARM64_REG_W0),
                                        emu.reg_read(regs.UC_ARM64_REG_W1)))
            # Only the error-list recorder and diagnostic logger are skipped.
            emu.reg_write(regs.UC_ARM64_REG_PC, emu.reg_read(regs.UC_ARM64_REG_X30))
        elif pc == RETURN:
            emu.emu_stop()

    def call(self, start, args):
        self.rejections.clear()
        self.seen.clear()
        preserved = {getattr(regs, 'UC_ARM64_REG_X' + str(i)):
                     0xA710000000000000 + i * 0x01010101 for i in range(19, 30)}
        for register, value in preserved.items():
            self.emu.reg_write(register, value)
        for i, value in enumerate(args):
            self.emu.reg_write(getattr(regs, 'UC_ARM64_REG_X' + str(i)), value)
        self.emu.reg_write(regs.UC_ARM64_REG_SP, STACK + 0xF000)
        self.emu.reg_write(regs.UC_ARM64_REG_X30, RETURN)
        self.emu.emu_start(start, RETURN, count=100000)
        if self.emu.reg_read(regs.UC_ARM64_REG_PC) != RETURN:
            raise ValueError('Native checker did not return within its instruction budget')
        if self.emu.reg_read(regs.UC_ARM64_REG_SP) != STACK + 0xF000:
            raise ValueError('Native checker did not restore its stack')
        if any(self.emu.reg_read(register) != value for register, value in preserved.items()):
            raise ValueError('Native checker violated the callee-saved register ABI')
        return self.emu.reg_read(regs.UC_ARM64_REG_W0)

    def model(self, fixture=None, seed=False):
        self.full, self.seeding = True, seed
        if fixture is not None:
            if len(fixture) != MODEL_BYTES:
                raise ValueError('Model fixture length mismatch')
            self.emu.mem_write(MODEL, bytes(fixture))
        try:
            return self.call(CHECK_START, [MODEL, 0, ERRORS])
        finally:
            self.full, self.seeding = False, False

    def scalar(self, pid, value):
        return self.call(DISPATCH, [pid, value, 0, 0])


def verify(raw, candidate):
    verify_elf_layout(raw)
    verify_elf_layout(candidate)
    old, new = NativeValidator(raw), NativeValidator(candidate)
    # Discover all and only the 54 native look bindings using this shared mask.
    references = [pid for pid in range(0x379D) if old.entry(pid)[1] == MASK_VA]
    assert references == list(LOOK_IDS)
    assert raw[OFFSET:OFFSET + 2] == BEFORE
    assert candidate[OFFSET:OFFSET + 2] == AFTER
    assert raw[:OFFSET] == candidate[:OFFSET] and raw[OFFSET + 2:] == candidate[OFFSET + 2:]
    values = [*range(257), 0x7FFFFFFF, 0x80000000, 0xFFFFFFFF]
    changed_decisions = 0
    for pid in LOOK_IDS:
        function, pointer, count = old.entry(pid)
        assert (function, pointer, count) == (0x28BBAD4, MASK_VA, 12)
        for value in values:
            before, after = old.scalar(pid, value), new.scalar(pid, value)
            assert before == int(value < 10)
            assert after == int(value < 12)
            changed_decisions += before != after
    assert changed_decisions == 108
    assert old.model(seed=True) == 1
    fixture = bytes(old.emu.mem_read(MODEL, MODEL_BYTES))
    assert old.model(fixture) == new.model(fixture) == 1
    assert len(old.seen) == 2018
    families = [(0x14E9, 0x186C), (0x14FB, 0x18B4), (0x150D, 0x18FC)]
    actual = [(pid, offset) for pid, offset, _ in old.seen if pid in {p for p, _ in families}]
    expected = [(pid, offset + 4 * slot) for pid, offset in families for slot in range(18)]
    assert actual == expected
    original_look_cases = 0
    for shift in range(10):
        sample = bytearray(fixture)
        struct.pack_into('<I', sample, 0x834, shift)
        for family, base in families:
            for slot in range(18):
                struct.pack_into('<I', sample, base + 4 * slot, (slot + shift) % 10)
        assert old.model(sample) == new.model(sample) == 1
        assert old.rejections == new.rejections == []
        assert bytes(old.emu.mem_read(MODEL, MODEL_BYTES)) == sample
        assert bytes(new.emu.mem_read(MODEL, MODEL_BYTES)) == sample
        original_look_cases += 1
    full_cases = []
    for pid, base in families:
        for slot in range(18):
            for value in (10, 11, 12, 0xFFFFFFFF):
                sample = bytearray(fixture)
                struct.pack_into('<I', sample, base + 4 * slot, value)
                before, after = old.model(sample), new.model(sample)
                assert before == 0 and old.rejections == [(pid, value)]
                assert after == int(value in (10, 11))
                assert new.rejections == ([] if after else [(pid, value)])
                full_cases.append({'family': hex(pid), 'slot': slot, 'value': value,
                                   'before': before, 'after': after})
    # Selected FL2/FL3 plus bindings, all three families together.
    for look, slot, value in [(17, 16, 10), (18, 17, 11)]:
        sample = bytearray(fixture)
        struct.pack_into('<I', sample, 0x834, look)
        for _, base in families:
            struct.pack_into('<I', sample, base + 4 * slot, value)
        assert old.model(sample) == 0 and len(old.rejections) == 3
        assert new.model(sample) == 1 and new.rejections == []
    # An unrelated field must still fail after the fix.
    invalid = bytearray(fixture)
    struct.pack_into('<I', invalid, 0x834, 19)
    assert old.model(invalid) == new.model(invalid) == 0
    assert old.rejections == new.rejections == [(0x66B, 19)]
    # Correct new bindings must not hide an independent bad field.
    mixed = bytearray(fixture)
    for _, base in families:
        for slot in range(18):
            struct.pack_into('<I', mixed, base + slot * 4, 10 + slot % 2)
    assert old.model(mixed) == 0 and len(old.rejections) == 54
    assert new.model(mixed) == 1 and not new.rejections
    struct.pack_into('<I', mixed, 0x834, 19)
    assert new.model(mixed) == 0 and new.rejections == [(0x66B, 19)]
    return {
        'elf_section_and_relocation_crosscheck': True,
        'native_tables_readonly_in_emulator': True,
        'callee_saved_registers_and_stack_preserved': True,
        'guest_writes_restricted_to_stack': True,
        'scalar_comparisons': len(LOOK_IDS) * len(values),
        'changed_decisions': changed_decisions,
        'original_look_values_0_through_9_unchanged': True,
        'out_of_range_values_still_rejected': True,
        'parameters_checked_per_full_model': 2018,
        'full_model_binding_cases': len(full_cases),
        'selected_fl2_fl3_cases': 2,
        'complete_model_original_look_cases': original_look_cases,
        'all_54_new_bindings_with_independent_bad_field': 'rejected',
        'unrelated_invalid_field_still_rejected': True,
        'synthetic_model_sha256': sha256(fixture),
        'logging_only_substitutions': ['0x2e1b068', '0x368297c'],
        'cases': full_cases,
    }
