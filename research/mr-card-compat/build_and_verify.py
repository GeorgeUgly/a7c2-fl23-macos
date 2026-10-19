"""Build and emulate an offline MR-card candidate. Does not access a camera."""
import argparse
import importlib.metadata
import json
import os
from pathlib import Path
import sys

from patch_profile import OFFSET, BEFORE, AFTER, CP_SHA256, identify, patch_ap, sha256


def write_new(path, raw):
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(descriptor, 'wb') as stream:
        stream.write(raw)
        stream.flush()
        os.fsync(stream.fileno())
    if path.read_bytes() != raw:
        raise ValueError('Output readback mismatch')


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, required=True,
                        help='Existing exact FL2/FL3 appFw.so, with or without the NTSC option')
    parser.add_argument('--output', type=Path, required=True, help='New private output directory')
    args = parser.parse_args(argv)
    if sys.flags.optimize:
        parser.error('Do not disable native-verification assertions with -O/PYTHONOPTIMIZE')
    if args.output.exists() or args.output.is_symlink() or not args.output.parent.is_dir():
        parser.error('Output must be a new directory with an existing parent')
    raw = args.source.read_bytes()
    name = identify(raw)
    candidate = patch_ap(raw)
    if patch_ap(candidate, reverse=True) != raw:
        raise ValueError('Reverse patch did not restore the complete input')
    # Research dependencies are required only here, never by the normal CLI.
    from verify_native import verify
    verification = verify(raw, candidate)
    cases = verification.pop('cases')
    if sha256(args.source.read_bytes()) != sha256(raw):
        raise ValueError('Source changed during analysis')
    report = {
        'format': 1,
        'status': 'offline_candidate_not_camera_validated',
        'model': 'ILCE-7CM2', 'firmware': '2.01',
        'base_profile': name,
        'camera_io': False, 'installed': False,
        'sources_unchanged': True,
        'source_ap_sha256': sha256(raw),
        'candidate_ap_sha256': sha256(candidate),
        'ap_bytes': len(candidate),
        'cp_changed': False, 'expected_cp_sha256': CP_SHA256,
        'patch': {'file_offset_and_va': hex(OFFSET), 'before_hex': BEFORE.hex(),
                  'after_hex': AFTER.hex(), 'meaning': 'Enable native look-binding values 10 and 11'},
        'reverse_restores_source': True,
        'verification': verification,
        'dependencies': {name: importlib.metadata.version(name)
                         for name in ('capstone', 'unicorn', 'pyelftools')},
        'limitations': [
            'The input to the native validator is a generated model fixture, not a reported CAMPR file.',
            'Card filesystem, file header/parser/checksum, serialization, UI and capture were not executed.',
            'No hardware installation, WBI handling, cold-start, restore or upgrade path was validated.',
            'No device writer or production feature flag is included; do not overwrite an existing workspace.',
        ],
    }
    args.output.mkdir(mode=0o700)
    write_new(args.output / 'appFw-mr-card.EXPERIMENTAL.so', candidate)
    write_new(args.output / 'verification.json', (json.dumps(report, indent=2) + '\n').encode())
    write_new(args.output / 'full-model-cases.json', (json.dumps(cases, indent=2) + '\n').encode())
    print(json.dumps(report, indent=2))


if __name__ == '__main__':
    main()
