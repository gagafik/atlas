"""Seed test rows by column name, so a new table column does not break a test."""


def _name(identifier):
    return '"'+identifier.replace('"','""')+'"'


def read_row(db,table,/,**where):
    """Return the first row matching `where` as a {column: value} dict."""
    clause=' WHERE '+' AND '.join(f'{_name(column)} IS ?' for column in where) if where else ''
    cursor=db.execute(f'SELECT * FROM {_name(table)}{clause}',tuple(where.values()))
    found=cursor.fetchone()
    assert found is not None,f'no row in {table} matching {where}'
    return dict(zip((column[0] for column in cursor.description),found))


def insert_row(db,table,base=None,/,**values):
    """Insert `base` (usually from read_row) with `values` over it; other columns take their defaults."""
    row={**(base or {}),**values}
    columns=','.join(map(_name,row))
    db.execute(f'INSERT INTO {_name(table)} ({columns}) VALUES ({",".join("?"*len(row))})',tuple(row.values()))
