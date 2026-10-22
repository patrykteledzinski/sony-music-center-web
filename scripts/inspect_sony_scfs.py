#!/usr/bin/env python3
"""Read-only analysis and container carving, not a complete SCFS decoder."""
import argparse
import collections
import hashlib
import json
import math
from pathlib import Path
import re
import struct
import zlib


def occurrences(data, needle):
    return [m.start() for m in re.finditer(re.escape(needle), data)]


def analyze(path, output):
    data=path.read_bytes()
    if len(data) < 0xd4 or not data.startswith(b'scfsscfs') or data[0xbc:0xbc+21] != b'scfs_compression_zlib':
        raise ValueError('Not the supported SCFS/zlib update layout')
    output.mkdir(parents=True,exist_ok=True)
    report={'input':str(path),'bytes':len(data),'sha256':hashlib.sha256(data).hexdigest(),
            'complete_unpack':False,'decryption_proven':False}
    header=data[:0xbc]
    (output/'header.bin').write_bytes(header)
    (output/'plugin-descriptor.bin').write_bytes(data[0xbc:0xd4])
    (output/'payload-opaque.bin').write_bytes(data[0xd4:])
    report['carved']={'header':[0,0xbc],'descriptor':[0xbc,0xd4],'opaque_payload':[0xd4,len(data)]}
    report['header_be32']=[hex(x[0]) for x in struct.iter_unpack('>I',header)]
    report['signatures']={name:occurrences(data,sig) for name,sig in {
        'ELF':b'\x7fELF','SquashFS_LE':b'hsqs','SquashFS_BE':b'sqsh',
        'RSA_OID':bytes.fromhex('06092a864886f70d010101'),
        'PKCS1_SHA256_OID':bytes.fromhex('06092a864886f70d01010b'),
        'PEM_key':b'-----BEGIN','SCFS':b'scfs'}.items()}
    report['command_hits']={s:occurrences(data,s.encode()) for s in [
        'PARTY','Party_Control','X_GetDeviceInfo','X_Invite','X_Entry','X_Start',
        'ListenerSessionID','SingerSessionID','TransportPort','schemas-sony-com:service:Party']}
    report['entropy']=[]
    for start in [0,0x1000,0x100000,len(data)//2,len(data)-65536]:
        block=data[start:start+65536];n=len(block)
        report['entropy'].append({'offset':start,'bits_per_byte':-sum((c/n)*math.log2(c/n) for c in collections.Counter(block).values())})
    report['plugin_id_hypothesis']=[]
    for offset in [0x20,0x24]:
        value=struct.unpack_from('>I',data,offset)[0]
        report['plugin_id_hypothesis'].append(dict(offset=offset,value=hex(value),
            function=value>>30,major=(value>>16)&0x3fff,interface=(value>>8)&255,minor=value&255,
            note='Official plugin bit layout; header field semantics not confirmed'))
    report['utf16_command_hits']={s:{enc:occurrences(data,s.encode(enc)) for enc in ['utf-16le','utf-16be']}
        for s in report['command_hits']}
    report['embedded_zlib']={'candidates':0,'validated_streams':[]}
    view=memoryview(data)
    for match in re.finditer(b'[\x08\x18\x28\x38\x48\x58\x68\x78](?=.)',data):
        start=match.start()
        if (data[start]*256+data[start+1])%31:continue
        report['embedded_zlib']['candidates']+=1
        try:
            decoder=zlib.decompressobj()
            out=decoder.decompress(view[start:start+65536],262144)
            if decoder.eof:
                name=f'embedded-zlib-{start:x}.bin';(output/name).write_bytes(out)
                report['embedded_zlib']['validated_streams'].append(dict(offset=start,bytes=len(out),file=name))
        except zlib.error:pass
    id_path=path.parent/'SONY_VUP.ID'
    if id_path.exists():
        decoded=bytes(x^0x2e for x in id_path.read_bytes())
        (output/'SONY_VUP.decoded.bin').write_bytes(decoded)
        report['decoded_id_fields']=[x.decode('ascii',errors='replace') for x in decoded.split(b'\0') if x]
    # Bounded probes only. A successful output requires a validated end/checksum.
    report['zlib_probes']={'header_candidate_attempts':0,'validated_streams':[]}
    for offset in range(0xd4, min(0xf4,len(data)-2)):
        for key in range(256):
            first,second=data[offset]^key,data[offset+1]^key
            if first & 15 != 8 or first>>4>7 or (first*256+second)%31:continue
            report['zlib_probes']['header_candidate_attempts']+=1
            translated=data[offset:offset+1024*1024].translate(bytes.maketrans(bytes(range(256)),bytes(i^key for i in range(256))))
            try:
                decoder=zlib.decompressobj();out=decoder.decompress(translated,4*1024*1024)
                if decoder.eof:
                    name=f'zlib-{offset:x}-xor-{key:02x}.bin';(output/name).write_bytes(out)
                    report['zlib_probes']['validated_streams'].append(dict(offset=offset,xor_byte=key,bytes=len(out),file=name))
            except zlib.error:pass
    (output/'analysis.json').write_text(json.dumps(report,indent=2)+'\n')
    return report


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('input',type=Path);p.add_argument('--output',required=True,type=Path);args=p.parse_args()
    result=analyze(args.input,args.output)
    print(json.dumps({k:result[k] for k in ['sha256','complete_unpack','decoded_id_fields','zlib_probes','command_hits']},indent=2))
