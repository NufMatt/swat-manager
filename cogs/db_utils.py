# db_utils.py

import aiosqlite
from contextlib import asynccontextmanager

from datetime import datetime, timedelta
from typing import Optional, Dict, List
from cogs.helpers import log  # Assumes you have a log function in helpers.py

DATABASE_FILE = "data.db"

@asynccontextmanager
async def get_db_connection():
    # Open connection
    conn = await aiosqlite.connect(DATABASE_FILE)
    # Optional PRAGMA tweaks for performance
    await conn.execute("PRAGMA journal_mode=WAL;")
    await conn.execute("PRAGMA synchronous=NORMAL;")
    try:
        yield conn
    finally:
        await conn.close()

# -------------------------------
# Database functions for recruitment
# -------------------------------

async def initialize_database():
    try:
        async with get_db_connection() as conn:
            cursor = await conn.cursor()
            await cursor.execute(
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
            await conn.commit()
            log("Database initialized successfully.")
    except aiosqlite.Error as e:
        log(f"Database Initialization Error: {e}", level="error")

async def add_entry(thread_id: str, recruiter_id: str, starttime: datetime, endtime: Optional[datetime], 
              role_type: str, embed_id: Optional[str], ingame_name: str, user_id: str, region: str) -> bool:
    if role_type not in ("trainee", "cadet"):
        raise ValueError("role_type must be either 'trainee' or 'cadet'.")
    start_str = starttime.isoformat()
    end_str = endtime.isoformat() if endtime else None
    try:
        async with get_db_connection() as conn:
            cursor = await conn.cursor()
            await cursor.execute(
                            """INSERT INTO entries 
                            (thread_id, recruiter_id, starttime, endtime, embed_id, ingame_name, user_id, region, role_type)
                            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                            (thread_id, recruiter_id, start_str, end_str, embed_id, ingame_name, user_id, region, role_type)
                        )
            await conn.commit()
            log(f"Added entry to DB: thread_id={thread_id}, user_id={user_id}, role_type={role_type}")
            return True
    except aiosqlite.IntegrityError:
        log("Database Error: Duplicate thread_id or integrity issue.", level="error")
        return False
    except aiosqlite.Error as e:
        log(f"Database Error (add_entry): {e}", level="error")
        return False

async def remove_entry(thread_id: str) -> bool:
    try:
        async with get_db_connection() as conn:
            cursor = await conn.cursor()
            await cursor.execute("DELETE FROM entries WHERE thread_id = ?", (thread_id,))
            await conn.commit()
            removed = (cursor.rowcount > 0)
            if removed:
                log(f"Removed entry from DB for thread_id={thread_id}")
            return removed
    except aiosqlite.Error as e:
        log(f"Database Error (remove_entry): {e}", level="error")
        return False

async def update_endtime(thread_id: str, new_endtime: datetime) -> bool:
    try:
        async with get_db_connection() as conn:
            cursor = await conn.cursor()
            await cursor.execute("UPDATE entries SET endtime = ? WHERE thread_id = ?", (new_endtime.isoformat(), thread_id))
            await conn.commit()
            updated = (cursor.rowcount > 0)
            if updated:
                log(f"Updated endtime for thread_id={thread_id} to {new_endtime.isoformat()}")
            return updated
    except aiosqlite.Error as e:
        log(f"Database Error (update_endtime): {e}", level="error")
        return False

async def get_entry(thread_id: str) -> Optional[Dict]:
    try:
        async with get_db_connection() as conn:
            cursor = await conn.cursor()
            await cursor.execute(
                """SELECT recruiter_id, starttime, endtime, role_type, embed_id, ingame_name, user_id, region, reminder_sent
                   FROM entries
                   WHERE thread_id = ?""",
                (thread_id,)
            )
            row = await cursor.fetchone()
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
    except aiosqlite.Error as e:
        log(f"Database Error (get_entry): {e}", level="error")
        return None

async def is_user_in_database(user_id: int) -> bool:
    try:
        async with get_db_connection() as conn:
            cursor = await conn.cursor()
            await cursor.execute("SELECT 1 FROM entries WHERE user_id = ? LIMIT 1", (str(user_id),))
            result = await cursor.fetchone()
            return result is not None
    except aiosqlite.Error as e:
        log(f"Database Error (is_user_in_database): {e}", level="error")
        return False

async def update_application_ingame_name(thread_id: str, new_name: str) -> bool:
    try:
        async with get_db_connection() as conn:
            cursor = await conn.cursor()
            await cursor.execute("UPDATE entries SET ingame_name = ? WHERE thread_id = ?", (new_name, thread_id))
            await conn.commit()
            updated = cursor.rowcount > 0
            if updated:
                log(f"Updated ingame_name for thread {thread_id} to {new_name}")
            return updated
    except aiosqlite.Error as e:
        log(f"DB Error (update_application_ingame_name): {e}", level="error")
        return False

# -------------------------------
# Role Requests
# -------------------------------

async def init_role_requests_db():
    try:
        async with get_db_connection() as conn:
            cursor = await conn.cursor()
            await cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS role_requests (
                    user_id TEXT PRIMARY KEY,
                    request_type TEXT NOT NULL,
                    details TEXT NOT NULL,
                    timestamp TEXT NOT NULL,
                    reminder_sent INTEGER DEFAULT 0
                )
                """
            )
            await conn.commit()
            log("Role requests DB initialized successfully.")
    except aiosqlite.Error as e:
        log(f"Role Requests DB Error: {e}", level="error")

async def add_role_request(user_id: str, request_type: str, details: str) -> bool:
    try:
        async with get_db_connection() as conn:
            cursor = await conn.cursor()
            ts = datetime.now().isoformat()
            await cursor.execute(
                """
                INSERT OR REPLACE INTO role_requests (user_id, request_type, details, timestamp)
                VALUES (?, ?, ?, ?)
                """,
                (user_id, request_type, details, ts)
            )
            await conn.commit()
            return True
    except aiosqlite.Error as e:
        log(f"DB Error (add_role_request): {e}", level="error")
        return False

async def remove_role_request(user_id: str) -> bool:
    try:
        async with get_db_connection() as conn:
            cursor = await conn.cursor()
            await cursor.execute("DELETE FROM role_requests WHERE user_id = ?", (user_id,))
            await conn.commit()
            return cursor.rowcount > 0
    except aiosqlite.Error as e:
        log(f"DB Error (remove_role_request): {e}", level="error")
        return False

async def get_role_request(user_id: str) -> Optional[Dict]:
    try:
        async with get_db_connection() as conn:
            cursor = await conn.cursor()
            await cursor.execute("SELECT user_id, request_type, details, timestamp FROM role_requests WHERE user_id = ?", (user_id,))
            row = await cursor.fetchone()
            if row:
                return {"user_id": row[0], "request_type": row[1], "details": row[2], "timestamp": row[3]}
            return None
    except aiosqlite.Error as e:
        log(f"DB Error (get_role_request): {e}", level="error")
        return None

async def clear_role_requests() -> None:
    try:
        async with get_db_connection() as conn:
            cursor = await conn.cursor()
            await cursor.execute("DELETE FROM role_requests")
            await conn.commit()
            log("All role requests have been cleared.")
    except aiosqlite.Error as e:
        log(f"Error clearing role requests: {e}", level="error")

async def get_role_requests() -> list:
    requests = []
    try:
        async with get_db_connection() as conn:
            cursor = await conn.cursor()
            await cursor.execute("SELECT user_id, request_type, details, timestamp FROM role_requests")
            rows = await cursor.fetchall()
            for row in rows:
                requests.append({
                    "user_id": row[0],
                    "request_type": row[1],
                    "details": row[2],
                    "timestamp": row[3]
                })
    except aiosqlite.Error as e:
        log(f"Error retrieving role requests: {e}", level="error")
    return requests

async def get_pending_role_requests_no_reminder() -> list:
    """Return role requests that have not yet been reminded (reminder_sent = 0)."""
    requests = []
    try:
        async with get_db_connection() as conn:
            cursor = await conn.cursor()
            await cursor.execute(
                "SELECT user_id, request_type, details, timestamp, reminder_sent FROM role_requests WHERE reminder_sent = 0"
            )
            rows = await cursor.fetchall()
            for row in rows:
                requests.append({
                    "user_id": row[0],
                    "request_type": row[1],
                    "details": row[2],
                    "timestamp": row[3],
                    "reminder_sent": row[4]
                })
    except aiosqlite.Error as e:
        log(f"Error retrieving pending role requests: {e}", level="error")
    return requests

async def mark_role_request_reminder_sent(user_id: str) -> bool:
    """Mark the role request for the given user as having had its reminder sent."""
    try:
        async with get_db_connection() as conn:
            cursor = await conn.cursor()
            await cursor.execute("UPDATE role_requests SET reminder_sent = 1 WHERE user_id = ?", (user_id,))
            await conn.commit()
            return cursor.rowcount > 0
    except aiosqlite.Error as e:
        log(f"Error marking reminder as sent for user_id {user_id}: {e}", level="error")
        return False

# -------------------------------
# Applications requests functions
# -------------------------------

async def init_application_requests_db():
    try:
        async with get_db_connection() as conn:
            cursor = await conn.cursor()
            await cursor.execute(
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
            await conn.commit()
            log("Application requests DB initialized successfully.")
    except aiosqlite.Error as e:
        log(f"Application Requests DB Error: {e}", level="error")

async def add_application_request(user_id: str, data: Dict) -> bool:
    try:
        async with get_db_connection() as conn:
            cursor = await conn.cursor()
            ts = datetime.now().isoformat()
            await cursor.execute(
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
            await conn.commit()
            return True
    except aiosqlite.Error as e:
        log(f"DB Error (add_application_request): {e}", level="error")
        return False

async def remove_application_request(user_id: str) -> bool:
    try:
        async with get_db_connection() as conn:
            cursor = await conn.cursor()
            await cursor.execute("DELETE FROM application_requests WHERE user_id = ?", (user_id,))
            await conn.commit()
            return cursor.rowcount > 0
    except aiosqlite.Error as e:
        log(f"DB Error (remove_application_request): {e}", level="error")
        return False

async def get_application_request(user_id: str) -> Optional[Dict]:
    try:
        async with get_db_connection() as conn:
            cursor = await conn.cursor()
            await cursor.execute("SELECT * FROM application_requests WHERE user_id = ?", (user_id,))
            row = await cursor.fetchone()
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
    except aiosqlite.Error as e:
        log(f"DB Error (get_application_request): {e}", level="error")
        return None

async def clear_pending_requests() -> None:
    try:
        async with get_db_connection() as conn:
            cursor = await conn.cursor()
            await cursor.execute("DELETE FROM application_requests")
            await conn.commit()
            log("All pending application requests have been cleared.")
    except aiosqlite.Error as e:
        log(f"Error clearing pending requests: {e}", level="error")

async def get_application_requests() -> list:
    requests = []
    try:
        async with get_db_connection() as conn:
            cursor = await conn.cursor()
            await cursor.execute("SELECT user_id, request_type, ingame_name, age, level, join_reason, previous_crews, region, timestamp FROM application_requests")
            rows = await cursor.fetchall()
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
    except aiosqlite.Error as e:
        log(f"Error retrieving application requests: {e}", level="error")
    return requests

# -------------------------------
# Applications database functions
# -------------------------------

async def init_applications_db():
    try:
        async with get_db_connection() as conn:
            cursor = await conn.cursor()
            await cursor.execute(
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
                    ban_history_reminder_count    INTEGER DEFAULT 0,
                    silenced                      INTEGER DEFAULT 0
                )
                """
            )
            await conn.commit()
            log("Applications DB (application_threads) initialized successfully async with new ban history columns.")
    except aiosqlite.Error as e:
        log(f"Applications DB Error: {e}", level="error")

async def add_application(
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
        async with get_db_connection() as conn:
            cursor = await conn.cursor()
            await cursor.execute(
                """
                INSERT INTO application_threads 
                (thread_id, applicant_id, recruiter_id, starttime, ingame_name, region, age, level, join_reason, previous_crews, is_closed, status)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, 'open')
                """,
                (thread_id, applicant_id, recruiter_id, start_str, ingame_name, region, age, level, join_reason, previous_crews)
            )
            await conn.commit()
            log(f"Added new application thread {thread_id} from user {applicant_id}")
            return True
    except aiosqlite.IntegrityError:
        log("Duplicate thread_id in 'application_threads' or integrity issue.", level="error")
        return False
    except aiosqlite.Error as e:
        log(f"DB Error (add_application): {e}", level="error")
        return False

async def get_application(thread_id: str) -> Optional[Dict]:
    try:
        async with get_db_connection() as conn:
            cursor = await conn.cursor()
            await cursor.execute(
                """
                SELECT applicant_id, recruiter_id, starttime, ingame_name, region, age, level, join_reason, previous_crews, is_closed, silenced
                FROM application_threads
                WHERE thread_id = ?
                """,
                (thread_id,)
            )
            row = await cursor.fetchone()
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
                "is_closed": row[9],
                "silenced": row[10]
            }
    except aiosqlite.Error as e:
        log(f"Database Error (get_application): {e}", level="error")
        return None

async def update_application_recruiter(thread_id: str, new_recruiter_id: str) -> bool:
    try:
        async with get_db_connection() as conn:
            cursor = await conn.cursor()
            await cursor.execute(
                """
                UPDATE application_threads
                SET recruiter_id = ?
                WHERE thread_id = ?
                """,
                (new_recruiter_id, thread_id)
            )
            await conn.commit()
            updated = (cursor.rowcount > 0)
            if updated:
                log(f"Application thread {thread_id} claimed by {new_recruiter_id}")
            return updated
    except aiosqlite.Error as e:
        log(f"DB Error (update_application_recruiter): {e}", level="error")
        return False

async def close_application(thread_id: str) -> bool:
    try:
        async with get_db_connection() as conn:
            cursor = await conn.cursor()
            await cursor.execute(
                """
                UPDATE application_threads
                SET is_closed = 1
                WHERE thread_id = ?
                """,
                (thread_id,)
            )
            await conn.commit()
            closed = (cursor.rowcount > 0)
            if closed:
                log(f"Application thread {thread_id} marked as closed.")
            return closed
    except aiosqlite.Error as e:
        log(f"DB Error (close_application): {e}", level="error")
        return False

async def remove_application(thread_id: str) -> bool:
    try:
        async with get_db_connection() as conn:
            cursor = await conn.cursor()
            await cursor.execute("DELETE FROM application_threads WHERE thread_id = ?", (thread_id,))
            await conn.commit()
            removed = (cursor.rowcount > 0)
            if removed:
                log(f"Removed application thread {thread_id} from DB.")
            return removed
    except aiosqlite.Error as e:
        log(f"DB Error (remove_application): {e}", level="error")
        return False

async def update_application_status(thread_id: str, new_status: str) -> bool:
    try:
        async with get_db_connection() as conn:
            cursor = await conn.cursor()
            await cursor.execute("UPDATE application_threads SET status = ? WHERE thread_id = ?", (new_status, thread_id))
            await conn.commit()
            updated = (cursor.rowcount > 0)
            if updated:
                log(f"Updated application {thread_id} status to {new_status}")
            return updated
    except aiosqlite.Error as e:
        log(f"DB Error (update_application_status): {e}", level="error")
        return False

async def mark_application_removed(thread_id: str) -> bool:
    try:
        async with get_db_connection() as conn:
            cursor = await conn.cursor()
            await cursor.execute("UPDATE application_threads SET status = 'removed', is_closed = 1 WHERE thread_id = ?", (thread_id,))
            await conn.commit()
            updated = (cursor.rowcount > 0)
            if updated:
                log(f"Marked application {thread_id} as removed")
            return updated
    except aiosqlite.Error as e:
        log(f"DB Error (mark_application_removed): {e}", level="error")
        return False

async def get_open_application(user_id: str) -> Optional[Dict]:
    try:
        async with get_db_connection() as conn:
            cursor = await conn.cursor()
            await cursor.execute(
                """
                SELECT thread_id, applicant_id, recruiter_id, starttime, ingame_name,
                       region, age, level, join_reason, previous_crews, is_closed, status
                FROM application_threads
                WHERE applicant_id = ? AND is_closed = 0 AND status = 'open'
                """,
                (user_id,)
            )
            row = await cursor.fetchone()
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
    except aiosqlite.Error as e:
        log(f"DB Error (get_open_application): {e}", level="error")
        return None

async def get_open_applications() -> list:
    applications = []
    try:
        async with get_db_connection() as conn:
            cursor = await conn.cursor()
            await cursor.execute(
                "SELECT thread_id, applicant_id, recruiter_id, ingame_name, region, ban_history_sent, starttime FROM application_threads WHERE is_closed = 0 AND status = 'open'"
            )
            rows = await cursor.fetchall()
            for row in rows:
                applications.append({
                    "thread_id": row[0],
                    "applicant_id": row[1],
                    "recruiter_id": row[2],
                    "ingame_name": row[3],
                    "region": row[4],
                    "ban_history_sent": int(row[5]),
                    "starttime": datetime.fromisoformat(row[6])
                })
    except aiosqlite.Error as e:
        log(f"DB Error (get_open_applications): {e}", level="error")
    return applications

def sort_applications(apps: list) -> list:
    def sort_key(app):
        if app["recruiter_id"]:
            return (2, 0)
        else:
            return (0, app["ban_history_sent"])
    return sorted(apps, key=sort_key)

async def set_application_silence(thread_id: str, silent: bool) -> bool:
    try:
        async with get_db_connection() as conn:
            await conn.execute(
                "UPDATE application_threads SET silenced = ? WHERE thread_id = ?",
                (1 if silent else 0, thread_id)
            )
            await conn.commit()
        return True
    except Exception as e:
        log(f"Error updating silenced status for thread {thread_id}: {e}", level="error")
        return False

async def is_application_silenced(thread_id: str) -> bool:
    try:
        async with get_db_connection() as conn:
            async with conn.execute(
                "SELECT silenced FROM application_threads WHERE thread_id = ?",
                (thread_id,)
            ) as cursor:
                row = await cursor.fetchone()
        return bool(row and row[0] == 1)
    except Exception as e:
        log(f"Error checking silenced status for thread {thread_id}: {e}", level="error")
        return False

# -------------------------------
# APPLICATION ATTEMPTS DATABASE FUNCTIONS
# -------------------------------

async def init_application_attempts_db():
    try:
        async with get_db_connection() as conn:
            cursor = await conn.cursor()
            await cursor.execute(
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
            await conn.commit()
            log("Application attempts DB initialized successfully.")
    except aiosqlite.Error as e:
        log(f"Application Attempts DB Error: {e}", level="error")

async def add_application_attempt(applicant_id: str, region: str, status: str, log_url: str) -> bool:
    try:
        async with get_db_connection() as conn:
            cursor = await conn.cursor()
            timestamp = datetime.now().isoformat()
            await cursor.execute(
                "INSERT INTO application_attempts (applicant_id, region, timestamp, status, log_url) VALUES (?, ?, ?, ?, ?)",
                (str(applicant_id), region, timestamp, status, log_url)
            )
            await conn.commit()
            return True
    except aiosqlite.Error as e:
        log(f"DB Error (add_application_attempt): {e}", level="error")
        return False

async def get_recent_closed_attempts(applicant_id: str) -> list:
    try:
        async with get_db_connection() as conn:
            cursor = await conn.cursor()
            seven_days_ago = (datetime.now() - timedelta(days=7)).isoformat()
            await cursor.execute(
                "SELECT timestamp, log_url FROM application_attempts WHERE applicant_id = ? AND status = 'closed_region_attempt' AND timestamp >= ?",
                (str(applicant_id), seven_days_ago)
            )
            rows = await cursor.fetchall()
            return [{"timestamp": row[0], "log_url": row[1]} for row in rows]
    except aiosqlite.Error as e:
        log(f"DB Error (get_recent_closed_attempts): {e}", level="error")
        return []

async def get_application_stats(days: int = 0) -> dict:
    stats = {"accepted": 0, "denied": 0, "withdrawn": 0, "open": 0}
    try:
        async with get_db_connection() as conn:
            cursor = await conn.cursor()
            cutoff = None
            if days and days > 0:
                cutoff = (datetime.now() - timedelta(days=days)).isoformat()
            for status in stats.keys():
                if cutoff:
                    await cursor.execute(
                        "SELECT COUNT(*) FROM application_threads WHERE status = ? AND starttime >= ?",
                        (status, cutoff)
                    )
                else:
                    await cursor.execute(
                        "SELECT COUNT(*) FROM application_threads WHERE status = ?",
                        (status,)
                    )
                stats[status] = await cursor.fetchone()[0]
    except aiosqlite.Error as e:
        log(f"DB Error (get_application_stats): {e}", level="error")
    return stats

async def get_application_history(applicant_id: str) -> list:
    history = []
    try:
        async with get_db_connection() as conn:
            cursor = await conn.cursor()
            await cursor.execute(
                "SELECT starttime, status, ingame_name, region FROM application_threads WHERE applicant_id = ?",
                (applicant_id,)
            )
            for row in await cursor.fetchall():
                history.append({
                    "timestamp": row[0],
                    "status": row[1],
                    "type": "submission",
                    "details": f"IGN: {row[2]}, Region: {row[3]}"
                })
            await cursor.execute(
                "SELECT timestamp, status, region, log_url FROM application_attempts WHERE applicant_id = ?",
                (applicant_id,)
            )
            for row in await cursor.fetchall():
                details = f"Region: {row[2]}"
                if row[3]:
                    details += f", [Log Entry]({row[3]})"
                history.append({
                    "timestamp": row[0],
                    "status": row[1],
                    "type": "attempt",
                    "details": details
                })
    except aiosqlite.Error as e:
        log(f"DB Error (get_application_history): {e}", level="error")
    return sorted(history, key=lambda x: x["timestamp"], reverse=True)

# -------------------------------
# APPLICATION STATUS
# -------------------------------

async def init_region_status():
    async with get_db_connection() as conn:
        cursor = await conn.cursor()
        await cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS region_status (
                region TEXT PRIMARY KEY,
                status TEXT NOT NULL
            )
            """
        )
        for region in ['EU', 'NA', 'SEA']:
            await cursor.execute(
                "INSERT OR IGNORE INTO region_status (region, status) VALUES (?, ?)",
                (region, "OPEN")
            )
        await conn.commit()

async def get_region_status(region: str) -> Optional[str]:
    try:
        async with get_db_connection() as conn:
            cursor = await conn.cursor()
            await cursor.execute("SELECT status FROM region_status WHERE region = ?", (region.upper(),))
            row = await cursor.fetchone()
            if row:
                return row[0]
            return None
    except aiosqlite.Error as e:
        log(f"Error getting region status: {e}", level="error")
        return None

async def update_region_status(region: str, status: str) -> bool:
    try:
        async with get_db_connection() as conn:
            cursor = await conn.cursor()
            await cursor.execute("UPDATE region_status SET status = ? WHERE region = ?", (status.upper(), region.upper()))
            await conn.commit()
            return cursor.rowcount > 0
    except aiosqlite.Error as e:
        log(f"Error updating region status: {e}", level="error")
        return False

# -------------------------------
# Timeouts/Blacklists Database Functions
# -------------------------------

async def init_timeouts_db():
    try:
        async with get_db_connection() as conn:
            cursor = await conn.cursor()
            await cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS timeouts (
                    user_id TEXT PRIMARY KEY,
                    type TEXT NOT NULL,
                    expires_at TEXT
                )
                """
            )
            await conn.commit()
            log("Timeouts/Blacklists DB initialized successfully.")
    except aiosqlite.Error as e:
        log(f"Timeouts DB Error: {e}", level="error")

async def add_timeout_record(user_id: str, record_type: str, expires_at: Optional[datetime] = None) -> bool:
    try:
        async with get_db_connection() as conn:
            cursor = await conn.cursor()
            await cursor.execute(
                "INSERT OR REPLACE INTO timeouts (user_id, type, expires_at) VALUES (?, ?, ?)",
                (user_id, record_type, expires_at.isoformat() if expires_at else None)
            )
            await conn.commit()
            return True
    except aiosqlite.Error as e:
        log(f"DB Error (add_timeout_record): {e}", level="error")
        return False

async def remove_timeout_record(user_id: str) -> bool:
    try:
        async with get_db_connection() as conn:
            cursor = await conn.cursor()
            await cursor.execute("DELETE FROM timeouts WHERE user_id = ?", (user_id,))
            await conn.commit()
            return cursor.rowcount > 0
    except aiosqlite.Error as e:
        log(f"DB Error (remove_timeout_record): {e}", level="error")
        return False

async def get_timeout_record(user_id: str) -> Optional[Dict]:
    try:
        async with get_db_connection() as conn:
            cursor = await conn.cursor()
            await cursor.execute("SELECT user_id, type, expires_at FROM timeouts WHERE user_id = ?", (user_id,))
            row = await cursor.fetchone()
            if row:
                return {
                    "user_id": row[0],
                    "type": row[1],
                    "expires_at": datetime.fromisoformat(row[2]) if row[2] else None
                }
            return None
    except aiosqlite.Error as e:
        log(f"DB Error (get_timeout_record): {e}", level="error")
        return None

async def get_all_timeouts() -> list:
    try:
        async with get_db_connection() as conn:
            cursor = await conn.cursor()
            await cursor.execute("SELECT user_id, type, expires_at FROM timeouts")
            rows = await cursor.fetchall()
            result = []
            for row in rows:
                result.append({
                    "user_id": row[0],
                    "type": row[1],
                    "expires_at": datetime.fromisoformat(row[2]) if row[2] else None
                })
            return result
    except aiosqlite.Error as e:
        log(f"DB Error (get_all_timeouts): {e}", level="error")
        return []


# -------------------------------
# Tickets & LOA Reminder DB
# -------------------------------


async def init_ticket_db():
    """Create or migrate the tickets table, ensuring a ticket_done column exists."""
    try:
        async with get_db_connection() as conn:
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS tickets (
                    thread_id    TEXT PRIMARY KEY,
                    user_id      TEXT NOT NULL,
                    created_at   TEXT NOT NULL,
                    ticket_type  TEXT NOT NULL
                )
            """)
            await conn.commit()

            # add ticket_done column if it doesn't exist
            cursor = await conn.execute("PRAGMA table_info(tickets)")
            cols = [row[1] for row in await cursor.fetchall()]
            if "ticket_done" not in cols:
                await conn.execute("ALTER TABLE tickets ADD COLUMN ticket_done TEXT")
                await conn.commit()

        log("Ticket DB ready (migrated with ticket_done column).")
    except aiosqlite.Error as e:
        log(f"Error migrating tickets table: {e}", level="error")

