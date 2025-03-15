# cogs/recruitment.py

import discord, asyncio, os, json, sqlite3, re, traceback, random
from discord import app_commands, ButtonStyle
from discord.ext import commands, tasks
from datetime import datetime, timedelta
from typing import Optional, Dict

# Adjust sys.path so config_testing.py (in the root) is found.
import sys
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from config_testing import (
    GUILD_ID, TRAINEE_NOTES_CHANNEL, CADET_NOTES_CHANNEL, TRAINEE_CHAT_CHANNEL,
    SWAT_CHAT_CHANNEL, TRAINEE_ROLE, CADET_ROLE, SWAT_ROLE_ID, OFFICER_ROLE_ID,
    RECRUITER_ID, LEADERSHIP_ID, EU_ROLE_ID, NA_ROLE_ID, SEA_ROLE_ID,
    TARGET_CHANNEL_ID, REQUESTS_CHANNEL_ID, TICKET_CHANNEL_ID, TOKEN_FILE,
    PLUS_ONE_EMOJI, MINUS_ONE_EMOJI, LEAD_BOT_DEVELOPER_ID, LEAD_BOT_DEVELOPER_EMOJI,
    INTEGRATIONS_MANAGER, RECRUITER_EMOJI, LEADERSHIP_EMOJI
)
from messages import trainee_messages, cadet_messages, welcome_to_swat, OPEN_TICKET_EMBED_TEXT
# Import only `log` from helpers to avoid redefining is_in_correct_guild
from cogs.helpers import log  

# -------------------------------
# Constants and Global Variables
# -------------------------------
APPLY_CHANNEL_ID = 1350481022734696468  # New channel for trainee applications
APPLY_EMBED_ID_FILE = "apply_embed.txt"  # File to store the persistent apply embed ID
pending_requests = {}       # For name change / other (old requests)
pending_applications = {}   # For trainee application requests (new)

# -------------------------------
# Database functions and initialization (extended)
# -------------------------------
DATABASE_FILE = "data.db"
REQUESTS_FILE = "requests.json"

def initialize_database():
    try:
        conn = sqlite3.connect(DATABASE_FILE)
        cursor = conn.cursor()
        # Extended table with new fields: age, ingame_level, primary_server, bans, and image_received.
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
                role_type TEXT NOT NULL CHECK(role_type IN ('trainee', 'cadet')),
                age TEXT,
                ingame_level TEXT,
                primary_server TEXT,
                bans TEXT,
                image_received INTEGER DEFAULT 0
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
              role_type: str, embed_id: Optional[str], ingame_name: str, user_id: str, region: str,
              age: Optional[str] = None, ingame_level: Optional[str] = None,
              primary_server: Optional[str] = None, bans: Optional[str] = None,
              image_received: int = 0) -> bool:
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
               (thread_id, recruiter_id, starttime, endtime, role_type, embed_id, ingame_name, user_id, region, age, ingame_level, primary_server, bans, image_received)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (thread_id, recruiter_id, start_str, end_str, role_type, embed_id, ingame_name, user_id, region,
             age, ingame_level, primary_server, bans, image_received)
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

