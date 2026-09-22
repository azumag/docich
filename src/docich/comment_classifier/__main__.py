"""``python -m docich.comment_classifier <comments_file>``.

Prints the canonical classification JSON array (index/user/comment/category/
is_english) on stdout and exits 0, or prints nothing and exits 1 when the
batch is unusable. Never prints raw errors, comment text or credentials to
stderr.
"""
from __future__ import annotations

import sys

from docich.semantic_decision.validator import dumps

from . import classify_file


def main(argv=None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if len(argv) != 1:
        print('usage: python -m docich.comment_classifier <comments_file>', file=sys.stderr)
        return 2
    try:
        rows, _event = classify_file(argv[0])
    except Exception:
        return 1
    print(dumps(rows))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
