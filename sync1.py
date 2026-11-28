from pathlib import Path

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

p = Path('src/docich/hanjuku/version.py')
t = p.read_text()
s = t.index('<<<<<<<'); e = t.index('>>>>>>>'); e = t.index(chr(10), e) + 1
p.write_text(t[:s] + "BOT_VERSION = 'hanjuku-chart-v1484-clear-ending-corner-end'" + chr(10) + t[e:])
print('version ok')

import re
p = Path('ops/vm_actions/collect_diagnostics.py')
t = p.read_text()
if 'v1484-clear-ending-corner-end' not in t:
    m = re.search(r'(\n\s*)"hanjuku-chart-v1483-ch12-boss-staging",', t)
    if m:
        t = t[:m.start()] + m.group(0) + '\n' + m.group(1) + '"hanjuku-chart-v1484-clear-ending-corner-end",' + t[m.end():]
        p.write_text(t)
        print('allowlist added')
