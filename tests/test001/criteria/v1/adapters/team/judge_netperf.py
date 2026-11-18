"""netperf：按完整结束标记判分，缺失、重复或冲突均不通过。"""
import sys
from common import markers

markers('netperf', sys.stdin.read())
