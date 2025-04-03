# db_utils.py

import sqlite3
from contextlib import contextmanager
from datetime import datetime, timedelta
from typing import Optional, Dict, List
from cogs.helpers import log  # Assumes you have a log function in helpers.py

DATABASE_FILE = "data.db"

@contextmanager
def get_db_connection():
    conn = sqlite3.connect(DATABASE_FILE)
    try:
        yield conn
    finally:
        conn.close()

# -------------------------------
# Database functions for recruitment
# -------------------------------

def initialize_database():
    try:
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS entries (
                    thread_id TEXT PRIMARY KEY,
                    recruiter_id TEXT NOT NULL,
                    starttime TEXT NOT NULL,
                    endtime TEXT,
                    embed_id TEXT,
                    ingame_name TEXT NOT NULL,
                    user_id TEXT NOT NULL,
                    region TEXT NOT NULL,
                    reminder_sent INTEGER DEFAULT 0,
                    role_type TEXT NOT NULL CHECK(role_type IN ('trainee', 'cadet'))
                )
                """
            )
            conn.commit()
            log("Database initialized successfully.")
    except sqlite3.Error as e:
        log(f"Database Initialization Error: {e}", level="error")

def add_entry(thread_id: str, recruiter_id: str, starttime: datetime, endtime: Optional[datetime], 
              role_type: str, embed_id: Optional[str], ingame_name: str, user_id: str, region: str) -> bool:
    if role_type not in ("trainee", "cadet"):
        raise ValueError("role_type must be either 'trainee' or 'cadet'.")
    start_str = starttime.isoformat()
    end_str = endtime.isoformat() if endtime else None
    try:
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """INSERT INTO entries 
                   (thread_id, recruiter_id, starttime, endtime, role_type, embed_id, ingame_name, user_id, region)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (thread_id, recruiter_id, start_str, end_str, role_type, embed_id, ingame_name, user_id, region)
            )
            conn.commit()
            log(f"Added entry to DB: thread_id={thread_id}, user_id={user_id}, role_type={role_type}")
            return True
    except sqlite3.IntegrityError:
        log("Database Error: Duplicate thread_id or integrity issue.", level="error")
        return False
    except sqlite3.Error as e:
        log(f"Database Error (add_entry): {e}", level="error")
        return False

def remove_entry(thread_id: str) -> bool:
    try:
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("DELETE FROM entries WHERE thread_id = ?", (thread_id,))
            conn.commit()
            removed = (cursor.rowcount > 0)
            if removed:
                log(f"Removed entry from DB for thread_id={thread_id}")
            return removed
    except sqlite3.Error as e:
        log(f"Database Error (remove_entry): {e}", level="error")
        return False

def update_endtime(thread_id: str, new_endtime: datetime) -> bool:
    try:
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("UPDATE entries SET endtime = ? WHERE thread_id = ?", (new_endtime.isoformat(), thread_id))
            conn.commit()
            updated = (cursor.rowcount > 0)
            if updated:
                log(f"Updated endtime for thread_id={thread_id} to {new_endtime.isoformat()}")
            return updated
    except sqlite3.Error as e:
        log(f"Database Error (update_endtime): {e}", level="error")
        return False

def get_entry(thread_id: str) -> Optional[Dict]:
    try:
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """SELECT recruiter_id, starttime, endtime, role_type, embed_id, ingame_name, user_id, region, reminder_sent
                   FROM entries
                   WHERE thread_id = ?""",
                (thread_id,)
            )
            row = cursor.fetchone()
            if row:
                return {
                    "thread_id": thread_id,
                    "recruiter_id": row[0],
                    "starttime": datetime.fromisoformat(row[1]),
                    "endtime": datetime.fromisoformat(row[2]) if row[2] else None,
                    "role_type": row[3],
                    "embed_id": row[4],
                    "ingame_name": row[5],
                    "user_id": row[6],
                    "region": row[7],
                    "reminder_sent": row[8]
                }
            return None
    except sqlite3.Error as e:
        log(f"Database Error (get_entry): {e}", level="error")
        return None

