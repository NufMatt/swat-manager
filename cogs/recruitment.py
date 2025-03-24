# cogs/recruitment.py

import discord
from discord import app_commands, ButtonStyle, Interaction
from discord.ext import commands, tasks
import asyncio, os, json, sqlite3, re, traceback, random
from datetime import datetime, timedelta
from typing import Optional, Dict

# Adjust the sys.path so that config_testing.py (in the root) is found.
import sys
import os
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from config_testing import (
    GUILD_ID, TRAINEE_NOTES_CHANNEL, CADET_NOTES_CHANNEL, TRAINEE_CHAT_CHANNEL,
    SWAT_CHAT_CHANNEL, TRAINEE_ROLE, CADET_ROLE, SWAT_ROLE_ID, OFFICER_ROLE_ID,
    RECRUITER_ID, LEADERSHIP_ID, EU_ROLE_ID, NA_ROLE_ID, SEA_ROLE_ID,
    TARGET_CHANNEL_ID, REQUESTS_CHANNEL_ID, TICKET_CHANNEL_ID, TOKEN_FILE,
    PLUS_ONE_EMOJI, MINUS_ONE_EMOJI, LEAD_BOT_DEVELOPER_ID, LEAD_BOT_DEVELOPER_EMOJI,
    INTEGRATIONS_MANAGER, RECRUITER_EMOJI, LEADERSHIP_EMOJI, APPLICATION_EMBED_ID_FILE, APPLY_CHANNEL_ID, ACTIVITY_CHANNEL_ID
)
from messages import trainee_messages, cadet_messages, welcome_to_swat, OPEN_TICKET_EMBED_TEXT
from cogs.helpers import is_in_correct_guild, log, create_user_activity_log_embed  # if you have additional helpers

# -------------------------------
# Database functions for recruitment
# -------------------------------
DATABASE_FILE = "data.db"
EMBED_ID_FILE = "embed.txt"
REQUESTS_FILE = "requests.json"

def initialize_database():
    try:
        conn = sqlite3.connect(DATABASE_FILE)
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
    finally:
        conn.close()

initialize_database()

def add_entry(thread_id: str, recruiter_id: str, starttime: datetime, endtime: datetime, 
              role_type: str, embed_id: str, ingame_name: str, user_id: str, region: str) -> bool:
    if role_type not in ("trainee", "cadet"):
        raise ValueError("role_type must be either 'trainee' or 'cadet'.")
    start_str = starttime.isoformat()
    end_str = endtime.isoformat() if endtime else None
    conn = None
    try:
        conn = sqlite3.connect(DATABASE_FILE)
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
    finally:
        if conn:
            conn.close()

def remove_entry(thread_id: str) -> bool:
    conn = None
    try:
        conn = sqlite3.connect(DATABASE_FILE)
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
    finally:
        if conn:
            conn.close()

def update_endtime(thread_id: str, new_endtime: datetime) -> bool:
    conn = None
    try:
        conn = sqlite3.connect(DATABASE_FILE)
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
    finally:
        if conn:
            conn.close()

def get_entry(thread_id: str) -> Optional[Dict]:
    conn = None
    try:
        conn = sqlite3.connect(DATABASE_FILE)
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
    finally:
        if conn:
            conn.close()

def is_user_in_database(user_id: int) -> bool:
    conn = None
    try:
        conn = sqlite3.connect(DATABASE_FILE)
        cursor = conn.cursor()
        cursor.execute(
            """SELECT 1 FROM entries WHERE user_id = ? LIMIT 1""",
            (str(user_id),)
        )
        result = cursor.fetchone()
        return result is not None
    except sqlite3.Error as e:
        log(f"Database Error (is_user_in_database): {e}", level="error")
        return False
    finally:
        if conn:
            conn.close()

#
# APPLICATIONS DATABASE FUNCTIONS
# 
def init_applications_db():
    try:
        conn = sqlite3.connect(DATABASE_FILE)
        cursor = conn.cursor()
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS application_threads (
                thread_id     TEXT PRIMARY KEY,
                applicant_id  TEXT NOT NULL,
                recruiter_id  TEXT,
                starttime     TEXT NOT NULL,
                ingame_name   TEXT NOT NULL,
                region        TEXT NOT NULL,
                age           TEXT NOT NULL,
                level         TEXT NOT NULL,
                ban_history   TEXT NOT NULL,
                is_closed     INTEGER DEFAULT 0,
                status        TEXT NOT NULL DEFAULT 'open'
            )
            """
        )
        conn.commit()
        log("Applications DB (application_threads) initialized successfully.")
    except sqlite3.Error as e:
        log(f"Applications DB Error: {e}", level="error")
    finally:
        if conn:
            conn.close()

            
init_applications_db()


def add_application(
    thread_id: str,
    applicant_id: str,
    recruiter_id: Optional[str],
    starttime: datetime,
    ingame_name: str,
    region: str,
    age: str,
    level: str,
    ban_history: str
) -> bool:
    conn = None
    try:
        conn = sqlite3.connect(DATABASE_FILE)
        cursor = conn.cursor()
        cursor.execute(
            """
            INSERT INTO application_threads 
            (thread_id, applicant_id, recruiter_id, starttime, ingame_name, region, age, level, ban_history, is_closed, status)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 0, 'open')
            """,
            (
                thread_id,
                applicant_id,
                recruiter_id,
                starttime.isoformat(),
                ingame_name,
                region,
                age,
                level,
                ban_history
            )
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
    finally:
        if conn:
            conn.close()


def get_application(thread_id: str) -> Optional[Dict]:
    """
    Fetches a row from application_threads for the new application system.
    """
    conn = None
    try:
        conn = sqlite3.connect(DATABASE_FILE)
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT applicant_id, recruiter_id, starttime, ingame_name, region, age, level, ban_history, is_closed
            FROM application_threads
            WHERE thread_id = ?
            """,
            (thread_id,)
        )
        row = cursor.fetchone()
        if not row:
            return None
        return {
            "thread_id":    thread_id,
            "applicant_id": row[0],
            "recruiter_id": row[1],
            "starttime":    datetime.fromisoformat(row[2]),
            "ingame_name":  row[3],
            "region":       row[4],
            "age":          row[5],
            "level":        row[6],
            "ban_history":  row[7],
            "is_closed":    row[8]
        }
    except sqlite3.Error as e:
        log(f"DB Error (get_application): {e}", level="error")
        return None
    finally:
        if conn:
            conn.close()

def update_application_recruiter(thread_id: str, new_recruiter_id: str) -> bool:
    """
    Updates the recruiter_id for the given thread in application_threads.
    """
    conn = None
    try:
        conn = sqlite3.connect(DATABASE_FILE)
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
    finally:
        if conn:
            conn.close()

def close_application(thread_id: str) -> bool:
    """
    Marks is_closed=1 for the given thread in application_threads.
    """
    conn = None
    try:
        conn = sqlite3.connect(DATABASE_FILE)
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
    finally:
        if conn:
            conn.close()

def remove_application(thread_id: str) -> bool:
    """
    Completely deletes the row from application_threads.
    """
    conn = None
    try:
        conn = sqlite3.connect(DATABASE_FILE)
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
    finally:
        if conn:
            conn.close()

def update_application_status(thread_id: str, new_status: str) -> bool:
    conn = None
    try:
        conn = sqlite3.connect(DATABASE_FILE)
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
    finally:
        if conn:
            conn.close()

def mark_application_removed(thread_id: str) -> bool:
    conn = None
    try:
        conn = sqlite3.connect(DATABASE_FILE)
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
    finally:
        if conn:
            conn.close()

# -------------------------------
# APPLICATION ATTEMPTS DATABASE FUNCTIONS
# -------------------------------

def init_application_attempts_db():
    try:
        conn = sqlite3.connect(DATABASE_FILE)
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
    finally:
        if conn:
            conn.close()

init_application_attempts_db()

def add_application_attempt(applicant_id: str, region: str, status: str, log_url: str) -> bool:
    try:
        conn = sqlite3.connect(DATABASE_FILE)
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
    finally:
        conn.close()

def get_recent_closed_attempts(applicant_id: str) -> list:
    try:
        conn = sqlite3.connect(DATABASE_FILE)
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
    finally:
        conn.close()

def get_application_stats() -> dict:
    stats = {"accepted": 0, "denied": 0, "withdrawn": 0, "open": 0}
    try:
        conn = sqlite3.connect(DATABASE_FILE)
        cursor = conn.cursor()
        for status in stats.keys():
            cursor.execute("SELECT COUNT(*) FROM application_threads WHERE status = ?", (status,))
            stats[status] = cursor.fetchone()[0]
    except sqlite3.Error as e:
        log(f"DB Error (get_application_stats): {e}", level="error")
    finally:
        conn.close()
    return stats

def get_application_history(applicant_id: str) -> list:
    history = []
    try:
        conn = sqlite3.connect(DATABASE_FILE)
        cursor = conn.cursor()
        # Get application submissions.
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
        # Get closed-region attempts.
        cursor.execute(
            "SELECT timestamp, status, region, log_url FROM application_attempts WHERE applicant_id = ?",
            (applicant_id,)
        )
        for row in cursor.fetchall():
            history.append({
                "timestamp": row[0],
                "status": row[1],
                "type": "attempt",
                "details": f"Region: {row[2]}, [Log Entry]({row[3]})" if row[3] else f"Region: {row[2]}"
            })
    except sqlite3.Error as e:
        log(f"DB Error (get_application_history): {e}", level="error")
    finally:
        conn.close()
    # Sort history by timestamp (most recent first)
    history.sort(key=lambda x: x["timestamp"], reverse=True)
    return history