async def init_loa_db():
    """Create the LOA reminders table."""
    try:
        async with get_db_connection() as conn:
            await conn.execute(
                """
                CREATE TABLE IF NOT EXISTS loa_reminders (
                    thread_id     TEXT PRIMARY KEY,
                    user_id       TEXT NOT NULL,
                    end_date      TEXT NOT NULL,
                    reminder_sent INTEGER NOT NULL DEFAULT 0
                )
                """
            )
            await conn.commit()
            log("LOA reminder database initialized successfully.")
    except aiosqlite.Error as e:
        log(f"Database init error for LOA reminders: {e}", level="error")

async def add_ticket(thread_id: str, user_id: str, created_at: str, ticket_type: str) -> None:
    try:
        async with get_db_connection() as conn:
            await conn.execute(
                """
                INSERT OR IGNORE INTO tickets (thread_id, user_id, created_at, ticket_type)
                VALUES (?, ?, ?, ?)
                """,
                (thread_id, user_id, created_at, ticket_type)
            )
            await conn.commit()
        log(f"Added ticket: thread_id={thread_id}, user_id={user_id}, type={ticket_type}")
    except aiosqlite.Error as e:
        log(f"Error adding ticket (thread_id={thread_id}): {e}", level="error")

async def get_ticket_info(thread_id: str) -> Optional[tuple]:
    try:
        async with get_db_connection() as conn:
            cursor = await conn.execute(
                """
                SELECT thread_id, user_id, created_at, ticket_type
                FROM tickets WHERE thread_id = ?
                """,
                (thread_id,)
            )
            return await cursor.fetchone()
    except aiosqlite.Error as e:
        log(f"Error reading ticket_info for {thread_id}: {e}", level="error")
        return None