def is_user_in_database(user_id: int) -> bool:
    try:
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT 1 FROM entries WHERE user_id = ? LIMIT 1", (str(user_id),))
            result = cursor.fetchone()
            return result is not None
    except sqlite3.Error as e:
        log(f"Database Error (is_user_in_database): {e}", level="error")
        return False

def update_application_ingame_name(thread_id: str, new_name: str) -> bool:
    try:
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("UPDATE entries SET ingame_name = ? WHERE thread_id = ?", (new_name, thread_id))
            conn.commit()
            updated = cursor.rowcount > 0
            if updated:
                log(f"Updated ingame_name for thread {thread_id} to {new_name}")
            return updated
    except sqlite3.Error as e:
        log(f"DB Error (update_application_ingame_name): {e}", level="error")
        return False

# -------------------------------
# Role Requests
# -------------------------------

def init_role_requests_db():
    try:
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS role_requests (
                    user_id TEXT PRIMARY KEY,
                    request_type TEXT NOT NULL,
                    details TEXT NOT NULL,
                    timestamp TEXT NOT NULL
                )
                """
            )
            conn.commit()
            log("Role requests DB initialized successfully.")
    except sqlite3.Error as e:
        log(f"Role Requests DB Error: {e}", level="error")

def add_role_request(user_id: str, request_type: str, details: str) -> bool:
    try:
        with get_db_connection() as conn:
            cursor = conn.cursor()
            ts = datetime.now().isoformat()
            cursor.execute(
                """
                INSERT OR REPLACE INTO role_requests (user_id, request_type, details, timestamp)
                VALUES (?, ?, ?, ?)
                """,
                (user_id, request_type, details, ts)
            )
            conn.commit()
            return True
    except sqlite3.Error as e:
        log(f"DB Error (add_role_request): {e}", level="error")
        return False

def remove_role_request(user_id: str) -> bool:
    try:
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("DELETE FROM role_requests WHERE user_id = ?", (user_id,))
            conn.commit()
            return cursor.rowcount > 0
    except sqlite3.Error as e:
        log(f"DB Error (remove_role_request): {e}", level="error")
        return False

def get_role_request(user_id: str) -> Optional[Dict]:
    try:
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT user_id, request_type, details, timestamp FROM role_requests WHERE user_id = ?", (user_id,))
            row = cursor.fetchone()
            if row:
                return {"user_id": row[0], "request_type": row[1], "details": row[2], "timestamp": row[3]}
            return None
    except sqlite3.Error as e:
        log(f"DB Error (get_role_request): {e}", level="error")
        return None

def clear_role_requests() -> None:
    try:
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("DELETE FROM role_requests")
            conn.commit()
            log("All role requests have been cleared.")
    except sqlite3.Error as e:
        log(f"Error clearing role requests: {e}", level="error")

def get_role_requests() -> list:
    requests = []
    try:
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT user_id, request_type, details, timestamp FROM role_requests")
            rows = cursor.fetchall()
            for row in rows:
                requests.append({
                    "user_id": row[0],
                    "request_type": row[1],
                    "details": row[2],
                    "timestamp": row[3]
                })
    except sqlite3.Error as e:
        log(f"Error retrieving role requests: {e}", level="error")
    return requests

# -------------------------------
# Applications requests functions
# -------------------------------

def init_application_requests_db():
    try:
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS application_requests (
                    user_id TEXT PRIMARY KEY,
                    request_type TEXT NOT NULL,
                    ingame_name TEXT NOT NULL,
                    age TEXT NOT NULL,
                    level TEXT NOT NULL,
                    join_reason TEXT NOT NULL,
                    previous_crews TEXT,
                    region TEXT NOT NULL,
                    timestamp TEXT NOT NULL
                )
                """
            )
            conn.commit()
            log("Application requests DB initialized successfully.")
    except sqlite3.Error as e:
        log(f"Application Requests DB Error: {e}", level="error")

