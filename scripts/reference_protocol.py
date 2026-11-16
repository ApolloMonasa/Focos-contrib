"""新参考环境的准备/执行协议；旧日志只允许显式按 legacy 回放。"""
import re


def validate_reference(text, mode, selected, libc):
    failures = re.findall(r'^[ \t]*REF_FAIL(?:[ \t]+([^\r\n]*))?\r?$', text, re.M)
    result = {'status': 'NOT_APPLICABLE', 'errors': [], 'script_exits': {}}
    if failures:
        result.update(status='ERROR', errors=['REF_FAIL ' + stage for stage in failures])
        return result
    if mode is None and re.search(r'^[ \t]*REF(?:_| RUN | RESULT )', text, re.M):
        mode = 'strict'
    if mode is None:
        return result
    if mode == 'legacy':
        result.update(status='LEGACY_UNVERIFIED', errors=['历史日志未经过新环境协议验证'])
        return result
    phase = 'start'
    build_id_seen = False
    section_active = False
    prepared = set()
    active = None
    attempts = set()
    exits = {}
    errors = []
    for raw in text.splitlines():
        line = raw.strip()
        group_marker = re.fullmatch(r'####\s*OS COMP TEST GROUP\s+(START|END)\s+(\S+)\s*####', line)
        if group_marker:
            state, name = group_marker.groups()
            suffix = re.search(r'-(musl|glibc)$', name)
            key = (name[:suffix.start()], suffix[1]) if suffix else (name, active[1] if active else None)
            if active != key or (state == 'START' and section_active) or (state == 'END' and not section_active):
                errors.append('group marker outside its reference attempt: ' + line)
            section_active = state == 'START'
            continue
        if not re.match(r'^[ \t]*REF(?:_| RUN | RESULT )', line):
            continue
        if line == 'REF_PROTOCOL 1' and phase == 'start':
            phase = 'modules'
        elif re.fullmatch(r'REF_BUILD_ID [0-9a-f]{64}', line) and phase == 'modules' and not build_id_seen:
            build_id_seen = True
        elif line == 'REF_MODULES_OK' and phase == 'modules':
            phase = 'mount'
        elif line == 'REF_MOUNT_OK' and phase == 'mount':
            phase = 'prepare'
        elif (m := re.fullmatch(r'REF_PREPARED (musl|glibc)', line)):
            if phase != 'prepare' or m[1] in prepared:
                errors.append('invalid/duplicate preparation: ' + line)
            prepared.add(m[1])
        elif (m := re.fullmatch(r'REF RUN (musl|glibc) ([a-z0-9_]+)_testcode\.sh', line)):
            key = (m[2], m[1])
            if phase not in ('prepare', 'run') or active or m[1] not in prepared or key in attempts:
                errors.append('invalid/duplicate run: ' + line)
            attempts.add(key)
            active = key
            phase = 'run'
        elif (m := re.fullmatch(r'REF RESULT (musl|glibc) ([a-z0-9_]+)_testcode\.sh (\d+)', line)):
            key = (m[2], m[1])
            if phase != 'run' or active != key or section_active or key in exits or int(m[3]) > 255:
                errors.append('invalid script result: ' + line)
            exits[key] = int(m[3])
            active = None
        elif line == 'REF_ALL_DONE' and phase == 'run' and active is None:
            phase = 'done'
        else:
            errors.append('unexpected reference marker: ' + line)
    if phase != 'done':
        errors.append('incomplete reference protocol')
    for group in selected:
        key = (group, libc)
        if key not in exits:
            errors.append(f'missing reference attempt/result: {group}-{libc}')
    result.update(status='ERROR' if errors else 'VALID', errors=errors,
                  script_exits={f'{g}-{c}': code for (g, c), code in exits.items()})
    return result