def update_image_received(thread_id: str) -> bool:
    conn = None
    try:
        conn = sqlite3.connect(DATABASE_FILE)
        cursor = conn.cursor()
        cursor.execute("UPDATE entries SET image_received = 1 WHERE thread_id = ?", (thread_id,))
        conn.commit()
        log(f"Marked image received for thread_id={thread_id}")
        return True
    except sqlite3.Error as e:
        log(f"Database Error (update_image_received): {e}", level="error")
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
            """SELECT recruiter_id, starttime, endtime, role_type, embed_id, ingame_name, user_id, region, reminder_sent, age, ingame_level, primary_server, bans, image_received
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
                "reminder_sent": row[8],
                "age": row[9],
                "ingame_level": row[10],
                "primary_server": row[11],
                "bans": row[12],
                "image_received": row[13]
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

def load_requests():
    global pending_requests
    if os.path.exists(REQUESTS_FILE):
        try:
            with open(REQUESTS_FILE, "r") as f:
                pending_requests = json.load(f)
            log(f"Requests loaded from {REQUESTS_FILE}, total: {len(pending_requests)}")
        except (json.JSONDecodeError, IOError) as e:
            log(f"Error loading {REQUESTS_FILE}: {e}", level="error")
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
        log(f"Error saving {REQUESTS_FILE}: {e}", level="error")


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
    # This is the old embed for the TARGET_CHANNEL; note that the trainee role request button is removed.
    embed = discord.Embed(
        title="**Welcome to the SWAT Community!** 🎉🚔",
        description=(
            "📌 **Select the appropriate button below:**\n\n"
            "🔹 **Request Name Change** – Need to update your name? Press this button and enter your new name **without any SWAT tags!**\n\n"
            "🔹 **Request Other** – Want another role? Click here and type your request! We’ll handle the rest.\n\n"
            "⚠️ **Important:** Follow the instructions carefully to avoid delays."
        ),
        colour=0x008040
    )
    return embed

def is_in_correct_guild(interaction: discord.Interaction) -> bool:
    return interaction.guild_id == GUILD_ID

async def update_recruiters(bot: discord.Client):
    try:
        guild = bot.get_guild(GUILD_ID)
        if not guild:
            log("Guild not found for updating recruiters.", level="error")
            return
        recruiter_role = guild.get_role(RECRUITER_ID)
        if not recruiter_role:
            log("Recruiter role not found for updating recruiters.", level="error")
            return
        recruiters = []
        for member in guild.members:
            if recruiter_role in member.roles:
                recruiters.append({"name": member.display_name, "id": member.id})
        global RECRUITERS
        RECRUITERS = recruiters
        log(f"Updated recruiters list: {RECRUITERS}")
    except Exception as e:
        log(f"Error in update_recruiters: {e}", level="error")

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
            await interaction.followup.send("❌ Not a registered ticket/application thread!", ephemeral=True)
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
# MISSING CLASSES FIX
# -------------------------------
# Minimal placeholders so your old references won't break.

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

class RequestActionView(discord.ui.View):
    def __init__(self, user_id: int = None, request_type: str = None, ingame_name: str = None, recruiter: str = None, new_name: str = None, region: str = None):
        super().__init__(timeout=None)
        self.user_id = user_id
        self.request_type = request_type
        self.ingame_name = ingame_name
        self.new_name = new_name
        self.recruiter = recruiter
        self.region = region


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

# -------------------------------
# (1) Old views for name change and other
# -------------------------------
class TraineeView(discord.ui.View):
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

# (2) New persistent view for the APPLY embed.
class ApplyView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)
    
    @discord.ui.button(label="Apply", style=discord.ButtonStyle.primary, custom_id="apply_button")
    async def apply_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        user_id_str = str(interaction.user.id)
        if user_id_str in pending_applications:
            await interaction.response.send_message("❌ You already have an open application.", ephemeral=True)
            return
        await interaction.response.send_modal(ApplyModal())

# (3) Modal for trainee application (new)
class ApplyModal(discord.ui.Modal, title="Trainee Application"):
    ingame_name = discord.ui.TextInput(label="In-Game Name", placeholder="Enter your in-game name")
    age = discord.ui.TextInput(label="Age", placeholder="Enter your age")
    ingame_level = discord.ui.TextInput(label="In-Game Level", placeholder="Enter your in-game level")
    primary_server = discord.ui.TextInput(label="Primary Server", placeholder="Enter your primary server")
    bans = discord.ui.TextInput(label="Any server/job bans in the last 30 days?", placeholder="Yes/No or details", style=discord.TextStyle.long)
    
    async def on_submit(self, interaction: discord.Interaction):
        user_id_str = str(interaction.user.id)
        if user_id_str in pending_applications:
            await interaction.response.send_message("❌ You already have an application open.", ephemeral=True)
            return
        # Store application data temporarily
        pending_applications[user_id_str] = {
            "request_type": "trainee_application",
            "ingame_name": self.ingame_name.value,
            "age": self.age.value,
            "ingame_level": self.ingame_level.value,
            "primary_server": self.primary_server.value,
            "bans": self.bans.value
        }
        guild = interaction.client.get_guild(GUILD_ID)
        apply_channel = guild.get_channel(APPLY_CHANNEL_ID)
        if not apply_channel:
            await interaction.response.send_message("❌ Apply channel not found.", ephemeral=True)
            return
        thread_name = f"App - {interaction.user.name}"
        try:
            thread = await apply_channel.create_thread(
                name=thread_name,
                type=discord.ChannelType.private_thread,
                invitable=False,
                reason="New trainee application"
            )
        except discord.Forbidden:
            await interaction.response.send_message("❌ Bot lacks permission to create thread.", ephemeral=True)
            return
        except discord.HTTPException as e:
            await interaction.response.send_message(f"❌ HTTP Exception: {e}", ephemeral=True)
            return
        # Save thread ID in application data
        pending_applications[user_id_str]["thread_id"] = thread.id
        # Insert application data into DB (using endtime 24h later for reminder purposes)
        start_time = datetime.now()
        end_time = start_time + timedelta(days=1)
        add_ok = add_entry(
            thread_id=str(thread.id),
            recruiter_id="0",  # not yet claimed
            starttime=start_time,
            endtime=end_time,
            role_type="trainee",
            embed_id=None,
            ingame_name=self.ingame_name.value,
            user_id=user_id_str,
            region=self.primary_server.value,
            age=self.age.value,
            ingame_level=self.ingame_level.value,
            primary_server=self.primary_server.value,
            bans=self.bans.value
        )
        if not add_ok:
            await interaction.response.send_message("❌ Failed to store your application.", ephemeral=True)
            return
        # In the new thread, send an embed with buttons for Claim, Withdraw, and Close.
        embed = discord.Embed(
            title="New Trainee Application",
            description=(
                "Please post a screenshot of your newly requested ban history.\n\n"
                "If no image is posted within 24 hours, recruiters will be pinged automatically."
            ),
            color=0x0080ff
        )
        embed.add_field(name="In-Game Name", value=self.ingame_name.value, inline=True)
        embed.add_field(name="Age", value=self.age.value, inline=True)
        embed.add_field(name="In-Game Level", value=self.ingame_level.value, inline=True)
        embed.add_field(name="Primary Server", value=self.primary_server.value, inline=True)
        embed.add_field(name="Bans (last 30 days)", value=self.bans.value, inline=False)
        view = ApplicationThreadView(applicant_id=interaction.user.id)
        msg = await thread.send(content=f"<@{interaction.user.id}>", embed=embed, view=view)
        # Optionally update DB with the embed message ID if needed.
        pending_applications[user_id_str]["message_id"] = msg.id
        await interaction.response.send_message("✅ Your application has been submitted!", ephemeral=True)

# (4) View for the application thread with Claim, Withdraw, and Close buttons.
class ApplicationThreadView(discord.ui.View):
    def __init__(self, applicant_id: int):
        super().__init__(timeout=None)
        self.applicant_id = applicant_id
        self.claimed_by = None  # To store recruiter ID once claimed

    @discord.ui.button(label="Claim", style=discord.ButtonStyle.success, custom_id="app_claim_button")
    async def claim_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        recruiter_role = interaction.guild.get_role(RECRUITER_ID)
        if recruiter_role not in interaction.user.roles:
            await interaction.response.send_message("❌ You do not have permission to claim this application.", ephemeral=True)
            return
        self.claimed_by = interaction.user.id
        applicant = interaction.guild.get_member(self.applicant_id)
        if not applicant:
            await interaction.response.send_message("❌ Applicant not found.", ephemeral=True)
            return
        new_thread_name = f"{interaction.user.name} - {applicant.name}"
        try:
            await interaction.channel.edit(name=new_thread_name)
        except discord.Forbidden:
            await interaction.response.send_message("❌ Insufficient permissions to rename thread.", ephemeral=True)
            return
        embed = interaction.message.embeds[0]
        embed.add_field(name="Claimed by:", value=f"<@{interaction.user.id}>", inline=False)
        await interaction.message.edit(embed=embed, view=self)
        # Update the DB entry to record the recruiter who claimed it.
        conn = sqlite3.connect(DATABASE_FILE)
        cursor = conn.cursor()
        cursor.execute("UPDATE entries SET recruiter_id = ? WHERE thread_id = ?", (str(interaction.user.id), str(interaction.channel.id)))
        conn.commit()
        conn.close()
        await interaction.response.send_message("✅ Application claimed.", ephemeral=True)

    @discord.ui.button(label="Withdraw", style=discord.ButtonStyle.secondary, custom_id="app_withdraw_button")
    async def withdraw_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.applicant_id:
            await interaction.response.send_message("❌ Only the applicant can withdraw their application.", ephemeral=True)
            return
        embed = discord.Embed(
            title="Application Withdrawn",
            description=f"<@{self.applicant_id}> has withdrawn their application.",
            color=0xffa500
        )
        await interaction.response.send_message(embed=embed)
        await close_thread(interaction, interaction.channel)

    @discord.ui.button(label="Close", style=discord.ButtonStyle.danger, custom_id="app_close_button")
    async def close_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.applicant_id and (interaction.guild.get_role(RECRUITER_ID) not in interaction.user.roles):
            await interaction.response.send_message("❌ You do not have permission to close this thread.", ephemeral=True)
            return
        embed = discord.Embed(title="Application Closed", description="This application thread has been closed.", color=0xff0000)
        await interaction.response.send_message(embed=embed)
        await close_thread(interaction, interaction.channel)

# (5) Modal for recruiters to provide denial reason (new)
class AppDenyModal(discord.ui.Modal, title="Application Denial Reason"):
    reason = discord.ui.TextInput(label="Reason for denial", style=discord.TextStyle.long, placeholder="Enter reason")
    reapply = discord.ui.TextInput(label="Can reapply? (Yes/No)", placeholder="Yes or No")
    def __init__(self, applicant_id: int):
        super().__init__()
        self.applicant_id = applicant_id
    async def on_submit(self, interaction: discord.Interaction):
        user = interaction.client.get_user(self.applicant_id)
        if user:
            try:
                await user.send(
                    f"Your trainee application has been **denied**.\n"
                    f"Reason: ```{self.reason.value}```\n"
                    f"Reapply allowed: ```{self.reapply.value}```"
                )
            except discord.Forbidden:
                log(f"Could not DM user {self.applicant_id}; DMs might be blocked.", level="error")
        else:
            await interaction.response.send_message("❌ Applicant not found.", ephemeral=True)
        embed = discord.Embed(
            title="Application Denied",
            description=f"Application has been denied by <@{interaction.user.id}>",
            color=0xff0000
        )
        embed.add_field(name="Reason", value=f"```{self.reason.value}```", inline=False)
        embed.add_field(name="Reapply allowed", value=f"```{self.reapply.value}```", inline=False)
        await interaction.response.send_message(embed=embed)
        await close_thread(interaction, interaction.channel)

# -------------------------------
# New App Commands for trainee applications
# -------------------------------
class RecruitmentCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.bot.loop.create_task(self._wait_and_start())

    async def _wait_and_start(self):
        await self.bot.wait_until_ready()
        load_requests()
        self.bot.add_view(TraineeView())
        self.bot.add_view(RequestActionView())
        self.bot.add_view(CloseThreadView())
        self.bot.add_view(ApplyView())  # New persistent view for the apply embed
        # Load embed message IDs from file(s)
        global embed_message_id
        if os.path.exists("embed.txt"):
            try:
                with open("embed.txt", "r") as f:
                    data = f.read().strip()
                    if data.isdigit():
                        embed_message_id = int(data)
                        log(f"Loaded embed_message_id: {embed_message_id}")
                    else:
                        log("Invalid data in embed.txt.")
                        embed_message_id = None
            except (ValueError, IOError) as e:
                log(f"Error reading embed.txt: {e}")
                embed_message_id = None
        global apply_embed_message_id
        if os.path.exists(APPLY_EMBED_ID_FILE):
            try:
                with open(APPLY_EMBED_ID_FILE, "r") as f:
                    data = f.read().strip()
                    if data.isdigit():
                        apply_embed_message_id = int(data)
                        log(f"Loaded apply_embed_message_id: {apply_embed_message_id}")
                    else:
                        log("Invalid data in apply_embed.txt.")
                        apply_embed_message_id = None
            except (ValueError, IOError) as e:
                log(f"Error reading {APPLY_EMBED_ID_FILE}: {e}")
                apply_embed_message_id = None
        # Start tasks
        self.check_embed_task.start()
        self.check_apply_embed_task.start()
        self.update_recruiters_task.start()
        self.check_expired_endtimes_task.start()
        await self.load_existing_tickets()
        log("RecruitmentCog setup complete. All tasks started.")

    def cog_unload(self):
        self.check_embed_task.cancel()
        self.check_apply_embed_task.cancel()
        self.update_recruiters_task.cancel()
        self.check_expired_endtimes_task.cancel()

    @tasks.loop(minutes=5)
    async def check_embed_task(self):
        # For the old embed in TARGET_CHANNEL
        global embed_message_id
        try:
            channel = self.bot.get_channel(TARGET_CHANNEL_ID)
            if channel:
                if embed_message_id:
                    try:
                        await channel.fetch_message(embed_message_id)
                    except discord.NotFound:
                        embed = create_embed()
                        view = TraineeView()
                        msg = await channel.send(embed=embed, view=view)
                        embed_message_id = msg.id
                        with open("embed.txt", "w") as f:
                            f.write(str(embed_message_id))
                        log(f"Embed not found; sent new embed with ID: {embed_message_id}")
                    except discord.Forbidden:
                        log("Bot lacks permission to fetch messages in TARGET_CHANNEL.", level="error")
                    except discord.HTTPException as e:
                        log(f"Failed to fetch message: {e}", level="error")
                else:
                    embed = create_embed()
                    view = TraineeView()
                    msg = await channel.send(embed=embed, view=view)
                    embed_message_id = msg.id
                    with open("embed.txt", "w") as f:
                        f.write(str(embed_message_id))
                    log(f"Created new embed with ID: {embed_message_id}")
        except Exception as e:
            log(f"Error in check_embed_task: {e}", level="error")

    @tasks.loop(minutes=5)
    async def check_apply_embed_task(self):
        # New task: Ensure the apply embed exists in the APPLY_CHANNEL.
        global apply_embed_message_id
        try:
            channel = self.bot.get_channel(APPLY_CHANNEL_ID)
            if channel:
                if apply_embed_message_id:
                    try:
                        await channel.fetch_message(apply_embed_message_id)
                    except discord.NotFound:
                        embed = discord.Embed(
                            title="Apply for Trainee Role",
                            description="Click the **Apply** button below to start your trainee application.",
                            color=0x0099ff
                        )
                        view = ApplyView()
                        msg = await channel.send(embed=embed, view=view)
                        apply_embed_message_id = msg.id
                        with open(APPLY_EMBED_ID_FILE, "w") as f:
                            f.write(str(apply_embed_message_id))
                        log(f"Apply embed not found; sent new apply embed with ID: {apply_embed_message_id}")
                    except discord.Forbidden:
                        log("Bot lacks permission to fetch messages in APPLY_CHANNEL.", level="error")
                    except discord.HTTPException as e:
                        log(f"Failed to fetch apply message: {e}", level="error")
                else:
                    embed = discord.Embed(
                        title="Apply for Trainee Role",
                        description="Click the **Apply** button below to start your trainee application.",
                        color=0x0099ff
                    )
                    view = ApplyView()
                    msg = await channel.send(embed=embed, view=view)
                    apply_embed_message_id = msg.id
                    with open(APPLY_EMBED_ID_FILE, "w") as f:
                        f.write(str(apply_embed_message_id))
                    log(f"Created new apply embed with ID: {apply_embed_message_id}")
        except Exception as e:
            log(f"Error in check_apply_embed_task: {e}", level="error")

    @tasks.loop(minutes=10)
    async def update_recruiters_task(self):
        await update_recruiters(self.bot)

    @tasks.loop(minutes=1)
    async def check_expired_endtimes_task(self):
        # This task checks for expired threads (both voting and application threads)
        conn = None
        try:
            conn = sqlite3.connect(DATABASE_FILE)
            cursor = conn.cursor()
            now = datetime.now()
            cursor.execute(
                """
                SELECT thread_id, recruiter_id, starttime, role_type, region, ingame_name, reminder_sent, image_received, user_id
                FROM entries 
                WHERE endtime <= ? AND reminder_sent = 0
                """,
                (now.isoformat(),)
            )
            expired_entries = cursor.fetchall()
            for thread_id, recruiter_id, starttime, role_type, region, ingame_name, reminder_sent, image_received, app_user_id in expired_entries:
                thread = self.bot.get_channel(int(thread_id)) if str(thread_id).isdigit() else None
                if thread and isinstance(thread, discord.Thread):
                    try:
                        start_time = datetime.fromisoformat(starttime)
                    except ValueError:
                        log(f"Error parsing starttime: {starttime}", level="error")
                        continue
                    # For application threads (trainee applications), if no image was posted, ping recruiters.
                    if role_type == "trainee" and image_received == 0:
                        await thread.send(f"<@{RECRUITER_ID}> Reminder: No ban history screenshot has been posted yet by <@{app_user_id}>.")
                    else:
                        # For cadet threads, send a reminder or start a new voting embed if desired.
                        if role_type == "cadet":
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
        # For recruitment, if you need to load active requests/applications, do so here.
        pass

    @app_commands.command(name="app_claim", description="Claim the current trainee application (alternative to button)")
    async def app_claim(self, interaction: discord.Interaction):
        # Simulate the claim button press in the application thread.
        if not isinstance(interaction.channel, discord.Thread):
            await interaction.response.send_message("❌ This command must be used in an application thread.", ephemeral=True)
            return
        recruiter_role = interaction.guild.get_role(RECRUITER_ID)
        if recruiter_role not in interaction.user.roles:
            await interaction.response.send_message("❌ You do not have permission to claim this application.", ephemeral=True)
            return
        new_thread_name = f"{interaction.user.name} - {interaction.channel.name.split(' - ')[-1]}"
        try:
            await interaction.channel.edit(name=new_thread_name)
        except discord.Forbidden:
            await interaction.response.send_message("❌ Insufficient permissions to rename thread.", ephemeral=True)
            return
        # Update DB with recruiter info:
        conn = sqlite3.connect(DATABASE_FILE)
        cursor = conn.cursor()
        cursor.execute("UPDATE entries SET recruiter_id = ? WHERE thread_id = ?", (str(interaction.user.id), str(interaction.channel.id)))
        conn.commit()
        conn.close()
        await interaction.response.send_message("✅ Application claimed via command.", ephemeral=True)

    @app_commands.command(name="app_accept", description="Accept the trainee application")
    async def app_accept(self, interaction: discord.Interaction):
        recruiter_role = interaction.guild.get_role(RECRUITER_ID)
        if recruiter_role not in interaction.user.roles:
            await interaction.response.send_message("❌ You do not have permission.", ephemeral=True)
            return
        data = get_entry(str(interaction.channel.id))
        if not data:
            await interaction.response.send_message("❌ Application data not found.", ephemeral=True)
            return
        embed = interaction.message.embeds[0] if interaction.message and interaction.message.embeds else discord.Embed()
        embed.title = "Application Approved"
        embed.color = discord.Color.green()
        embed.add_field(name="Handled by:", value=f"<@{interaction.user.id}>", inline=False)
        await interaction.response.send_message(embed=embed)
        guild = interaction.guild
        member = guild.get_member(int(data["user_id"]))
        if member:
            trainee_role_obj = guild.get_role(TRAINEE_ROLE)
            try:
                await member.add_roles(trainee_role_obj)
            except Exception as e:
                await interaction.followup.send(f"Error assigning role: {e}", ephemeral=True)
        await close_thread(interaction, interaction.channel)

    @app_commands.command(name="app_deny", description="Deny the trainee application and timeout for 7 days")
    async def app_deny(self, interaction: discord.Interaction):
        recruiter_role = interaction.guild.get_role(RECRUITER_ID)
        if recruiter_role not in interaction.user.roles:
            await interaction.response.send_message("❌ You do not have permission.", ephemeral=True)
            return
        data = get_entry(str(interaction.channel.id))
        if not data:
            await interaction.response.send_message("❌ Application data not found.", ephemeral=True)
            return
        # Here you could also add code to mark the user as timed out for 7 days.
        await interaction.response.send_modal(AppDenyModal(applicant_id=int(data["user_id"])))

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
            log(f"Member with ID {data['user_id']} not found in guild (they may have left).", level="warning")
        embed = discord.Embed(
            title="❌ " + str(data["ingame_name"]) + " has been removed!",
            colour=0xf94144
        )
        embed.set_footer(text="🔒This thread is locked now!")
        await interaction.followup.send(embed=embed)

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


async def setup(bot: commands.Bot):
    await bot.add_cog(RecruitmentCog(bot))


# -------------------------------
# Event Listener: Detect images in application threads
# -------------------------------
class RecruitmentListener(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
    
    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        # Only consider messages in threads that are trainee application threads.
        if message.author.bot:
            return
        if isinstance(message.channel, discord.Thread):
            data = get_entry(str(message.channel.id))
            if data and data["role_type"] == "trainee":
                # Check if the message is from the applicant.
                if int(data["user_id"]) == message.author.id:
                    # Check if any attachment is an image.
                    if message.attachments:
                        for attachment in message.attachments:
                            if attachment.content_type and attachment.content_type.startswith("image/"):
                                # Mark image as received.
                                if data["image_received"] == 0:
                                    update_image_received(str(message.channel.id))
                                    await message.channel.send(f"<@{RECRUITER_ID}> An image has been posted by <@{message.author.id}>.")
                                break

async def setup_listener(bot: commands.Bot):
    await bot.add_cog(RecruitmentListener(bot))