def add_application_request(user_id: str, data: Dict) -> bool:
    try:
        with get_db_connection() as conn:
            cursor = conn.cursor()
            ts = datetime.now().isoformat()
            cursor.execute(
                """
                INSERT OR REPLACE INTO application_requests 
                (user_id, request_type, ingame_name, age, level, join_reason, previous_crews, region, timestamp)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    user_id,
                    data.get("request_type"),
                    data.get("ingame_name"),
                    data.get("age"),
                    data.get("level"),
                    data.get("join_reason"),
                    data.get("previous_crews"),
                    data.get("region"),
                    ts
                )
            )
            conn.commit()
            return True
    except sqlite3.Error as e:
        log(f"DB Error (add_application_request): {e}", level="error")
        return False

def remove_application_request(user_id: str) -> bool:
    try:
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("DELETE FROM application_requests WHERE user_id = ?", (user_id,))
            conn.commit()
            return cursor.rowcount > 0
    except sqlite3.Error as e:
        log(f"DB Error (remove_application_request): {e}", level="error")
        return False

def get_application_request(user_id: str) -> Optional[Dict]:
    try:
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM application_requests WHERE user_id = ?", (user_id,))
            row = cursor.fetchone()
            if row:
                return {
                    "user_id": row[0],
                    "request_type": row[1],
                    "ingame_name": row[2],
                    "age": row[3],
                    "level": row[4],
                    "join_reason": row[5],
                    "previous_crews": row[6],
                    "region": row[7],
                    "timestamp": row[8]
                }
            return None
    except sqlite3.Error as e:
        log(f"DB Error (get_application_request): {e}", level="error")
        return None

def clear_pending_requests() -> None:
    try:
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("DELETE FROM application_requests")
            conn.commit()
            log("All pending application requests have been cleared.")
    except sqlite3.Error as e:
        log(f"Error clearing pending requests: {e}", level="error")

def get_application_requests() -> list:
    requests = []
    try:
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT user_id, request_type, ingame_name, age, level, join_reason, previous_crews, region, timestamp FROM application_requests")
            rows = cursor.fetchall()
            for row in rows:
                requests.append({
                    "user_id": row[0],
                    "request_type": row[1],
                    "ingame_name": row[2],
                    "age": row[3],
                    "level": row[4],
                    "join_reason": row[5],
                    "previous_crews": row[6],
                    "region": row[7],
                    "timestamp": row[8]
                })
    except sqlite3.Error as e:
        log(f"Error retrieving application requests: {e}", level="error")
    return requests

# -------------------------------
# Applications database functions
# -------------------------------

def init_applications_db():
    try:
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS application_threads (
                    thread_id                     TEXT PRIMARY KEY,
                    applicant_id                  TEXT NOT NULL,
                    recruiter_id                  TEXT,
                    starttime                     TEXT NOT NULL,
                    ingame_name                   TEXT NOT NULL,
                    region                        TEXT NOT NULL,
                    age                           TEXT NOT NULL,
                    level                         TEXT NOT NULL,
                    join_reason                   TEXT NOT NULL,
                    previous_crews                TEXT,
                    is_closed                     INTEGER DEFAULT 0,
                    status                        TEXT NOT NULL DEFAULT 'open',
                    ban_history_sent              INTEGER DEFAULT 0,
                    ban_history_reminder_count    INTEGER DEFAULT 0
                )
                """
            )
            conn.commit()
            log("Applications DB (application_threads) initialized successfully with new ban history columns.")
    except sqlite3.Error as e:
        log(f"Applications DB Error: {e}", level="error")