async def remove_ticket(thread_id: str) -> None:
    try:
        async with get_db_connection() as conn:
            await conn.execute(
                "DELETE FROM tickets WHERE thread_id = ?",
                (thread_id,)
            )
            await conn.commit()
        log(f"Removed ticket from DB: thread_id={thread_id}")
    except aiosqlite.Error as e:
        log(f"Error removing ticket {thread_id} from DB: {e}", level="error")

async def get_all_tickets() -> List[Dict]:
    """Return all tickets as a list of dicts."""
    tickets = []
    try:
        async with get_db_connection() as conn:
            cursor = await conn.execute(
                "SELECT thread_id, user_id, created_at, ticket_type FROM tickets"
            )
            rows = await cursor.fetchall()
        for thread_id, user_id, created_at, ticket_type in rows:
            tickets.append({
                "thread_id": thread_id,
                "user_id": user_id,
                "created_at": created_at,
                "ticket_type": ticket_type
            })
    except aiosqlite.Error as e:
        log(f"Error fetching all tickets: {e}", level="error")
    return tickets

async def add_loa_reminder(thread_id: str, user_id: str, end_date_iso: str) -> None:
    try:
        async with get_db_connection() as conn:
            await conn.execute(
                """
                INSERT OR REPLACE INTO loa_reminders (thread_id, user_id, end_date, reminder_sent)
                VALUES (?, ?, ?, 0)
                """,
                (thread_id, user_id, end_date_iso)
            )
            await conn.commit()
        log(f"LOA reminder added: thread_id={thread_id}, end_date={end_date_iso}")
    except aiosqlite.Error as e:
        log(f"Error adding LOA reminder {thread_id}: {e}", level="error")

