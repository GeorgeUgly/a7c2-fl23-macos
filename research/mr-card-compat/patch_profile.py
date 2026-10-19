"""Exact-source, offline-only MR-card compatibility candidate for A7C II 2.01.

No device access, installer integration, state migration, or in-place writes.
"""
import hashlib

AP_BYTES = 99960744
OFFSET = 0x4B2DD02
BEFORE = b'\x00\x00'
AFTER = b'\x01\x01'
CP_SHA256 = '60a9de1a0636c7f6b5b98607a4d25eeb48634e25294f632743a6d256e583b370'
PROFILES = {
    'fl23': (
        '1aaeb8fbe0297950cab454d498d1a5ba874f91542aa2737aae27a73971e324d7',
        '22137afdb226ed2a5c0804eb95e802f4d697955fa793c7661e6b14bd48127d89',
    ),
    'fl23_no_ntsc_notice': (
        '31b7336d39e211bdd9d2cb51d17e831a81703b86b9fab81bd40c97081fd4e8c9',
        'f75abf49555190549898fcd8d96562e5884898addbc970023642a7bbe5896ddd',
    ),
}


def sha256(raw):
    return hashlib.sha256(raw).hexdigest()


def identify(raw, reverse=False):
    digest = sha256(raw)
    matches = [name for name, pair in PROFILES.items()
               if len(raw) == AP_BYTES and digest == pair[int(reverse)]]
    if len(matches) != 1:
        raise ValueError('Unsupported full AP fingerprint; no candidate produced')
    return matches[0]


def patch_ap(raw, reverse=False):
    name = identify(raw, reverse)
    old, new = (AFTER, BEFORE) if reverse else (BEFORE, AFTER)
    if raw[OFFSET:OFFSET + 2] != old:
        raise ValueError('MR-card capability window differs from the exact profile')
    result = bytearray(raw)
    result[OFFSET:OFFSET + 2] = new
    if sha256(result) != PROFILES[name][int(not reverse)]:
        raise ValueError('Candidate full AP fingerprint mismatch')
    return bytes(result)
