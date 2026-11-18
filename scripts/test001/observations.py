"""兼容观察 API；所有结果都是同一证据计划的投影。"""

from .formats import LMBENCH_COMMANDS, LMBENCH_INDEX, LMBENCH_LABELS, IOZONE_HEADERS
from .proofs import build_plan

__all__ = [
    'LMBENCH_COMMANDS',
    'LMBENCH_INDEX',
    'LMBENCH_LABELS',
    'IOZONE_HEADERS',
    'check_observations',
    'observed_names',
    'extract_metrics',
]


def check_observations(group, text, expected, *, events=None, line_offset=0):
    return build_plan(group, text, expected, events=events, line_offset=line_offset)


def observed_names(group, text, expected=None, *, events=None):
    plan = build_plan(group, text, expected or [], events=events)
    return [name for name, claims in plan.claims.items() for _ in claims]


def extract_metrics(group, text, expected, *, line_offset=0, events=None):
    return build_plan(group, text, expected, events=events, line_offset=line_offset).metrics