async def get_loa_reminder(thread_id: str) -> Optional[tuple]:
    try:
        async with get_db_connection() as conn:
            cursor = await conn.execute(
                """
                SELECT thread_id, user_id, end_date, reminder_sent
                FROM loa_reminders WHERE thread_id = ?
                """,
                (thread_id,)
            )
            return await cursor.fetchone()
    except aiosqlite.Error as e:
        log(f"Error reading LOA reminder for {thread_id}: {e}", level="error")
        return None

async def remove_loa_reminder(thread_id: str) -> None:
    try:
        async with get_db_connection() as conn:
            await conn.execute(
                "DELETE FROM loa_reminders WHERE thread_id = ?",
                (thread_id,)
            )
            await conn.commit()
        log(f"LOA reminder removed: thread_id={thread_id}")
    except aiosqlite.Error as e:
        log(f"Error removing LOA reminder {thread_id}: {e}", level="error")

async def update_loa_end_date(thread_id: str, new_end_date_iso: str) -> None:
    try:
        async with get_db_connection() as conn:
            await conn.execute(
                """
                UPDATE loa_reminders
                SET end_date = ?, reminder_sent = 0
                WHERE thread_id = ?
                """,
                (new_end_date_iso, thread_id)
            )
            await conn.commit()
        log(f"LOA reminder extended: thread_id={thread_id}, new_end_date={new_end_date_iso}")
    except aiosqlite.Error as e:
        log(f"Error updating LOA reminder {thread_id}: {e}", level="error")

