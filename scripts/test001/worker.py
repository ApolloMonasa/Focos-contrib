"""私有快照的子入口；公开启动器不通过参数或环境切换身份。"""

import sys
from pathlib import Path

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from test001.cli import main

if __name__ == '__main__':
    raise SystemExit(main())
