"""从分析器生成的 names.json 读取冻结项，不复用仓库外的 inventory。"""
import json
from pathlib import Path


def load_inventory():
    """返回团队判据需要的组名与冻结子用例；内容来自本次 PROFILE。"""
    with Path(__file__).with_name('names.json').open('r', encoding='utf-8') as handle:
        return {'groups': json.load(handle)}
