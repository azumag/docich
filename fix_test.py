from pathlib import Path
p = Path('tests/test_hanjuku_month_audit.py')
lines = p.read_text().splitlines(True)

# locate the duplicated old header and the interleaved test block
start = next(i for i, l in enumerate(lines) if l.startswith('def _run_boss(runtime, count=401, *, battle_at=None, hp=False,') and 'live_sortie' in l)
# old header is the same first line but without live_sortie continuation
old_header_idx = None
for i, l in enumerate(lines):
    if l.startswith('def _run_boss(runtime, count=401, *, battle_at=None, hp=False,') and 'live_sortie' not in l:
        old_header_idx = i
        break
assert old_header_idx is not None
# remove old header + its continuation line
del lines[old_header_idx:old_header_idx + 2]

# now find the test function that got interleaved
test_start = None
for i, l in enumerate(lines):
    if l.startswith('def test_chapter_four_season_king_battle_counts_as_boss_evidence'):
        test_start = i
        break
assert test_start is not None
# test block ends at the line starting with 'def _run_boss' after it
test_end = None
for i in range(test_start + 1, len(lines)):
    if lines[i].startswith('def _run_boss'):
        test_end = i
        break
assert test_end is not None
block = lines[test_start:test_end]
while block and not block[-1].strip():
    block.pop()
del lines[test_start:test_end]

# insert the test block after the _run_boss body (after its 'return value' line)
run_idx = next(i for i, l in enumerate(lines) if l.startswith('def _run_boss'))
ret_idx = next(i for i in range(run_idx, len(lines)) if lines[i].strip() == 'return value')
insert_at = ret_idx + 1
lines[insert_at:insert_at] = ['\n', '\n'] + block + ['\n']
p.write_text(''.join(lines))
print('repaired')
