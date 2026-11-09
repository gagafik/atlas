"""The letterhead of server-rendered printouts (DESIGN.md, «Бланк для печати»): the school's colour logo, unmodified and embedded so that a saved copy keeps it, the school name and a thin brand line."""
import base64
from functools import cache
from html import escape
from pathlib import Path
ROOT=Path(__file__).resolve().parent.parent
FILE=Path('brand','quantum-stem-logo-color.png')
CSS='.letterhead{display:flex;flex-wrap:wrap;align-items:center;justify-content:space-between;gap:18px 24px;padding-bottom:20px;margin-bottom:20px;border-bottom:1px solid #083a81}.letterhead img{display:block;height:48px;width:auto}.letterhead p{margin:0;font-weight:bold}'
@cache
def logo():
    # public/ in a checkout, dist/ in the Docker image (only dist is copied there); no file, no logo.
    for base in ('public','dist'):
        path=ROOT/base/FILE
        if path.is_file():return 'data:image/png;base64,'+base64.b64encode(path.read_bytes()).decode()
    return ''
def letterhead(school):
    src=logo()
    return '<header class="letterhead">'+(f'<img src="{src}" width="931" height="325" alt="Quantum STEM School">' if src else '')+('<p>'+escape(school)+'</p>' if school else '')+'</header>'
