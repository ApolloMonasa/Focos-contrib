"""运行固定 LTP 输出函数的小实验；不执行 LTP 用例，不产生兼容性成绩。

先按 index.json 的 source 文件表准备原样源码，再传 SOURCE_ROOT NEW_OUT。
C 只提取原样输出/退出函数，外部状态和清理替身在下方显式给出。
"""
import hashlib
import json
from pathlib import Path
import subprocess
import sys

HERE = Path(__file__).resolve().parent


def function(source, signature):
    start = source.index(signature + '\n{')
    end = source.index('\n}\n', start) + 3
    return source[start:end]


def main(source, out):
    index = json.loads((HERE / 'index.json').read_text())
    for name, expected in index['sources'].items():
        data = (source / name).read_bytes()
        if hashlib.sha256(data).hexdigest() != expected:
            raise ValueError(f'source mismatch: {name}')
    for libc, expected in index['wrapper_sources'].items():
        wrapper = HERE.parents[1] / 'ltp-audit-inputs' / (libc + '-ltp_testcode.sh')
        if hashlib.sha256(wrapper.read_bytes()).hexdigest() != expected:
            raise ValueError(f'wrapper mismatch: {libc}')
    out.mkdir()  # 实验目录必须独占；不覆盖旧证据。
    ltp = source / 'ltp-full-20240524'
    common = '''#include <stdio.h>
#include <stdlib.h>
#include <stdarg.h>
#include <string.h>
#include <unistd.h>
#include <errno.h>
#include <pthread.h>
#include "tst_res_flags.h"
#include "tst_ansi_color.h"
#define MIN(a,b) ((a)<(b)?(a):(b))
static int TST_ERR, TST_RET;
static const char *tst_strerrno(int e) { (void)e; return "probe"; }
'''
    modern = (ltp / 'lib/tst_test.c').read_text()
    modern_code = common + '''
#define tst_brk(...) abort()
#define tst_res_(...) abort()
struct counters { int passed, failed, broken, skipped, warnings; } counts;
static struct counters *results = &counts;
static void do_cleanup(void) {}
static void print_failure_hints(void) {}
''' + function(modern, 'static void print_result(const char *file, const int lineno, int ttype,\n\t\t\t const char *fmt, va_list va)') + function(modern, 'static void do_exit(int ret)') + '''
static void emit(int type, const char *fmt, ...) {
 va_list args; va_start(args,fmt); print_result("probe.c", 7, type, fmt, args); va_end(args);
}
int main(void) {
 int flags[] = {TPASS,TFAIL,TBROK,TCONF,TWARN,TINFO,TDEBUG};
 for (int i=0;i<7;i++) emit(flags[i], "quoted TPASS: testcase lua date.lua success");
 counts=(struct counters){1,1,1,1,1}; do_exit(0);
}
'''
    old = (ltp / 'lib/tst_res.c').read_text()
    old_code = common + '''
#define USERMESG 4096
#define VERBOSE 0
#define NOPASS 1
#define DISCARD 2
#define NO_NEWLIB_ASSERT(a,b) ((void)0)
static int T_exitval, T_mode=VERBOSE, TEST_ERRNO, TEST_RETURN, passed_cnt=1;
static pthread_mutex_t tmutex=PTHREAD_MUTEX_INITIALIZER;
static void tst_old_flush(void) {}
static const char *strttype(int t) {
 switch (TTYPE_RESULT(t)) {
 case TPASS:return "TPASS"; case TFAIL:return "TFAIL"; case TBROK:return "TBROK";
 case TCONF:return "TCONF"; case TWARN:return "TWARN"; default:return "TINFO";
 }
}
''' + function(old, 'static void tst_print(const char *tcid, int tnum, int ttype, const char *tmesg)') + function(old, 'void tst_exit(void)') + '''
int main(void) {
 int flags[] = {TPASS,TFAIL,TBROK,TCONF,TWARN,TINFO};
 for (int i=0;i<6;i++) tst_print("probe", i+1, flags[i], "quoted TPASS: testcase lua date.lua success");
 tst_exit();
}
'''
    runs = {}
    def run(name, argv, env):
        result = subprocess.run(argv, cwd=out, env=env, capture_output=True, timeout=15)
        (out / (name + '.stdout')).write_bytes(result.stdout)
        (out / (name + '.stderr')).write_bytes(result.stderr)
        runs[name] = {'exit': result.returncode, 'argv': argv}
    for name, code in (('modern-c', modern_code), ('old-c', old_code)):
        path = out / (name + '.c'); path.write_text(code)
        subprocess.run(['cc', '-pthread', '-I', str(ltp / 'include'), str(path),
                        str(ltp / 'lib/tst_ansi_color.c'), '-o', str(out / name)], check=True)
        for color in ('0', '1'):
            run(name + '-' + color, [str(out / name)],
                {'PATH': '/usr/bin:/bin', 'LC_ALL': 'C', 'LTP_COLORIZE_OUTPUT': color})
    for lib in ('tst_test.sh', 'test.sh'):
        for mode in ('pass', 'skip', 'mixed'):
            modern_shell = lib == 'tst_test.sh'
            call = 'tst_res' if modern_shell else 'tst_resm'
            exit_call = '_tst_do_exit' if modern_shell else 'tst_exit'
            script = f'. "{ltp}/testcases/lib/{lib}"\n'
            script += f'{call} TINFO "quoted TPASS: testcase lua date.lua success"\n'
            if mode != 'skip': script += f'{call} TPASS "probe pass"\n'
            if mode != 'pass': script += f'{call} TCONF "probe skip"\n'
            script += exit_call + '\n'
            path = out / (lib + '-' + mode + '.sh'); path.write_text(script)
            for color in ('0', '1'):
                run(lib + '-' + mode + '-' + color, ['/bin/sh', str(path)],
                    {'PATH': str(ltp / 'testcases/lib') + ':/usr/bin:/bin',
                     'LC_ALL': 'C', 'TST_NO_DEFAULT_RUN': '1', 'TST_ID': 'probe',
                     'TCID': 'probe', 'TST_TOTAL': '1', 'LTP_COLORIZE_OUTPUT': color})
    # 原样比赛包装脚本；两个子程序只返回 0/1，不是实际 LTP 用例。
    children = out / 'ltp/testcases/bin'; children.mkdir(parents=True)
    for status in (0, 1):
        path = children / f'exit{status}'; path.write_text(f'#!/bin/sh\nexit {status}\n'); path.chmod(0o755)
    for libc in ('musl', 'glibc'):
        wrapper = HERE.parents[1] / 'ltp-audit-inputs' / (libc + '-ltp_testcode.sh')
        run('wrapper-' + libc, ['/bin/bash', str(wrapper)], {'PATH': '/usr/bin:/bin', 'LC_ALL': 'C'})
    (out / 'runs.json').write_text(json.dumps(runs, indent=2) + '\n')
    print(json.dumps({name: value['exit'] for name, value in runs.items()}, indent=2))


if __name__ == '__main__':
    main(Path(sys.argv[1]).resolve(), Path(sys.argv[2]).resolve())
