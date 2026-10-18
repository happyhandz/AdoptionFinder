import json
import sqlite3
import threading
import time

from . import config

SCHEMA = """
CREATE TABLE IF NOT EXISTS listings (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  source TEXT NOT NULL,
  source_id TEXT NOT NULL,
  url TEXT,
  name TEXT,
  breed_primary TEXT,
  breed_secondary TEXT,
  mixed INTEGER DEFAULT 0,
  color TEXT,
  age_group TEXT,
  age_text TEXT,
  age_months REAL,
  sex TEXT,
  size TEXT,
  description TEXT,
  photos TEXT DEFAULT '[]',
  city TEXT, state TEXT, zip TEXT,
  lat REAL, lon REAL,
  org TEXT,
  transport INTEGER DEFAULT 0,
  listed_at REAL,
  first_seen REAL,
  last_seen REAL,
  missed INTEGER DEFAULT 0,
  status TEXT DEFAULT 'active',
  canonical_id INTEGER,
  photo_hash TEXT,
  UNIQUE(source, source_id)
);
CREATE INDEX IF NOT EXISTS idx_listings_status ON listings(status);
CREATE INDEX IF NOT EXISTS idx_listings_canon ON listings(canonical_id);
CREATE TABLE IF NOT EXISTS saved_searches (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  name TEXT NOT NULL,
  params TEXT NOT NULL,
  created_at REAL
);
CREATE TABLE IF NOT EXISTS source_status (
  source TEXT PRIMARY KEY,
  state TEXT,
  last_ok REAL,
  last_run REAL,
  last_error TEXT,
  count INTEGER DEFAULT 0
);
CREATE TABLE IF NOT EXISTS dedupe_split (a INTEGER NOT NULL, b INTEGER NOT NULL, PRIMARY KEY (a, b));
CREATE TABLE IF NOT EXISTS petfinder_scrub (
  url_key TEXT NOT NULL, dog_id TEXT NOT NULL, seen_at REAL,
  PRIMARY KEY (url_key, dog_id)
);
CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE IF NOT EXISTS geo_cache (
  key TEXT PRIMARY KEY,
  lat REAL, lon REAL, zip TEXT, label TEXT
);
"""

LISTING_COLS = [
    "source", "source_id", "url", "name", "breed_primary", "breed_secondary", "mixed",
    "color", "age_group", "age_text", "age_months", "sex", "size", "description", "photos",
    "city", "state", "zip", "lat", "lon", "org", "transport", "listed_at",
]


