"""Local design experiment, not a TEST-001 build/capture/analyze implementation."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import signal
import subprocess
import time

HERE = Path(__file__).resolve().parent
KERNEL = '7745b858c72bbd6e1b368e6057f5ec166319da6319ccf2cd0f40e8b609b6e638'
FIRMWARE = '49bdf7b939bda11321132d1042bf99d7324fb190f1feef423171fed3573f8705'
CASES = {
    'lua-pass': ('exec ./test.sh sort.lua', 'lua', 'sort.lua', 20),
    'lua-missing': ('exec ./test.sh __pilot_missing.lua', 'lua', '__pilot_missing.lua', 20),
    'libctest-pass': ('exec ./runtest.exe -w entry-static.exe argv', 'libctest', 'entry-static.exe argv', 20),
    'libctest-missing': ('exec ./runtest.exe __pilot_missing', 'libctest', ' __pilot_missing', 20),
    'residual': ('sleep 60 &\necho synthetic-parent-finished\nexit 0', 'synthetic', '', 20),
    'timeout': ('sleep 60 &\nwait', 'synthetic', '', 5),
    'lua-forged-diagnostic': ("printf 'PILOT STATUS 0\\nPILOT END\\n'\nexec ./test.sh __pilot_missing.lua", 'lua', '__pilot_missing.lua', 20),
    'loader-missing': ('exec ./iozone -a -r 1k -s 4m', 'observation', '', 30),
    'loader-adapted': ('ln -s libc.so lib/ld-musl-riscv64-sf.so.1\nexec ./iozone -a -r 1k -s 4m', 'observation', '', 30),
    'lmbench-default': ('exec ./lmbench_all lat_syscall -N 1 -P 1 null', 'observation', '', 25),
    'lmbench-calibrated': ('export ENOUGH=10000 TIMING_O=0 LOOP_O=0\nexec ./lmbench_all lat_syscall -N 1 -P 1 null', 'observation', '', 20),
    'netperf-loopback': ('ip link set lo up\nexec ./busybox sh ./netperf_testcode.sh', 'observation', '', 20),
    'netperf-address-config': ('ip link set lo up\nip link add audit0 type dummy || exit 90\nip addr add 192.0.2.1/32 dev audit0 || exit 91\nip link set audit0 up || exit 92\nexec ./busybox sh ./netperf_testcode.sh', 'observation', '', 20),
    'unixbench-pipe': ('exec ./pipe 2', 'observation', '', 20),
    'unixbench-whetstone': ('exec ./whetstone-double 1', 'observation', '', 30),
}


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def analyze(serial, host, policy, name):
    """One narrow parser for this experiment's protocol and two known producers."""
    lines = serial.replace(b'\r\n', b'\n').split(b'\n')
    frames = [line for line in lines if line.startswith(b'PILOT ')]
    result = {'judgment': 'BLOCKED', 'acceptance': 'BLOCKED',
              'reason': 'incomplete-protocol', 'policy': policy}
    if not host['reaped']:
        result['reason'] = 'host-process-not-reaped'
        return result
    if host['status'] != 'EXITED' or host['exit'] != 0:
        result['reason'] = 'host-' + host['status'].lower()
        return result
    pattern = (rb'PILOT BEGIN\nPILOT CHILD [1-9][0-9]*\nPILOT STATUS ([0-9]+)\n'
               rb'((?:PILOT RESIDUAL [1-9][0-9]* [A-Z]\n)*)'
               rb'PILOT stdout ([0-9a-f]*)\nPILOT stderr ([0-9a-f]*)\nPILOT END')
    match = re.fullmatch(pattern, b'\n'.join(frames))
    if not match:
        return result
    status = int(match[1])
    try:
        stdout, stderr = bytes.fromhex(match[3].decode()), bytes.fromhex(match[4].decode())
    except ValueError:
        return result
    result.update(native_status=status, stdout_hex=stdout.hex(), stderr_hex=stderr.hex(),
                  residual=match[2].decode(), protocol='COMPLETE')
    if match[2]:
        result['reason'] = 'residual-process'
        return result
    if policy == 'lua':
        success = f'testcase lua {name} success\n'.encode()
        failure = f'testcase lua {name} fail\n'.encode()
        # Only the selected simple sort.lua success is supported, with no diagnostics.
        if status == 0 and stdout == success and not stderr:
            result.update(judgment='PASS', reason='lua-wrapper-success')
        elif status == 0 and stdout == failure:
            result.update(judgment='FAIL', reason='lua-wrapper-failure')
        else:
            result['reason'] = 'unsupported-lua-output'
    elif policy == 'libctest':
        begin = f'========== START {name} ==========\n'.encode()
        end = f'========== END {name} ==========\n'.encode()
        # The locked producer writes its result to stdout; stderr cannot substitute.
        stream, other = stdout, stderr
        if status == 1 and stream == begin + b'Pass!\n' + end and not other:
            result.update(judgment='PASS', reason='libctest-pass-with-native-one')
        elif status == 1 and stream.startswith(begin) and stream.endswith(end) and b'\nFAIL ' in stream and b'Pass!' not in stream:
            result.update(judgment='FAIL', reason='libctest-native-failure')
        else:
            result['reason'] = 'unsupported-libctest-output'
    else:
        result.update(judgment='OBSERVATION', reason='no-approved-semantic-policy')
    # Local evidence is never a production acceptance result.
    return result