async def mark_reminder_sent(thread_id: str) -> None:
    try:
        async with get_db_connection() as conn:
            await conn.execute(
                "UPDATE loa_reminders SET reminder_sent = 1 WHERE thread_id = ?",
                (thread_id,)
            )
            await conn.commit()
        log(f"LOA reminder marked sent: thread_id={thread_id}")
    except aiosqlite.Error as e:
        log(f"Error marking reminder sent for {thread_id}: {e}", level="error")

async def get_expired_loa() -> List[tuple]:
    today_iso = datetime.utcnow().date().isoformat()
    try:
        async with get_db_connection() as conn:
            cursor = await conn.execute(
                """
                SELECT thread_id, user_id
                FROM loa_reminders
                WHERE end_date < ? AND reminder_sent = 0
                """,
                (today_iso,)
            )
            return await cursor.fetchall()
    except aiosqlite.Error as e:
        log(f"Error fetching expired LOA: {e}", level="error")
        return []

async def has_active_loa_for_user(user_id: str) -> bool:
    async with get_db_connection() as conn:
        cursor = await conn.execute(
            "SELECT 1 FROM loa_reminders WHERE user_id = ? AND reminder_sent = 0 LIMIT 1",
            (user_id,)
        )
        return await cursor.fetchone() is not None

