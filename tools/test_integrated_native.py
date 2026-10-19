"""Exercise the pinned C installer state machine with host syscall fixtures.

Requires private exact firmware inputs; never accesses USB or real Linux mounts.
Only sc() and the entry name are replaced for a host C shared-library harness.
The production AArch64 modules are checked separately by the build tool.
"""
import argparse
import ctypes as c
import hashlib
import importlib.util
import json
from pathlib import Path
import struct
import subprocess
import sys
import tempfile

from build_integrated_native import entries_for, render_manifest, verify, profile


class Fixture:
    def __init__(self, images, mode, failure=None):
        self.images = [bytearray(v) for v in images]
        self.mode, self.failure = mode, failure
        self.ro = True
        self.handles, self.next_fd = {}, 10
        self.result = bytearray()
        self.writes, self.remounts, self.errors = [], [], []
        self.partial = False
        self.partial_fd = None

    def syscall(self, no, a, b, count, offset, e, f):
        try:
            if no == 175:return 0
            if no == 172:return 100
            if no == 35:
                assert c.string_at(b) == b'/tmp/jiritsu/libJit.so'
                return 0
            if no == 56:
                path=c.string_at(b).decode()
                if path=='/usr/lib/appFw.so': obj=0
                elif path=='/cp/cpapp-b.bin':obj=1
                elif path=='/tmp/jiritsu/a7c2.install.request':obj=struct.pack('<8sII',b'A7INS001',self.mode,0)
                elif path=='/tmp/jiritsu/a7c2.install.result':obj='result'
                elif path=='/proc/mounts':obj=b'/dev/nflasha15 /usr ext2 '+(b'ro,' if self.ro else b'rw,')+b'relatime 0 0\n'
                else:raise AssertionError('Unexpected open: '+path)
                fd=self.next_fd;self.next_fd+=1;self.handles[fd]=(obj,count)
                return fd
            if no == 57:
                del self.handles[a];return 0
            if no == 40:
                assert c.string_at(b)==b'/usr'
                self.ro=bool(offset&1);self.remounts.append(self.ro);return 0
            if no == 80:
                obj,flags=self.handles[a]
                assert isinstance(obj,int)
                ino=100+obj+(10 if flags==2 and self.failure=='inode' else 0)
                raw=struct.pack('<QQIIIIQQqIIQ7Q',1,ino,0o100644,1,0,0,0,0,len(self.images[obj]),4096,0,0,*([0]*7))
                c.memmove(b,raw,len(raw));return 0
            if no in (63,67):
                obj,_=self.handles[a]
                raw=self.images[obj] if isinstance(obj,int) else obj
                at=offset if no==67 else 0
                data=bytes(raw[at:at+count]);c.memmove(b,data,len(data));return len(data)
            if no==64:
                obj,_=self.handles[a];assert obj=='result'
                self.result.extend(c.string_at(b,count));return count
            if no==68:
                obj,flags=self.handles[a];assert isinstance(obj,int) and flags==2 and not self.ro
                if self.partial_fd==a:
                    self.partial_fd=None;return -5
                n=count
                if self.failure=='partial' and not self.partial:
                    n=max(1,count//2);self.partial=True;self.partial_fd=a
                self.images[obj][offset:offset+n]=c.string_at(b,n)
                self.writes.append((obj,offset,n));return n
            if no==82:return 0
            raise AssertionError('Unexpected syscall: '+str(no))
        except BaseException as exc:
            self.errors.append(repr(exc));return -5


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--workspace',required=True,type=Path)
    parser.add_argument('--output',required=True,type=Path)
    args=parser.parse_args()
    if args.output.exists() or args.output.is_symlink():parser.error('Use a new output directory')
    base=verify()
    spec=importlib.util.spec_from_file_location('core_fixture',base/'core.py')
    core=importlib.util.module_from_spec(spec);spec.loader.exec_module(core)
    stock=[(args.workspace/'original'/(name+'.bin')).read_bytes() for name in core.PROGRAMS]
    assert [core.sha(raw) for raw in stock]==core.ORIGINAL
    fl23=[core.patch_program(raw,chip) for chip,raw in enumerate(stock)]
    source=(base/'native/installer.c').read_text()
    start,end=source.index('static i64 sc('),source.index('static i64 op(')
    shim='''typedef i64 (*Hook)(i64,i64,i64,i64,i64,i64,i64);static Hook callback;
__attribute__((visibility("default"))) void set_hook(Hook hook){callback=hook;}
static i64 sc(i64 n,i64 a,i64 b,i64 d,i64 e,i64 f,i64 g){return callback(n,a,b,d,e,f,g);}
'''
    adapted=(source[:start]+shim+source[end:]).replace('void _init(void)','void test_entry(void)')
    adapted+='\n__attribute__((visibility("default"))) void reset_test(void){r=(struct Report){0};req=(struct Request){0};fd[0]=fd[1]=-1;mounted_rw=0;}\n'
    cases=[]
    callback_type=c.CFUNCTYPE(c.c_longlong,*([c.c_longlong]*7))
    with tempfile.TemporaryDirectory(prefix='a7c2-native-fixture-') as directory:
        directory=Path(directory)
        for selected in (profile.BASE,profile.COMBINED):
            final=[selected.patch_ap(fl23[0]),fl23[1]]
            for name in ('upgrade','installer'):
                entries=entries_for(base,upgrade=name=='upgrade',selected=selected)
                (directory/'integrated_manifest.h').write_text(render_manifest(entries,name=='upgrade',selected))
                (directory/'fixture.c').write_text(adapted)
                library=directory/(selected.PREFIX+name+'.dylib')
                subprocess.run(['cc','-dynamiclib','-O2','-ffreestanding','-fno-builtin',
                                '-DPATCH_MANIFEST="integrated_manifest.h"','-I',str(directory),
                                '-I',str(base/'native'),str(directory/'fixture.c'),'-o',str(library)],check=True)
                dll=c.CDLL(str(library));dll.set_hook.argtypes=[callback_type]
                tests=[('preflight',0,None),('install',1,None),('restore',2,None),
                       ('wrong_source',1,'wrong_source'),('inode_changed',1,'inode'),('partial_write',1,'partial')]
                for label,mode,failure in tests:
                    initial=final if mode==2 else fl23 if name=='upgrade' else stock
                    fixture=Fixture(initial,mode,failure)
                    if failure=='wrong_source':fixture.images[0][0x1000]^=1
                    untouched=[bytes(v) for v in fixture.images]
                    hook=callback_type(fixture.syscall)
                    dll.reset_test();dll.set_hook(hook);dll.test_entry()
                    assert not fixture.errors,fixture.errors
                    raw=bytes(fixture.result);assert len(raw)==240
                    values=struct.unpack_from('<8sIIIIqqqqqqqQQQQ',raw)
                    assert fixture.ro and not fixture.handles
                    if failure:
                        assert values[5]!=0
                        assert fixture.images==[bytearray(v) for v in untouched]
                        if failure!='partial':assert not fixture.writes
                        else:assert values[6]==0
                    else:
                        selected.parse_native(core,name,raw,mode)
                        expected=initial if mode==0 else final if mode==1 else fl23 if name=='upgrade' else stock
                        assert fixture.images==[bytearray(v) for v in expected]
                        if mode==0:assert not fixture.writes and not fixture.remounts
                    if name=='upgrade':assert not any(chip==1 for chip,_,_ in fixture.writes)
                    cases.append({'profile':selected.FEATURES,'module':name,'case':label,'mode':mode,'passed':True,
                                  'firmware_write_calls':len(fixture.writes),'readonly_restored':fixture.ro,
                                  'status':values[5],'rollback_status':values[6]})
                    print(selected.PREFIX+name,label,'PASS',flush=True)
    args.output.mkdir(mode=0o700)
    report={'status':'host_native_installer_fixture_tests_passed','camera_io':False,'cases':cases,
            'source_sha256':hashlib.sha256(source.encode()).hexdigest(),
            'limit':'Pinned installer C runs on host with only syscall/entry substitution; not a Linux kernel, AArch64 execution, real ext2 or hardware recovery test.'}
    target=args.output/'native-installer-tests.json'
    with target.open('x') as stream:json.dump(report,stream,indent=2);stream.write('\n')
    target.chmod(0o600)


if __name__=='__main__':main()