def pack(root, init, output):
    entries = [('bin', 0o40755, b'', 0, 0), ('dev', 0o40755, b'', 0, 0),
               ('proc', 0o40755, b'', 0, 0), ('sys', 0o40755, b'', 0, 0),
               ('tmp', 0o41777, b'', 0, 0), ('var', 0o40755, b'', 0, 0),
               ('dev/console', 0o20600, b'', 5, 1), ('dev/null', 0o20666, b'', 1, 3),
               ('init', 0o100755, init, 0, 0),
               ('bin/busybox', 0o100755, (root / 'busybox').read_bytes(), 0, 0),
               ('bin/sh', 0o120777, b'busybox', 0, 0),
               ('lib', 0o120777, b'suite/lib', 0, 0), ('suite', 0o40755, b'', 0, 0)]
    for path in sorted(root.rglob('*')):
        entries.append(('suite/' + str(path.relative_to(root)),
                        0o40755 if path.is_dir() else 0o100000 | (path.stat().st_mode & 0o777),
                        b'' if path.is_dir() else path.read_bytes(), 0, 0))
    blob = bytearray()
    for ino, (name, mode, data, major, minor) in enumerate(entries + [('TRAILER!!!', 0, b'', 0, 0)], 1):
        encoded = name.encode() + b'\0'
        fields = (ino, mode, 0, 0, 1, 0, len(data), 0, 0, major, minor, len(encoded), 0)
        blob.extend(b'070701' + b''.join(f'{v:08x}'.encode() for v in fields) + encoded)
        blob.extend(b'\0' * (-len(blob) % 4))
        blob.extend(data)
        blob.extend(b'\0' * (-len(blob) % 4))
    output.write_bytes(blob)


