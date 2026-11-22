"""Database connection layer.

Default: local SQLite file (unchanged behavior).
If DATABASE_URL is set (e.g. a Neon Postgres connection string), the app
uses Postgres instead — that's what gives the Render deployment durable
storage on the free plan, since the SQLite file lives on Render's
ephemeral filesystem and is wiped on every deploy.

The Postgres path translates the app's SQLite-flavored SQL on the fly:
  ? placeholders                -> %s
  datetime('now', ...)          -> now() - interval ...
  date('now', ...)              -> current_date - interval ...
  INSERT OR REPLACE INTO        -> INSERT ... ON CONFLICT (job_id) DO UPDATE
  INTEGER PRIMARY KEY AUTOINCREMENT -> SERIAL PRIMARY KEY
  DATETIME                      -> TIMESTAMP
Rows come back as dicts, matching sqlite3.Row access patterns used by the
app (row['col'] and dict(row) both work).
"""
import os
import re


def translate_sql(sql):
    """Translate one SQLite-flavored statement to Postgres SQL."""
    s = sql
    # INSERT OR REPLACE -> upsert on the unique job_id
    m = re.search(r'INSERT OR REPLACE INTO (\w+)\s*\(([^)]+)\)\s*VALUES', s, re.I)
    if m:
        cols = [c.strip() for c in m.group(2).split(',')]
        updates = ', '.join(f'{c} = EXCLUDED.{c}' for c in cols if c != 'job_id')
        s = re.sub(r'INSERT OR REPLACE INTO', 'INSERT INTO', s, flags=re.I)
        s = s.rstrip().rstrip(';')
        s += f' ON CONFLICT (job_id) DO UPDATE SET {updates}'
    # datetime('now', '-' || ? || ' days') -> (now() - (%s || ' days')::interval)
    s = re.sub(r"datetime\('now',\s*'-\s*'\s*\|\|\s*\?\s*\|\|\s*'\s*days'\)",
               "(now() - (%s || ' days')::interval)", s)
    s = re.sub(r"date\('now',\s*'-\s*'\s*\|\|\s*\?\s*\|\|\s*'\s*days'\)",
               "(current_date - (%s || ' days')::interval)", s)
    # datetime('now', '-30 days') -> (now() - interval '30 days')
    s = re.sub(r"datetime\('now',\s*'-(\d+) days'\)", r"(now() - interval '\1 days')", s)
    s = re.sub(r"date\('now',\s*'-(\d+) days'\)", r"(current_date - interval '\1 days')", s)
    s = re.sub(r"datetime\('now'\)", 'now()', s)
    # DDL translations
    s = re.sub(r'INTEGER PRIMARY KEY AUTOINCREMENT', 'SERIAL PRIMARY KEY', s, flags=re.I)
    s = re.sub(r'\bDATETIME\b', 'TIMESTAMP', s, flags=re.I)
    # placeholders
    s = s.replace('?', '%s')
    return s


class _PGCursor:
    def __init__(self, cur):
        self._cur = cur
        self._lastrowid = None

    def fetchone(self):
        row = self._cur.fetchone()
        return dict(row) if row is not None else None

    def fetchall(self):
        return [dict(r) for r in self._cur.fetchall()]

    @property
    def lastrowid(self):
        if self._lastrowid is None:
            try:
                row = self._cur.fetchone()
                if row is not None:
                    self._lastrowid = dict(row)['id']
            except Exception:
                pass
        return self._lastrowid


class PGConnection:
    """Minimal sqlite3.Connection-shaped wrapper over psycopg2."""

    def __init__(self, dsn):
        import psycopg2
        import psycopg2.extras
        self._conn = psycopg2.connect(dsn, cursor_factory=psycopg2.extras.RealDictCursor)

    def execute(self, sql, params=()):
        pg_sql = translate_sql(sql)
        cur = self._conn.cursor()
        if re.match(r'\s*INSERT INTO applications\b', pg_sql, re.I) and 'RETURNING' not in pg_sql.upper():
            pg_sql += ' RETURNING id'
        cur.execute(pg_sql, tuple(params) if params else None)
        return _PGCursor(cur)

    def executescript(self, script):
        cur = self._conn.cursor()
        for stmt in script.split(';'):
            stmt = stmt.strip()
            if stmt:
                cur.execute(translate_sql(stmt))
        self._conn.commit()

    def commit(self):
        self._conn.commit()

    def close(self):
        self._conn.close()


def connect(db_path):
    """Return a connection: Postgres when DATABASE_URL is set, else SQLite."""
    dsn = os.environ.get('DATABASE_URL')
    if dsn:
        return PGConnection(dsn)
    import sqlite3
    os.makedirs(os.path.dirname(db_path), exist_ok=True)
    conn = sqlite3.connect(db_path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn
