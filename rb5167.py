from pathlib import Path
import re

def union(path):
    p = Path(path)
    lines = p.read_text().splitlines(True)
    out, i = [], 0
    while i < len(lines):
        if lines[i].startswith('<<<<<<<'):
            i += 1
            ours = []
            while i < len(lines) and not lines[i].startswith('======='):
                ours.append(lines[i]); i += 1
            i += 1
            theirs = []
            while i < len(lines) and not lines[i].startswith('>>>>>>>'):
                theirs.append(lines[i]); i += 1
            i += 1
            merged = list(ours)
            for line in theirs:
                if line not in merged:
                    merged.append(line)
            out.extend(merged)
        else:
            out.append(lines[i]); i += 1
    p.write_text(''.join(out))
    print('union', path)

union('ops/vm_actions/collect_diagnostics.py')
union('ops/vm_actions/tests/test_collect_diagnostics.py')

p = Path('src/docich/hanjuku/version.py')
t = p.read_text()
s = t.index('<<<<<<<'); e = t.index('>>>>>>>'); e = t.index(chr(10), e) + 1
p.write_text(t[:s] + "BOT_VERSION = 'hanjuku-chart-v1507-verified-month-blind-nav'" + chr(10) + t[e:])

for path in ('ops/vm_actions/collect_diagnostics.py', 'ops/vm_actions/tests/test_collect_diagnostics.py'):
    p = Path(path); t = p.read_text()
    if 'v1507-verified-month-blind-nav' in t:
        continue
    lines = t.splitlines(True)
    for i, line in enumerate(lines):
        if 'v1506-hp1-nonboss-resource-hold' in line:
            indent = line[:len(line) - len(line.lstrip())]
            lines.insert(i + 1, indent + '"hanjuku-chart-v1507-verified-month-blind-nav",' + chr(10))
            p.write_text(''.join(lines))
            print('added', path)
            break