class DB:
    def __init__(self, path=None):
        path = path or config.DB_PATH
        if str(path) != ":memory:":
            path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(path), check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.lock = threading.RLock()
        with self.lock:
            self.conn.executescript(SCHEMA)
            try:  # added later: when a source first completed a poll (to tell "new" dogs from the initial load)
                self.conn.execute("ALTER TABLE source_status ADD COLUMN first_ok REAL")
                self.conn.execute("UPDATE source_status SET first_ok=last_ok WHERE first_ok IS NULL AND last_ok IS NOT NULL")
            except sqlite3.OperationalError:
                pass  # column already exists
            self.conn.commit()

    def q(self, sql, args=()):
        with self.lock:
            return [dict(r) for r in self.conn.execute(sql, args).fetchall()]

    def run(self, sql, args=()):
        with self.lock:
            cur = self.conn.execute(sql, args)
            self.conn.commit()
            return cur

    # --- listings -------------------------------------------------------
    def upsert_listing(self, l):
        """Insert or refresh a listing. Returns (id, is_new)."""
        now = time.time()
        row = dict(l)
        if isinstance(row.get("photos"), (list, tuple)):
            row["photos"] = json.dumps(row["photos"])
        with self.lock:
            existing = self.conn.execute(
                "SELECT id FROM listings WHERE source=? AND source_id=?",
                (row["source"], row["source_id"]),
            ).fetchone()
            if existing:
                sets = ",".join(f"{c}=?" for c in LISTING_COLS if c not in ("source", "source_id"))
                vals = [row.get(c) for c in LISTING_COLS if c not in ("source", "source_id")]
                self.conn.execute(
                    f"UPDATE listings SET {sets}, last_seen=?, missed=0, status='active' WHERE id=?",
                    vals + [now, existing["id"]],
                )
                self.conn.commit()
                return existing["id"], False
            cols = LISTING_COLS + ["first_seen", "last_seen"]
            vals = [row.get(c) for c in LISTING_COLS] + [now, now]
            cur = self.conn.execute(
                f"INSERT INTO listings ({','.join(cols)}) VALUES ({','.join('?' * len(cols))})", vals
            )
            lid = cur.lastrowid
            self.conn.execute("UPDATE listings SET canonical_id=? WHERE id=?", (lid, lid))
            self.conn.commit()
            return lid, True

    def known_ids(self, source):
        """All source_ids ever stored for a source (any status)."""
        return {r["source_id"] for r in self.q("SELECT source_id FROM listings WHERE source=?", (source,))}

    def touch(self, source, source_ids):
        """Mark listings as still present without re-fetching them."""
        now = time.time()
        ids = list(source_ids)
        with self.lock:
            for i in range(0, len(ids), 500):
                chunk = ids[i:i + 500]
                self.conn.execute(
                    f"UPDATE listings SET last_seen=?, missed=0, status='active' "
                    f"WHERE source=? AND status!='skip' AND source_id IN ({','.join('?' * len(chunk))})",
                    [now, source] + chunk,
                )
            self.conn.commit()

    def skip(self, source, source_id):
        """Remember an id we looked at and don't want (cat, adopted...) so it isn't re-fetched."""
        self.run(
            "INSERT OR IGNORE INTO listings (source, source_id, status, first_seen, last_seen) VALUES (?,?,'skip',?,?)",
            (source, source_id, time.time(), time.time()),
        )

    def retire(self, source, source_ids):
        """Permanently drop listings the source itself says are no longer available (status 'skip': a later
        sitemap/touch never brings them back, unlike 'gone')."""
        ids = list(source_ids)
        with self.lock:
            for i in range(0, len(ids), 500):
                chunk = ids[i:i + 500]
                self.conn.execute(f"UPDATE listings SET status='skip' WHERE source=? AND source_id IN ({','.join('?' * len(chunk))})",
                                  [source] + chunk)
            self.conn.commit()

    def run_many(self, sql, rows):
        with self.lock:
            self.conn.executemany(sql, list(rows))
            self.conn.commit()

    def mark_gone(self, source, source_ids):
        """Mark specific listings as no longer available."""
        ids = list(source_ids)
        with self.lock:
            for i in range(0, len(ids), 500):
                chunk = ids[i:i + 500]
                self.conn.execute(
                    f"UPDATE listings SET status='gone' WHERE source=? AND status='active' "
                    f"AND source_id IN ({','.join('?' * len(chunk))})", [source] + chunk)
            self.conn.commit()

    def expire_stale(self, source, days):
        """Imported (non-polled) sources: dogs not seen in any import for N days are considered gone."""
        with self.lock:
            self.conn.execute(
                "UPDATE listings SET status='gone' WHERE source=? AND status='active' AND last_seen < ?",
                (source, time.time() - days * 86400))
            self.conn.commit()

    def count_active(self, source):
        return self.q("SELECT COUNT(*) AS n FROM listings WHERE source=? AND status='active'", (source,))[0]["n"]

    def count_seen_since(self, source, since):
        """Active listings of a source that were stored or refreshed at or after `since`."""
        return self.q("SELECT COUNT(*) AS n FROM listings WHERE source=? AND status='active' AND last_seen>=?",
                      (source, since))[0]["n"]

    def remove_unseen(self, source, since):
        """Mark as gone every active listing of the source that was not stored or refreshed since `since`.
        Returns how many were removed. (Only the cleanup check calls this; a normal refresh never removes.)"""
        with self.lock:
            n = self.conn.execute("UPDATE listings SET status='gone' WHERE source=? AND status='active' AND last_seen<?",
                                  (source, since)).rowcount
            self.conn.commit()
            return n

    def active_listings(self):
        rows = self.q("SELECT * FROM listings WHERE status='active'")
        for r in rows:
            r["photos"] = json.loads(r["photos"] or "[]")
        return rows

    # --- saved searches -------------------------------------------------
    def list_searches(self):
        rows = self.q("SELECT * FROM saved_searches ORDER BY name COLLATE NOCASE")
        for r in rows:
            r["params"] = json.loads(r["params"])
        return rows

    def save_search(self, name, params, sid=None):
        if sid:
            self.run("UPDATE saved_searches SET name=?, params=? WHERE id=?", (name, json.dumps(params), sid))
            return sid
        return self.run(
            "INSERT INTO saved_searches (name, params, created_at) VALUES (?,?,?)",
            (name, json.dumps(params), time.time()),
        ).lastrowid

    def delete_search(self, sid):
        self.run("DELETE FROM saved_searches WHERE id=?", (sid,))

    # --- source status --------------------------------------------------
    def set_status(self, source, ok, error=None, count=0):
        now = time.time()
        if ok:
            self.run(
                "INSERT INTO source_status (source,state,last_ok,first_ok,last_run,last_error,count) VALUES (?,?,?,?,?,NULL,?) "
                "ON CONFLICT(source) DO UPDATE SET state='ok', last_ok=?, first_ok=COALESCE(first_ok, ?), last_run=?, "
                "last_error=NULL, count=?",
                (source, "ok", now, now, now, count, now, now, now, count),
            )
        else:
            self.run(
                "INSERT INTO source_status (source,state,last_run,last_error) VALUES (?,?,?,?) "
                "ON CONFLICT(source) DO UPDATE SET state='degraded', last_run=?, last_error=?",
                (source, "degraded", now, error, now, error),
            )

    def statuses(self):
        return self.q("SELECT * FROM source_status ORDER BY source")

    # --- dedupe ---------------------------------------------------------
    def splits(self):
        """Pairs of listing ids the user said are NOT the same dog."""
        return {(r["a"], r["b"]) for r in self.q("SELECT a, b FROM dedupe_split")}

    def add_split(self, x, y):
        a, b = sorted((int(x), int(y)))
        self.run("INSERT OR IGNORE INTO dedupe_split (a, b) VALUES (?, ?)", (a, b))

    def set_photo_hash(self, lid, h):
        self.run("UPDATE listings SET photo_hash=? WHERE id=?", (h, lid))

    def set_canonicals(self, mapping):
        with self.lock:
            self.conn.executemany("UPDATE listings SET canonical_id=? WHERE id=?", [(c, i) for i, c in mapping.items()])
            self.conn.commit()

    # --- settings -------------------------------------------------------
    def get_setting(self, key, default=None):
        r = self.q("SELECT value FROM settings WHERE key=?", (key,))
        return r[0]["value"] if r else default

    def set_setting(self, key, value):
        self.run("INSERT INTO settings (key,value) VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=?",
                 (key, str(value), str(value)))

    # --- geo cache ------------------------------------------------------
    def geo_get(self, key):
        r = self.q("SELECT * FROM geo_cache WHERE key=?", (key,))
        return r[0] if r else None

    def geo_put(self, key, lat, lon, zip_, label):
        self.run(
            "INSERT OR REPLACE INTO geo_cache (key,lat,lon,zip,label) VALUES (?,?,?,?,?)",
            (key, lat, lon, zip_, label),
        )
