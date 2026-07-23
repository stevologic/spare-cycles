"""SQLite layer for PromptPool. One file, WAL mode, short-lived connections."""

import hashlib
import os
import secrets
import sqlite3
import time
from contextlib import contextmanager

DATA_DIR = os.environ.get(
    "PROMPTPOOL_DATA",
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "data"),
)
DB_PATH = os.path.join(DATA_DIR, "promptpool.db")

SCHEMA = """
CREATE TABLE IF NOT EXISTS accounts(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  name TEXT UNIQUE NOT NULL,
  display_name TEXT NOT NULL,
  key_hash TEXT UNIQUE NOT NULL,
  created_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS projects(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  owner_id INTEGER NOT NULL REFERENCES accounts(id),
  slug TEXT UNIQUE NOT NULL,
  name TEXT NOT NULL,
  tagline TEXT NOT NULL DEFAULT '',
  description TEXT NOT NULL DEFAULT '',
  repo_url TEXT NOT NULL DEFAULT '',
  model TEXT NOT NULL,
  fallback_model TEXT NOT NULL DEFAULT '',
  temperature REAL,
  max_tokens INTEGER,
  inference_key_hash TEXT UNIQUE NOT NULL,
  created_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS nodes(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  account_id INTEGER NOT NULL REFERENCES accounts(id),
  name TEXT NOT NULL,
  token_hash TEXT UNIQUE NOT NULL,
  runners TEXT NOT NULL DEFAULT '[]',
  models TEXT NOT NULL DEFAULT '[]',
  last_seen REAL,
  jobs_done INTEGER NOT NULL DEFAULT 0,
  created_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS supports(
  account_id INTEGER NOT NULL REFERENCES accounts(id),
  project_id INTEGER NOT NULL REFERENCES projects(id),
  created_at REAL NOT NULL,
  PRIMARY KEY(account_id, project_id)
);
CREATE TABLE IF NOT EXISTS jobs(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  project_id INTEGER NOT NULL REFERENCES projects(id),
  kind TEXT NOT NULL DEFAULT 'batch',
  title TEXT NOT NULL DEFAULT '',
  prompt TEXT NOT NULL,
  model TEXT NOT NULL,
  fallback_model TEXT NOT NULL DEFAULT '',
  temperature REAL,
  max_tokens INTEGER,
  status TEXT NOT NULL DEFAULT 'queued',
  attempts INTEGER NOT NULL DEFAULT 0,
  node_id INTEGER REFERENCES nodes(id),
  use_model TEXT,
  output TEXT,
  error TEXT,
  runner TEXT,
  model_used TEXT,
  tokens_in INTEGER NOT NULL DEFAULT 0,
  tokens_out INTEGER NOT NULL DEFAULT 0,
  created_at REAL NOT NULL,
  claimed_at REAL,
  deadline REAL,
  finished_at REAL
);
CREATE INDEX IF NOT EXISTS idx_jobs_status ON jobs(status, project_id);
CREATE TABLE IF NOT EXISTS pair_codes(
  code TEXT PRIMARY KEY,
  account_id INTEGER NOT NULL REFERENCES accounts(id),
  expires_at REAL NOT NULL
);
"""

MAX_ATTEMPTS = 3
LEASE_REALTIME = 240   # seconds a node has to finish a realtime job
LEASE_BATCH = 900


@contextmanager
def connect():
    """Commit-and-close connection context (plain `with sqlite3.connect(...)`
    commits but never closes, leaking handles in a long-running server)."""
    conn = sqlite3.connect(DB_PATH, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    conn.execute("PRAGMA foreign_keys=ON")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init() -> None:
    os.makedirs(DATA_DIR, exist_ok=True)
    with connect() as conn:
        conn.executescript(SCHEMA)


def new_key(prefix: str) -> tuple[str, str]:
    """Return (plaintext, sha256hash). Plaintext is shown once, never stored."""
    plain = f"{prefix}_{secrets.token_urlsafe(24)}"
    return plain, hash_key(plain)


def hash_key(plain: str) -> str:
    return hashlib.sha256(plain.encode()).hexdigest()


def now() -> float:
    return time.time()


def requeue_expired(conn: sqlite3.Connection) -> None:
    """Return timed-out running jobs to the queue; fail them after MAX_ATTEMPTS."""
    t = now()
    conn.execute(
        "UPDATE jobs SET status='queued', node_id=NULL, use_model=NULL, "
        "claimed_at=NULL, deadline=NULL "
        "WHERE status='running' AND deadline < ? AND attempts < ?",
        (t, MAX_ATTEMPTS),
    )
    conn.execute(
        "UPDATE jobs SET status='failed', finished_at=?, "
        "error='no node completed this job in time' "
        "WHERE status='running' AND deadline < ? AND attempts >= ?",
        (t, t, MAX_ATTEMPTS),
    )
    # Realtime jobs nobody ever claimed go stale after 10 minutes.
    conn.execute(
        "UPDATE jobs SET status='expired', finished_at=?, "
        "error='no node picked this up in time' "
        "WHERE status='queued' AND kind='realtime' AND created_at < ?",
        (t, t - 600),
    )
