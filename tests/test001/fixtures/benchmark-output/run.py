from pathlib import Path
import subprocess,json,concurrent.futures,hashlib
import sys
out=Path(sys.argv[1]);kernel=Path(sys.argv[2]);firmware=Path(sys.argv[3])
assert not any(out.glob('*-run.json')), 'Use a fresh run directory'
assert hashlib.sha256(kernel.read_bytes()).hexdigest()=='7745b858c72bbd6e1b368e6057f5ec166319da6319ccf2cd0f40e8b609b6e638'
assert hashlib.sha256(firmware.read_bytes()).hexdigest()=='49bdf7b939bda11321132d1042bf99d7324fb190f1feef423171fed3573f8705'
def run(libc):
 argv=['qemu-system-riscv64','-machine','virt','-m','1024M','-smp','2','-nographic','-no-reboot','-nic','none','-bios',str(firmware),'-kernel',str(kernel),'-initrd',str(out/(libc+'.cpio')),'-append','console=ttyS0 rdinit=/init panic=-1']
 with (out/(libc+'.serial.log')).open('wb') as stdout,(out/(libc+'.stderr.log')).open('wb') as stderr:
  try:r=subprocess.run(argv,stdin=subprocess.DEVNULL,stdout=stdout,stderr=stderr,timeout=1600);code=r.returncode
  except subprocess.TimeoutExpired:code='HOST_TIMEOUT'
 (out/(libc+'-run.json')).write_text(json.dumps({'argv':argv,'timeout':1600,'exit':code},indent=2)+'\n');return libc,code
with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
 for result in pool.map(run,('musl','glibc')):print(result,flush=True)
