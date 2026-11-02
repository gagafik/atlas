"""Check the two builds side by side.

    python tools/demo/check_builds.py dist dist-demo

The normal build (dist) must hold no demo code or data; the demo build
(dist-demo) must hold the four role snapshots and no service worker.
"""
import json
import sys
from pathlib import Path

MARKERS = ['Это демо', 'Смотреть как', 'demo-data', 'Школа Пример']
ROLES = ['admin', 'teacher', 'parent', 'student']


def marker_forms(marker):
    # Minified bundles may keep text as UTF-8 or as \uXXXX escapes.
    return {marker.encode('utf-8'), json.dumps(marker)[1:-1].encode('ascii')}


def check(dist, demo):
    problems = []
    dist, demo = Path(dist), Path(demo)
    if not dist.is_dir():
        return [f'{dist} is not a directory']
    if not demo.is_dir():
        return [f'{demo} is not a directory']
    if (dist / 'demo-data').exists():
        problems.append('dist contains demo-data')
    for path in sorted(dist.rglob('*')):
        if not path.is_file():
            continue
        data = path.read_bytes()
        for marker in MARKERS:
            if any(form in data for form in marker_forms(marker)):
                problems.append(f'dist/{path.relative_to(dist).as_posix()} contains the demo marker "{marker}"')
    for role in ROLES:
        if not (demo / 'demo-data' / f'{role}.json').is_file():
            problems.append(f'dist-demo has no demo-data/{role}.json')
    if (demo / 'sw.js').exists():
        problems.append('dist-demo contains sw.js')
    return problems


def main(argv):
    if len(argv) != 2:
        print(__doc__)
        return 2
    problems = check(*argv)
    for problem in problems:
        print(problem)
    if problems:
        return 1
    print('Builds are separate: no demo markers in dist, four snapshots and no service worker in dist-demo.')
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