def add_application(
    thread_id: str,
    applicant_id: str,
    recruiter_id: Optional[str],
    starttime: datetime,
    ingame_name: str,
    region: str,
    age: str,
    level: str,
    join_reason: str = "",
    previous_crews: str = ""
) -> bool:
    start_str = starttime.isoformat()
    try:
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                INSERT INTO application_threads 
                (thread_id, applicant_id, recruiter_id, starttime, ingame_name, region, age, level, join_reason, previous_crews, is_closed, status)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, 'open')
                """,
                (thread_id, applicant_id, recruiter_id, start_str, ingame_name, region, age, level, join_reason, previous_crews)
            )
            conn.commit()
            log(f"Added new application thread {thread_id} from user {applicant_id}")
            return True
    except sqlite3.IntegrityError:
        log("Duplicate thread_id in 'application_threads' or integrity issue.", level="error")
        return False
    except sqlite3.Error as e:
        log(f"DB Error (add_application): {e}", level="error")
        return False

def get_application(thread_id: str) -> Optional[Dict]:
    try:
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                SELECT applicant_id, recruiter_id, starttime, ingame_name, region, age, level, join_reason, previous_crews, is_closed
                FROM application_threads
                WHERE thread_id = ?
                """,
                (thread_id,)
            )
            row = cursor.fetchone()
            if not row:
                return None
            return {
                "thread_id": thread_id,
                "applicant_id": row[0],
                "recruiter_id": row[1],
                "starttime": datetime.fromisoformat(row[2]),
                "ingame_name": row[3],
                "region": row[4],
                "age": row[5],
                "level": row[6],
                "join_reason": row[7],
                "previous_crews": row[8],
                "is_closed": row[9]
            }
    except sqlite3.Error as e:
        log(f"Database Error (get_application): {e}", level="error")
        return None

def update_application_recruiter(thread_id: str, new_recruiter_id: str) -> bool:
    try:
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                UPDATE application_threads
                SET recruiter_id = ?
                WHERE thread_id = ?
                """,
                (new_recruiter_id, thread_id)
            )
            conn.commit()
            updated = (cursor.rowcount > 0)
            if updated:
                log(f"Application thread {thread_id} claimed by {new_recruiter_id}")
            return updated
    except sqlite3.Error as e:
        log(f"DB Error (update_application_recruiter): {e}", level="error")
        return False

def close_application(thread_id: str) -> bool:
    try:
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                UPDATE application_threads
                SET is_closed = 1
                WHERE thread_id = ?
                """,
                (thread_id,)
            )
            conn.commit()
            closed = (cursor.rowcount > 0)
            if closed:
                log(f"Application thread {thread_id} marked as closed.")
            return closed
    except sqlite3.Error as e:
        log(f"DB Error (close_application): {e}", level="error")
        return False

def remove_application(thread_id: str) -> bool:
    try:
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("DELETE FROM application_threads WHERE thread_id = ?", (thread_id,))
            conn.commit()
            removed = (cursor.rowcount > 0)
            if removed:
                log(f"Removed application thread {thread_id} from DB.")
            return removed
    except sqlite3.Error as e:
        log(f"DB Error (remove_application): {e}", level="error")
        return False

def update_application_status(thread_id: str, new_status: str) -> bool:
    try:
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("UPDATE application_threads SET status = ? WHERE thread_id = ?", (new_status, thread_id))
            conn.commit()
            updated = (cursor.rowcount > 0)
            if updated:
                log(f"Updated application {thread_id} status to {new_status}")
            return updated
    except sqlite3.Error as e:
        log(f"DB Error (update_application_status): {e}", level="error")
        return False

def mark_application_removed(thread_id: str) -> bool:
    try:
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("UPDATE application_threads SET status = 'removed', is_closed = 1 WHERE thread_id = ?", (thread_id,))
            conn.commit()
            updated = (cursor.rowcount > 0)
            if updated:
                log(f"Marked application {thread_id} as removed")
            return updated
    except sqlite3.Error as e:
        log(f"DB Error (mark_application_removed): {e}", level="error")
        return False

