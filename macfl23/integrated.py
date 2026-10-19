"""Two exact 2.01 release profiles; shared hashes identify code, not a body."""
import hashlib
import json
from pathlib import Path
import struct
from dataclasses import dataclass

AP_FL23 = '1aaeb8fbe0297950cab454d498d1a5ba874f91542aa2737aae27a73971e324d7'
CP_FL23 = '60a9de1a0636c7f6b5b98607a4d25eeb48634e25294f632743a6d256e583b370'
ASSETS = Path(__file__).resolve().parent / 'assets'
ORIGINAL_SPANS = [(0x3528804,112),(0x4b2ed19,2),(0x59892b8,40),
                  (0x5989370,40),(0x4b4a5e4,12),(0x4b3b544,12)]
MR = (0x4b2dd02,bytes.fromhex('0000'),bytes.fromhex('0101'))
NARROW = [(0x2061880,bytes.fromhex('626d8052'),bytes.fromhex('07000014')),
          (0x2061da8,bytes.fromhex('626d8052'),bytes.fromhex('0a000014'))]


@dataclass(frozen=True)
class Profile:
    FEATURES: list
    AP_FINAL: str
    PATCHES: list
    SPANS: list
    PREFIX: str
    ASSETS = ASSETS
    CP_FL23 = CP_FL23
    AP_FL23 = AP_FL23

    def patch_ap(self,raw,reverse=False):
        source,target=(self.AP_FINAL,AP_FL23) if reverse else (AP_FL23,self.AP_FINAL)
        if hashlib.sha256(raw).hexdigest()!=source:
            raise ValueError('所选版本的完整 AP 指纹不匹配')
        result=bytearray(raw)
        for offset,before,after in self.PATCHES:
            old,new=(after,before) if reverse else (before,after)
            if result[offset:offset+len(old)]!=old:raise ValueError('版本补丁窗口不匹配')
            result[offset:offset+len(old)]=new
        if hashlib.sha256(result).hexdigest()!=target:raise ValueError('版本结果完整指纹不匹配')
        return bytes(result)

    def assets(self):
        manifest=json.loads((ASSETS/(self.PREFIX+'manifest.json')).read_text())
        names={self.PREFIX+name+'.so' for name in ('installer','upgrade','runtime')}
        if manifest.get('features')!=self.FEATURES or set(manifest['files'])!=names:
            raise ValueError('所选版本模块清单不匹配')
        for name,expected in manifest['files'].items():
            if hashlib.sha256((ASSETS/name).read_bytes()).hexdigest()!=expected:
                raise ValueError('版本模块完整指纹不匹配：'+name)
        return manifest

    def parse_native(self,core,name,raw,mode=None):
        if name in ('installer','upgrade'):
            if mode not in (0,1,2) or len(raw)!=240:raise ValueError('版本模块模式或报告长度不符')
            v=struct.unpack_from('<8sIIIIqqqqqqqQQQQ',raw)
            count=len(self.PATCHES)+(40 if name=='installer' else 0)
            if (v[:3]!=(b'A7IRP001',1,mode) or v[3]!=9 or any(v[i] for i in (5,6,7,8,9,10))
                    or v[4]!=(0 if mode==0 else count) or v[-2:]!=(99960744,18090880)):
                raise ValueError('版本模块未完整成功；保留状态，不重试')
            source=core.ORIGINAL if name=='installer' else [AP_FL23,CP_FL23]
            final=[self.AP_FINAL,CP_FL23]
            before=[raw[112:144].hex(),raw[144:176].hex()]
            after=[raw[176:208].hex(),raw[208:240].hex()]
            if before!=(final if mode==2 else source):raise ValueError('版本模块写入前完整指纹不符')
            if mode and after!=(source if mode==2 else final):raise ValueError('版本模块写入后完整指纹不符')
            return {'mode':mode,'window_writes':v[4],'before':before,'after':after,'readonly_verified':True}
        if name=='runtime':
            count=len(self.SPANS)
            if mode is not None or len(raw)!=56+count*256+240:raise ValueError('版本运行报告长度或模式不符')
            v=struct.unpack_from('<8sIIqqqQII',raw)
            if v[:3]!=(b'A7RUN001',1,count) or v[3] or v[4] or v[7]!=16 or v[8]:
                raise ValueError('版本运行状态身份或结果不符')
            spans=[{'offset':offset,'length':n,'memory':raw[56+i*256:56+i*256+n].hex(),
                    'disk':raw[184+i*256:184+i*256+n].hex()} for i,(offset,n) in enumerate(self.SPANS)]
            at=56+count*256
            return {'spans':spans,'image_mode':raw[at:at+16].split(b'\0')[0].decode('ascii','replace')}
        raise ValueError('未知版本模块')


BASE=Profile(['fl23','mr_card_compat'],
             '22137afdb226ed2a5c0804eb95e802f4d697955fa793c7661e6b14bd48127d89',
             [MR],ORIGINAL_SPANS+[(MR[0],2)],'fl23-v2-')
COMBINED=Profile(BASE.FEATURES+['no_ntsc_notice_v2'],
                 'c8cc89e73e0bd03c91e5a9c7147c2d8c829cfea6b8dbd6fd2be20a4eacf423db',
                 [MR,*NARROW],BASE.SPANS+[(o,len(b)) for o,b,a in NARROW],'integrated-')


def enabled(state):return state.get('features') in (BASE.FEATURES,COMBINED.FEATURES)


def select(state):
    for profile in (BASE,COMBINED):
        if state.get('features')==profile.FEATURES:return profile
    raise ValueError('不是支持的新版本配置')


# Explicit combination aliases retained for standalone research tooling.
FEATURES,AP_FINAL,PATCHES,SPANS,PREFIX=COMBINED.FEATURES,COMBINED.AP_FINAL,COMBINED.PATCHES,COMBINED.SPANS,COMBINED.PREFIX
patch_ap,assets,parse_native=COMBINED.patch_ap,COMBINED.assets,COMBINED.parse_native
