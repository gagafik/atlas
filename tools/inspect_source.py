"""Read the supplied Excel HTML/MIME export without changing the source."""
from email import policy
from email.parser import BytesParser
from html.parser import HTMLParser
from pathlib import Path
import json
import sys


class TableReader(HTMLParser):
    def __init__(self):
        super().__init__()
        self.rows = []
        self.row = None
        self.cell = None

    def handle_starttag(self, tag, attrs):
        if tag == 'tr':
            self.row = []
        if tag in ('td', 'th'):
            self.cell = dict(attrs)
            self.cell['text'] = ''
        if tag == 'br' and self.cell is not None:
            self.cell['text'] += '\n'

    def handle_data(self, data):
        if self.cell is not None:
            self.cell['text'] += data

    def handle_endtag(self, tag):
        if tag in ('td', 'th') and self.cell is not None:
            self.cell['text'] = self.cell['text'].strip()
            if self.row is not None:
                self.row.append(self.cell)
            self.cell = None
        if tag == 'tr' and self.row is not None:
            self.rows.append(self.row)
            self.row = None


def read_source(path):
    message = BytesParser(policy=policy.default).parsebytes(Path(path).read_bytes())
    html = message.get_payload(decode=True).decode('utf-8')
    reader = TableReader()
    reader.feed(html)
    return reader.rows


def expand(rows):
    grid = {}
    origins = []
    for r, row in enumerate(rows):
        c = 0
        for cell in row:
            while (r, c) in grid:
                c += 1
            cell = {**cell, 'row': r, 'col': c}
            origins.append(cell)
            for rr in range(r, r + int(cell.get('rowspan', 1))):
                for cc in range(c, c + int(cell.get('colspan', 1))):
                    grid[rr, cc] = cell
            c += int(cell.get('colspan', 1))
    return grid, origins


if __name__ == '__main__':
    rows = read_source(sys.argv[1])
    output = Path('data/source-rows.json')
    output.parent.mkdir(exist_ok=True)
    output.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding='utf-8')
    print('ROWS', len(rows))
    grid, origins = expand(rows)
    classes = [c['text'] for c in origins if c['col'] == 0]
    print('CLASS LABELS', json.dumps(classes, ensure_ascii=False))
    cells = [c for c in origins if c['row'] > 1 and c['col'] > 0 and c['text']]
    from collections import Counter
    print('CELLS', len(cells), 'LINE COUNTS', Counter(len(c['text'].split('\n')) for c in cells))
    print('GROUP LABELS', json.dumps(sorted(set(c['text'].split('\n')[3] for c in cells if len(c['text'].split('\n')) == 5)), ensure_ascii=False))
    for name in ['8A', '10A', '11A', '0 kaz', '0 rus']:
        found = [(c['row'], c['col'], c['text']) for c in cells if grid.get((c['row'], 0), {}).get('text') == name][:14]
        print(name, json.dumps(found, ensure_ascii=False))
