import discord
from config import *
import logging
import inspect
from datetime import datetime
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

def init_stored_embeds_db():
    import sqlite3
    try:
        conn = sqlite3.connect(DATABASE_FILE)
        cursor = conn.cursor()
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS stored_embeds (
                embed_key TEXT PRIMARY KEY,
                message_id TEXT NOT NULL,
                channel_id TEXT NOT NULL
            )
            """
        )
        conn.commit()
        log("Stored embeds DB initialized successfully.")
    except Exception as e:
        log(f"Stored embeds DB Error: {e}", level="error")
    finally:
        conn.close()
        
init_stored_embeds_db()


def set_stored_embed(embed_key: str, message_id: str, channel_id: str) -> bool:
    import sqlite3
    try:
        conn = sqlite3.connect(DATABASE_FILE)
        cursor = conn.cursor()
        cursor.execute(
            """
            INSERT OR REPLACE INTO stored_embeds (embed_key, message_id, channel_id)
            VALUES (?, ?, ?)
            """,
            (embed_key, message_id, channel_id)
        )
        conn.commit()
        return True
    except Exception as e:
        log(f"DB Error (set_stored_embed): {e}", level="error")
        return False
    finally:
        conn.close()

def get_stored_embed(embed_key: str) -> Optional[Dict]:
    import sqlite3
    try:
        conn = sqlite3.connect(DATABASE_FILE)
        cursor = conn.cursor()
        cursor.execute(
            "SELECT message_id, channel_id FROM stored_embeds WHERE embed_key = ?",
            (embed_key,)
        )
        row = cursor.fetchone()
        if row:
            return {"message_id": row[0], "channel_id": row[1]}
        return None
    except Exception as e:
        log(f"DB Error (get_stored_embed): {e}", level="error")
        return None
    finally:
        conn.close()

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