async def get_active_loa_reminders() -> List[Dict]:
    reminders = []
    async with get_db_connection() as conn:
        cursor = await conn.execute(
            "SELECT thread_id, user_id, end_date FROM loa_reminders WHERE reminder_sent = 0"
        )
        rows = await cursor.fetchall()
    for thread_id, user_id, end_date in rows:
        reminders.append({
            "thread_id": thread_id,
            "user_id": user_id,
            "end_date": end_date
        })
    return reminders

# -------------------------------
# Ticket-Done Scheduling Table
# -------------------------------
async def update_ticket_done(thread_id: str, done_at_iso: str):
    """Mark a ticket as done at the given UTC ISO timestamp."""
    try:
        async with get_db_connection() as conn:
            await conn.execute(
                "UPDATE tickets SET ticket_done = ? WHERE thread_id = ?",
                (done_at_iso, thread_id)
            )
            await conn.commit()
        log(f"Ticket {thread_id} marked done at {done_at_iso}")
    except aiosqlite.Error as e:
        log(f"Error updating ticket_done: {e}", level="error")


async def get_tickets_to_lock() -> list:
    """
    Return all thread_ids whose ticket_done ≤ (now – 24h).
    """
    cutoff = datetime.utcnow() - timedelta(minutes=1) ## CHANGE IN PRODUCTIOn
    try:
        async with get_db_connection() as conn:
            cursor = await conn.execute(
                "SELECT thread_id, ticket_done FROM tickets WHERE ticket_done IS NOT NULL"
            )
            rows = await cursor.fetchall()

        to_lock = []
        for thread_id, done_iso in rows:
            try:
                if datetime.fromisoformat(done_iso) <= cutoff:
                    to_lock.append(thread_id)
            except ValueError:
                continue
        return to_lock

    except aiosqlite.Error as e:
        log(f"Error fetching tickets to lock: {e}", level="error")
        return []


async def get_ticket_done(thread_id: str) -> str | None:
    """Fetch the ISO timestamp when this ticket was marked done (or None)."""
    try:
        async with get_db_connection() as conn:
            cursor = await conn.execute(
                "SELECT ticket_done FROM tickets WHERE thread_id = ?",
                (thread_id,)
            )
            row = await cursor.fetchone()
        return row[0] if row and row[0] else None

    except aiosqlite.Error as e:
        log(f"Error querying ticket_done: {e}", level="error")
        return None


async def clear_ticket_done(thread_id: str):
    """Unset the ticket_done flag (cancel auto-lock)."""
    try:
        async with get_db_connection() as conn:
            await conn.execute(
                "UPDATE tickets SET ticket_done = NULL WHERE thread_id = ?",
                (thread_id,)
            )
            await conn.commit()
        log(f"Cleared ticket_done for {thread_id}")
    except aiosqlite.Error as e:
        log(f"Error clearing ticket_done: {e}", level="error")