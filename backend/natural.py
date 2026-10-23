"""Human order for class and person names: 7А before 10А, case-insensitive."""
import re


def natural(text):
    return tuple((0,int(p),'') if p.isascii() and p.isdigit() else (1,0,p.casefold()) for p in re.split(r'([0-9]+)',(text or '').strip()) if p)
