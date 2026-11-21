from pathlib import Path
import json,hashlib,subprocess
import sys
out=Path(sys.argv[1])
entries0=[('bin',0o40755,b'',0,0),('dev',0o40755,b'',0,0),('proc',0o40755,b'',0,0),('sys',0o40755,b'',0,0),('tmp',0o41777,b'',0,0),('var',0o40755,b'',0,0),('dev/console',0o20600,b'',5,1),('dev/null',0o20666,b'',1,3),('init',0o100755,(out/'init.sh').read_bytes(),0,0)]
for libc in ('musl','glibc'):
 base=out/'root'/libc
 entries=entries0+[('bin/busybox',0o100755,(base/'busybox').read_bytes(),0,0),('bin/sh',0o120777,b'busybox',0,0),('lib',0o120777,b'suite/lib',0,0),('suite',0o40755,b'',0,0)]
 for p in sorted(base.rglob('*')):
  name='suite/'+str(p.relative_to(base))
  entries.append((name,0o40755 if p.is_dir() else 0o100000|(p.stat().st_mode&0o777),b'' if p.is_dir() else p.read_bytes(),0,0))
 blob=bytearray();members=[]
 for ino,(name,mode,raw,major,minor) in enumerate(entries+[('TRAILER!!!',0,b'',0,0)],1):
  enc=name.encode()+b'\0';fields=(ino,mode,0,0,1,0,len(raw),0,0,major,minor,len(enc),0)
  blob.extend(b'070701'+b''.join(f'{v:08x}'.encode() for v in fields)+enc);blob.extend(b'\0'*(-len(blob)%4));blob.extend(raw);blob.extend(b'\0'*(-len(blob)%4))
  if name!='TRAILER!!!':members.append({'path':name,'mode':oct(mode),'size':len(raw),'sha256':hashlib.sha256(raw).hexdigest()})
 (out/(libc+'.cpio')).write_bytes(blob);(out/(libc+'-members.json')).write_text(json.dumps(members,indent=2)+'\n')
 print(libc,len(blob))
