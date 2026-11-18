#!/usr/bin/env python3
"""TEST-001 入口；不写 pyc，参数错误退出 2。"""
import sys

sys.dont_write_bytecode = True

from test001.cli import main

if __name__ == '__main__':
    raise SystemExit(main())
