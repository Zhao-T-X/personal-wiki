"""Guard against the recurring frontend bug: a class used in a template that nothing defines.

This repo has repeatedly shipped views using classes whose definitions were never
ported from the prototype (`.panel`, `.tabs`, `.tle`, `.row`, `.faint`, ...). The
result is text that renders at the wrong weight/colour or layouts that collapse,
which is hard to spot in review and easy to spot here.

A class is reported only when it is used in a template and defined neither
globally (any .css file or non-scoped <style>) nor in that same component's scoped
style block - a scoped definition is a legitimate definition.

Usage:  python scripts/check_css.py        # exit code 1 when something is missing
"""
from __future__ import annotations

import re
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1] / 'frontend' / 'src'

RE_TEMPLATE = re.compile(r'<template>(.*?)</template>', re.S)
RE_STYLE = re.compile(r'<style([^>]*)>(.*?)</style>', re.S)
RE_CLASS_ATTR = re.compile(r'(?<!:)\bclass="([^"]*)"')
RE_BIND_CLASS = re.compile(r':class="([^"]*)"')
RE_STR_LITERAL = re.compile(r"'([^']+)'|\"([^\"]+)\"")
RE_SELECTOR_CLASS = re.compile(r'\.([A-Za-z][\w-]*)')
RE_VAR_USE = re.compile(r'var\((--[\w-]+)')
RE_VAR_DEF = re.compile(r'(--[\w-]+)\s*:')

# Identifiers that appear in :class expressions but are not class names (they are
# values compared against, or keys of library config objects).
IGNORE = {'entities', 'claims', 'events', 'ideas', 'questions', 'verified', 'rejected'}


def static_classes(template: str) -> list[str]:
    out: list[str] = []
    for match in RE_CLASS_ATTR.finditer(template):
        out.extend(match.group(1).split())
    return out


def dynamic_classes(template: str) -> list[str]:
    """Class names inside :class bindings (object keys and string literals)."""
    out: list[str] = []
    for match in RE_BIND_CLASS.finditer(template):
        expr = match.group(1)
        for literal in RE_STR_LITERAL.finditer(expr):
            out.append(literal.group(1) or literal.group(2) or '')
        inner = expr.strip()
        if inner.startswith('{'):
            for part in inner.strip('{}').split(','):
                key = part.split(':')[0].strip().strip("'\"")
                if re.fullmatch(r'[A-Za-z][\w-]*', key):
                    out.append(key)
    return [c for c in out if c]


def main() -> int:
    global_classes: set[str] = set()
    global_vars: set[str] = set()
    scoped_by_file: dict[str, set[str]] = defaultdict(set)
    used_by_file: dict[str, list[str]] = defaultdict(list)
    var_uses: dict[str, set[str]] = defaultdict(set)

    for path in sorted(ROOT.rglob('*')):
        if path.suffix == '.css':
            text = path.read_text(encoding='utf-8')
            global_classes.update(RE_SELECTOR_CLASS.findall(text))
            global_vars.update(RE_VAR_DEF.findall(text))
        elif path.suffix == '.vue':
            text = path.read_text(encoding='utf-8')
            rel = str(path.relative_to(ROOT)).replace('\\', '/')
            for attrs, body in RE_STYLE.findall(text):
                if 'scoped' in attrs:
                    scoped_by_file[rel].update(RE_SELECTOR_CLASS.findall(body))
                else:
                    global_classes.update(RE_SELECTOR_CLASS.findall(body))
                global_vars.update(RE_VAR_DEF.findall(body))
            for template in RE_TEMPLATE.findall(text):
                used_by_file[rel].extend(static_classes(template) + dynamic_classes(template))
            for var in RE_VAR_USE.findall(text):
                var_uses[rel].add(var)

    missing_total = 0
    for rel, used in sorted(used_by_file.items()):
        missing: dict[str, int] = defaultdict(int)
        for cls in used:
            if cls in IGNORE or cls in global_classes or cls in scoped_by_file.get(rel, set()):
                continue
            if not re.fullmatch(r'[A-Za-z][\w-]*', cls):
                continue
            missing[cls] += 1
        for cls, count in sorted(missing.items(), key=lambda x: -x[1]):
            missing_total += count
            print(f'{rel}: .{cls} used {count}x but never defined')

    missing_vars: dict[str, set[str]] = defaultdict(set)
    for rel, names in var_uses.items():
        for name in names:
            if name not in global_vars:
                missing_vars.setdefault(name, set()).add(rel)
    for name, where in sorted(missing_vars.items()):
        print(f'{name} used in {len(where)} file(s) but never defined')

    if missing_total or missing_vars:
        print(f'\nFAIL: {missing_total} undefined class usage(s), '
              f'{len(missing_vars)} undefined variable(s)')
        return 1
    print(f'ok: no undefined classes or variables '
          f'({len(global_classes)} classes / {len(global_vars)} variables defined)')
    return 0


if __name__ == '__main__':
    sys.exit(main())