def get_open_application(user_id: str) -> Optional[Dict]:
    try:
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                SELECT thread_id, applicant_id, recruiter_id, starttime, ingame_name,
                       region, age, level, join_reason, previous_crews, is_closed, status
                FROM application_threads
                WHERE applicant_id = ? AND is_closed = 0 AND status = 'open'
                """,
                (user_id,)
            )
            row = cursor.fetchone()
            if row:
                return {
                    "thread_id": row[0],
                    "applicant_id": row[1],
                    "recruiter_id": row[2],
                    "starttime": datetime.fromisoformat(row[3]),
                    "ingame_name": row[4],
                    "region": row[5],
                    "age": row[6],
                    "level": row[7],
                    "join_reason": row[8],
                    "previous_crews": row[9],
                    "is_closed": row[10],
                    "status": row[11]
                }
            else:
                return None
    except sqlite3.Error as e:
        log(f"DB Error (get_open_application): {e}", level="error")
        return None

def get_open_applications() -> list:
    applications = []
    try:
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT thread_id, applicant_id, recruiter_id, ingame_name, region, ban_history_sent, starttime FROM application_threads WHERE is_closed = 0 AND status = 'open'"
            )
            rows = cursor.fetchall()
            for row in rows:
                applications.append({
                    "thread_id": row[0],
                    "applicant_id": row[1],
                    "recruiter_id": row[2],
                    "ingame_name": row[3],
                    "region": row[4],
                    "ban_history_sent": int(row[5]),
                    "starttime": row[6]
                })
    except sqlite3.Error as e:
        log(f"DB Error (get_open_applications): {e}", level="error")
    return applications

def sort_applications(apps: list) -> list:
    def sort_key(app):
        if app["recruiter_id"]:
            return (2, 0)
        else:
            return (0, app["ban_history_sent"])
    return sorted(apps, key=sort_key)

# -------------------------------
# APPLICATION ATTEMPTS DATABASE FUNCTIONS
# -------------------------------

def init_application_attempts_db():
    try:
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS application_attempts (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    applicant_id TEXT NOT NULL,
                    region TEXT NOT NULL,
                    timestamp TEXT NOT NULL,
                    status TEXT NOT NULL,
                    log_url TEXT
                )
                """
            )
            conn.commit()
            log("Application attempts DB initialized successfully.")
    except sqlite3.Error as e:
        log(f"Application Attempts DB Error: {e}", level="error")

def add_application_attempt(applicant_id: str, region: str, status: str, log_url: str) -> bool:
    try:
        with get_db_connection() as conn:
            cursor = conn.cursor()
            timestamp = datetime.now().isoformat()
            cursor.execute(
                "INSERT INTO application_attempts (applicant_id, region, timestamp, status, log_url) VALUES (?, ?, ?, ?, ?)",
                (str(applicant_id), region, timestamp, status, log_url)
            )
            conn.commit()
            return True
    except sqlite3.Error as e:
        log(f"DB Error (add_application_attempt): {e}", level="error")
        return False

def get_recent_closed_attempts(applicant_id: str) -> list:
    try:
        with get_db_connection() as conn:
            cursor = conn.cursor()
            seven_days_ago = (datetime.now() - timedelta(days=7)).isoformat()
            cursor.execute(
                "SELECT timestamp, log_url FROM application_attempts WHERE applicant_id = ? AND status = 'closed_region_attempt' AND timestamp >= ?",
                (str(applicant_id), seven_days_ago)
            )
            rows = cursor.fetchall()
            return [{"timestamp": row[0], "log_url": row[1]} for row in rows]
    except sqlite3.Error as e:
        log(f"DB Error (get_recent_closed_attempts): {e}", level="error")
        return []

def get_application_stats(days: int = 0) -> dict:
    stats = {"accepted": 0, "denied": 0, "withdrawn": 0, "open": 0}
    try:
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cutoff = None
            if days and days > 0:
                cutoff = (datetime.now() - timedelta(days=days)).isoformat()
            for status in stats.keys():
                if cutoff:
                    cursor.execute(
                        "SELECT COUNT(*) FROM application_threads WHERE status = ? AND starttime >= ?",
                        (status, cutoff)
                    )
                else:
                    cursor.execute(
                        "SELECT COUNT(*) FROM application_threads WHERE status = ?",
                        (status,)
                    )
                stats[status] = cursor.fetchone()[0]
    except sqlite3.Error as e:
        log(f"DB Error (get_application_stats): {e}", level="error")
    return stats

