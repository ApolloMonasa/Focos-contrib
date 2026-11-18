"""报告序列化：先写来源副本和 Markdown，最后发布 report.json。"""
from __future__ import annotations

import re
from pathlib import Path

from .errors import PublishError
from .fsutil import OutputDir, sha256_bytes
from .strictjson import dumps


def _code(value) -> str:
    """生成可容纳任意反引号/竖线的行内代码，避免破坏 Markdown。"""
    text = str(value).replace('\r', ' ').replace('\n', ' ')
    longest = max((len(run) for run in re.findall(r'`+', text)), default=0)
    fence = '`' * (longest + 1)
    if text.startswith(('`', ' ')) or text.endswith(('`', ' ')):
        text = f' {text} '
    return f'{fence}{text}{fence}'


def _cell(value) -> str:
    """表格单元只转义结构字符；名称仍保留原文。"""
    return str(value).replace('\r', ' ').replace('\n', ' ').replace('|', '\\|')


def _markdown(report: dict) -> str:
    lines = [
        '# TEST-001 分析报告',
        '',
        '本报告只分析给定日志或已校验运行包，不重新执行被测程序。',
        '',
        f"- 来源：`{report['origin']}`",
        f"- subject/platform：`{report['subject']}` / `{report['platform']}`",
        f"- PROFILE：`{report['profile']['profile_id']}` revision {report['profile']['revision']}",
        f"- CRITERIA：`{report['criteria']['criteria_id']}` revision {report['criteria']['revision']}",
        f"- 采集状态：`{report['capture'].get('status')}`，退出码：`{report['capture'].get('exit_code')}`",
        f"- 输入完整性：`{report['input_integrity']}`",
        f"- 协议：`{report['protocol']['status']}`",
        f"- 结论：`{report['acceptance']['status']}`",
        '',
        '## 分母与计数',
        '',
        '| selected | observed_pass | accepted_pass | partial | fail | missing | not_run | judge_error |',
        '| ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |',
        '| {selected_total} | {observed_pass} | {accepted_pass} | {partial} | {fail} | {missing} | {not_run} | {judge_error} |'.format(
            **report['totals']),
        '',
    ]
    if report['acceptance']['reasons']:
        lines += ['## 阻断原因', '']
        lines += [f'- {_code(reason)}' for reason in report['acceptance']['reasons']]
        lines.append('')
    if report['protocol'].get('errors'):
        lines += ['## 协议错误', '']
        lines += [f'- {_code(error)}' for error in report['protocol']['errors']]
        lines.append('')

    lines += [
        '## 组',
        '',
        '| 组 | libc | 段 | 适配器 | selected | pass | partial | fail | missing | not_run | judge_error |',
        '| --- | --- | --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |',
    ]
    for group in report['groups']:
        counts = group['counts']
        lines.append(
            f"| {_cell(group['group'])} | {_cell(group['libc'])} | {_cell(group['section'])} | {_cell(group['adapter'])} | "
            f"{counts['selected_total']} | {counts['pass']} | {counts['partial']} | "
            f"{counts['fail']} | {counts['missing']} | {counts['not_run']} | {counts['judge_error']} |"
        )
    lines.append('')
    native_groups = [group for group in report['groups'] if group.get('native')]
    if native_groups:
        lines += [
            '## LTP 原生计数',
            '',
            '| 组 | TPASS | TFAIL | TBROK | TCONF | TWARN | TINFO | TDEBUG | 缺汇总 | 不完整汇总 | 汇总不一致 |',
            '| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |',
        ]
        for group in native_groups:
            native = group['native']
            events = native.get('events', {})
            lines.append(
                f"| {_cell(group['group'])} | {events.get('TPASS', 0)} | {events.get('TFAIL', 0)} | "
                f"{events.get('TBROK', 0)} | {events.get('TCONF', 0)} | {events.get('TWARN', 0)} | "
                f"{events.get('TINFO', 0)} | {events.get('TDEBUG', 0)} | "
                f"{native.get('summary_missing', 0)} | {native.get('summary_incomplete', 0)} | "
                f"{native.get('summary_mismatch', 0)} |"
            )
        lines.append('')
        unattributed = [
            (group, event)
            for group in native_groups
            for event in group['native'].get('unattributed', [])
        ]
        if unattributed:
            lines += ['## LTP 未归属结果', '',
                      '以下结果不在任何 invocation 内，无法归属，阻断验收。', '']
            for group, event in unattributed:
                lines.append(
                    f"- {_code(group['shard_id'])} line {event['line']}："
                    f"{_code(event['event'])}"
                )
            lines.append('')

    metrics = [(group, metric)
               for group in report['groups']
               for metric in group.get('metrics', [])]
    if metrics:
        lines += ['## 性能原值', '',
                  '冻结 adapter 只给 PASS/FAIL；以下数值和单位直接来自原始段文本。', '',
                  '| 组 | 指标 | 值 | 单位 | 行 |',
                  '| --- | --- | ---: | --- | ---: |']
        for group, metric in metrics[:100]:
            label = metric.get('name', metric.get('label', ''))
            lines.append(
                f"| {_cell(group['group'])} | {_cell(label)} | "
                f"{metric['value']} | {_cell(metric['unit'])} | {metric['line']} |"
            )
        if len(metrics) > 100:
            lines.append(f'- 其余 {len(metrics) - 100} 项见 report.json。')
        lines.append('')

    non_pass = []
    for group in report['groups']:
        for item in group['items']:
            if item['judgment'] != 'PASS':
                non_pass.append((group, item))
    if non_pass:
        lines += ['## 非 PASS 项', '', '最多列出 100 项；完整逐项结果在 report.json。', '']
        for group, item in non_pass[:100]:
            reasons = ', '.join(item.get('reasons', [])) or '未记录原因'
            lines.append(
                f"- {_code(group['shard_id'])} / {_code(item['name'])}："
                f"{_code(item['judgment'])}（{_code(reasons)}）"
            )
        if len(non_pass) > 100:
            lines.append(f'- 其余 {len(non_pass) - 100} 项省略。')
        lines.append('')

    extras = [(group, extra) for group in report['groups'] for extra in group.get('extras', [])]
    if extras:
        lines += ['## 范围外名称', '', '未知名称单列，不进入分母，但阻断验收。', '']
        for group, extra in extras[:50]:
            lines.append(f"- {_code(group['shard_id'])} / {_code(extra['name'])}")
        if len(extras) > 50:
            lines.append(f'- 其余 {len(extras) - 50} 项省略。')
        lines.append('')

    if report['origin'] == 'external_log':
        lines += [
            '## 来源边界', '',
            '外部日志不证明本次主机运行条件；capture 为 NOT_RECORDED，'
            'subject/platform 均为 unknown，验收不能通过。', '',
        ]
    lines += [
        '## 实现身份', '',
        f"- 分析实现清单：`{report['implementation']['manifest_sha256']}`",
        f"- PROFILE 文件：`{report['profile']['source_file_sha256']}`",
        f"- CRITERIA 文件：`{report['criteria']['source_file_sha256']}`",
        '',
    ]
    return '\n'.join(lines)


def write_report(output: OutputDir, report: dict, source_raw: bytes | None = None) -> Path:
    """报告目录已独占创建；report.json 是最后写入的完成标记。"""
    if source_raw is not None:
        expected = report['input']['sha256']
        if sha256_bytes(source_raw) != expected:
            raise PublishError('source log changed before publication')
    markdown = _markdown(report)
    markdown_raw = markdown.encode('utf-8')
    report = dict(report)
    report['report_md'] = {'file': 'report.md', 'sha256': sha256_bytes(markdown_raw)}
    report_raw = dumps(report).encode('utf-8')
    try:
        if source_raw is not None:
            output.write('source.log', source_raw)
        output.write('report.md', markdown_raw, mode=0o644)
        output.write_atomic('report.json', report_raw, mode=0o644)
    except PublishError:
        raise
    except OSError as exc:
        raise PublishError(f'cannot publish report: {exc}') from exc
    return output.path / 'report.json'
