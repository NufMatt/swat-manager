#!/usr/bin/env python3
"""
migrate_db.py

Migrate old GTA:CNR playtime logs (per-tick) into the new optimized schema
with session-based storage.
"""

import sqlite3
from datetime import datetime, timedelta
import argparse

def parse_args():
    p = argparse.ArgumentParser(
        description="Convert old player_logs.db → new session-based schema."
    )
    p.add_argument(
        "--old", "-o",
        default="player_logs.db",
        help="Path to OLD SQLite DB (with playtime_log table)."
    )
    p.add_argument(
        "--new", "-n",
        default="player_logs_new.db",
        help="Path to NEW SQLite DB to create."
    )
    p.add_argument(
        "--interval", "-i",
        type=int,
        default=60,
        help="Your CHECK_INTERVAL in seconds (gap threshold = 2× this)."
    )
    return p.parse_args()

def iso_to_dt(s: str) -> datetime:
    # assume ISO format in playtime_log.log_time
    return datetime.fromisoformat(s)

def dt_to_str(dt: datetime) -> str:
    # target format: "YYYY-MM-DD HH:MM:SS+00:00"
    return dt.strftime("%Y-%m-%d %H:%M:%S+00:00")

def create_new_schema(cur):
    # players_info
    cur.execute("""
    CREATE TABLE IF NOT EXISTS players_info (
      uid TEXT PRIMARY KEY,
      current_name TEXT,
      crew TEXT,
      rank INTEGER,
      total_playtime REAL
    )""")
    # sessions
    cur.execute("""
    CREATE TABLE IF NOT EXISTS sessions (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      uid TEXT,
      server TEXT,
      start_time TEXT,
      end_time TEXT,
      duration REAL
    )""")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_sessions_uid ON sessions(uid)")
    # name_changes
    cur.execute("""
    CREATE TABLE IF NOT EXISTS name_changes (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      uid TEXT,
      old_name TEXT,
      new_name TEXT,
      change_time TEXT
    )""")

def migrate_players_info(old_cur, new_cur):
    rows = old_cur.execute("SELECT uid, current_name, total_playtime FROM players_info").fetchall()
    for r in rows:
        new_cur.execute("""
        INSERT OR REPLACE INTO players_info
          (uid, current_name, crew, rank, total_playtime)
        VALUES (?, ?, ?, ?, ?)
        """, (r["uid"], r["current_name"], "", 0, r["total_playtime"]))

def migrate_name_changes(old_cur, new_cur):
    rows = old_cur.execute("""
      SELECT uid, old_name, new_name, change_time
      FROM name_changes
      ORDER BY change_time
    """).fetchall()
    for r in rows:
        new_cur.execute("""
        INSERT INTO name_changes
          (uid, old_name, new_name, change_time)
        VALUES (?, ?, ?, ?)
        """, (r["uid"], r["old_name"], r["new_name"], r["change_time"]))

def migrate_sessions(old_cur, new_cur, interval_sec):
    # pull all playtime_log entries, ordered by uid & time
    logs = old_cur.execute("""
      SELECT uid, log_time, seconds
        FROM playtime_log
       ORDER BY uid, log_time
    """).fetchall()

    gap_thresh = interval_sec * 2
    from itertools import groupby

    for uid, grp in groupby(logs, key=lambda r: r["uid"]):
        session_entries = []
        last_dt = None

        def flush_session():
            if not session_entries:
                return
            # session start at first.log_time
            start_dt = iso_to_dt(session_entries[0]["log_time"])
            total_secs = sum(r["seconds"] for r in session_entries)
            end_dt = start_dt + timedelta(seconds=total_secs)
            new_cur.execute("""
            INSERT INTO sessions
              (uid, server, start_time, end_time, duration)
            VALUES (?, ?, ?, ?, ?)
            """, (
                uid,
                "UNKNOWN",               # old data doesn’t track server
                dt_to_str(start_dt),
                dt_to_str(end_dt),
                total_secs
            ))

        for row in grp:
            this_dt = iso_to_dt(row["log_time"])
            if last_dt is None:
                # first entry of first session
                session_entries = [row]
            else:
                gap = (this_dt - last_dt).total_seconds()
                if gap <= gap_thresh:
                    session_entries.append(row)
                else:
                    # gap too big → flush old session, start new
                    flush_session()
                    session_entries = [row]
            last_dt = this_dt

        # flush whatever’s left
        flush_session()

def main():
    args = parse_args()

    # connect
    old_conn = sqlite3.connect(args.old, detect_types=sqlite3.PARSE_DECLTYPES)
    old_conn.row_factory = sqlite3.Row
    new_conn = sqlite3.connect(args.new)
    new_conn.row_factory = sqlite3.Row

    old_cur = old_conn.cursor()
    new_cur = new_conn.cursor()

    # 1) create new schema
    create_new_schema(new_cur)
    new_conn.commit()

    # 2) migrate players_info
    migrate_players_info(old_cur, new_cur)
    new_conn.commit()

    # 3) migrate name_changes
    migrate_name_changes(old_cur, new_cur)
    new_conn.commit()

    # 4) migrate sessions
    migrate_sessions(old_cur, new_cur, args.interval)
    new_conn.commit()

    old_conn.close()
    new_conn.close()
    print(f"▶️ Migration complete! New DB at {args.new}")

if __name__ == "__main__":
    main()
