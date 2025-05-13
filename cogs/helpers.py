import discord
from config_testing import *
import logging
import inspect
import aiosqlite
from datetime import datetime, timezone
from typing import Optional, Dict
DATABASE_FILE = "data.db"

# 1) Generate a log file name based on date/time
LOG_FILENAME = datetime.now().strftime("botlog_%Y-%m-%d_%H-%M-%S.log")

# 2) Configure the logging to write to that file
logging.basicConfig(
    filename=LOG_FILENAME,
    filemode="a",            # append to the file
    level=logging.INFO,      # or DEBUG, etc.
    format="%(asctime)s - %(message)s",  # We'll prepend module info ourselves below
    datefmt="%Y-%m-%d %H:%M:%S"
)

def log(message: str, level: str = "info"):
    # 1) Identify which file (module) called log()
    #    - we look one frame up in the stack
    caller_frame = inspect.stack()[1]
    caller_module = inspect.getmodule(caller_frame[0])
    module_name = caller_module.__name__ if caller_module else "UnknownModule"

    # 2) Build the final text with [module_name]
    full_msg = f"[{module_name}] {message}"

    # 3) Dispatch to the built-in logger with the chosen level
    if level.lower() == "error":
        logging.error(full_msg)
    elif level.lower() == "warning":
        logging.warning(full_msg)
    elif level.lower() == "debug":
        logging.debug(full_msg)
    else:
        logging.info(full_msg)

def is_in_correct_guild(interaction: discord.Interaction) -> bool:
    return interaction.guild_id == GUILD_ID

def create_user_activity_log_embed(type: str, action: str, user: discord.Member, details: str) -> discord.Embed:
    """
    Creates a styled embed for logging activity in the activity channel.
    
    :param type: A category or type of log (e.g. "User Activity")
    :param action: The specific action (e.g., "Accepted Application", "Removed Trainee", etc.)
    :param user: The discord.Member who performed the action.
    :param details: Additional details about the action.
    :return: A discord.Embed object.
    """
    if type == "recruitment":
        color = discord.Color.green()
        title = "📋 Recruitment Log"
    elif type == "playerlist":
        color = discord.Color.orange()
        title = "📊 Player List Log"
    elif type == "verification":
        title = "🔒 Verification Log"
        color = discord.Color.purple()
    elif type == "tickets":
        title = "🎫 Ticket Log"
        color = discord.Color.red()
    else:
        title = "📌 Activity Log"
        color = discord.Color.black()
    
    embed = discord.Embed(
        title=title,
        description="",
        color=color,
        timestamp=datetime.now()
    )

    embed.add_field(name="🛠 Action:", value=f"**{action}**", inline=False)
    embed.add_field(name="👤 Performed By:", value=f"{user.mention} ({user.display_name})", inline=True)
    embed.add_field(name="📄 Details:", value=f"{details}", inline=False)

    embed.set_footer(text="🔒 This log is visible only to team members.")
    # Use user.avatar.url if available; if not, fall back to default_avatar.url
    embed.set_thumbnail(url=user.avatar.url if user.avatar else user.default_avatar.url)

    return embed

async def init_stored_embeds_db():
    """
    Initialize the stored_embeds table in the database if it doesn't exist.
    """
    try:
        async with aiosqlite.connect(DATABASE_FILE) as db:
            await db.execute(
                """
                CREATE TABLE IF NOT EXISTS stored_embeds (
                    embed_key   TEXT PRIMARY KEY,
                    message_id  TEXT NOT NULL,
                    channel_id  TEXT NOT NULL
                )
                """
            )
            await db.commit()
        log("Stored embeds DB initialized successfully.")
    except Exception as e:
        log(f"Stored embeds DB Error: {e}", level="error")


async def get_stored_embed(embed_key: str) -> Optional[Dict]:
    async with aiosqlite.connect(DATABASE_FILE) as db:
        async with db.execute(
            "SELECT message_id, channel_id FROM stored_embeds WHERE embed_key = ?", (embed_key,)
        ) as cursor:
            row = await cursor.fetchone()
    return {"message_id": row[0], "channel_id": row[1]} if row else None

async def set_stored_embed(embed_key: str, message_id: int, channel_id: int):
    async with aiosqlite.connect(DATABASE_FILE) as db:
        await db.execute("""
            INSERT OR REPLACE INTO stored_embeds (embed_key, message_id, channel_id)
            VALUES (?,?,?)
        """, (embed_key, message_id, channel_id))
        await db.commit()

def remove_stored_embed(embed_key: str) -> bool:
    import sqlite3
    try:
        conn = sqlite3.connect(DATABASE_FILE)
        cursor = conn.cursor()
        cursor.execute("DELETE FROM stored_embeds WHERE embed_key = ?", (embed_key,))
        conn.commit()
        return cursor.rowcount > 0
    except Exception as e:
        log(f"DB Error (remove_stored_embed): {e}", level="error")
        return False
    finally:
        conn.close()
        return False

def d_timestamp(iso_str: str, style: str = "") -> str:
    """
    Convert an ISO-format datetime string into a Discord timestamp tag.

    :param iso_str: A datetime in ISO format, e.g. "2025-05-13T16:23:49.739179" or with offset "2025-05-13T16:23:49+02:00"
    :param style:  (optional) One of Discord’s formatting codes:
                   - 't' short time (15:23)
                   - 'T' long time (15:23:49)
                   - 'd' short date (13/05/2025)
                   - 'D' long date (13 May 2025)
                   - 'f' short date/time (13 May 2025 15:23)
                   - 'F' long date/time (Tuesday, 13 May 2025 15:23)
                   - 'R' relative (e.g. “2 hours ago”)
    :returns:     A string like `<t:unix_timestamp>` or `<t:unix_timestamp:style>`

    Example:
      iso_to_discord_timestamp("2025-05-13T16:23:49.739179", "f")
      → "<t:1747164229:f>"
    """
    # Parse ISO; handle Z-suffix as UTC
    try:
        dt = datetime.fromisoformat(iso_str)
    except ValueError:
        # fallback for trailing 'Z'
        dt = datetime.fromisoformat(iso_str.replace('Z', '+00:00'))
    # Assume UTC if no tzinfo
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    unix_ts = int(dt.timestamp())
    return f"<t:{unix_ts}:{style}>" if style else f"<t:{unix_ts}>"