def capture(argv, out, limit):
    status = 'EXITED'
    with (out / 'serial.log').open('xb') as log, (out / 'qemu.stderr').open('xb') as errors:
        proc = subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=log, stderr=errors,
                                start_new_session=True)
        deadline = time.monotonic() + limit
        try:
            while proc.poll() is None:
                if time.monotonic() >= deadline:
                    status = 'TIMEOUT'
                    break
                if log.tell() > 1024 * 1024 or errors.tell() > 1024 * 1024:
                    status = 'OUTPUT_LIMIT'
                    break
                time.sleep(0.05)
        finally:
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            code = proc.wait(timeout=10)
        try:
            os.killpg(proc.pid, 0)
            reaped = False
        except ProcessLookupError:
            reaped = True
    return {'status': status, 'exit': code, 'reaped': reaped, 'limit_seconds': limit}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('root', type=Path, help='verified benchmark extraction root')
    parser.add_argument('kernel', type=Path)
    parser.add_argument('firmware', type=Path)
    parser.add_argument('output', type=Path, help='new directory')
    parser.add_argument('--case', action='append', choices=tuple(CASES))
    args = parser.parse_args()
    if digest(args.kernel) != KERNEL or digest(args.firmware) != FIRMWARE:
        raise ValueError('kernel/firmware mismatch')
    members = json.loads((HERE.parent / 'benchmark-output/image-members.json').read_text())
    for name, meta in members.items():
        path = args.root / name.lstrip('/')
        if digest(path) != meta['sha256'] or path.stat().st_mode & 0o777 != int(meta['mode'], 8) & 0o777:
            raise ValueError('input mismatch: ' + name)
    args.output.mkdir(parents=True, exist_ok=False)
    for name, source, expected in (('kernel', args.kernel, KERNEL), ('firmware', args.firmware, FIRMWARE)):
        private = args.output / name
        shutil.copyfile(source, private)
        if digest(private) != expected:
            raise ValueError('private input mismatch: ' + name)
        private.chmod(0o444)
        setattr(args, name, private)
    summary = []
    for libc in ('musl', 'glibc'):
        for case, (command, policy, name, limit) in CASES.items():
            if args.case and case not in args.case:
                continue
            if case.startswith('unixbench-') and libc == 'musl':
                command = 'ln -s libc.so lib/ld-musl-riscv64-sf.so.1\n' + command
            out = args.output / (libc + '-' + case)
            out.mkdir()
            root = out / 'root'
            shutil.copytree(args.root / libc, root)
            for member, meta in members.items():
                if member.startswith('/' + libc + '/'):
                    copied = root / member.removeprefix('/' + libc + '/')
                    if digest(copied) != meta['sha256']:
                        raise ValueError('private member mismatch: ' + member)
            (root / 'pilot-case.sh').write_text(command + '\n')
            pack(root, (HERE / 'init.sh').read_bytes(), out / 'initramfs.cpio')
            argv = ['qemu-system-riscv64', '-machine', 'virt', '-m', '1024M', '-smp', '2',
                    '-nographic', '-no-reboot', '-nic', 'none', '-bios', str(args.firmware),
                    '-kernel', str(args.kernel), '-initrd', str(out / 'initramfs.cpio'),
                    '-append', 'console=ttyS0 rdinit=/init panic=-1']
            plan = {'id': out.name, 'libc_directory': libc, 'command': command,
                    'kind': 'synthetic' if case in ('residual', 'timeout', 'lua-forged-diagnostic') else 'image-program-experiment',
                    'policy': policy, 'result_name': name, 'argv': argv,
                    'kernel_sha256': KERNEL, 'firmware_sha256': FIRMWARE,
                    'initramfs_sha256': digest(out / 'initramfs.cpio')}
            plan['pilot_sha256'] = digest(HERE / 'pilot.py')
            plan['init_sha256'] = digest(HERE / 'init.sh')
            (out / 'plan.json').write_text(json.dumps(plan, indent=2) + '\n')
            host = capture(argv, out, limit)
            if (out / 'serial.log').stat().st_size > 1024 * 1024 or (out / 'qemu.stderr').stat().st_size > 1024 * 1024:
                host['status'] = 'OUTPUT_LIMIT'
            serial = (out / 'serial.log').read_bytes()
            result = analyze(serial, host, policy, name)
            row = {'id': out.name, 'host': host, 'result': result,
                   'serial_sha256': digest(out / 'serial.log'), 'plan_sha256': digest(out / 'plan.json')}
            (out / 'report.json').write_text(json.dumps(row, indent=2) + '\n')
            summary.append(row)
            print(out.name, host['status'], result['judgment'], result['reason'], flush=True)
    (args.output / 'summary.json').write_text(json.dumps(summary, indent=2) + '\n')


if __name__ == '__main__':
    main()