# -------------------------------
# APPLICATION STATUS
# -------------------------------
def init_region_status():
    conn = sqlite3.connect(DATABASE_FILE)
    cursor = conn.cursor()
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS region_status (
            region TEXT PRIMARY KEY,
            status TEXT NOT NULL
        )
        """
    )
    # Insert default values if not present.
    for region in ['EU', 'NA', 'SEA']:
        cursor.execute(
            "INSERT OR IGNORE INTO region_status (region, status) VALUES (?, ?)",
            (region, "OPEN")
        )
    conn.commit()
    conn.close()

init_region_status()
def get_region_status(region: str) -> Optional[str]:
    conn = sqlite3.connect(DATABASE_FILE)
    cursor = conn.cursor()
    try:
        cursor.execute("SELECT status FROM region_status WHERE region = ?", (region.upper(),))
        row = cursor.fetchone()
        if row:
            return row[0]
        return None
    except sqlite3.Error as e:
        log(f"Error getting region status: {e}", level="error")
        return None
    finally:
        conn.close()

def update_region_status(region: str, status: str) -> bool:
    conn = sqlite3.connect(DATABASE_FILE)
    cursor = conn.cursor()
    try:
        cursor.execute("UPDATE region_status SET status = ? WHERE region = ?", (status.upper(), region.upper()))
        conn.commit()
        return cursor.rowcount > 0
    except sqlite3.Error as e:
        log(f"Error updating region status: {e}", level="error")
        return False
    finally:
        conn.close()


# -------------------------------
# ROLE Requests managment
# -------------------------------
pending_requests = {}  # key: str(user_id), value: dict with request info

def load_requests():
    global pending_requests
    if os.path.exists(REQUESTS_FILE):
        try:
            with open(REQUESTS_FILE, "r") as f:
                pending_requests = json.load(f)
            log(f"Requests loaded from {REQUESTS_FILE}, total: {len(pending_requests)}")
        except (json.JSONDecodeError, IOError) as e:
            log(f"Error loading requests.json: {e}", level="error")
            pending_requests = {}
    else:
        pending_requests = {}
        log("No requests file found; starting with empty pending_requests.")

def save_requests():
    try:
        with open(REQUESTS_FILE, "w") as f:
            json.dump(pending_requests, f)
        log(f"Requests saved to {REQUESTS_FILE}, total: {len(pending_requests)}")
    except IOError as e:
        log(f"Error saving requests.json: {e}", level="error")

# -------------------------------
# Trainee Application Requests Management
# -------------------------------
APPLICATIONS_FILE = "applications_requests.json"
pending_applications = {}  # key: str(user_id), value: dict with trainee-application info

def load_applications():
    global pending_applications
    if os.path.exists(APPLICATIONS_FILE):
        try:
            with open(APPLICATIONS_FILE, "r") as f:
                pending_applications = json.load(f)
            log(f"Applications loaded from {APPLICATIONS_FILE}, total: {len(pending_applications)}")
        except (json.JSONDecodeError, IOError) as e:
            log(f"Error loading {APPLICATIONS_FILE}: {e}", level="error")
            pending_applications = {}
    else:
        pending_applications = {}
        log(f"No {APPLICATIONS_FILE} file found; starting with empty pending_applications.")

def save_applications():
    try:
        with open(APPLICATIONS_FILE, "w") as f:
            json.dump(pending_applications, f)
        log(f"Applications saved to {APPLICATIONS_FILE}, total: {len(pending_applications)}")
    except IOError as e:
        log(f"Error saving {APPLICATIONS_FILE}: {e}", level="error")


# -------------------------------
# Helper functions
# -------------------------------


def get_rounded_time() -> datetime:
    now = datetime.now()
    minutes_to_add = (15 - now.minute % 15) % 15
    return now + timedelta(minutes=minutes_to_add)

def create_discord_timestamp(dt_obj: datetime) -> str:
    unix_timestamp = int(dt_obj.timestamp())
    return f"<t:{unix_timestamp}>"

def create_embed() -> discord.Embed:
    embed = discord.Embed(
        title="**Welcome to the SWAT Community!** 🎉🚔",
        description=(
            "📌 **Select the appropriate button below:**\n\n"
            "🔹 **Request Trainee Role** – If you applied through the website and got accepted **and received a DM from a recruiter**, press this button! "
            "Fill in your **EXACT** in-game name, select the region you play in, and choose the recruiter who accepted you. "
            "If everything checks out, you’ll receive a message in the trainee chat!\n\n"
            "🔹 **Request Name Change** – Need to update your name? Press this button and enter your new name **without any SWAT tags!** "
            "🚨 **Make sure your IGN and Discord name match at all times!** If they don’t, request a name change here!\n\n"
            "🔹 **Request Other** – Want another role? Click here and type your request! We’ll handle the rest.\n\n"
            "⚠️ **Important:** Follow the instructions carefully to avoid delays. Let’s get you set up and ready to roll! 🚀"
        ),
        colour=0x008040
    )
    return embed

def format_status(status: str) -> str:
    status = status.upper()
    if status == "OPEN":
        return "✅ Open"
    elif status == "CLOSED":
        return "❌ Closed"
    else:
        return "UNKNOWN"

def create_application_embed() -> discord.Embed:
    eu_status = format_status(get_region_status("EU") or "UNKNOWN")
    na_status = format_status(get_region_status("NA") or "UNKNOWN")
    sea_status = format_status(get_region_status("SEA") or "UNKNOWN")
    
    embed = discord.Embed(
        title="🚨 S.W.A.T. Recruitment - Application Requirements 🚨",
        description=(
            "Are you interested in joining **S.W.A.T.**? Make sure you meet the following requirements before applying:\n\n"
            "🔸 **Level 20+**\n"
            "🟢 **16+ years old**\n"
            "🎤 **Working mic & speak English**\n"
            "🎯 **Enjoy activities and events**\n\n"
            "If you wish to submit a **Trainee Application**, please note that **you must first provide your ban history**. "
            "A recruiter will review your application shortly after submission. 🚔\n\n"
        ),
        color=discord.Color.blue()
    )

    embed.add_field(name="🇪🇺 **EU**", value=f"```{eu_status}```", inline=True)
    embed.add_field(name="🇺🇸 **NA**", value=f"```{na_status}```", inline=True)
    embed.add_field(name="🌏 **SEA**", value=f"```{sea_status}```", inline=True)
    embed.set_footer(text="S.W.A.T. Application Manager")
    return embed

def is_in_correct_guild(interaction: discord.Interaction) -> bool:
    return interaction.guild_id == GUILD_ID

async def set_user_nickname(member: discord.Member, role_label: str, username: str = None):
    try:
        base_nick = username if username else (member.nick or member.name)
        temp_name = re.sub(r'(?:\s*\[(?:CADET|TRAINEE|SWAT)\])+$', '', base_nick, flags=re.IGNORECASE)
        await member.edit(nick=f"{temp_name} [{role_label.upper()}]")
        log(f"Set nickname for user {member.id} to '{temp_name} [{role_label.upper()}]'")
    except discord.Forbidden:
        log(f"Forbidden: Cannot change nickname for {member.id}", level="error")
    except discord.HTTPException as e:
        log(f"HTTPException changing nickname for {member.id}: {e}", level="error")

async def close_thread(interaction: discord.Interaction, thread: discord.Thread) -> None:
    try:
        result = remove_entry(thread.id)
        if result:
            try:
                await thread.edit(locked=True, archived=True)
                log(f"Closed and archived thread {thread.id}")
            except discord.Forbidden:
                await interaction.followup.send("❌ Bot lacks permission to lock/archive this thread.", ephemeral=True)
            except discord.HTTPException as e:
                await interaction.followup.send(f"❌ Error archiving thread: {e}", ephemeral=True)
        else:
            await interaction.followup.send("❌ Not a registered voting thread!", ephemeral=True)
    except Exception as e:
        await interaction.followup.send(f"❌ Error closing thread: {e}", ephemeral=True)

async def create_voting_embed(start_time, end_time, recruiter: int, region, ingame_name, extended: bool = False) -> discord.Embed:
    try:
        if not isinstance(start_time, datetime):
            start_time = datetime.fromisoformat(str(start_time))
        if not isinstance(end_time, datetime):
            end_time = datetime.fromisoformat(str(end_time))
        embed = discord.Embed(
            description=(
                "SWAT, please express your vote below.\n"
                f"Use {PLUS_ONE_EMOJI}, ❔, or {MINUS_ONE_EMOJI} accordingly."
            ),
            color=0x000000
        )
        flags = {"EU": "🇪🇺 ", "NA": "🇺🇸 ", "SEA": "🇸🇬 "}
        region_name = region[:-1] if region and region[-1].isdigit() else region
        title = f"{flags.get(region_name, '')}{region}"
        embed.add_field(name="InGame Name:", value=ingame_name, inline=True)
        embed.add_field(name="Region:", value=title, inline=True)
        embed.add_field(name="", value="", inline=False)
        embed.add_field(name="Voting started:", value=create_discord_timestamp(start_time), inline=True)
        end_title = "Voting will end: (Extended)" if extended else "Voting will end:"
        embed.add_field(name=end_title, value=create_discord_timestamp(end_time), inline=True)
        embed.add_field(name="Thread managed by:", value=f"<@{recruiter}>", inline=False)
        return embed
    except Exception as e:
        log(f"Error in create_voting_embed: {e}", level="error")
        return discord.Embed(description="❌ Error creating voting embed.", color=0xff0000)

# -------------------------------
# Persistent Views and Modals
# -------------------------------
class ApplicationControlView(discord.ui.View):
    """
    Buttons: 'Claim' (recruiter can claim) and 'Withdraw' (applicant can withdraw).
    Now uses the DB instead of storing applicant_id in memory.
    """
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(
        label="Claim", 
        style=discord.ButtonStyle.primary, 
        custom_id="app_claim"  # a fixed custom_id that never changes
    )
    async def claim_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        # Look up application data in DB using the thread ID:
        app_data = get_application(str(interaction.channel.id))
        if not app_data:
            await interaction.response.send_message("❌ No application data found for this thread!", ephemeral=True)
            return

        # Check if the user has the Recruiter role:
        recruiter_role = interaction.guild.get_role(RECRUITER_ID)
        if not recruiter_role or recruiter_role not in interaction.user.roles:
            await interaction.response.send_message("❌ Only recruiters can claim this application!", ephemeral=True)
            return

        # Update DB to mark the current user as the recruiter:
        updated = update_application_recruiter(str(interaction.channel.id), str(interaction.user.id))
        if updated:
            await interaction.response.send_message(
                f"✅ {interaction.user.mention} has claimed this application."
            )
        else:
            await interaction.response.send_message("❌ Failed to update recruiter in DB!", ephemeral=True)

    @discord.ui.button(
        label="Withdraw", 
        style=discord.ButtonStyle.danger, 
        custom_id="app_withdraw"  # a fixed custom_id that never changes
    )
    async def withdraw_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        # Again, look up the DB record
        app_data = get_application(str(interaction.channel.id))
        if not app_data:
            await interaction.response.send_message("❌ No application data found for this thread!", ephemeral=True)
            return

        # Only the original applicant can withdraw:
        if interaction.user.id != int(app_data["applicant_id"]):
            await interaction.response.send_message("❌ You are not the owner of this application!", ephemeral=True)
            return

        # Mark is_closed=1 in DB:
        closed = close_application(str(interaction.channel.id))
        if not closed:
            await interaction.response.send_message("❌ Could not close or already closed in DB!", ephemeral=True)
            return

        # Lock/archive the thread:
        embed = discord.Embed(
            title=f"Application withdrawn by {interaction.user.display_name}",
            colour=0xf51616
        )
        embed.set_footer(text="🔒This application thread is locked now!")
        await interaction.response.send_message(embed=embed)
        activity_channel = interaction.guild.get_channel(ACTIVITY_CHANNEL_ID)
        if activity_channel:
            embed = create_user_activity_log_embed("recruitment", f"Application Withdrawn", interaction.user, f"User has withdrawn an application. (Thread ID: <#{interaction.channel.id}>)")
            await activity_channel.send(embed=embed)

        try:
            await interaction.channel.edit(locked=True, archived=True)
        except discord.Forbidden:
            await interaction.response.send_message("❌ Bot lacks permission to lock/archive this thread!", ephemeral=True)
            return
        except discord.HTTPException as e:
            await interaction.response.send_message(f"❌ HTTP error: {e}", ephemeral=True)
            return



class CloseThreadView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)
    
    @discord.ui.button(label="Close Thread", style=discord.ButtonStyle.danger, custom_id="close_thread")
    async def close_thread_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        thread = interaction.channel
        if not is_in_correct_guild(interaction):
            await interaction.response.send_message("❌ This command can only be used in the specified guild.", ephemeral=True)
            return
        ticket_data = get_entry(str(thread.id))
        if not ticket_data:
            await interaction.response.send_message("❌ No ticket data found for this thread.", ephemeral=True)
            return

        # Determine which role can close this thread
        ticket_type = ticket_data[3]
        if ticket_type == "recruiters":
            closing_role = interaction.guild.get_role(RECRUITER_ID)
        elif ticket_type == "botdeveloper":
            closing_role = interaction.guild.get_role(LEAD_BOT_DEVELOPER_ID)
        elif ticket_type == "loa":
            closing_role = interaction.guild.get_role(LEADERSHIP_ID)
        else:
            closing_role = interaction.guild.get_role(LEADERSHIP_ID)
        if closing_role not in interaction.user.roles and interaction.user.id != int(ticket_data[1]):
            await interaction.response.send_message("❌ You do not have permission to close this ticket.", ephemeral=True)
            return
        try:
            from cogs.tickets import remove_ticket  # If your tickets logic is separate
            remove_ticket(str(thread.id))
            embed = discord.Embed(
                title=f"Ticket closed by {interaction.user.display_name}",
                colour=0xf51616
            )
            embed.set_footer(text="🔒This ticket is locked now!")
            await interaction.response.send_message(embed=embed)
            await thread.edit(locked=True, archived=True)
            log(f"Ticket thread {thread.id} closed by user {interaction.user.id}")
        except discord.Forbidden:
            await interaction.response.send_message("❌ I don't have permission to close this thread.", ephemeral=True)
        except discord.HTTPException as e:
            await interaction.response.send_message(f"❌ Failed to close thread: {e}", ephemeral=True)


class RoleRequestView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)
    @discord.ui.button(label="Request Name Change", style=discord.ButtonStyle.secondary, custom_id="request_name_change")
    async def request_name_change(self, interaction: discord.Interaction, button: discord.ui.Button):
        user_id_str = str(interaction.user.id)
        if not is_in_correct_guild(interaction):
            await interaction.response.send_message("❌ This command can only be used in the specified guild.", ephemeral=True)
            return
        if user_id_str in pending_requests:
            await interaction.response.send_message("❌ You already have an open request.", ephemeral=True)
            return
        await interaction.response.send_modal(NameChangeModal())

    @discord.ui.button(label="Request Other", style=discord.ButtonStyle.secondary, custom_id="request_other")
    async def request_other(self, interaction: discord.Interaction, button: discord.ui.Button):
        user_id_str = str(interaction.user.id)
        if user_id_str in pending_requests:
            await interaction.response.send_message("❌ You already have an open request.", ephemeral=True)
            return
        await interaction.response.send_modal(RequestOther())

###
### BUTTONS FOR APPLICATION
###

class ApplicationView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)
    
    @discord.ui.button(label="Open a Trainee Application", style=discord.ButtonStyle.primary, custom_id="request_trainee_role")
    async def request_trainee_role(self, interaction: discord.Interaction, button: discord.ui.Button):
        user_id_str = str(interaction.user.id)
        if not is_in_correct_guild(interaction):
            await interaction.response.send_message("❌ This command can only be used in the specified guild.", ephemeral=True)
            return
        if user_id_str in pending_requests:
            await interaction.response.send_message("❌ You already have an open request.", ephemeral=True)
            return
        if any(r.id == SWAT_ROLE_ID for r in interaction.user.roles):
            await interaction.response.send_message("❌ You are already SWAT!", ephemeral=True)
            return
        if any(r.id in [TRAINEE_ROLE, CADET_ROLE] for r in interaction.user.roles):
            await interaction.response.send_message("❌ You already have a trainee/cadet role!", ephemeral=True)
            return
        
        # Prompt the user to select their region first.
        await interaction.response.send_message(
            "Please select your **Region** for your application:",
            view=RegionSelectionView(interaction.user.id),
            ephemeral=True
        )


class RequestActionView(discord.ui.View):
    def __init__(self, user_id: int = None, request_type: str = None, ingame_name: str = None, recruiter: str = None, new_name: str = None, region: str = None):
        super().__init__(timeout=None)
        self.user_id = user_id
        self.request_type = request_type
        self.ingame_name = ingame_name
        self.new_name = new_name
        self.recruiter = recruiter
        self.region = region

    @discord.ui.button(label="Accept", style=discord.ButtonStyle.success, custom_id="request_accept")
    async def accept_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        recruiter_role = interaction.guild.get_role(RECRUITER_ID)
        if not is_in_correct_guild(interaction):
            await interaction.response.send_message("❌ This command can only be used in the specified guild.", ephemeral=True)
            return
        if not recruiter_role or recruiter_role not in interaction.user.roles:
            await interaction.response.send_message("❌ You do not have permission to use this command.", ephemeral=True)
            return
        try:
            embed = interaction.message.embeds[0]
            embed.color = discord.Color.green()
            if self.request_type in ["name_change", "other"]:
                embed.title += " (Done)"
            else:
                embed.title += " (Accepted)"
            embed.add_field(name="Handled by:", value=f"<@{interaction.user.id}>", inline=False)
            user_id_str = str(self.user_id)
            if self.request_type == "trainee_role":
                # remove from pending_applications
                if user_id_str in pending_applications:
                    del pending_applications[user_id_str]
                    save_applications()
            else:
                # remove from pending_requests
                if user_id_str in pending_requests:
                    del pending_requests[user_id_str]
                    save_requests()
            if self.request_type == "trainee_role":
                guild = interaction.client.get_guild(GUILD_ID)
                if not guild:
                    await interaction.response.send_message("❌ Guild not found.", ephemeral=True)
                    return
                if is_user_in_database(self.user_id):
                    await interaction.response.send_message("❌ There is already a user with this ID in the database.", ephemeral=True)
                    return
                member = guild.get_member(self.user_id)
                if member:
                    await set_user_nickname(member, "trainee", self.ingame_name)
                    trainee_role_obj = guild.get_role(TRAINEE_ROLE)
                    if trainee_role_obj:
                        try:
                            await member.add_roles(trainee_role_obj)
                        except discord.Forbidden:
                            await interaction.followup.send("❌ Bot lacks permission to assign roles.", ephemeral=True)
                            return
                        except discord.HTTPException as e:
                            await interaction.followup.send(f"❌ HTTP Error assigning role: {e}", ephemeral=True)
                            return
                    else:
                        await interaction.response.send_message("❌ Trainee role not found.", ephemeral=True)
                        return
                    if self.region == "EU":
                        EU_role = guild.get_role(EU_ROLE_ID)
                        if EU_role:
                            try:
                                await member.add_roles(EU_role)
                            except discord.Forbidden:
                                await interaction.followup.send("❌ Bot lacks permission to assign roles.", ephemeral=True)
                                return
                            except discord.HTTPException as e:
                                await interaction.followup.send(f"❌ HTTP Error assigning role: {e}", ephemeral=True)
                                return
                        else:
                            await interaction.response.send_message("❌ NA role not found.", ephemeral=True)
                            return
                    elif self.region == "NA":
                        NA_role = guild.get_role(NA_ROLE_ID)
                        if NA_role:
                            try:
                                await member.add_roles(NA_role)
                            except discord.Forbidden:
                                await interaction.followup.send("❌ Bot lacks permission to assign roles.", ephemeral=True)
                                return
                            except discord.HTTPException as e:
                                await interaction.followup.send(f"❌ HTTP Error assigning role: {e}", ephemeral=True)
                                return
                        else:
                            await interaction.response.send_message("❌ EU role not found.", ephemeral=True)
                            return
                    elif self.region == "SEA":
                        SEA_role = guild.get_role(SEA_ROLE_ID)
                        if SEA_role:
                            try:
                                await member.add_roles(SEA_role)
                            except discord.Forbidden:
                                await interaction.followup.send("❌ Bot lacks permission to assign roles.", ephemeral=True)
                                return
                            except discord.HTTPException as e:
                                await interaction.followup.send(f"❌ HTTP Error assigning role: {e}", ephemeral=True)
                                return
                        else:
                            await interaction.response.send_message("❌ SEA role not found.", ephemeral=True)
                            return
                    channel = guild.get_channel(TRAINEE_NOTES_CHANNEL)
                    if channel:
                        start_time = get_rounded_time()
                        end_time = start_time + timedelta(days=7)
                        thread_name = f"{self.ingame_name} | TRAINEE Notes"
                        try:
                            thread = await channel.create_thread(
                                name=thread_name,
                                message=None,
                                type=discord.ChannelType.public_thread,
                                reason="New Trainee accepted",
                                invitable=False
                            )
                        except discord.Forbidden:
                            await interaction.response.send_message("❌ Forbidden: Cannot create thread.", ephemeral=True)
                            return
                        except discord.HTTPException as e:
                            await interaction.response.send_message(f"❌ HTTP Error creating thread: {e}", ephemeral=True)
                            return
                        try:
                            voting_embed = await create_voting_embed(start_time, end_time, interaction.user.id, self.region, self.ingame_name)
                            embed_msg = await thread.send(embed=voting_embed)
                            await embed_msg.add_reaction(PLUS_ONE_EMOJI)
                            await embed_msg.add_reaction("❔")
                            await embed_msg.add_reaction(MINUS_ONE_EMOJI)
                        except discord.Forbidden:
                            await interaction.response.send_message("❌ Forbidden: Cannot create embed.", ephemeral=True)
                            return
                        except discord.HTTPException as e:
                            await interaction.response.send_message(f"❌ HTTP Error creating embed: {e}", ephemeral=True)
                            return
                        add_ok = add_entry(
                            thread_id=thread.id,
                            recruiter_id=str(interaction.user.id),
                            starttime=start_time,
                            endtime=end_time,
                            role_type="trainee",
                            embed_id=str(embed_msg.id),
                            ingame_name=self.ingame_name,
                            user_id=str(self.user_id),
                            region=str(self.region)
                        )
                        if add_ok:
                            trainee_channel = guild.get_channel(TRAINEE_CHAT_CHANNEL)
                            if trainee_channel:
                                message = random.choice(trainee_messages).replace("{username}", f"<@{self.user_id}>")
                                trainee_embed = discord.Embed(description=message, colour=0x008000)
                                await trainee_channel.send(f"<@{self.user_id}>")
                                await trainee_channel.send(embed=trainee_embed)
                        else:
                            await interaction.response.send_message("❌ Failed to add user to database.", ephemeral=True)
                else:
                    await interaction.response.send_message("❌ Member not found in guild.", ephemeral=True)
            await interaction.message.edit(embed=embed, view=None)
        except IndexError:
            await interaction.response.send_message("❌ No embed found on this message.", ephemeral=True)
        except Exception as e:
            await interaction.response.send_message(f"❌ Error accepting request: {e}", ephemeral=True)

    @discord.ui.button(label="Ignore", style=discord.ButtonStyle.danger, custom_id="request_ignore")
    async def ignore_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not is_in_correct_guild(interaction):
            await interaction.response.send_message("❌ This command can only be used in the specified guild.", ephemeral=True)
            return
        try:
            if self.request_type in ["name_change", "other"]:
                leadership_role = interaction.guild.get_role(LEADERSHIP_ID)
                if not leadership_role or (leadership_role not in interaction.user.roles):
                    await interaction.response.send_message("❌ You do not have permission to ignore this request.", ephemeral=True)
                    return
            else:
                recruiter_role = interaction.guild.get_role(RECRUITER_ID)
                if not recruiter_role or (recruiter_role not in interaction.user.roles):
                    await interaction.response.send_message("❌ You do not have permission to ignore this request.", ephemeral=True)
                    return
            updated_embed = interaction.message.embeds[0]
            updated_embed.color = discord.Color.red()
            updated_embed.title += " (Ignored)"
            updated_embed.add_field(name="Ignored by:", value=f"<@{interaction.user.id}>", inline=False)
            await interaction.message.edit(embed=updated_embed, view=None)
            user_id_str = str(self.user_id)
            if self.request_type == "trainee_role":
                # remove from pending_applications
                if user_id_str in pending_applications:
                    del pending_applications[user_id_str]
                    save_applications()
            else:
                # remove from pending_requests
                if user_id_str in pending_requests:
                    del pending_requests[user_id_str]
                    save_requests()
        except Exception as e:
            await interaction.response.send_message(f"❌ Error ignoring request: {e}", ephemeral=True)

    @discord.ui.button(label="Deny w/Reason", style=discord.ButtonStyle.danger, custom_id="request_deny_reason")
    async def deny_with_reason(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not is_in_correct_guild(interaction):
            await interaction.response.send_message("❌ This command can only be used in the specified guild.", ephemeral=True)
            return        
        recruiter_role = interaction.guild.get_role(RECRUITER_ID)
        leadership_role = interaction.guild.get_role(LEADERSHIP_ID)
        if self.request_type in ["name_change", "other"]:
            if not leadership_role or (leadership_role not in interaction.user.roles):
                await interaction.response.send_message("❌ You do not have permission to deny this request.", ephemeral=True)
                return
        else:
            if not recruiter_role or (recruiter_role not in interaction.user.roles):
                await interaction.response.send_message("❌ You do not have permission to deny this request.", ephemeral=True)
                return
            updated_embed = interaction.message.embeds[0]
            updated_embed.color = discord.Color.red()
            updated_embed.title += " (Denied with reason)"
            updated_embed.add_field(name="Ignored by:", value=f"<@{interaction.user.id}>", inline=False)
            await interaction.message.edit(embed=updated_embed, view=None)
            user_id_str = str(self.user_id)
            if user_id_str in pending_requests:
                save_requests()
        modal = DenyReasonModal(self.user_id)
        await interaction.response.send_modal(modal)

class DenyReasonModal(discord.ui.Modal):
    def __init__(self, user_id: int):
        super().__init__(title="Denial Reason")
        self.user_id = user_id

    reason = discord.ui.TextInput(
        label="Reason for Denial",
        style=discord.TextStyle.long,
        placeholder="Explain why this request is denied...",
        required=True
    )

    async def on_submit(self, interaction: discord.Interaction):
        reason_text = self.reason.value
        await interaction.response.defer()
        user = interaction.client.get_user(self.user_id)
        if user:
            try:
                await user.send(
                    f"Your request has been **denied** for the following reason:\n"
                    f"```\n{reason_text}\n```"
                )
            except discord.Forbidden:
                log(f"Could not DM user {self.user_id}; user may have DMs blocked.")
        if interaction.message and interaction.message.embeds:
            updated_embed = interaction.message.embeds[0]
            updated_embed.color = discord.Color.red()
            updated_embed.title += " (Denied with reason)"
            updated_embed.add_field(name="Reason:", value=f"```\n{reason_text}\n```", inline=False)
            updated_embed.add_field(name="Denied by:", value=f"<@{interaction.user.id}>", inline=False)
            await interaction.message.edit(embed=updated_embed, view=None)
        user_id_str = str(self.user_id)
        if self.request_type == "trainee_role":
            # remove from pending_applications
            if user_id_str in pending_applications:
                del pending_applications[user_id_str]
                save_applications()
        else:
            # remove from pending_requests
            if user_id_str in pending_requests:
                del pending_requests[user_id_str]
                save_requests()
        await interaction.followup.send("✅ Denial reason submitted. User has been notified.", ephemeral=True)

class RegionSelectionView(discord.ui.View):
    def __init__(self, user_id: int):
        super().__init__(timeout=None)
        self.user_id = user_id
        self.add_item(RegionSelection())

class RegionSelection(discord.ui.Select):
    def __init__(self):
        options = [
            discord.SelectOption(label="EU",  description="Europe"),
            discord.SelectOption(label="NA",  description="North America"),
            discord.SelectOption(label="SEA", description="Southeast Asia"),
        ]
        super().__init__(
            placeholder="Select the region you play in",
            min_values=1,
            max_values=1,
            options=options
        )
    
    async def callback(self, interaction: discord.Interaction):
        selected_region = self.values[0]
        if get_region_status(selected_region) == "CLOSED":
            guild = interaction.client.get_guild(GUILD_ID)
            if guild:
                activity_channel = guild.get_channel(ACTIVITY_CHANNEL_ID)
                if activity_channel:
                    embed = create_user_activity_log_embed(
                        "recruitment",
                        "Closed Region Application Attempt",
                        interaction.user,
                        f"User attempted to apply for {selected_region} which is closed."
                    )
                    attempt_msg = await activity_channel.send(embed=embed)
                    # Save the log URL (jump_url) for future reference.
                    add_application_attempt(interaction.user.id, selected_region, "closed_region_attempt", attempt_msg.jump_url)
            await interaction.response.send_message(
                f"❌ Applications for {selected_region} are currently closed.",
                ephemeral=True
            )
            return
        
        # If region is open, proceed to show the modal for further details.
        modal = TraineeDetailsModal(selected_region)
        await interaction.response.send_modal(modal)



class TraineeDetailsModal(discord.ui.Modal, title="Trainee Application Details"):
    def __init__(self, region: str):
        super().__init__()
        self.region = region

    ingame_name = discord.ui.TextInput(
        label="In-Game Name",
        placeholder="Enter your in-game name"
    )

    age = discord.ui.TextInput(
        label="Your Age",
        placeholder="Enter your age (e.g., >16)",
        required=True,
        max_length=3
    )

    level = discord.ui.TextInput(
        label="In-Game Level",
        placeholder="e.g., 22",
        required=True,
        max_length=3
    )

    ban_history = discord.ui.TextInput(
        label="Ban History",
        style=discord.TextStyle.long,
        placeholder="Any recent bans? If yes, explain briefly.",
        required=True
    )

    async def on_submit(self, interaction: discord.Interaction):
        user_id_str = str(interaction.user.id)
        pending_applications[user_id_str] = {
            "request_type": "trainee_role",
            "ingame_name": self.ingame_name.value,
            "age": self.age.value,
            "level": self.level.value,
            "ban_history": self.ban_history.value,
            "region": self.region
        }
        save_applications()
        await finalize_trainee_request(interaction, user_id_str)

class NameChangeModal(discord.ui.Modal, title="Request Name Change"):
    new_name = discord.ui.TextInput(label="New Name", placeholder="Enter your new name")

    async def on_submit(self, interaction: discord.Interaction):
        try:
            user_id_str = str(interaction.user.id)
            pending_requests[user_id_str] = {
                "request_type": "name_change",
                "new_name": self.new_name.value
            }
            save_requests()
            guild = interaction.client.get_guild(GUILD_ID)
            if not guild:
                await interaction.response.send_message("❌ Guild not found.", ephemeral=True)
                return
            if not is_in_correct_guild(interaction):
                await interaction.response.send_message("❌ This command can only be used in the specified guild.", ephemeral=True)
                return
            channel = guild.get_channel(REQUESTS_CHANNEL_ID)
            if not channel:
                await interaction.response.send_message("❌ Requests channel not found.", ephemeral=True)
                return
            base_nick = interaction.user.nick if interaction.user.nick else interaction.user.name
            new_name_cleaned = re.sub(r'^(?:\[(CADET|TRAINEE|SWAT)\]\s*)?|(?:\s*\[(CADET|TRAINEE|SWAT)\])+$', '', self.new_name.value, flags=re.IGNORECASE)
            suffix_match = re.search(r'\[(CADET|TRAINEE|SWAT)\]', base_nick, flags=re.IGNORECASE)
            suffix = suffix_match.group(0) if suffix_match else ""
            new_name_final = new_name_cleaned + (" " + suffix if suffix else "")
            embed = discord.Embed(
                title="New Name Change Request:",
                description=f"User <@{interaction.user.id}> has requested a name change!",
                colour=0x298ecb
            )
            embed.add_field(name="New Name:", value=f"```{new_name_final}```", inline=True)
            embed.add_field(name="Make sure to actually change the name BEFORE clicking accept!", value="", inline=False)
            view = RequestActionView(
                user_id=interaction.user.id,
                request_type="name_change",
                new_name=self.new_name.value
            )
            await channel.send(embed=embed, view=view)
            await interaction.response.send_message("✅ Submitting successful!", ephemeral=True)
        except Exception as e:
            await interaction.response.send_message(f"❌ Error submitting name change modal: {e}", ephemeral=True)

class RequestOther(discord.ui.Modal, title="RequestOther"):
    other = discord.ui.TextInput(label="Requesting:", placeholder="What do you want to request?")

    async def on_submit(self, interaction: discord.Interaction):
        try:
            user_id_str = str(interaction.user.id)
            pending_requests[user_id_str] = {
                "request_type": "other",
                "other": self.other.value
            }
            save_requests()
            guild = interaction.client.get_guild(GUILD_ID)
            if not guild:
                await interaction.response.send_message("❌ Guild not found.", ephemeral=True)
                return
            channel = guild.get_channel(REQUESTS_CHANNEL_ID)
            if not channel:
                await interaction.response.send_message("❌ Requests channel not found.", ephemeral=True)
                return
            embed = discord.Embed(
                title="New Other Request:",
                description=f"User <@{interaction.user.id}> has requested Other!",
                colour=0x298ecb
            )
            embed.add_field(name="Request:", value=f"```{self.other.value}```", inline=True)
            embed.add_field(name="Make sure to actually ADD the ROLE BEFORE clicking accept!", value="", inline=False)
            view = RequestActionView(
                user_id=interaction.user.id,
                request_type="other",
                new_name=self.other.value
            )
            await channel.send(embed=embed, view=view)
            await interaction.response.send_message("✅ Submitting successful!", ephemeral=True)
        except Exception as e:
            await interaction.response.send_message(f"❌ Error submitting 'other' request modal: {e}", ephemeral=True)

# Define finalize_trainee_request as a module-level function.
async def finalize_trainee_request(interaction: discord.Interaction, user_id_str: str):
    try:
        if not interaction.response.is_done():
            await interaction.response.defer(ephemeral=True)
        
        request = pending_applications.get(user_id_str)
        if not request:
            try:
                await interaction.followup.send("❌ No pending request found to finalize.", ephemeral=True)
            except discord.NotFound:
                log("Webhook not found when sending pending request not found message.")
            return

        region = request.get("region")
        age     = request.get("age")
        level   = request.get("level")
        bans    = request.get("ban_history")
        ign     = request.get("ingame_name")

        if not region:
            try:
                await interaction.followup.send("❌ Please complete the region selection first.", ephemeral=True)
            except discord.NotFound:
                log("Webhook not found when sending region selection message.")
            return
        
        guild = interaction.client.get_guild(GUILD_ID)
        if not guild:
            try:
                await interaction.followup.send("❌ Guild not found.", ephemeral=True)
            except discord.NotFound:
                log("Webhook not found when sending guild not found message.")
            return

        # Log the normal application submission.
        activity_channel = guild.get_channel(ACTIVITY_CHANNEL_ID)
        if activity_channel:
            embed = create_user_activity_log_embed(
                "recruitment",
                "Application Opened",
                interaction.user,
                f"User has opened an application for {region}."
            )
            await activity_channel.send(embed=embed)

        # Create a private thread for the application.
        apply_channel = guild.get_channel(APPLY_CHANNEL_ID)
        if not apply_channel:
            try:
                await interaction.followup.send("❌ The application channel was not found!", ephemeral=True)
            except discord.NotFound:
                log("Webhook not found when sending application channel not found message.")
            return
        
        thread = await apply_channel.create_thread(
            name=f"{ign} - Trainee Application",
            message=None,
            type=discord.ChannelType.private_thread,
            reason=f"Trainee application from user {user_id_str}",
            invitable=False
        )
        
        # Build the application overview embed.
        history = get_application_history(str(interaction.user.id))
        has_history = len(history) > 0
        recent_attempts = get_recent_closed_attempts(str(interaction.user.id))

        embed = discord.Embed(
            title="📋 Application Overview",
            description=f"**Applicant:** <@{interaction.user.id}>",
            color=0x0080c0
        )
        embed.add_field(name="🎮 In-Game Name", value=ign, inline=False)
        embed.add_field(name="🔞 Age", value=age, inline=True)
        embed.add_field(name="💪 Level", value=level, inline=True)
        embed.add_field(name="📝 Ban History", value=bans, inline=False)
        embed.add_field(name="🌍 Region", value=region, inline=True)

        # Optional field: add internal references if the applicant has applied before.
        if has_history or recent_attempts:
            int_refs = ""
            if has_history:
                int_refs += f"- Has History \n"
            for att in recent_attempts:
                int_refs += f"- [Log Entry]({att['log_url']})\n"

            embed.add_field(
                name="⚠️ Internal Refs:",
                value=int_refs,
                inline=False
            )
        
        # Final instructions to the applicant.
        embed.add_field(
            name="⏳ Next Steps",
            value=(
                "A recruiter will review your application soon and respond.\n"
                "Next: **Provide your FULL ban history** by posting it as a picture in this thread. 📸"
            ),
            inline=False
        )

        control_view = ApplicationControlView()
        await thread.send(
            content=f"<@{interaction.user.id}>",
            embed=embed,
            view=control_view
        )

        add_application(
            thread_id=str(thread.id),
            applicant_id=str(interaction.user.id),
            recruiter_id=None,
            starttime=datetime.now(),
            ingame_name=ign,
            region=region,
            age=age,
            level=level,
            ban_history=bans
        )

        try:
            await interaction.followup.send(
                "✅ Your trainee role application has been submitted via private thread. Please share your whole ban history in the thread while you wait for a response from our recruiters!",
                ephemeral=True
            )
        except discord.NotFound:
            log("Webhook not found when sending submission confirmation.")
    except Exception as e:
        try:
            await interaction.followup.send(f"❌ Error finalizing trainee request: {e}", ephemeral=True)
        except discord.NotFound:
            log(f"Webhook not found when sending error message: {e}")





# -------------------------------
# Recruitment Cog
# -------------------------------
class RecruitmentCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.ban_history_reminded = set()  # to track threads that already got a reminder
        self.bot.loop.create_task(self._wait_and_start())

    async def _wait_and_start(self):
        await self.bot.wait_until_ready()
        load_requests()
        load_applications()
        self.bot.add_view(RoleRequestView())
        self.bot.add_view(RequestActionView())
        self.bot.add_view(ApplicationView())
        self.bot.add_view(CloseThreadView())
        self.bot.add_view(ApplicationControlView())

        # Load embed message ID from file
        global embed_message_id
        if os.path.exists(EMBED_ID_FILE):
            try:
                with open(EMBED_ID_FILE, "r") as f:
                    embed_id_data = f.read().strip()
                    if embed_id_data.isdigit():
                        embed_message_id = int(embed_id_data)
                        log(f"Loaded embed_message_id: {embed_message_id}")
                    else:
                        log("Invalid data in embed.txt.")
                        embed_message_id = None
            except (ValueError, IOError) as e:
                log(f"Error reading {EMBED_ID_FILE}: {e}")
                embed_message_id = None
        
        # LOAD Application Embed ID
        global application_embed_message_id
        if os.path.exists(APPLICATION_EMBED_ID_FILE):
            try:
                with open(APPLICATION_EMBED_ID_FILE, "r") as f:
                    application_embed_id_data = f.read().strip()
                    if application_embed_id_data.isdigit():
                        application_embed_message_id = int(application_embed_id_data)
                        log(f"Loaded embed_message_id: {application_embed_message_id}")
                    else:
                        log("Invalid data in the application embed file.")
                        application_embed_message_id = None
            except (ValueError, IOError) as e:
                log(f"Error reading {APPLICATION_EMBED_ID_FILE}: {e}")
                application_embed_message_id = None
        
        # Start tasks
        self.check_embed_task.start()
        self.check_application_embed_task.start()
        self.check_expired_endtimes_task.start()
        self.check_ban_history_reminder.start()
        await self.load_existing_tickets()
        log("RecruitmentCog setup complete. All tasks started.")

    def cog_unload(self):
        self.check_embed_task.cancel()
        self.check_application_embed_task.cancel()
        self.check_expired_endtimes_task.cancel()

    @tasks.loop(minutes=5)
    async def check_embed_task(self):
        global embed_message_id
        try:
            channel = self.bot.get_channel(TARGET_CHANNEL_ID)
            if channel:
                if embed_message_id:
                    try:
                        await channel.fetch_message(embed_message_id)
                    except discord.NotFound:
                        embed = create_embed()
                        view = RoleRequestView()
                        msg = await channel.send(embed=embed, view=view)
                        embed_message_id = msg.id
                        with open(EMBED_ID_FILE, "w") as f:
                            f.write(str(embed_message_id))
                        log(f"Embed not found; sent new embed with ID: {embed_message_id}")
                    except discord.Forbidden:
                        log("Bot lacks permission to fetch messages in this channel.", level="error")
                    except discord.HTTPException as e:
                        log(f"Failed to fetch message: {e}", level="error")
                else:
                    embed = create_embed()
                    view = RoleRequestView()
                    msg = await channel.send(embed=embed, view=view)
                    embed_message_id = msg.id
                    with open(EMBED_ID_FILE, "w") as f:
                        f.write(str(embed_message_id))
                    log(f"Created new embed with ID: {embed_message_id}")
        except Exception as e:
            log(f"Error in check_embed_task: {e}", level="error")
    
    @tasks.loop(minutes=5)
    async def check_application_embed_task(self):
        global application_embed_message_id
        try:
            channel = self.bot.get_channel(APPLY_CHANNEL_ID)
            if channel:
                if application_embed_message_id:
                    try:
                        await channel.fetch_message(application_embed_message_id)
                    except discord.NotFound:
                        embed = create_application_embed()
                        view = ApplicationView()
                        msg = await channel.send(embed=embed, view=view)
                        application_embed_message_id = msg.id
                        with open(APPLICATION_EMBED_ID_FILE, "w") as f:
                            f.write(str(application_embed_message_id))
                        log(f"Embed not found; sent new embed with ID: {application_embed_message_id}")
                    except discord.Forbidden:
                        log("Bot lacks permission to fetch messages in this channel.", level="error")
                    except discord.HTTPException as e:
                        log(f"Failed to fetch message: {e}", level="error")
                else:
                    embed = create_application_embed()
                    view = ApplicationView()
                    msg = await channel.send(embed=embed, view=view)
                    application_embed_message_id = msg.id
                    with open(APPLICATION_EMBED_ID_FILE, "w") as f:
                        f.write(str(application_embed_message_id))
                    log(f"Created new embed with ID: {application_embed_message_id}")
        except Exception as e:
            log(f"Error in check_application_embed_task: {e}", level="error")

    @tasks.loop(minutes=1)
    async def check_expired_endtimes_task(self):
        conn = None
        try:
            conn = sqlite3.connect(DATABASE_FILE)
            cursor = conn.cursor()
            now = datetime.now()
            cursor.execute(
                """
                SELECT thread_id, recruiter_id, starttime, role_type, region, ingame_name
                FROM entries 
                WHERE endtime <= ? AND reminder_sent = 0
                """,
                (now.isoformat(),)
            )
            expired_entries = cursor.fetchall()
            for thread_id, recruiter_id, starttime, role_type, region, ingame_name in expired_entries:
                thread = self.bot.get_channel(int(thread_id)) if str(thread_id).isdigit() else None
                if thread and isinstance(thread, discord.Thread):
                    try:
                        start_time = datetime.fromisoformat(starttime)
                    except ValueError:
                        log(f"Error parsing starttime: {starttime}", level="error")
                        continue
                    days_open = (now - start_time).days
                    embed = discord.Embed(
                        description=f"**Reminder:** This thread has been open for **{days_open} days**.",
                        color=0x008040
                    )
                    if role_type == "trainee":
                        recruiter = self.bot.get_user(int(recruiter_id))
                        if recruiter:
                            await thread.send(f"<@{recruiter_id}>", embed=embed)
                        else:
                            await thread.send(embed=embed)
                    elif role_type == "cadet":
                        voting_embed = await create_voting_embed(start_time, now, int(recruiter_id), region, ingame_name)
                        await thread.send(f"<@&{SWAT_ROLE_ID}> It's time for another cadet voting!⌛")
                        msg = await thread.send(embed=voting_embed)
                        await msg.add_reaction(PLUS_ONE_EMOJI)
                        await msg.add_reaction("❔")
                        await msg.add_reaction(MINUS_ONE_EMOJI)
                    cursor.execute("UPDATE entries SET reminder_sent = 1 WHERE thread_id = ?", (thread_id,))
                    conn.commit()
                else:
                    log(f"Thread with ID {thread_id} not found or invalid.", level="error")
        except sqlite3.Error as e:
            log(f"Database error in check_expired_endtimes_task: {e}", level="error")
        except Exception as e:
            log(f"Error in check_expired_endtimes_task: {e}", level="error")
        finally:
            if conn:
                conn.close()

    async def load_existing_tickets(self):
        # For recruitment, if you need to load active requests, do so here.
        pass

    @tasks.loop(minutes=30)
    async def check_ban_history_reminder(self):
        # Open a connection to the applications DB
        conn = sqlite3.connect(DATABASE_FILE)
        cursor = conn.cursor()
        # Select all application threads that are still open
        cursor.execute("SELECT thread_id, applicant_id, starttime FROM application_threads WHERE is_closed = 0")
        rows = cursor.fetchall()
        conn.close()

        now = datetime.now()
        for thread_id, applicant_id, starttime in rows:
            start = datetime.fromisoformat(starttime)
            # If more than 24 hours have passed and we haven't already sent a reminder:
            if now - start > timedelta(hours=24) and thread_id not in self.ban_history_reminded:
                # Try to get the thread channel
                channel = self.bot.get_channel(int(thread_id))
                if channel and isinstance(channel, discord.Thread):
                    # Look through recent messages in the thread (limit 100)
                    found_image = False
                    async for msg in channel.history(limit=100):
                        if msg.author.id == int(applicant_id) and msg.attachments:
                            for att in msg.attachments:
                                if att.content_type and att.content_type.startswith("image/"):
                                    found_image = True
                                    break
                        if found_image:
                            break
                    if not found_image:
                        await channel.send(f"<@{applicant_id}> <@&{RECRUITER_ID}> Reminder: Please post your ban history (as an image) in this thread.")
                        self.ban_history_reminded.add(thread_id)


#
# DETECT IF PICTURE IS SENT
#
    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        # Ignore bot messages
        if message.author.bot:
            return

        # Only process if the message is sent in a thread channel
        if not isinstance(message.channel, discord.Thread):
            return

        # Check if this thread is an application thread
        app_data = get_application(str(message.channel.id))
        if not app_data:
            return

        # Only proceed if the application has not been claimed
        if app_data.get("recruiter_id"):
            return

        # Only check messages from the applicant
        if message.author.id != int(app_data["applicant_id"]):
            return

        # Check each attachment
        for att in message.attachments:
            is_image = False
            # First, try the content_type
            if att.content_type and att.content_type.startswith("image/"):
                is_image = True
            else:
                # Fallback: check the filename extension
                lower_name = att.filename.lower()
                if lower_name.endswith(('.png', '.jpg', '.jpeg', '.gif', '.webp')):
                    is_image = True
            if is_image:
                embed = discord.Embed(
                    title="✅ Ban History Submitted!",
                    description=(
                        "Your **ban history** has been successfully submitted.\n"
                        "A recruiter will review your application shortly. ⏳\n\n"
                        "If you have any questions, feel free to ask in this thread!"
                    ),
                    color=discord.Color.green()
                )
                embed.set_footer(text="S.W.A.T Recruitment | Please be patient while we review your application.")
                await message.channel.send(f"<@&{RECRUITER_ID}> {message.author.mention}", embed=embed)
                break

    @app_commands.command(name="hello", description="Say hello to the bot")
    async def hello_command(self, interaction: discord.Interaction):
        if not is_in_correct_guild(interaction):
            await interaction.response.send_message("❌ This command can only be used in the specified guild.", ephemeral=True)
            return
        await interaction.response.send_message(f"✅ Hello, {interaction.user.mention}!", ephemeral=True)

    @app_commands.command(name="force_add", description="Manually add an existing trainee / cadet thread to the database!")
    async def force_add(self, interaction: discord.Interaction, user_id: str, ingame_name: str, region: app_commands.Choice[str], role_type: app_commands.Choice[str]):
        if not is_in_correct_guild(interaction):
            await interaction.response.send_message("❌ This command can only be used in the specified guild.", ephemeral=True)
            return
        try:
            thread = interaction.channel
            user_id_int = int(user_id)
            guild = interaction.client.get_guild(GUILD_ID)
            leadership_role = guild.get_role(LEADERSHIP_ID) if guild else None
            if not leadership_role or leadership_role not in interaction.user.roles:
                await interaction.response.send_message("❌ You do not have permission to use this command.", ephemeral=True)
                return
            selected_region = region.value
            selected_role = role_type.value
            start_time = get_rounded_time()
            end_time = start_time + timedelta(days=7)
            validate_entry = add_entry(
                thread_id=str(thread.id),
                recruiter_id=str(interaction.user.id),
                starttime=start_time,
                endtime=end_time,
                role_type=str(selected_role),
                embed_id=None,
                ingame_name=ingame_name,
                user_id=str(user_id_int),
                region=selected_region
            )
            if validate_entry:
                await interaction.response.send_message(
                    f"✅ Successfully added user ID `{user_id_int}` with in-game name `{ingame_name}` as `{selected_role}` in region `{selected_region}`.",
                    ephemeral=True
                )
                activity_channel = self.bot.get_channel(ACTIVITY_CHANNEL_ID)
                if activity_channel:
                    embed = create_user_activity_log_embed("recruitment", f"Manually added trainee", interaction.user, f"User has added <@{user_id_int} as a trainee.")
                    await activity_channel.send(embed=embed)
            else:
                await interaction.response.send_message(
                    f"❌ Error adding user ID `{user_id_int}` to the database. Possibly a duplicate or DB issue.",
                    ephemeral=True
                )
        except ValueError:
            await interaction.response.send_message("❌ Invalid user ID provided.", ephemeral=True)
        except Exception as e:
            await interaction.response.send_message(f"❌ Error: {e}", ephemeral=True)

    @app_commands.command(name="list_requests", description="Lists the currently stored pending requests.")
    async def list_requests(self, interaction: discord.Interaction):
        if not is_in_correct_guild(interaction):
            await interaction.response.send_message("❌ This command can only be used in the specified guild.", ephemeral=True)
            return
        guild = interaction.client.get_guild(GUILD_ID)
        leadership_role = guild.get_role(LEADERSHIP_ID) if guild else None
        if not leadership_role or leadership_role not in interaction.user.roles:
            await interaction.response.send_message("❌ You do not have permission to list requests.", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        if not pending_requests:
            await interaction.followup.send("There are **no** pending requests at the moment.", ephemeral=True)
            return
        lines = []
        for user_id_str, request_data in pending_requests.items():
            req_type = request_data.get("request_type", "N/A")
            detail = ""
            if req_type == "trainee_role":
                detail = f"InGame Name: {request_data.get('ingame_name', 'Unknown')}, Region: {request_data.get('region', 'Not Selected')}"
            elif req_type == "name_change":
                detail = f"New Name: {request_data.get('new_name', 'Unknown')}"
            elif req_type == "other":
                detail = f"Request: {request_data.get('other', 'No details')}"
            lines.append(f"• **User ID**: {user_id_str} | **Type**: `{req_type}` | {detail}")
        reply_text = "\n".join(lines)
        await interaction.followup.send(f"**Current Pending Requests:**\n\n{reply_text}", ephemeral=True)

    @app_commands.command(name="clear_requests", description="Clears the entire pending requests list.")
    async def clear_requests(self, interaction: discord.Interaction):
        if not is_in_correct_guild(interaction):
            await interaction.response.send_message("❌ This command can only be used in the specified guild.", ephemeral=True)
            return
        guild = interaction.client.get_guild(GUILD_ID)
        leadership_role = guild.get_role(LEADERSHIP_ID) if guild else None
        if not leadership_role or leadership_role not in interaction.user.roles:
            await interaction.response.send_message("❌ You do not have permission to clear requests.", ephemeral=True)
            return
        pending_requests.clear()
        save_requests()
        await interaction.response.send_message("✅ All pending requests have been **cleared**!", ephemeral=True)
        activity_channel = self.bot.get_channel(ACTIVITY_CHANNEL_ID)
        if activity_channel:
            embed = create_user_activity_log_embed("recruitment", f"Cleared Requests", interaction.user, f"User has cleared all requests.")
            await activity_channel.send(embed=embed)

    @app_commands.command(name="votinginfo", description="Show info about the current voting thread")
    async def votinginfo_command(self, interaction: discord.Interaction):
        if not is_in_correct_guild(interaction):
            await interaction.response.send_message("❌ This command can only be used in the specified guild.", ephemeral=True)
            return
        if not isinstance(interaction.channel, discord.Thread):
            await interaction.response.send_message("❌ Use this command inside a thread.", ephemeral=True)
            return
        data = get_entry(str(interaction.channel.id))
        if not data:
            await interaction.response.send_message("❌ This thread is not associated with any trainee/cadet voting!", ephemeral=True)
            return
        embed = discord.Embed(title="Voting Information", color=discord.Color.blue())
        embed.add_field(name="Thread Name", value=interaction.channel.name, inline=False)
        embed.add_field(name="Thread ID",  value=interaction.channel.id, inline=False)
        embed.add_field(name="Start Time", value=str(data["starttime"]), inline=False)
        embed.add_field(name="End Time",   value=str(data["endtime"]), inline=False)
        embed.add_field(name="Type",       value=data["role_type"], inline=False)
        embed.add_field(name="Recruiter",  value=f"<@{data['recruiter_id']}>", inline=False)
        embed.add_field(name="Embed ID",   value=str(data["embed_id"]), inline=False)
        embed.add_field(name="InGame Name",value=data["ingame_name"], inline=False)
        embed.add_field(name="User ID",    value=f"<@{data['user_id']}>", inline=False)
        embed.add_field(name="Region",     value=data['region'], inline=False)
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @app_commands.command(name="remove", description="Remove a user from trainee / cadet program and close thread!")
    async def lock_thread_command(self, interaction: discord.Interaction):
        if not is_in_correct_guild(interaction):
            await interaction.response.send_message("❌ This command can only be used in the specified guild.", ephemeral=True)
            return
        guild = interaction.client.get_guild(GUILD_ID)
        recruiter_role = guild.get_role(RECRUITER_ID) if guild else None
        if not recruiter_role or recruiter_role not in interaction.user.roles:
            await interaction.response.send_message("❌ You do not have permission to use this command.", ephemeral=True)
            return
        if not isinstance(interaction.channel, discord.Thread):
            await interaction.response.send_message("❌ This is not a thread.", ephemeral=True)
            return
        data = get_entry(str(interaction.channel.id))
        if not data:
            await interaction.response.send_message("❌ No DB entry for this thread!", ephemeral=True)
            return
        await interaction.response.defer()
        channel_name = "❌ " + str(interaction.channel.name)
        try:
            await interaction.channel.edit(name=channel_name)
        except Exception:
            log("Renaming thread failed", level="warning")
        await close_thread(interaction, interaction.channel)
        if not guild:
            await interaction.followup.send("❌ Guild not found.", ephemeral=True)
            return
        member = guild.get_member(int(data["user_id"]))
        if member:
            try:
                temp_name = re.sub(r'(?:\s*\[(?:CADET|TRAINEE|SWAT)\])+$', '', member.nick if member.nick else member.name, flags=re.IGNORECASE)
                await member.edit(nick=temp_name)
            except discord.Forbidden:
                await interaction.followup.send("❌ Forbidden: Cannot remove tag from nickname.", ephemeral=True)
            except discord.HTTPException as e:
                await interaction.followup.send(f"❌ HTTP Error removing tag from nickname: {e}", ephemeral=True)
            t_role = guild.get_role(TRAINEE_ROLE)
            c_role = guild.get_role(CADET_ROLE)
            try:
                if t_role in member.roles:
                    await member.remove_roles(t_role)
                elif c_role in member.roles:
                    await member.remove_roles(c_role)
            except discord.Forbidden:
                await interaction.followup.send("❌ Forbidden: Cannot remove roles.", ephemeral=True)
            except discord.HTTPException as e:
                await interaction.followup.send(f"❌ HTTP Error removing roles: {e}", ephemeral=True)
        else:
            log(f"Member with ID {data['user_id']} not found in guild (they may have left). Skipping nickname and role removal.", level="warning")
        embed = discord.Embed(
            title="❌ " + str(data["ingame_name"]) + " has been removed!",
            colour=0xf94144
        )
        embed.set_footer(text="🔒This thread is locked now!")
        await interaction.followup.send(embed=embed)
        activity_channel = self.bot.get_channel(ACTIVITY_CHANNEL_ID)
        if activity_channel:
            embed = create_user_activity_log_embed("recruitment", f"Promotion", interaction.user, f"User has removed a trainee/cadet. (Thread ID: <#{interaction.channel.id}>)")
            await activity_channel.send(embed=embed)

    @app_commands.command(name="promote", description="Promote the user in the current voting thread (Trainee->Cadet or Cadet->SWAT).")
    async def promote_user_command(self, interaction: discord.Interaction):
        if not is_in_correct_guild(interaction):
            await interaction.response.send_message("❌ This command can only be used in the specified guild.", ephemeral=True)
            return
        guild = interaction.client.get_guild(GUILD_ID)
        recruiter_role = guild.get_role(RECRUITER_ID) if guild else None
        if not recruiter_role or recruiter_role not in interaction.user.roles:
            await interaction.response.send_message("❌ You do not have permission to use this command.", ephemeral=True)
            return
        if not isinstance(interaction.channel, discord.Thread):
            await interaction.response.send_message("❌ This command must be used in a thread.", ephemeral=True)
            return
        data = get_entry(str(interaction.channel.id))
        if not data:
            await interaction.response.send_message("❌ No DB entry for this thread!", ephemeral=True)
            return
        await interaction.response.defer()
        removed = remove_entry(str(interaction.channel.id))
        if removed:
            try:
                channel_name = "✅ " + str(interaction.channel.name)
                await interaction.channel.edit(name=channel_name)
                await interaction.channel.edit(locked=True, archived=True)
            except discord.Forbidden:
                await interaction.followup.send("❌ Forbidden: Cannot lock/archive thread.", ephemeral=True)
            except discord.HTTPException as e:
                await interaction.followup.send(f"❌ HTTP Error locking thread: {e}", ephemeral=True)
            if data["role_type"] == "trainee":
                promotion = "Cadet"
            else:
                promotion = "SWAT Officer"
            embed = discord.Embed(
                title="🏅 " + str(data["ingame_name"]) + " has been promoted to " + str(promotion) + "!🎉",
                colour=0x43bccd
            )
            embed.set_footer(text="🔒This thread is locked now!")
            await interaction.followup.send(embed=embed)
        else:
            await interaction.followup.send("❌ Not a registered voting thread!", ephemeral=True)
            return
        if not guild:
            return
        member = guild.get_member(int(data["user_id"]))
        if not member:
            await interaction.followup.send("❌ User not found in guild!", ephemeral=True)
            return
        old_role_type = data["role_type"]
        ingame_name = data["ingame_name"]
        try:
            if old_role_type == "trainee":
                await set_user_nickname(member, "cadet")
                t_role = guild.get_role(TRAINEE_ROLE)
                c_role = guild.get_role(CADET_ROLE)
                if t_role in member.roles:
                    await member.remove_roles(t_role)
                await member.add_roles(c_role)
                channel_obj = guild.get_channel(CADET_NOTES_CHANNEL)
                if channel_obj:
                    start_time = get_rounded_time()
                    end_time = start_time + timedelta(days=7)
                    try:
                        thread = await channel_obj.create_thread(
                            name=f"{ingame_name} | CADET Notes",
                            message=None,
                            type=discord.ChannelType.public_thread,
                            reason="Promoted to cadet!",
                            invitable=False
                        )
                    except discord.Forbidden:
                        await interaction.followup.send("❌ Forbidden: Cannot create cadet thread.", ephemeral=True)
                        return
                    except discord.HTTPException as e:
                        await interaction.followup.send(f"❌ HTTP Error creating cadet thread: {e}", ephemeral=True)
                        return
                    voting_embed = await create_voting_embed(start_time, end_time, interaction.user.id, data["region"], ingame_name)
                    embed_msg = await thread.send(embed=voting_embed)
                    await embed_msg.add_reaction(PLUS_ONE_EMOJI)
                    await embed_msg.add_reaction("❔")
                    await embed_msg.add_reaction(MINUS_ONE_EMOJI)
                    swat_chat = guild.get_channel(SWAT_CHAT_CHANNEL)
                    if swat_chat:
                        message_text = random.choice(cadet_messages).replace("{username}", f"<@{data['user_id']}>")
                        cadet_embed = discord.Embed(description=message_text, colour=0x008000)
                        await swat_chat.send(f"<@{data['user_id']}>")
                        await swat_chat.send(embed=cadet_embed)
                    add_entry(
                        thread_id=thread.id,
                        recruiter_id=data["recruiter_id"],
                        starttime=start_time,
                        endtime=end_time,
                        role_type="cadet",
                        embed_id=str(embed_msg.id),
                        ingame_name=ingame_name,
                        user_id=data["user_id"],
                        region=data["region"]
                    )
            elif old_role_type == "cadet":
                await set_user_nickname(member, "swat")
                c_role = guild.get_role(CADET_ROLE)
                s_role = guild.get_role(SWAT_ROLE_ID)
                o_role = guild.get_role(OFFICER_ROLE_ID)
                if c_role in member.roles:
                    await member.remove_roles(c_role)
                await member.add_roles(s_role)
                await member.add_roles(o_role)
                try:
                    await member.send(welcome_to_swat)
                except discord.Forbidden:
                    log(f"Could not DM user {member.id} (Forbidden).", level="warning")
                except discord.HTTPException as e:
                    log(f"HTTP error DMing user {member.id}: {e}", level="warning")
                activity_channel = self.bot.get_channel(ACTIVITY_CHANNEL_ID)
                if activity_channel:
                    embed = create_user_activity_log_embed("recruitment", f"Promotion", interaction.user, f"User has promoted to SWAT Officer. (Thread ID: <#{interaction.channel.id}>)")
                    await activity_channel.send(embed=embed)
        except discord.Forbidden:
            await interaction.followup.send("❌ Forbidden: Cannot assign roles or change nickname.", ephemeral=True)
        except discord.HTTPException as e:
            await interaction.followup.send(f"❌ HTTP Error during promotion: {e}", ephemeral=True)

    @app_commands.command(name="extend", description="Extend the current thread's voting period.")
    @app_commands.describe(days="How many days to extend?")
    async def extend_thread_command(self, interaction: discord.Interaction, days: int):
        if not is_in_correct_guild(interaction):
            await interaction.response.send_message("❌ This command can only be used in the specified guild.", ephemeral=True)
            return
        recruiter_role = interaction.guild.get_role(RECRUITER_ID)
        if not recruiter_role or recruiter_role not in interaction.user.roles:
            await interaction.response.send_message("❌ You do not have permission to use this command.", ephemeral=True)
            return
        if not isinstance(interaction.channel, discord.Thread):
            await interaction.response.send_message("❌ Use this in a thread channel.", ephemeral=True)
            return
        data = get_entry(str(interaction.channel.id))
        if not data:
            await interaction.response.send_message("❌ No DB entry for this thread!", ephemeral=True)
            return
        if days < 1 or days > 50:
            await interaction.response.send_message("❌ You can only extend from 1 to 50 days!", ephemeral=True)
            return
        try:
            if not isinstance(data["endtime"], datetime):
                old_end = datetime.fromisoformat(str(data["endtime"]))
            else:
                old_end = data["endtime"]
            new_end = old_end + timedelta(days=days)
        except ValueError:
            await interaction.response.send_message("❌ Invalid endtime format in database.", ephemeral=True)
            return
        if update_endtime(str(interaction.channel.id), new_end):
            if data["embed_id"]:
                try:
                    msg = await interaction.channel.fetch_message(int(data["embed_id"]))
                    new_embed = await create_voting_embed(data["starttime"], new_end, int(data["recruiter_id"]), data["region"], data["ingame_name"], extended=True)
                    await msg.edit(embed=new_embed)
                except discord.NotFound:
                    await interaction.response.send_message("❌ Voting embed message not found.", ephemeral=True)
                    return
                except discord.Forbidden:
                    await interaction.response.send_message("❌ Forbidden: Cannot edit the voting embed message.", ephemeral=True)
                    return
                except discord.HTTPException as e:
                    await interaction.response.send_message(f"❌ HTTP Error editing the voting embed: {e}", ephemeral=True)
                    return
            embed = discord.Embed(
                description=f"✅ This {str(data['role_type'])} voting has been extended by {str(days)} day(s)!",
                colour=0xf9c74f
            )
            await interaction.response.send_message(embed=embed)
        else:
            await interaction.response.send_message("❌ Failed to update endtime in DB.", ephemeral=True)

    @app_commands.command(name="resend_voting", description="Resends a voting embed!")
    async def resend_voting_command(self, interaction: discord.Interaction):
        if not is_in_correct_guild(interaction):
            await interaction.response.send_message("❌ This command can only be used in the specified guild.", ephemeral=True)
            return
        recruiter_role = interaction.guild.get_role(RECRUITER_ID)
        if not recruiter_role or recruiter_role not in interaction.user.roles:
            await interaction.response.send_message("❌ You do not have permission to use this command.", ephemeral=True)
            return
        if not isinstance(interaction.channel, discord.Thread):
            await interaction.response.send_message("❌ This command must be used in a thread.", ephemeral=True)
            return
        try:
            data = get_entry(str(interaction.channel.id))
            if not data:
                await interaction.response.send_message("❌ No DB entry for this thread!", ephemeral=True)
                return
            voting_embed = await create_voting_embed(data["starttime"], data["endtime"], data["recruiter_id"], data["region"], data["ingame_name"])
            embed_msg = await interaction.channel.send(embed=voting_embed)
            await embed_msg.add_reaction(PLUS_ONE_EMOJI)
            await embed_msg.add_reaction("❔")
            await embed_msg.add_reaction(MINUS_ONE_EMOJI)
            await interaction.response.send_message("✅ Voting embed has been resent.", ephemeral=True)
        except Exception as e:
            await interaction.response.send_message(f"❌ Error occurred: {e}", ephemeral=True)

    @app_commands.command(name="early_vote", description="Resends a voting embed!")
    async def early_vote(self, interaction: discord.Interaction):
        if not is_in_correct_guild(interaction):
            await interaction.response.send_message("❌ This command can only be used in the specified guild.", ephemeral=True)
            return
        recruiter_role = interaction.guild.get_role(RECRUITER_ID)
        if not recruiter_role or recruiter_role not in interaction.user.roles:
            await interaction.response.send_message("❌ You do not have permission to use this command.", ephemeral=True)
            return
        if not isinstance(interaction.channel, discord.Thread):
            await interaction.response.send_message("❌ This command must be used in a thread.", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        try:
            data = get_entry(str(interaction.channel.id))
            if not data:
                await interaction.followup.send("❌ No DB entry for this thread!", ephemeral=True)
                return
            if str(data["reminder_sent"]) == "0":
                thread = interaction.client.get_channel(int(data.get("thread_id", ""))) if str(data.get("thread_id", "")).isdigit() else None
                if thread and isinstance(thread, discord.Thread):
                    try:
                        if not isinstance(data["starttime"], datetime):
                            start_time = datetime.fromisoformat(str(data["starttime"]))
                        else:
                            start_time = data["endtime"]
                    except ValueError:
                        log(f"Error parsing starttime: {data['starttime']}", level="error")
                    conn = sqlite3.connect(DATABASE_FILE)
                    cursor = conn.cursor()
                    now = datetime.now()
                    if data["role_type"] == "cadet":
                        voting_embed = discord.Embed(
                            description=(
                                "SWAT, please express your vote below.\n"
                                f"Use {PLUS_ONE_EMOJI}, ❔, or {MINUS_ONE_EMOJI} accordingly."
                            ),
                            color=0x000000
                        )
                        flags = {"EU": "🇪🇺 ", "NA": "🇺🇸 ", "SEA": "🇸🇬 "}
                        region_name = data["region"][:-1] if data["region"] and data["region"][-1].isdigit() else data["region"]
                        title = f"{flags.get(region_name, '')}{data['region']}"
                        voting_embed.add_field(name="InGame Name:", value=data["ingame_name"], inline=True)
                        voting_embed.add_field(name="Region:", value=title, inline=True)
                        voting_embed.add_field(name="", value="", inline=False)
                        voting_embed.add_field(name="Voting started:", value=create_discord_timestamp(start_time), inline=True)
                        voting_embed.add_field(name="Voting has ended!", value="", inline=True)
                        voting_embed.add_field(name="", value="", inline=False)
                        voting_embed.add_field(name="Thread managed by:", value=f"<@{data['recruiter_id']}>", inline=True)
                        voting_embed.add_field(name="Early voting issued by:", value=f"<@{interaction.user.id}>", inline=True)
                        await thread.send(f"<@&{SWAT_ROLE_ID}> It's time for another cadet voting!⌛")
                        embed_msg = await thread.send(embed=voting_embed)
                        await embed_msg.add_reaction(PLUS_ONE_EMOJI)
                        await embed_msg.add_reaction("❔")
                        await embed_msg.add_reaction(MINUS_ONE_EMOJI)
                        cursor.execute(
                            """
                            UPDATE entries 
                            SET reminder_sent = 1 
                            WHERE thread_id = ?
                            """,
                            (interaction.channel.id,)
                        )
                        conn.commit()
                        await interaction.followup.send("✅ Early vote has been issued.", ephemeral=True)
                    else:
                        await interaction.followup.send("❌ Not a cadet thread!", ephemeral=True)
            else:
                await interaction.followup.send("❌ Reminder has already been sent!", ephemeral=True)
        except Exception as e:
            if interaction.response.is_done():
                await interaction.followup.send(f"❌ Error occurred: {e}", ephemeral=True)
            else:
                await interaction.response.send_message(f"❌ Error occurred: {e}", ephemeral=True)


#
# APPLICATION COMMANDS
#
    @app_commands.command(name="app_info", description="Show info about the current application thread.")
    async def app_info_command(self, interaction: discord.Interaction):
        if not is_in_correct_guild(interaction):
            await interaction.response.send_message("❌ Wrong guild!", ephemeral=True)
            return
        if not isinstance(interaction.channel, discord.Thread):
            await interaction.response.send_message("❌ This must be used in a thread!", ephemeral=True)
            return

        app_data = get_application(str(interaction.channel.id))
        if not app_data:
            await interaction.response.send_message("❌ No application data found for this thread!", ephemeral=True)
            return

        embed = discord.Embed(title="Application Info", color=discord.Color.blue())
        embed.add_field(name="Applicant", value=f"<@{app_data['applicant_id']}>", inline=False)
        embed.add_field(name="Recruiter", value=f"<@{app_data['recruiter_id']}>" 
                        if app_data['recruiter_id'] else "No one claimed yet", inline=False)
        embed.add_field(name="Started", value=str(app_data["starttime"]), inline=False)
        embed.add_field(name="IGN", value=app_data["ingame_name"], inline=True)
        embed.add_field(name="Region", value=app_data["region"], inline=True)
        embed.add_field(name="Age", value=app_data["age"], inline=True)
        embed.add_field(name="Level", value=app_data["level"], inline=True)
        embed.add_field(name="Ban History", value=app_data["ban_history"], inline=False)
        embed.add_field(name="is_closed?", value=str(app_data["is_closed"]), inline=False)

        await interaction.response.send_message(embed=embed, ephemeral=True)

    @app_commands.command(name="app_remove", description="Remove this application and lock/archive the thread.")
    async def app_remove_command(self, interaction: discord.Interaction):
        if not is_in_correct_guild(interaction):
            await interaction.response.send_message("❌ Wrong guild!", ephemeral=True)
            return
        if not isinstance(interaction.channel, discord.Thread):
            await interaction.response.send_message("❌ Must be used in a thread!", ephemeral=True)
            return

        # Instead of deleting, mark the application as removed.
        removed = mark_application_removed(str(interaction.channel.id))
        if not removed:
            await interaction.response.send_message("❌ No application data found or already removed!", ephemeral=True)
            return

        embed = discord.Embed(
            title="❌ This application has been removed!",
            colour=0xf94144
        )
        embed.set_footer(text="🔒This thread is locked now!")
        await interaction.response.send_message(embed=embed)

        activity_channel = self.bot.get_channel(ACTIVITY_CHANNEL_ID)
        if activity_channel:
            embed = create_user_activity_log_embed("recruitment", f"Application Removed", interaction.user, f"User has removed this application. (Thread ID: <#{interaction.channel.id}>)")
            await activity_channel.send(embed=embed)

        # Now lock/archive the thread
        try:
            await interaction.channel.edit(locked=True, archived=True)
        except discord.Forbidden:
            await interaction.response.send_message("❌ Forbidden: Cannot lock/archive the thread!", ephemeral=True)
            return
        except discord.HTTPException as e:
            await interaction.response.send_message(f"❌ HTTP error: {e}", ephemeral=True)
            return

    @app_commands.command(name="app_accept", description="Accept this application, awarding the Trainee role to the applicant.")
    async def app_accept_command(self, interaction: discord.Interaction):
        # Immediately defer so we can use followup responses
        await interaction.response.defer(ephemeral=False)

        # Use followup.send for error responses (ephemeral)
        if not is_in_correct_guild(interaction):
            await interaction.followup.send("❌ Wrong guild!", ephemeral=True)
            return

        # Must be used in a thread
        if not isinstance(interaction.channel, discord.Thread):
            await interaction.followup.send("❌ Must be used in a thread!", ephemeral=True)
            return

        app_data = get_application(str(interaction.channel.id))
        if not app_data:
            await interaction.followup.send("❌ No application data found for this thread!", ephemeral=True)
            return

        if app_data["is_closed"] == 1:
            await interaction.followup.send("❌ This application is already closed!", ephemeral=True)
            return

        # Make sure it's claimed
        if not app_data["recruiter_id"]:
            await interaction.followup.send("❌ This application has not been claimed by any recruiter!", ephemeral=True)
            return

        # Check if the user issuing command is a Recruiter
        recruiter_role = interaction.guild.get_role(RECRUITER_ID)
        if not recruiter_role or recruiter_role not in interaction.user.roles:
            await interaction.followup.send("❌ You do not have permission to accept this application.", ephemeral=True)
            return

        # Now do the "Trainee add" logic:
        applicant_id = int(app_data["applicant_id"])
        if is_user_in_database(applicant_id):
            await interaction.followup.send("❌ That user is already in the voting database!", ephemeral=True)
            return

        guild = interaction.guild
        member = guild.get_member(applicant_id)
        if not member:
            await interaction.followup.send("❌ That user is no longer in the guild!", ephemeral=True)
            return

        # 1) Adjust nickname to include [TRAINEE]
        await set_user_nickname(member, "trainee", app_data["ingame_name"])

        # 2) Add the Trainee role
        trainee_role_obj = guild.get_role(TRAINEE_ROLE)
        if trainee_role_obj:
            try:
                await member.add_roles(trainee_role_obj)
            except discord.Forbidden:
                await interaction.followup.send("❌ Bot lacks permission to assign the Trainee role.", ephemeral=True)
                return
        else:
            await interaction.followup.send("❌ Trainee role not found.", ephemeral=True)
            return

        # 3) Add region role (EU, NA, SEA)
        region = app_data["region"]
        region_role_id = None
        if region.upper() == "EU":
            region_role_id = EU_ROLE_ID
        elif region.upper() == "NA":
            region_role_id = NA_ROLE_ID
        elif region.upper() == "SEA":
            region_role_id = SEA_ROLE_ID
        if region_role_id:
            region_role = guild.get_role(region_role_id)
            if region_role:
                try:
                    await member.add_roles(region_role)
                except discord.Forbidden:
                    await interaction.followup.send("❌ Bot lacks permission to assign region role.", ephemeral=True)
                    return

        # 4) Create new thread in the Trainee Notes channel with a voting embed
        notes_channel = guild.get_channel(TRAINEE_NOTES_CHANNEL)
        if not notes_channel:
            await interaction.followup.send("❌ Trainee notes channel not found.", ephemeral=True)
            return

        start_time = get_rounded_time()
        end_time   = start_time + timedelta(days=7)
        thread_title = f"{app_data['ingame_name']} | TRAINEE Notes"
        try:
            trainee_thread = await notes_channel.create_thread(
                name=thread_title,
                type=discord.ChannelType.public_thread,
                invitable=False,
                reason="New Trainee accepted"
            )
        except discord.Forbidden:
            await interaction.followup.send("❌ Cannot create new thread in Trainee notes channel.", ephemeral=True)
            return

        # 5) Send the voting embed and add reactions
        voting_embed = await create_voting_embed(start_time, end_time, app_data["recruiter_id"], region, app_data["ingame_name"])
        msg = await trainee_thread.send(embed=voting_embed)
        await msg.add_reaction(PLUS_ONE_EMOJI)
        await msg.add_reaction("❔")
        await msg.add_reaction(MINUS_ONE_EMOJI)

        # 6) Insert into the "entries" table for tracking
        inserted = add_entry(
            thread_id=str(trainee_thread.id),
            recruiter_id=app_data["recruiter_id"],
            starttime=start_time,
            endtime=end_time,
            role_type="trainee",
            embed_id=str(msg.id),
            ingame_name=app_data["ingame_name"],
            user_id=str(applicant_id),
            region=region
        )
        if not inserted:
            # Optionally log the DB insertion failure
            pass

        # 7) Post a welcome message in the trainee chat
        trainee_chat = guild.get_channel(TRAINEE_CHAT_CHANNEL)
        if trainee_chat:
            import random
            message_text = random.choice(trainee_messages).replace("{username}", f"<@{applicant_id}>")
            welcome_embed = discord.Embed(description=message_text, colour=0x008000)
            await trainee_chat.send(f"<@{applicant_id}>")
            await trainee_chat.send(embed=welcome_embed)

        update_application_status(str(interaction.channel.id), 'accepted')
        # Mark application as closed in your DB and lock/archive the application thread
        close_application(str(interaction.channel.id))
        acceptance_embed = discord.Embed(
            title="✅ This application has been **ACCEPTED**!",
            description=f"<@{applicant_id}> is now a Trainee.",
            colour=0x00b050
        )
        acceptance_embed.set_footer(text="🔒 This thread is locked now.")
        await interaction.followup.send(embed=acceptance_embed, ephemeral=False)
        activity_channel = self.bot.get_channel(ACTIVITY_CHANNEL_ID)
        if activity_channel:
            embed = create_user_activity_log_embed("recruitment", f"Application Accepted", interaction.user, f"User has accepted this application. (Thread ID: <#{interaction.channel.id}>)")
            await activity_channel.send(embed=embed)
        try:
            await interaction.channel.edit(locked=True, archived=True)
        except discord.Forbidden:
            pass

    @app_commands.command(name="app_deny", description="Deny the application with a reason and a note about reapplying.")
    @app_commands.describe(reason="Why is this application being denied?", can_reapply="Indicate if they can reapply later (e.g., 'Yes after 2 weeks').")
    async def app_deny_command(self, interaction: discord.Interaction, reason: str, can_reapply: str):
        await interaction.response.defer(ephemeral=False)
        if not is_in_correct_guild(interaction):
            await interaction.followup.send("❌ Wrong guild!", ephemeral=True)
            return
        if not isinstance(interaction.channel, discord.Thread):
            await interaction.followup.send("❌ Must be used inside a thread!", ephemeral=True)
            return
        app_data = get_application(str(interaction.channel.id))
        if not app_data:
            await interaction.followup.send("❌ No application data found for this thread!", ephemeral=True)
            return
        if app_data["is_closed"] == 1:
            await interaction.followup.send("❌ This application is already closed!", ephemeral=True)
            return

        # Mark the application as closed.
        close_application(str(interaction.channel.id))
        # Update the status to 'denied'
        update_application_status(str(interaction.channel.id), 'denied')
        # Check if the user is allowed to deny (e.g., recruiter role)
        recruiter_role = interaction.guild.get_role(RECRUITER_ID)
        if not recruiter_role or recruiter_role not in interaction.user.roles:
            await interaction.followup.send("❌ You do not have permission to deny applications.", ephemeral=True)
            return

        # Mark the application as closed
        close_application(str(interaction.channel.id))

        # DM the applicant about the denial
        applicant_id = int(app_data["applicant_id"])
        applicant_user = interaction.client.get_user(applicant_id)
        if applicant_user:
            try:
                await applicant_user.send(
                    f"Your application to join S.W.A.T. has been **denied**.\n\n"
                    f"**Reason:** {reason}\n"
                    f"**Can reapply?:** {can_reapply}\n\n"
                    f"Thank you for your interest."
                )
            except discord.Forbidden:
                pass  # user may have DMs disabled

        # Lock/archive the thread with a public denial embed
        denied_embed = discord.Embed(
            title="❌ This application has been **DENIED**",
            description=f"**Reason:** {reason}\n**Can reapply?:** {can_reapply}",
            colour=0xff0000
        )
        denied_embed.set_footer(text="🔒 This thread is locked now!")
        await interaction.followup.send(embed=denied_embed, ephemeral=False)
        activity_channel = self.bot.get_channel(ACTIVITY_CHANNEL_ID)
        if activity_channel:
            embed = create_user_activity_log_embed("recruitment", f"Application Denied", interaction.user, f"User has denied this application. (Thread ID: <#{interaction.channel.id}>)")
            await activity_channel.send(embed=embed)
        try:
            await interaction.channel.edit(locked=True, archived=True)
        except discord.Forbidden:
            pass

    @app_commands.command(name="app_claim", description="Claim this application.")
    async def app_claim_command(self, interaction: discord.Interaction):
        # Look up application data in DB using the thread ID:
        app_data = get_application(str(interaction.channel.id))
        if not app_data:
            await interaction.response.send_message("❌ No application data found for this thread!", ephemeral=True)
            return

        # Check if the user has the Recruiter role:
        recruiter_role = interaction.guild.get_role(RECRUITER_ID)
        if not recruiter_role or recruiter_role not in interaction.user.roles:
            await interaction.response.send_message("❌ Only recruiters can claim this application!", ephemeral=True)
            return

        # Update DB to mark the current user as the recruiter:
        updated = update_application_recruiter(str(interaction.channel.id), str(interaction.user.id))
        if updated:
            await interaction.response.send_message(
                f"✅ {interaction.user.mention} has claimed this application."
            )
        else:
            await interaction.response.send_message("❌ Failed to update recruiter in DB!", ephemeral=True)

#
# Toggle Application Status Command
#

    @app_commands.command(name="toggle_applications", description="Toggle applications for a region as OPEN or CLOSED.")
    @app_commands.describe(
        region="Select a region",
        status="Select the new status"
    )
    @app_commands.choices(
        region=[
            app_commands.Choice(name="EU", value="EU"),
            app_commands.Choice(name="NA", value="NA"),
            app_commands.Choice(name="SEA", value="SEA")
        ],
        status=[
            app_commands.Choice(name="Open", value="OPEN"),
            app_commands.Choice(name="Closed", value="CLOSED")
        ]
)
    async def toggle_applications(self, interaction: discord.Interaction, region: str = "EU", status: str = "OPEN"):
        # Since region and status are strings, just convert them to uppercase for consistency.
        region_val = region.upper()   # e.g. "EU", "NA", or "SEA"
        status_val = status.upper()     # "OPEN" or "CLOSED"

        # Update the region status in the database
        if update_region_status(region_val, status_val):
            # Re-create the application embed with updated statuses
            new_embed = create_application_embed()
            
            # Get the channel where the application embed is posted
            channel = self.bot.get_channel(APPLY_CHANNEL_ID)
            try:
                # Fetch the existing embed message using its stored ID
                msg = await channel.fetch_message(application_embed_message_id)
                # Edit the embed to show the new statuses
                await msg.edit(embed=new_embed)
            except Exception as e:
                log(f"Error editing application embed: {e}", level="error")
            
            activity_channel = self.bot.get_channel(ACTIVITY_CHANNEL_ID)
            if activity_channel:
                embed = create_user_activity_log_embed("recruitment", f"Application Status Change", interaction.user, f"User has changed {region_val} to {status_val}")
                await activity_channel.send(embed=embed)
            
            await interaction.response.send_message(
                f"Applications for **{region_val}** have been set to **{status_val}**.", ephemeral=True
            )
        else:
            await interaction.response.send_message("Failed to update region status.", ephemeral=True)

    @app_commands.command(name="app_stats", description="Show application statistics.")
    async def app_stats(self, interaction: discord.Interaction):
        guild = interaction.client.get_guild(GUILD_ID)
        recruiter_role = guild.get_role(RECRUITER_ID) if guild else None
        if not recruiter_role or recruiter_role not in interaction.user.roles:
            await interaction.response.send_message("❌ You do not have permission to use this command.", ephemeral=True)
            return
        stats = get_application_stats()
        embed = discord.Embed(title="Application Statistics", color=discord.Color.blue())
        embed.add_field(name="Accepted Applications", value=str(stats["accepted"]), inline=True)
        embed.add_field(name="Denied Applications", value=str(stats["denied"]), inline=True)
        embed.add_field(name="Withdrawn Applications", value=str(stats["withdrawn"]), inline=True)
        embed.add_field(name="Current Open Applications", value=str(stats["open"]), inline=True)
        await interaction.response.send_message(embed=embed, ephemeral=True)


    @app_commands.command(name="app_history", description="Show all application attempts for a user.")
    async def app_history(self, interaction: discord.Interaction, user_id: str):
        guild = interaction.client.get_guild(GUILD_ID)
        recruiter_role = guild.get_role(RECRUITER_ID) if guild else None
        if not recruiter_role or recruiter_role not in interaction.user.roles:
            await interaction.response.send_message("❌ You do not have permission to use this command.", ephemeral=True)
            return

        history = get_application_history(user_id)
        if not history:
            await interaction.response.send_message("No application history found for this user.", ephemeral=True)
            return

        lines = []
        # Define emoji mappings for each type and status.
        type_emojis = {
            "submission": "📥",
            "attempt": "🔍"
        }
        status_emojis = {
            "accepted": "✅",
            "denied": "❌",
            "withdrawn": "⚠️",
            "open": "🟢"
        }
        for entry in history:
            try:
                dt = datetime.fromisoformat(entry['timestamp'])
                formatted_time = dt.strftime("%Y-%m-%d %H:%M")
            except Exception:
                formatted_time = entry['timestamp']

            type_emoji = type_emojis.get(entry["type"], "")
            status_emoji = status_emojis.get(entry["status"].lower(), "")
            line = (
                f"{type_emoji} **{formatted_time}**\n"
                f"Type: *{entry['type'].capitalize()}*  |  Status: {status_emoji} **{entry['status'].capitalize()}**\n"
                f"Details: {entry['details']}\n"
                "────────────────────────"
            )
            lines.append(line)
        description = "\n".join(lines)

        embed = discord.Embed(
            title=f"📜 Application History for {user_id}",
            description=description,
            color=discord.Color.green()
        )
        embed.set_footer(text="Note: Timestamps are in local time (YYYY-MM-DD HH:MM).")
        await interaction.response.send_message(embed=embed, ephemeral=True)


async def setup(bot: commands.Bot):
    await bot.add_cog(RecruitmentCog(bot))
