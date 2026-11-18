"""严格 JSON：重复键、NaN/Inf 和非 UTF-8 输入都拒绝。"""
from __future__ import annotations

import json
import math


class StrictJSONError(ValueError):
    """输入不是本工具接受的 JSON。"""


def _pairs(items):
    result = {}
    for key, value in items:
        if key in result:
            raise StrictJSONError(f'duplicate JSON field: {key}')
        result[key] = value
    return result


def _constant(value):
    raise StrictJSONError(f'non-finite JSON constant: {value}')


def _finite(value, path='$'):
    if isinstance(value, float) and not math.isfinite(value):
        raise StrictJSONError(f'non-finite JSON number at {path}')
    if isinstance(value, dict):
        for key, item in value.items():
            _finite(item, f'{path}.{key}')
    elif isinstance(value, list):
        for index, item in enumerate(value):
            _finite(item, f'{path}[{index}]')


def loads(raw: bytes | str, *, label: str = 'JSON'):
    """解析一份 JSON，并拒绝重复键和非有限数值。"""
    if isinstance(raw, bytes):
        try:
            raw = raw.decode('utf-8')
        except UnicodeDecodeError as exc:
            raise StrictJSONError(f'{label} is not UTF-8') from exc
    try:
        value = json.loads(raw, object_pairs_hook=_pairs, parse_constant=_constant)
    except StrictJSONError:
        raise
    except json.JSONDecodeError as exc:
        raise StrictJSONError(f'{label} is not valid JSON: {exc}') from exc
    _finite(value)
    return value


def canonical_bytes(value) -> bytes:
    """用于配置身份的规范编码；不写缩进和空白差异。"""
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(',', ':'),
        allow_nan=False,
    ).encode('utf-8')


def canonical_hash(value, *, omit=()) -> str:
    """对规范编码计算 SHA256；omit 中的顶层字段不参与。"""
    import hashlib

    if omit:
        value = {key: item for key, item in value.items() if key not in omit}
    return hashlib.sha256(canonical_bytes(value)).hexdigest()


def dumps(value, *, indent=2) -> str:
    """输出可再解析的 UTF-8 JSON，禁止 NaN/Inf。"""
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, indent=indent,
        allow_nan=False,
    ) + '\n'
