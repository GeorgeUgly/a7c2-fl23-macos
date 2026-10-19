"""Offline two-profile regression using exact local inputs, no camera I/O."""
import argparse
import collections
import hashlib
import importlib.util
import itertools
import json
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT))
sys.path.insert(0,str(ROOT/'research/mr-card-compat'))
from macfl23 import integrated
from macfl23.engine import verify as verify_engine
from verify_native import verify as verify_mr

if not __debug__:raise RuntimeError('Verification assertions must remain enabled')
parser=argparse.ArgumentParser(description=__doc__)
parser.add_argument('--workspace',required=True,type=Path)
parser.add_argument('--output',required=True,type=Path)
args=parser.parse_args()
if args.output.exists() or args.output.is_symlink():parser.error('Use a new private output directory')
base=verify_engine()
spec=importlib.util.spec_from_file_location('exact_core',base/'core.py')
core=importlib.util.module_from_spec(spec);spec.loader.exec_module(core)
stock=[(args.workspace/'original'/(name+'.bin')).read_bytes() for name in core.PROGRAMS]
assert [core.sha(v) for v in stock]==core.ORIGINAL
before=core.patch_program(stock[0],0)
cp=core.patch_program(stock[1],1)
assert core.sha(cp)==integrated.CP_FL23
profiles={}
for profile in (integrated.BASE,integrated.COMBINED):
    candidate=profile.patch_ap(before)
    assert profile.patch_ap(candidate,True)==before
    assert core.patch_program(profile.patch_ap(candidate,True),0,True)==stock[0]
    without_mr=bytearray(candidate)
    without_mr[0x4b2dd02:0x4b2dd04]=b'\0\0'
    report=verify_mr(bytes(without_mr),candidate)
    report.pop('cases')
    profiles[profile.PREFIX]={'ap_sha256':core.sha(candidate),'cp_sha256':core.sha(cp),
        'exact_reverse':True,'mr_validator':report,'asset_manifest':profile.assets()}
    print(profile.PREFIX,'native MR validation PASS',flush=True)
after=integrated.COMBINED.patch_ap(before)
assert integrated.BASE.patch_ap(before)[0x2060000:0x2063000]==before[0x2060000:0x2063000]
spec=importlib.util.spec_from_file_location('boot_audit',ROOT/'research/ntsc-boot-notice/audit_extended.py')
audit=importlib.util.module_from_spec(spec);spec.loader.exec_module(audit)
real_uc = audit.Uc
mode = 0

class Fixture:
    def __init__(self, *a, **kw): self.inner = real_uc(*a, **kw)
    def __getattr__(self, name): return getattr(self.inner, name)
    def reg_write(self, register, value):
        return self.inner.reg_write(register, mode if register == audit.regs.UC_ARM64_REG_X23 else value)

audit.Uc = Fixture
reports = {}
for mode in (0, 1):
    counts = collections.Counter()
    for region, path, current, default, reset, boot, display, pair, pa, bp, pc in itertools.product(
        range(2), ('first', 'second'), range(2), range(2), range(2), range(4), range(2),
        [(0,0x4b7),(0,0x36c),(1,0x390),(1,0x391)],range(2),[(0,0),(0,1),(1,0)],range(2)):
        params = (path,current,default,reset,boot,display,*pair,pa,*bp,pc)
        old = audit.execute(before,*params,region=region)
        new = audit.execute(after,*params,region=region)
        if old[0]['message_id'] == 0x36b:
            assert new[0]['message_id'] != 0x36b
            assert new[0] == audit.execute(before,*params,region=0)[0]
            counts['ntsc_selection_removed'] += 1
        else:
            assert old == new, (mode, region, params,old[0],new[0])
            counts['non_ntsc_selection_trace_gprs_identical'] += 1
        counts['total'] += 1
    reports[str(mode)] = dict(counts)
    print(mode, dict(counts), flush=True)
report={'status':'offline_two_profiles_passed_hardware_unverified','camera_io':False,
    'profiles':profiles,'boot_by_initial_x23':reports,'boot_total':sum(r['total'] for r in reports.values()),
    'non_ntsc_selection_trace_gprs_changed':0,
    'limits':['Explicit external predicates and generated MR models; no real CAMPR file, UI, complete boot, flash or capture executed.'],
    'script_sha256':hashlib.sha256(Path(__file__).read_bytes()).hexdigest()}
verify_engine()
assert [core.sha((args.workspace/'original'/(name+'.bin')).read_bytes()) for name in core.PROGRAMS]==core.ORIGINAL
args.output.mkdir(mode=0o700)
p=args.output/'verification.json'
with p.open('x') as f:json.dump(report,f,indent=2);f.write('\n')
p.chmod(0o600)