def get_application_history(applicant_id: str) -> list:
    history = []
    try:
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT starttime, status, ingame_name, region FROM application_threads WHERE applicant_id = ?",
                (applicant_id,)
            )
            for row in cursor.fetchall():
                history.append({
                    "timestamp": row[0],
                    "status": row[1],
                    "type": "submission",
                    "details": f"IGN: {row[2]}, Region: {row[3]}"
                })
            cursor.execute(
                "SELECT timestamp, status, region, log_url FROM application_attempts WHERE applicant_id = ?",
                (applicant_id,)
            )
            for row in cursor.fetchall():
                details = f"Region: {row[2]}"
                if row[3]:
                    details += f", [Log Entry]({row[3]})"
                history.append({
                    "timestamp": row[0],
                    "status": row[1],
                    "type": "attempt",
                    "details": details
                })
    except sqlite3.Error as e:
        log(f"DB Error (get_application_history): {e}", level="error")
    return sorted(history, key=lambda x: x["timestamp"], reverse=True)

# -------------------------------
# APPLICATION STATUS
# -------------------------------

def init_region_status():
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS region_status (
                region TEXT PRIMARY KEY,
                status TEXT NOT NULL
            )
            """
        )
        for region in ['EU', 'NA', 'SEA']:
            cursor.execute(
                "INSERT OR IGNORE INTO region_status (region, status) VALUES (?, ?)",
                (region, "OPEN")
            )
        conn.commit()

init_region_status()

def get_region_status(region: str) -> Optional[str]:
    try:
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT status FROM region_status WHERE region = ?", (region.upper(),))
            row = cursor.fetchone()
            if row:
                return row[0]
            return None
    except sqlite3.Error as e:
        log(f"Error getting region status: {e}", level="error")
        return None

def update_region_status(region: str, status: str) -> bool:
    try:
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("UPDATE region_status SET status = ? WHERE region = ?", (status.upper(), region.upper()))
            conn.commit()
            return cursor.rowcount > 0
    except sqlite3.Error as e:
        log(f"Error updating region status: {e}", level="error")
        return False

# -------------------------------
# Timeouts/Blacklists Database Functions
# -------------------------------

def init_timeouts_db():
    try:
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS timeouts (
                    user_id TEXT PRIMARY KEY,
                    type TEXT NOT NULL,
                    expires_at TEXT
                )
                """
            )
            conn.commit()
            log("Timeouts/Blacklists DB initialized successfully.")
    except sqlite3.Error as e:
        log(f"Timeouts DB Error: {e}", level="error")

def add_timeout_record(user_id: str, record_type: str, expires_at: Optional[datetime] = None) -> bool:
    try:
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "INSERT OR REPLACE INTO timeouts (user_id, type, expires_at) VALUES (?, ?, ?)",
                (user_id, record_type, expires_at.isoformat() if expires_at else None)
            )
            conn.commit()
            return True
    except sqlite3.Error as e:
        log(f"DB Error (add_timeout_record): {e}", level="error")
        return False

def remove_timeout_record(user_id: str) -> bool:
    try:
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("DELETE FROM timeouts WHERE user_id = ?", (user_id,))
            conn.commit()
            return cursor.rowcount > 0
    except sqlite3.Error as e:
        log(f"DB Error (remove_timeout_record): {e}", level="error")
        return False

def get_timeout_record(user_id: str) -> Optional[Dict]:
    try:
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT user_id, type, expires_at FROM timeouts WHERE user_id = ?", (user_id,))
            row = cursor.fetchone()
            if row:
                return {
                    "user_id": row[0],
                    "type": row[1],
                    "expires_at": datetime.fromisoformat(row[2]) if row[2] else None
                }
            return None
    except sqlite3.Error as e:
        log(f"DB Error (get_timeout_record): {e}", level="error")
        return None

def get_all_timeouts() -> list:
    try:
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT user_id, type, expires_at FROM timeouts")
            rows = cursor.fetchall()
            result = []
            for row in rows:
                result.append({
                    "user_id": row[0],
                    "type": row[1],
                    "expires_at": datetime.fromisoformat(row[2]) if row[2] else None
                })
            return result
    except sqlite3.Error as e:
        log(f"DB Error (get_all_timeouts): {e}", level="error")
        return []
