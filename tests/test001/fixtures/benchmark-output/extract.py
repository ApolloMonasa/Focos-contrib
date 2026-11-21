from pathlib import Path
import subprocess,json,hashlib
import sys
image=Path(sys.argv[1]);out=Path(sys.argv[2]);out.mkdir(parents=True,exist_ok=False)
h=hashlib.sha256()
with image.open('rb') as f:
 while b:=f.read(1024*1024):h.update(b)
assert h.hexdigest()=='95973543db6b84a9a5e70f30da466ce292867aff5b689fb14c88dc9406e378b8'
print('image hash verified',flush=True)
records={}
def get(path):
 r=subprocess.run(['debugfs','-R','cat '+path,str(image)],capture_output=True,check=True,timeout=20)
 return r.stdout
def listing(path):
 return subprocess.check_output(['debugfs','-R','ls -p '+path,str(image)],stderr=subprocess.DEVNULL,text=True)
def extract(path,dest):
 text=listing(path)
 for line in text.splitlines():
  fields=line.split('/')
  if len(fields)<7 or fields[5] in ('.','..'):continue
  name=fields[5];mode=int(fields[2],8);child=path+'/'+name
  if mode&0o170000==0o040000:
   if path in ('/musl','/glibc') and name not in ('basic','lib'):continue
   (dest/name).mkdir(exist_ok=True);extract(child,dest/name)
  elif mode&0o170000==0o100000:
   raw=get(child); assert len(raw)==int(fields[6]),child
   (dest/name).write_bytes(raw);(dest/name).chmod(mode&0o777)
   records[child]={'mode':oct(mode),'size':len(raw),'sha256':hashlib.sha256(raw).hexdigest()}
  else: print('nonregular skipped',child,oct(mode),flush=True)
for libc in ('musl','glibc'):
 d=out/'root'/libc;d.mkdir(parents=True,exist_ok=True);extract('/'+libc,d)
 ld=d/'ltp/testcases/bin';ld.mkdir(parents=True,exist_ok=True)
 for name in ('getpid01','getppid01','getuid01','access01'):
  path='/'+libc+'/ltp/testcases/bin/'+name;raw=get(path)
  if raw:
   (ld/name).write_bytes(raw);(ld/name).chmod(0o755);records[path]={'mode':'0o100755','size':len(raw),'sha256':hashlib.sha256(raw).hexdigest()}
 print(libc,'extracted',flush=True)
(out/'image-members.json').write_text(json.dumps(records,indent=2)+'\n')
