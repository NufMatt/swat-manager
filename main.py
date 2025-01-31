import discord
from discord import app_commands, ButtonStyle
from discord.ext import commands, tasks
import asyncio
import os
import json
from datetime import datetime, timedelta
import sqlite3
from typing import Optional, Dict
import re
from messages import trainee_messages, cadet_messages, welcome_to_swat
import random
from config import GUILD_ID, TRAINEE_NOTES_CHANNEL, CADET_NOTES_CHANNEL, TRAINEE_CHAT_CHANNEL, SWAT_CHAT_CHANNEL, TRAINEE_ROLE, CADET_ROLE, SWAT_ROLE_ID, OFFICER_ROLE_ID, RECRUITER_ID, LEADERSHIP_ID, EU_ROLE_ID, NA_ROLE_ID, SEA_ROLE_ID, TARGET_CHANNEL_ID, REQUESTS_CHANNEL_ID, TICKET_CHANNEL_ID

# --------------------------------------
#               CONSTANTS
# --------------------------------------
DATABASE_FILE = "data.db"
EMBED_ID_FILE = "embed.txt"
REQUESTS_FILE = "requests.json"
EMBED_FILE   = "tickets_embed.json"

# --------------------------------------
#      DATABASE SETUP RECRUITMENT
# --------------------------------------
def initialize_database():
    """Initialize the SQLite database and create the entries table if it doesn't exist."""
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
    except sqlite3.Error as e:
        print(f"❌ Database Initialization Error: {e}")
    finally:
        conn.close()

initialize_database()

def add_entry(thread_id: str, recruiter_id: str, starttime: datetime, endtime: datetime, 
              role_type: str, embed_id: str, ingame_name: str, user_id: str, region: str) -> bool:
    """Add a new entry to the database."""
    if role_type not in ("trainee", "cadet"):
        raise ValueError("role_type must be either 'trainee' or 'cadet'.")

    start_str = starttime.isoformat()  # store as 2025-01-31T12:34:56.789012
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
        return True
    except sqlite3.IntegrityError:
        print("❌ Database Error: Duplicate thread_id or other integrity issue.")
        return False
    except sqlite3.Error as e:
        print(f"❌ Database Error (add_entry): {e}")
        return False
    finally:
        if conn:
            conn.close()

def remove_entry(thread_id: str) -> bool:
    """Remove an entry from the database based on thread_id."""
    conn = None
    try:
        conn = sqlite3.connect(DATABASE_FILE)
        cursor = conn.cursor()
        cursor.execute("DELETE FROM entries WHERE thread_id = ?", (thread_id,))
        conn.commit()
        rows_deleted = cursor.rowcount
        return rows_deleted > 0
    except sqlite3.Error as e:
        print(f"❌ Database Error (remove_entry): {e}")
        return False
    finally:
        if conn:
            conn.close()

def update_endtime(thread_id: str, new_endtime: datetime) -> bool:
    """Update the endtime of an existing entry."""
    conn = None
    try:
        conn = sqlite3.connect(DATABASE_FILE)
        cursor = conn.cursor()
        cursor.execute("UPDATE entries SET endtime = ? WHERE thread_id = ?", (str(new_endtime), thread_id))
        conn.commit()
        rows_updated = cursor.rowcount
        return rows_updated > 0
    except sqlite3.Error as e:
        print(f"❌ Database Error (update_endtime): {e}")
        return False
    finally:
        if conn:
            conn.close()

def get_entry(thread_id: str) -> Optional[Dict]:
    """Retrieve an entry for a specific thread."""
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
        print(f"❌ Database Error (get_entry): {e}")
        return None
    finally:
        if conn:
            conn.close()

def is_user_in_database(user_id: int) -> bool:
    """Check if a user is already in the database."""
    conn = None
    try:
        conn = sqlite3.connect(DATABASE_FILE)
        cursor = conn.cursor()
        # Convert user_id to string in parameter to match DB storage
        cursor.execute(
            """SELECT 1 FROM entries 
               WHERE user_id = ?
               LIMIT 1""",
            (str(user_id),)
        )
        result = cursor.fetchone()
        return result is not None
    except sqlite3.Error as e:
        print(f"❌ Database Error (is_user_in_database): {e}")
        return False
    finally:
        if conn:
            conn.close()

# -----------------------
# DATABASE SETUP TICKET
# -----------------------
def init_ticket_db():
    conn = sqlite3.connect("tickets.db")
    cur = conn.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS tickets (
            thread_id TEXT PRIMARY KEY,
            user_id   TEXT NOT NULL,
            created_at TEXT NOT NULL,
            ticket_type TEXT NOT NULL
        )
    """)
    conn.commit()
    conn.close()

def add_ticket(thread_id: str, user_id: str, created_at: str, ticket_type: str):
    conn = sqlite3.connect("tickets.db")
    cur = conn.cursor()
    cur.execute("""
        INSERT INTO tickets (thread_id, user_id, created_at, ticket_type)
        VALUES (?, ?, ?, ?)
    """, (thread_id, user_id, created_at, ticket_type))
    conn.commit()
    conn.close()

def get_ticket_info(thread_id: str):
    conn = sqlite3.connect("tickets.db")
    cur = conn.cursor()
    cur.execute("""
        SELECT thread_id, user_id, created_at, ticket_type FROM tickets
        WHERE thread_id = ?
    """, (thread_id,))
    row = cur.fetchone()
    conn.close()
    return row  # (thread_id, user_id, created_at, ticket_type)

def remove_ticket(thread_id: str):
    conn = sqlite3.connect("tickets.db")
    cur = conn.cursor()
    cur.execute("DELETE FROM tickets WHERE thread_id = ?", (thread_id,))
    conn.commit()
    conn.close()

init_ticket_db()
# --------------------------------------
#            BOT SETUP
# --------------------------------------
intents = discord.Intents.default()
intents.members = True
intents.guilds = True

bot = commands.Bot(command_prefix="!", intents=intents)

# Store the embed message ID for checking
embed_message_id = None  

# --------------------------------------
#          REQUESTS MANAGEMENT
# --------------------------------------
pending_requests = {}  # key: str(user_id), value: dict with request info

def load_requests():
    """Load pending requests from the JSON file into memory."""
    global pending_requests
    if os.path.exists(REQUESTS_FILE):
        try:
            with open(REQUESTS_FILE, "r") as f:
                pending_requests = json.load(f)
        except (json.JSONDecodeError, IOError) as e:
            print(f"❌ Error loading requests.json: {e}")
            pending_requests = {}
    else:
        pending_requests = {}

def save_requests():
    """Save current pending requests dictionary to disk."""
    try:
        with open(REQUESTS_FILE, "w") as f:
            json.dump(pending_requests, f)
    except IOError as e:
        print(f"❌ Error saving requests.json: {e}")

# --------------------------------------
#         HELPER FUNCTIONS
# --------------------------------------
def get_rounded_time() -> datetime:
    """Return the current time, rounded up to the nearest 15 minutes."""
    now = datetime.now()
    minutes_to_add = (15 - now.minute % 15) % 15
    return now + timedelta(minutes=minutes_to_add)

def create_discord_timestamp(dt_obj: datetime) -> str:
    """Convert datetime object to a Discord <t:...> timestamp string."""
    unix_timestamp = int(dt_obj.timestamp())
    return f"<t:{unix_timestamp}>"

def create_embed() -> discord.Embed:
    """Create the main management embed with buttons."""
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

async def update_recruiters():
    """Update the list of recruiters from the guild."""
    try:
        guild = bot.get_guild(GUILD_ID)
        if not guild:
            print("❌ Guild not found for updating recruiters.")
            return

        recruiter_role = guild.get_role(RECRUITER_ID)
        if not recruiter_role:
            print("❌ Recruiter role not found for updating recruiters.")
            return

        recruiters = []
        for member in guild.members:
            if recruiter_role in member.roles:
                recruiters.append({
                    "name": member.display_name,
                    "id": member.id
                })

        global RECRUITERS
        RECRUITERS = recruiters
        print("✅ Updated recruiters list:", RECRUITERS)
    except Exception as e:
        print(f"❌ Error in update_recruiters: {e}")

async def set_user_nickname(member: discord.Member, role_label: str, username: str = None):
    """Remove any trailing [TRAINEE/Cadet/SWAT] bracketed text and set the new bracket."""
    try:
        base_nick = member.nick if member.nick else member.name
        temp_name = re.sub(r'(?:\s*\[(?:CADET|TRAINEE|SWAT)\])+$', '', base_nick, flags=re.IGNORECASE)
        await member.edit(nick=f"{temp_name} [{role_label.upper()}]")
    except discord.Forbidden:
        print(f"❌ Forbidden: Cannot change nickname for {member.id}")
    except discord.HTTPException as e:
        print(f"❌ HTTPException changing nickname for {member.id}: {e}")

async def close_thread(interaction: discord.Interaction, thread: discord.Thread) -> None:
    """Remove DB entry for the thread, lock & archive it."""
    try:
        result = remove_entry(thread.id)
        if result:
            try:
                await thread.edit(locked=True, archived=True)
            except discord.Forbidden:
                await interaction.followup.send("❌ Bot lacks permission to lock/archive this thread.", ephemeral=True)
            except discord.HTTPException as e:
                await interaction.followup.send(f"❌ Error archiving thread: {e}", ephemeral=True)
        else:
            await interaction.followup.send("❌ Not a registered voting thread!", ephemeral=True)
    except Exception as e:
        await interaction.followup.send(f"❌ Error closing thread: {e}", ephemeral=True)

async def create_voting_embed(start_time, end_time, recruiter: int, region, ingame_name, extended: bool = False) -> discord.Embed:
    """Create the standard voting embed with plus/minus/uncertain reactions."""
    try:
        if not isinstance(start_time, datetime):
            start_time = datetime.strptime(str(start_time), "%Y-%m-%d %H:%M:%S.%f")
        if not isinstance(end_time, datetime):
            end_time = datetime.strptime(str(end_time), "%Y-%m-%d %H:%M:%S.%f")

        embed = discord.Embed(
            description=(
                "SWAT, please express your vote below.\n"
                "Use <:plus_one:1334498534187208714>, ❔, or <:minus_one:1334498485390544989> accordingly."
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
        print(f"❌ Error in create_voting_embed: {e}")
        return discord.Embed(description="❌ Error creating voting embed.", color=0xff0000)

# --------------------------------------
#   PERSISTENT VIEW & RELATED CLASSES -> THREAD MANAGMENT
# --------------------------------------
class TraineeView(discord.ui.View):
    """Persistent view for the main management embed buttons."""
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="Request Trainee Role", style=discord.ButtonStyle.primary, custom_id="request_trainee_role")
    async def request_trainee_role(self, interaction: discord.Interaction, button: discord.ui.Button):
        user_id_str = str(interaction.user.id)
        
        # Checks
        if user_id_str in pending_requests:
            await interaction.response.send_message("❌ You already have an open request.", ephemeral=True)
            return
        if any(r.id == SWAT_ROLE_ID for r in interaction.user.roles):
            await interaction.response.send_message("❌ You are already SWAT!", ephemeral=True)
            return
        if any(r.id in [TRAINEE_ROLE, CADET_ROLE] for r in interaction.user.roles):
            await interaction.response.send_message("❌ You already have a trainee/cadet role!", ephemeral=True)
            return

        await interaction.response.send_modal(TraineeRoleModal())

    @discord.ui.button(label="Request Name Change", style=discord.ButtonStyle.secondary, custom_id="request_name_change")
    async def request_name_change(self, interaction: discord.Interaction, button: discord.ui.Button):
        user_id_str = str(interaction.user.id)
        
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

class RequestActionView(discord.ui.View):
    """View with Accept/Ignore buttons for new request embed."""
    def __init__(self, user_id: int = None, request_type: str = None, ingame_name: str = None, recruiter: str = None, new_name: str = None, region: str = None):
        super().__init__(timeout=None)
        self.user_id      = user_id
        self.request_type = request_type
        self.ingame_name  = ingame_name
        self.new_name     = new_name
        self.recruiter    = recruiter
        self.region       = region

    @discord.ui.button(label="Accept", style=discord.ButtonStyle.success, custom_id="request_accept")
    async def accept_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        recruiter_role = interaction.guild.get_role(RECRUITER_ID)
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

            # Remove from pending requests
            user_id_str = str(self.user_id)
            if user_id_str in pending_requests:
                del pending_requests[user_id_str]
                save_requests()

            # If it's a trainee request:
            if self.request_type == "trainee_role":
                guild = bot.get_guild(GUILD_ID)
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
                        end_time   = start_time + timedelta(days=7)  # For demonstration
                        thread_name= f"{self.ingame_name} | TRAINEE Notes"
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

                        voting_embed = await create_voting_embed(start_time, end_time, interaction.user.id, self.region, self.ingame_name)
                        embed_msg = await thread.send(embed=voting_embed)
                        await embed_msg.add_reaction("<:plus_one:1334498534187208714>")
                        await embed_msg.add_reaction("❔")
                        await embed_msg.add_reaction("<:minus_one:1334498485390544989>")

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
            if user_id_str in pending_requests:
                del pending_requests[user_id_str]
                save_requests()

        except Exception as e:
            await interaction.response.send_message(f"❌ Error ignoring request: {e}", ephemeral=True)

    @discord.ui.button(label="Deny w/Reason", style=discord.ButtonStyle.danger, custom_id="request_deny_reason")
    async def deny_with_reason(self, interaction: discord.Interaction, button: discord.ui.Button):
        """Opens a modal so the recruiter/leadership can specify a reason and DM the user."""
        # 1) Check role/permission if you want
        recruiter_role = interaction.guild.get_role(RECRUITER_ID)
        leadership_role = interaction.guild.get_role(LEADERSHIP_ID)
        # Example logic: If it's a name change or "other" request, only leadership can deny with reason:
        if self.request_type in ["name_change", "other"]:
            if not leadership_role or (leadership_role not in interaction.user.roles):
                await interaction.response.send_message("❌ You do not have permission to deny this request.", ephemeral=True)
                return
        else:
            # For a trainee request, a recruiter might deny
            if not recruiter_role or (recruiter_role not in interaction.user.roles):
                await interaction.response.send_message("❌ You do not have permission to deny this request.", ephemeral=True)
                return

            updated_embed = interaction.message.embeds[0]
            updated_embed.color = discord.Color.red()
            updated_embed.title += " (Denied with reason)"
            updated_embed.add_field(name="Ignored by:", value=f"<@{interaction.user.id}>", inline=False)
            # updated_embed.add_field(name="Reason:", value=f"```{reason}```")
            await interaction.message.edit(embed=updated_embed, view=None)

            user_id_str = str(self.user_id)
            if user_id_str in pending_requests:
                del pending_requests[user_id_str]
                save_requests()
        modal = DenyReasonModal(self.user_id)
        await interaction.response.send_modal(modal)

class CloseThreadView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="Close Thread", style=discord.ButtonStyle.danger, custom_id="close_thread")
    async def close_thread_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        thread = interaction.channel
        user = interaction.user

        # Optional: Restrict who can close the thread (e.g., only the ticket creator or specific roles)
        # Example: Only the user who opened the ticket can close it
        ticket_data = get_ticket_info(str(thread.id))
        if not ticket_data:
            await interaction.response.send_message("❌ No ticket data found for this thread.", ephemeral=True)
            return

        if interaction.user.id != int(ticket_data[1]):  # Assuming ticket_data[1] is user_id
            await interaction.response.send_message("❌ You do not have permission to close this thread.", ephemeral=True)
            return

        try:
            ticket_data = get_ticket_info(str(interaction.channel.id))
            if not ticket_data:
                await interaction.response.send_message("❌ This thread is not a registered ticket.", ephemeral=True)
                return

            remove_ticket(str(thread.id))
            embed = discord.Embed(title=f"Ticket closed by {interaction.user.nick}",
                      colour=0xf51616)
            embed.set_footer(text="🔒This ticket is locked now!")
            await interaction.response.send_message(embed=embed)
            await interaction.channel.edit(locked=True, archived=True)
    
        except discord.Forbidden:
            await interaction.response.send_message("❌ I don't have permission to close this thread.", ephemeral=True)
        except discord.HTTPException as e:
            await interaction.response.send_message(f"❌ Failed to close thread: {e}", ephemeral=True)
        except Exception as e:
            await interaction.response.send_message(f"❌ An unexpected error occurred: {e}", ephemeral=True)

# Command to add a trainee
@app_commands.describe(
    user_id="User's Discord ID",
    ingame_name="Exact in-game name",
    region="Region of the user (NA, EU, or SEA)",
    role_type="What role"
)
@app_commands.choices(
    region=[
        app_commands.Choice(name="NA", value="NA"),
        app_commands.Choice(name="EU", value="EU"),
        app_commands.Choice(name="SEA", value="SEA")
    ],
    role_type=[
        app_commands.Choice(name="cadet", value="cadet"),
        app_commands.Choice(name="trainee", value="trainee")
    ]
)
@bot.tree.command(name="force_add", description="Manually add an existing trainee / cadet thread to the database!")
async def force_add(
    interaction: discord.Interaction, 
    user_id: str, 
    ingame_name: str, 
    region: app_commands.Choice[str], 
    role_type: app_commands.Choice[str]
):
    """Forcibly add a user as trainee or cadet, linking this thread to the DB."""
    try:
        thread = interaction.channel
        user_id_int = int(user_id)
        recruiter_role = interaction.guild.get_role(RECRUITER_ID)
        if not recruiter_role or (recruiter_role not in interaction.user.roles):
            await interaction.response.send_message("❌ You do not have permission to use this command.", ephemeral=True)
            return
        
        selected_region = region.value
        selected_role = role_type.value
        start_time = get_rounded_time()
        end_time   = start_time + timedelta(days=7)

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

@bot.tree.command(name="list_requests", description="Lists the currently stored pending requests.")
async def list_requests(interaction: discord.Interaction):
    leadership_role = interaction.guild.get_role(LEADERSHIP_ID)
    if not leadership_role or (leadership_role not in interaction.user.roles):
        await interaction.response.send_message("❌ You do not have permission to list requests.", ephemeral=True)
        return

    await interaction.response.defer(ephemeral=True)  

    if not pending_requests:
        await interaction.followup.send("There are **no** pending requests at the moment.", ephemeral=True)
        return

    # Build a display of all requests
    lines = []
    for user_id_str, request_data in pending_requests.items():
        req_type = request_data.get("request_type", "N/A")
        detail   = ""

        # For extra clarity, you can pull more fields depending on the request type:
        if req_type == "trainee_role":
            detail = f"InGame Name: {request_data.get('ingame_name', 'Unknown')}, Region: {request_data.get('region', 'Not Selected')}"
        elif req_type == "name_change":
            detail = f"New Name: {request_data.get('new_name', 'Unknown')}"
        elif req_type == "other":
            detail = f"Request: {request_data.get('other', 'No details')}"

        # Format a line for this user/request
        lines.append(f"• **User ID**: {user_id_str} | **Type**: `{req_type}` | {detail}")

    # Join the lines; note the 2000-char limit. If large, chunk them into multiple messages.
    reply_text = "\n".join(lines)
    await interaction.followup.send(f"**Current Pending Requests:**\n\n{reply_text}", ephemeral=True)

@bot.tree.command(name="clear_requests", description="Clears the entire pending requests list.")
async def clear_requests(interaction: discord.Interaction):
    leadership_role = interaction.guild.get_role(LEADERSHIP_ID)
    if not leadership_role or (leadership_role not in interaction.user.roles):
        await interaction.response.send_message("❌ You do not have permission to clear requests.", ephemeral=True)
        return

    # Clear everything
    pending_requests.clear()
    save_requests()  # Writes the now-empty dictionary to requests.json

    # FIXED HERE: use a normal send_message instead of followup
    await interaction.response.send_message("✅ All pending requests have been **cleared**!", ephemeral=True)

# -----------------------
# PERSISTENT VIEW
# -----------------------
class TicketView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)
    
    @discord.ui.button(label="Leadership", style=discord.ButtonStyle.primary, custom_id="leadership_ticket")
    async def leadership_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.create_ticket(interaction, "leadership")

    @discord.ui.button(label="Recruiters", style=discord.ButtonStyle.secondary, custom_id="recruiter_ticket")
    async def recruiter_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.create_ticket(interaction, "recruiters")

    async def create_ticket(self, interaction: discord.Interaction, ticket_type: str):
        """Creates a private thread and pings the correct role."""
        role_id = LEADERSHIP_ID if ticket_type == "leadership" else RECRUITER_ID
        now_str = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC")
        
        # Create a private thread in the same channel
        channel = interaction.channel
        thread_name = f"[{ticket_type.capitalize()}] - {interaction.user.display_name}"
        thread = await channel.create_thread(
            name=thread_name,
            type=discord.ChannelType.private_thread,
            invitable=False
        )

        try:
            await thread.send(f"<@&{role_id}> <@{interaction.user.id}>")
            embed = discord.Embed(title="🎟️ Ticket Opened", description="Thank you for reaching out! Our team will assist you shortly.\n\n📌 In the meantime:\n🔹 Can you provide more details about your issue?\n🔹 Be clear and precise so we can help faster.\n\n⏳ Please be patient – we’ll be with you soon!", colour=0x158225)
            await thread.send(embed=embed, view=CloseThreadView())  # Attach the CloseThreadView here
        except discord.Forbidden:
            await interaction.response.send_message("❌ Forbidden: Cannot send messages in the thread.", ephemeral=True)
            return
        except discord.HTTPException as e:
            await interaction.response.send_message(f"❌ HTTP Error sending messages: {e}", ephemeral=True)
            return

        # Save the ticket info
        add_ticket(
            thread_id=str(thread.id),
            user_id=str(interaction.user.id),
            created_at=now_str,
            ticket_type=ticket_type
        )

        # Acknowledge to the user
        await interaction.response.send_message("✅ Your ticket has been created!", ephemeral=True)

# --------------------------------------
#            MODAL CLASSES
# --------------------------------------
async def finalize_trainee_request(interaction: discord.Interaction, user_id_str: str):
    """Finalize the trainee request after selections."""
    try:
        request = pending_requests.get(user_id_str)
        if not request:
            await interaction.followup.send("❌ No pending request found to finalize.", ephemeral=True)
            return
               
        region = request.get("region")
        recruiter_name = request.get("selected_recruiter_name")
        recruiter_id = request.get("selected_recruiter_id")  # Access the recruiter's ID
        
        if not region or not recruiter_name or not recruiter_id:
            await interaction.followup.send("❌ Please complete all selections.", ephemeral=True)
            return
        
        guild = bot.get_guild(GUILD_ID)
        if not guild:
            await interaction.followup.send("❌ Guild not found.", ephemeral=True)
            return

        channel = guild.get_channel(REQUESTS_CHANNEL_ID)
        if not channel:
            await interaction.followup.send("❌ Requests channel not found.", ephemeral=True)
            return

        embed = discord.Embed(
            title="New Trainee Role Request:",
            description=f"User <@{interaction.user.id}> has requested a trainee role!",
            color=0x0080c0
        )
        embed.add_field(name="In-Game Name:", value=f"```{request['ingame_name']}```", inline=True)
        embed.add_field(name="Accepted By:", value=f"```{recruiter_name}```", inline=True)
        embed.add_field(name="Region:", value=f"```{region}```", inline=True)
        
        view = RequestActionView(
            user_id=interaction.user.id,
            request_type="trainee_role",
            ingame_name=request['ingame_name'],
            region=region,
            recruiter=recruiter_name
        )
        
        # Tag the recruiter who accepted them:
        await channel.send(f"<@{recruiter_id}>")
        await channel.send(embed=embed, view=view)

        await interaction.followup.send("✅ Your trainee role request has been submitted! Please allow us some time to accept this request.", ephemeral=True)

    except Exception as e:
        await interaction.followup.send(f"❌ Error finalizing trainee request: {e}", ephemeral=True)

RECRUITERS = [
    {"name": "Bain", "id": 111111111111111111},
    {"name": "Arcadia", "id": 222222222222222222},
    {"name": "Happy", "id": 333333333333333333},
]  # Replace with actual data or dynamically updated

class DenyReasonModal(discord.ui.Modal):
    """Modal to capture the denial reason for a request and DM the user."""
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

        # 1) Attempt to DM the user
        user = interaction.client.get_user(self.user_id)
        if user:
            try:
                await user.send(
                    f"Your request has been **denied** for the following reason:\n"
                    f"```\n{reason_text}\n```"
                )
            except discord.Forbidden:
                print("❌ Could not DM user " + str(self.user_id) + "; user may have DMs blocked.")

        # 2) Update the existing embed (change color, add fields, remove buttons)
        if interaction.message and interaction.message.embeds:
            updated_embed = interaction.message.embeds[0]
            updated_embed.color = discord.Color.red()
            updated_embed.add_field(name="Reason:", value=f"```\n{reason_text}\n```", inline=False)

            await interaction.message.edit(embed=updated_embed, view=None)

        # 3) Remove from pending_requests
        user_id_str = str(self.user_id)
        if user_id_str in pending_requests:
            del pending_requests[user_id_str]
            save_requests()

        # 4) Acknowledge the action
        await interaction.response.send_message("✅ Denial reason submitted. User has been notified.", ephemeral=True)



class RegionSelect(discord.ui.Select):
    def __init__(self):
        options = [
            discord.SelectOption(label="EU",  description="Europe"),
            discord.SelectOption(label="NA",  description="North America"),
            discord.SelectOption(label="SEA", description="Southeast Asia"),
        ]
        super().__init__(
            placeholder="Select what region you play the most!",
            min_values=1,
            max_values=1,
            options=options
        )
    
    async def callback(self, interaction: discord.Interaction):
        try:
            user_id_str = str(self.view.user_id)
            selected_region = self.values[0]
            
            if user_id_str in pending_requests:
                pending_requests[user_id_str]["region"] = selected_region
                save_requests()
                await interaction.response.send_message(f"✅ Region selected: {selected_region}", ephemeral=True)
            else:
                await interaction.response.send_message("❌ No pending request found.", ephemeral=True)
        except Exception as e:
            await interaction.response.send_message(f"❌ Error selecting region: {e}", ephemeral=True)

class RecruiterSelect(discord.ui.Select):
    def __init__(self):
        # Create options from the global RECRUITERS list
        recruiter_options = []
        for rec in RECRUITERS:
            recruiter_options.append(
                discord.SelectOption(label=rec["name"], description=f"Recruiter: {rec['name']}", value=str(rec["id"]))
            )
        super().__init__(
            placeholder="Select the recruiter who accepted you...",
            min_values=1,
            max_values=1,
            options=recruiter_options
        )
    
    async def callback(self, interaction: discord.Interaction):
        try:
            user_id_str = str(self.view.user_id)
            selected_recruiter_id = self.values[0]
            
            selected_recruiter = next((rec for rec in RECRUITERS if str(rec["id"]) == selected_recruiter_id), None)
            if selected_recruiter:
                if user_id_str in pending_requests:
                    pending_requests[user_id_str]["selected_recruiter_name"] = selected_recruiter["name"]
                    pending_requests[user_id_str]["selected_recruiter_id"]   = selected_recruiter["id"]
                    save_requests()
                    await interaction.response.send_message(f"✅ Recruiter selected: {selected_recruiter['name']}", ephemeral=True)
                    
                    # Finalize the request after recruiter selection
                    await finalize_trainee_request(interaction, user_id_str)
                else:
                    await interaction.response.send_message("❌ No pending request found.", ephemeral=True)
            else:
                await interaction.response.send_message("❌ Selected recruiter not found.", ephemeral=True)
        except Exception as e:
            await interaction.response.send_message(f"❌ Error selecting recruiter: {e}", ephemeral=True)

class TraineeDropdownView(discord.ui.View):
    def __init__(self, user_id: int):
        super().__init__(timeout=None)
        self.user_id = user_id
        self.add_item(RegionSelect())
        self.add_item(RecruiterSelect())

class TraineeRoleModal(discord.ui.Modal, title="Request Trainee Role"):
    ingame_name = discord.ui.TextInput(label="In-Game Name", placeholder="Enter your in-game name")
    
    async def on_submit(self, interaction: discord.Interaction):
        try:
            user_id_str = str(interaction.user.id)
            
            # Store initial modal data
            pending_requests[user_id_str] = {
                "request_type": "trainee_role",
                "ingame_name": self.ingame_name.value
            }
            save_requests()
        
            view = TraineeDropdownView(user_id=interaction.user.id)
            await interaction.response.send_message(
                "Please select your **Region** and **Recruiter** below:",
                view=view,
                ephemeral=True
            )
        except Exception as e:
            await interaction.response.send_message(f"❌ Error submitting trainee role modal: {e}", ephemeral=True)

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

            guild = bot.get_guild(GUILD_ID)
            if not guild:
                await interaction.response.send_message("❌ Guild not found.", ephemeral=True)
                return

            channel = guild.get_channel(REQUESTS_CHANNEL_ID)
            if not channel:
                await interaction.response.send_message("❌ Requests channel not found.", ephemeral=True)
                return

            base_nick = interaction.user.nick if interaction.user.nick else interaction.user.name
            new_name_cleaned = re.sub(r'(?:\s*\[(?:CADET|TRAINEE|SWAT)\])+$', '', self.new_name.value, flags=re.IGNORECASE)
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

            guild = bot.get_guild(GUILD_ID)
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

# --------------------------------------
#         BOT EVENTS & COMMANDS
# --------------------------------------
@bot.event
async def on_ready():
    print(f'✅ Logged in as {bot.user.name}')
    try:
        synced = await bot.tree.sync()
        print(f"✅ Synced {len(synced)} commands.")
    except Exception as e:
        print(f"❌ Failed to sync commands: {e}")
    
    load_requests()  # Load any pending requests from disk
    bot.add_view(TraineeView())  # Register the persistent view
    bot.add_view(TicketView())
    bot.add_view(RequestActionView())
    bot.add_view(CloseThreadView()) 
    
    global embed_message_id
    if os.path.exists(EMBED_ID_FILE):
        try:
            with open(EMBED_ID_FILE, "r") as f:
                embed_id_data = f.read().strip()
                if embed_id_data.isdigit():
                    embed_message_id = int(embed_id_data)
                    print(f"✅ Loaded embed_message_id: {embed_message_id}")
                else:
                    print("❌ Invalid data in embed.txt.")
                    embed_message_id = None
        except (ValueError, IOError) as e:
            print(f"❌ Error reading {EMBED_ID_FILE}: {e}")
            embed_message_id = None

    try:
        check_embed.start()
    except Exception as e:
        print(f"❌ Error starting check_embed task: {e}")
    try:
        update_recruiters_task.start()
    except Exception as e:
        print(f"❌ Error starting update_recruiters_task: {e}")
    try:
        check_expired_endtimes.start()
    except Exception as e:
        print(f"❌ Error starting check_expired_endtimes task: {e}")
    try:
        ensure_ticket_embed.start()
    except Exception as e:
        print(f"❌ Error starting ensure_ticket_embed task: {e}")
        

@bot.tree.command(name="hello", description="Say hello to the bot")
async def hello_command(interaction: discord.Interaction):
    await interaction.response.send_message(f"✅ Hello, {interaction.user.mention}!", ephemeral=True)

@bot.tree.command(name="ticket_internal", description="Creates a ticket without pinging anybody!")
async def ticket_internal(interaction: discord.Interaction):
        """Creates a private thread and pings the correct role."""
        now_str = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC")
        leadership_role = interaction.guild.get_role(LEADERSHIP_ID)
        if not leadership_role or (leadership_role not in interaction.user.roles):
            await interaction.response.send_message("❌ You do not have permission to open a private ticket.", ephemeral=True)
            return
        
        # Create a private thread in the same channel
        channel = bot.get_channel(TICKET_CHANNEL_ID)
        if channel:
            # Create a private thread in the same channel
            channel = bot.get_channel(TICKET_CHANNEL_ID)
            thread_name = f"[INT] - {interaction.user.display_name}"
            thread = await channel.create_thread(
                name=thread_name,
                type=discord.ChannelType.private_thread,
                invitable=False
            )

            try:
                await thread.send(f"<@{interaction.user.id}>")
                embed = discord.Embed(title="🔒 Private Ticket Opened", description="This ticket is private. To invite someone, please **tag them** in this thread.  \n\n📌 Only tagged members will be able to see and respond.", colour=0xe9ee1e)
                await thread.send(embed=embed, view=CloseThreadView())  # Attach the CloseThreadView here
            except discord.Forbidden:
                await interaction.response.send_message("❌ Forbidden: Cannot send messages in the thread.", ephemeral=True)
                return
            except discord.HTTPException as e:
                await interaction.response.send_message(f"❌ HTTP Error sending messages: {e}", ephemeral=True)
                return
            # Save the ticket info
            add_ticket(
                thread_id=str(thread.id),
                user_id=str(interaction.user.id),
                created_at=now_str,
                ticket_type="other"
            )

            await interaction.response.send_message("✅ Your ticket has been created!", ephemeral=True)
        else:
            await interaction.response.send_message("❌ Ticket channel not found", ephemeral=True)

@bot.tree.command(name="testconfiguration", description="Check if the bot is properly configured.")
async def testconfiguration(interaction: discord.Interaction):
    # We'll store pass/fail messages here
    checks = []

    checks.append("\n")
    checks.append("## Checking message permissions \n")
    
    # 1. Check if we can read/send messages in the current channel
    if interaction.channel.permissions_for(interaction.guild.me).send_messages:
        checks.append("✅ Can send messages in this channel")
    else:
        checks.append("❌ Cannot send messages in this channel")

    # 2. Check if we can rename a user (we'll try renaming the command user and revert)
    checks.append("\n")
    checks.append("## Checking renaming permissions \n")
    bot_member = interaction.guild.me  # The bot's own member object
    
    # 1. Check for the 'Manage Nicknames' permission at the guild level
    if bot_member.guild_permissions.manage_nicknames:
        checks.append("✅ Bot has the 'Manage Nicknames' permission")
    else:
        checks.append("❌ Bot does NOT have the 'Manage Nicknames' permission")

    # 2. Check role hierarchy (bot's top role must be higher than the user's top role)
    if bot_member.top_role > interaction.user.top_role:
        checks.append(f"✅ Bot's top role is higher than {interaction.user.display_name}'s top role, so renaming is possible!")
    else:
        checks.append(f"❌ {interaction.user.display_name} has a role equal or higher than the bot's top role! No renaming possible!")

    checks.append("\n")
    checks.append("## Checking thread permissions \n")
    # 3. Check if we can create threads
    try:
        if interaction.channel.permissions_for(interaction.guild.me).create_public_threads:
            checks.append("✅ Can create public threads")
        else:
            checks.append("❌ Cannot create public threads")
    except Exception as e:
        checks.append(f"❌ Error checking thread creation perms: {e}")

    try:
        if interaction.channel.permissions_for(interaction.guild.me).create_private_threads:
            checks.append("✅ Bot has permission for creating private threads")
        else:
            checks.append("❌ Bot does not have permission to create private threads")
    except Exception as e:
        checks.append(f"❌ Error checking thread creation perms: {e}")

    checks.append("\n ## Checking channel permissions \n")
    trainee_notes = interaction.guild.get_channel(TRAINEE_NOTES_CHANNEL)
    if not trainee_notes:
        checks.append(f"❌ Channel with ID {TRAINEE_NOTES_CHANNEL} not found.")
    else:
        bot_perms = trainee_notes.permissions_for(interaction.guild.me)
        if bot_perms.send_messages:
            checks.append(f"✅ Can send messages in <#{TRAINEE_NOTES_CHANNEL}>")
        else:
            checks.append(f"❌ Cannot send messages in <#{TRAINEE_NOTES_CHANNEL}>")

        if bot_perms.create_public_threads:
            checks.append(f"✅ Can create threads in <#{TRAINEE_NOTES_CHANNEL}>")
        else:
            checks.append(f"❌ Cannot create threads in <#{TRAINEE_NOTES_CHANNEL}>")

    cadet_notes = interaction.guild.get_channel(CADET_NOTES_CHANNEL)
    if not cadet_notes:
        checks.append(f"❌ Channel with ID {CADET_NOTES_CHANNEL} not found.")
    else:
        bot_perms = cadet_notes.permissions_for(interaction.guild.me)
        if bot_perms.send_messages:
            checks.append(f"✅ Can send messages in <#{CADET_NOTES_CHANNEL}>")
        else:
            checks.append(f"❌ Cannot send messages in <#{CADET_NOTES_CHANNEL}>")

        if bot_perms.create_public_threads:
            checks.append(f"✅ Can create threads in <#{CADET_NOTES_CHANNEL}>")
        else:
            checks.append(f"❌ Cannot create threads in <#{CADET_NOTES_CHANNEL}>")

    trainee_chat = interaction.guild.get_channel(TRAINEE_CHAT_CHANNEL)
    if not trainee_chat:
        checks.append(f"❌ Channel with ID {TRAINEE_CHAT_CHANNEL} not found.")
    else:
        bot_perms = trainee_chat.permissions_for(interaction.guild.me)
        if bot_perms.send_messages:
            checks.append(f"✅ Can send messages in <#{TRAINEE_CHAT_CHANNEL}>")
        else:
            checks.append(f"❌ Cannot send messages in <#{TRAINEE_CHAT_CHANNEL}>")

        if bot_perms.create_public_threads:
            checks.append(f"✅ Can create threads in <#{TRAINEE_CHAT_CHANNEL}>")
        else:
            checks.append(f"❌ Cannot create threads in <#{TRAINEE_CHAT_CHANNEL}>")

    swat_chat = interaction.guild.get_channel(SWAT_CHAT_CHANNEL)
    if not swat_chat:
        checks.append(f"❌ Channel with ID {SWAT_CHAT_CHANNEL} not found.")
    else:
        bot_perms = swat_chat.permissions_for(interaction.guild.me)
        if bot_perms.send_messages:
            checks.append(f"✅ Can send messages in <#{SWAT_CHAT_CHANNEL}>")
        else:
            checks.append(f"❌ Cannot send messages in <#{SWAT_CHAT_CHANNEL}>")

        if bot_perms.create_public_threads:
            checks.append(f"✅ Can create threads in <#{SWAT_CHAT_CHANNEL}>")
        else:
            checks.append(f"❌ Cannot create threads in <#{SWAT_CHAT_CHANNEL}>")

    main_embed = interaction.guild.get_channel(TARGET_CHANNEL_ID)
    if not main_embed:
        checks.append(f"❌ Channel with ID {TARGET_CHANNEL_ID} not found.")
    else:
        bot_perms = main_embed.permissions_for(interaction.guild.me)
        if bot_perms.send_messages:
            checks.append(f"✅ Can send messages in <#{TARGET_CHANNEL_ID}>")
        else:
            checks.append(f"❌ Cannot send messages in <#{TARGET_CHANNEL_ID}>")

        if bot_perms.create_public_threads:
            checks.append(f"✅ Can create threads in <#{TARGET_CHANNEL_ID}>")
        else:
            checks.append(f"❌ Cannot create threads in <#{TARGET_CHANNEL_ID}>")

    requests_channel = interaction.guild.get_channel(REQUESTS_CHANNEL_ID)
    if not requests_channel:
        checks.append(f"❌ Channel with ID {REQUESTS_CHANNEL_ID} not found.")
    else:
        bot_perms = requests_channel.permissions_for(interaction.guild.me)
        if bot_perms.send_messages:
            checks.append(f"✅ Can send messages in <#{REQUESTS_CHANNEL_ID}>")
        else:
            checks.append(f"❌ Cannot send messages in <#{REQUESTS_CHANNEL_ID}>")

        if bot_perms.create_public_threads:
            checks.append(f"✅ Can create threads in <#{REQUESTS_CHANNEL_ID}>")
        else:
            checks.append(f"❌ Cannot create threads in <#{REQUESTS_CHANNEL_ID}>")

    checks.append("\n")
    checks.append("## Checking role permissions \n")
    # 4. Check if we can manage roles (e.g., trainee role)
    trainee_role = interaction.guild.get_role(TRAINEE_ROLE)
    if trainee_role:
        if trainee_role < interaction.guild.me.top_role:
            checks.append("✅ Bot can manage the trainee role")
        else:
            checks.append("❌ Trainee role is above bot's top role, cannot manage it")
    else:
        checks.append("❌ Trainee role not found")

    cadet_role = interaction.guild.get_role(CADET_ROLE)
    if cadet_role:
        if cadet_role < interaction.guild.me.top_role:
            checks.append("✅ Bot can manage the cadet role")
        else:
            checks.append("❌ Cadet role is above bot's top role, cannot manage it")
    else:
        checks.append("❌ Cadet role not found")
        
    swat_role = interaction.guild.get_role(SWAT_ROLE_ID)
    if swat_role:
        if swat_role < interaction.guild.me.top_role:
            checks.append("✅ Bot can manage the swat role")
        else:
            checks.append("❌ Swat role is above bot's top role, cannot manage it")
    else:
        checks.append("❌ Swat role not found")
    
    officer_role = interaction.guild.get_role(OFFICER_ROLE_ID)
    if officer_role:
        if officer_role < interaction.guild.me.top_role:
            checks.append("✅ Bot can manage the Officer role")
        else:
            checks.append("❌ Officer role is above bot's top role, cannot manage it")
    else:
        checks.append("❌ Officer role not found")
        
    # 5. Summarize results in an embed
    embed = discord.Embed(
        title="Bot Configuration Check",
        description="\n".join(checks),
        color=discord.Color.green() if all("✅" in c for c in checks) else discord.Color.red()
    )
    await interaction.response.send_message(embed=embed, ephemeral=True)


@tasks.loop(minutes=5)
async def check_embed():
    """Periodically ensure the main Trainee Management embed is present."""
    global embed_message_id
    try:
        channel = bot.get_channel(TARGET_CHANNEL_ID)
        if channel and embed_message_id:
            try:
                await channel.fetch_message(embed_message_id)
            except discord.NotFound:
                embed = create_embed()
                view = TraineeView()
                msg = await channel.send(embed=embed, view=view)
                embed_message_id = msg.id
                with open(EMBED_ID_FILE, "w") as f:
                    f.write(str(embed_message_id))
                print(f"✅ Embed not found; sent new embed and updated embed_message_id: {embed_message_id}")
            except discord.Forbidden:
                print("❌ Bot lacks permission to fetch messages in this channel.")
            except discord.HTTPException as e:
                print(f"❌ Failed to fetch message: {e}")
        elif channel and embed_message_id is None:
            embed = create_embed()
            view = TraineeView()
            msg = await channel.send(embed=embed, view=view)
            embed_message_id = msg.id
            try:
                with open(EMBED_ID_FILE, "w") as f:
                    f.write(str(embed_message_id))
            except IOError as e:
                print(f"❌ Error writing embed ID to file: {e}")
            print(f"✅ Created new embed with ID: {embed_message_id}")
    except Exception as e:
        print(f"❌ Error in check_embed: {e}")

@tasks.loop(minutes=10)
async def update_recruiters_task():
    await update_recruiters()

@tasks.loop(minutes=1)
async def check_expired_endtimes():
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
            (now,)
        )
        expired_entries = cursor.fetchall()

        for thread_id, recruiter_id, starttime, role_type, region, ingame_name in expired_entries:
            thread = bot.get_channel(int(thread_id)) if thread_id.isdigit() else None

            if thread and isinstance(thread, discord.Thread):
                try:
                    start_time = datetime.strptime(starttime, "%Y-%m-%d %H:%M:%S.%f")
                except ValueError:
                    print(f"❌ Error parsing starttime: {starttime}")
                    continue

                days_open = (now - start_time).days
                embed = discord.Embed(
                    description=f"**Reminder:** This thread has been open for **{days_open} days**.",
                    color=0x008040
                )

                if role_type == "trainee":
                    recruiter = bot.get_user(int(recruiter_id))
                    if recruiter:
                        await thread.send(f"<@{recruiter_id}>", embed=embed)
                    else:
                        await thread.send(embed=embed)

                elif role_type == "cadet":
                    voting_embed = discord.Embed(
                        description=(
                            "SWAT, please express your vote below.\n"
                            "Use <:plus_one:1334498534187208714>, ❔, or <:minus_one:1334498485390544989> accordingly."
                        ),
                        color=0x000000
                    )
                    flags = {"EU": "🇪🇺 ", "NA": "🇺🇸 ", "SEA": "🇸🇬 "}
                    region_name = region[:-1] if region and region[-1].isdigit() else region
                    title = f"{flags.get(region_name, '')}{region}"
                    voting_embed.add_field(name="InGame Name:", value=ingame_name, inline=True)
                    voting_embed.add_field(name="Region:", value=title, inline=True)
                    voting_embed.add_field(name="", value="", inline=False)
                    voting_embed.add_field(name="Voting started:", value=create_discord_timestamp(start_time), inline=True)
                    voting_embed.add_field(name="Voting has ended!", value="", inline=True)
                    voting_embed.add_field(name="Thread managed by:", value=f"<@{recruiter_id}>", inline=False)
                    await thread.send(f"<@&{SWAT_ROLE_ID}> It's time for another cadet voting!⌛")
                    embed_msg = await thread.send(embed=voting_embed)
                    await embed_msg.add_reaction("<:plus_one:1334498534187208714>")
                    await embed_msg.add_reaction("❔")
                    await embed_msg.add_reaction("<:minus_one:1334498485390544989>")

                cursor.execute(
                    """
                    UPDATE entries 
                    SET reminder_sent = 1 
                    WHERE thread_id = ?
                    """,
                    (thread_id,)
                )
                conn.commit()
            else:
                print(f"❌ Thread with ID {thread_id} not found or invalid thread.")

    except sqlite3.Error as e:
        print(f"❌ Database error in check_expired_endtimes: {e}")
    except Exception as e:
        print(f"❌ Error in check_expired_endtimes: {e}")
    finally:
        if conn:
            conn.close()

@tasks.loop(minutes=5)
async def ensure_ticket_embed():
    channel = bot.get_channel(TICKET_CHANNEL_ID)
    if not channel:
        return
    
    # Load the stored embed ID (if any)
    stored_embed_id = None
    if os.path.exists(EMBED_FILE):
        with open(EMBED_FILE, "r") as f:
            data = json.load(f)
            stored_embed_id = data.get("embed_id")

    # If we have an embed ID, try to fetch the message
    if stored_embed_id:
        try:
            # If the message is found, we're done
            await channel.fetch_message(stored_embed_id)
            return
        except (discord.NotFound, discord.Forbidden, discord.HTTPException):
            # The message no longer exists or can't be fetched
            pass

    # If the embed doesn't exist, send a new one
    embed = discord.Embed(title="🎟️ Open a Ticket", description="Need help? Choose the right department for your request:\n👑 Leadership – Crew-related issues, complaints, verification or giveaway claims.\n🛡️ Recruiters – Inquiries about Trainee program, applications or recruitment in general.\n\n📌 Click a button below to open a private thread with the right team!",
                      colour=0x28afcc)
    sent_msg = await channel.send(embed=embed, view=TicketView())

    # Save the new embed ID
    with open(EMBED_FILE, "w") as f:
        json.dump({"embed_id": sent_msg.id}, f)

# --------------------------------------
#     STAFF / MANAGEMENT COMMANDS -> Threads
# --------------------------------------
@app_commands.describe(
    user_id="User's Discord ID",
    ingame_name="Exact in-game name",
    region="Region of the user (NA, EU, or SEA)"
)
@app_commands.choices(region=[
    app_commands.Choice(name="NA", value="NA"),
    app_commands.Choice(name="EU", value="EU"),
    app_commands.Choice(name="SEA", value="SEA")
])
@bot.tree.command(name="add_trainee", description="Manually add a user as a trainee")
async def add_trainee_command_ephemeral(
    interaction: discord.Interaction, 
    user_id: str, 
    ingame_name: str, 
    region: app_commands.Choice[str]
):
    """Manually add a user as trainee and create a voting thread."""
    try:
        user_id_int = int(user_id)
    except ValueError:
        await interaction.response.send_message("❌ Invalid user ID.", ephemeral=True)
        return

    recruiter_role = interaction.guild.get_role(RECRUITER_ID)
    if not recruiter_role or recruiter_role not in interaction.user.roles:
        await interaction.response.send_message("❌ You do not have permission to use this command.", ephemeral=True)
        return

    if is_user_in_database(user_id_int):
        await interaction.response.send_message(
            "❌ This trainee is already in the database.",
            ephemeral=True
        )
        return

    guild = bot.get_guild(GUILD_ID)
    if not guild:
        await interaction.response.send_message("❌ Guild not found.", ephemeral=True)
        return

    member = guild.get_member(user_id_int)
    if not member:
        await interaction.response.send_message("❌ User not found in guild!", ephemeral=True)
        return

    try:
        await interaction.response.defer(ephemeral=True)
        await set_user_nickname(member, "trainee")
        role_obj = guild.get_role(TRAINEE_ROLE)
        if role_obj:
            await member.add_roles(role_obj)
        else:
            await interaction.followup.send("❌ Trainee role not found.", ephemeral=True)
            return

        channel = guild.get_channel(TRAINEE_NOTES_CHANNEL)
        if channel:
            start_time = get_rounded_time()
            end_time = start_time + timedelta(days=7)
            thread_name = f"{ingame_name} | TRAINEE Notes"
            try:
                thread = await channel.create_thread(
                    name=thread_name,
                    message=None,
                    type=discord.ChannelType.public_thread,
                    reason="New Trainee accepted",
                    invitable=False
                )
            except discord.Forbidden:
                await interaction.followup.send("❌ Forbidden: Cannot create thread.", ephemeral=True)
                return
            except discord.HTTPException as e:
                await interaction.followup.send(f"❌ HTTP Error creating thread: {e}", ephemeral=True)
                return

            voting_embed = await create_voting_embed(start_time, end_time, interaction.user.id, region.value, ingame_name)
            embed_msg = await thread.send(embed=voting_embed)
            await embed_msg.add_reaction("<:plus_one:1334498534187208714>")
            await embed_msg.add_reaction("❔")
            await embed_msg.add_reaction("<:minus_one:1334498485390544989>")

            add_ok = add_entry(
                thread_id=thread.id,
                recruiter_id=str(interaction.user.id),
                starttime=start_time,
                endtime=end_time,
                role_type="trainee",
                embed_id=str(embed_msg.id),
                ingame_name=ingame_name,
                user_id=str(user_id_int),
                region=region.value
            )
            if not add_ok:
                await interaction.followup.send("❌ Failed to add user to database.", ephemeral=True)
                return

            await interaction.followup.send("✅ Trainee added successfully!", ephemeral=True)
        else:
            await interaction.followup.send("❌ Cannot find the trainee notes channel.", ephemeral=True)
    except Exception as e:
        await interaction.followup.send(f"❌ Error adding trainee: {e}", ephemeral=True)

@bot.tree.command(name="votinginfo", description="Show info about the current voting thread")
async def votinginfo_command(interaction: discord.Interaction):
    """Display info about the currently used thread, if it exists in DB."""
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
    embed.add_field(name="End Time",   value=str(data["endtime"]),   inline=False)
    embed.add_field(name="Type",       value=data["role_type"],      inline=False)
    embed.add_field(name="Recruiter",  value=f"<@{data['recruiter_id']}>", inline=False)
    embed.add_field(name="Embed ID",   value=str(data["embed_id"]),  inline=False)
    embed.add_field(name="InGame Name",value=data["ingame_name"],    inline=False)
    embed.add_field(name="User ID",    value=f"<@{data['user_id']}>",inline=False)
    embed.add_field(name="Region",     value=data['region'],         inline=False)
    await interaction.response.send_message(embed=embed, ephemeral=True)

@bot.tree.command(name="remove", description="Remove a user from trainee / cadet program and close thread!")
async def lock_thread_command(interaction: discord.Interaction):
    """Close the thread if it's a valid voting thread."""
    recruiter_role = interaction.guild.get_role(RECRUITER_ID)
    if not recruiter_role or (recruiter_role not in interaction.user.roles):
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
    await close_thread(interaction, interaction.channel)

    guild = bot.get_guild(GUILD_ID)
    if not guild:
        await interaction.followup.send("❌ Guild not found.", ephemeral=True)
        return

    member = guild.get_member(int(data["user_id"]))
    if not member:
        await interaction.followup.send("❌ User not found in guild!", ephemeral=True)
        return
    
    try:
        temp_name = re.sub(r'(?:\s*\[(?:CADET|TRAINEE|SWAT)\])+$', '', member.nick if member.nick else member.name, flags=re.IGNORECASE)
        await member.edit(nick=temp_name)
    except discord.Forbidden:
        await interaction.followup.send("❌ Forbidden: Cannot remove bracket from nickname.", ephemeral=True)
    except discord.HTTPException as e:
        await interaction.followup.send(f"❌ HTTP Error removing bracket from nickname: {e}", ephemeral=True)

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

    embed = discord.Embed(title="❌ " + str(data["ingame_name"]) + " has been removed!", colour=0xf94144)
    embed.set_footer(text="🔒This thread is locked now!")
    await interaction.followup.send(embed=embed)

@bot.tree.command(name="promote", description="Promote the user in the current voting thread (Trainee->Cadet or Cadet->SWAT).")
async def promote_user_command(interaction: discord.Interaction):
    """Promote a user from Trainee->Cadet or Cadet->SWAT, closing the old thread and creating a new one if needed."""
    recruiter_role = interaction.guild.get_role(RECRUITER_ID)
    if not recruiter_role or (recruiter_role not in interaction.user.roles):
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

    guild = bot.get_guild(GUILD_ID)
    if not guild:
        return

    member = guild.get_member(int(data["user_id"]))
    if not member:
        await interaction.followup.send("❌ User not found in guild!", ephemeral=True)
        return

    old_role_type = data["role_type"]
    ingame_name   = data["ingame_name"]

    try:
        if old_role_type == "trainee":
            # Promote to CADET
            await set_user_nickname(member, "cadet")
            t_role = guild.get_role(TRAINEE_ROLE)
            c_role = guild.get_role(CADET_ROLE)
            if t_role in member.roles:
                await member.remove_roles(t_role)
            await member.add_roles(c_role)

            channel_obj = guild.get_channel(CADET_NOTES_CHANNEL)
            if channel_obj:
                start_time = get_rounded_time()
                end_time   = start_time + timedelta(days=7)
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
                await embed_msg.add_reaction("<:plus_one:1334498534187208714>")
                await embed_msg.add_reaction("❔")
                await embed_msg.add_reaction("<:minus_one:1334498485390544989>")

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
            # Promote to SWAT
            await set_user_nickname(member, "swat")
            c_role = guild.get_role(CADET_ROLE)
            s_role = guild.get_role(SWAT_ROLE_ID)
            if c_role in member.roles:
                await member.remove_roles(c_role)
            await member.add_roles(s_role)
            try:
                await member.send(welcome_to_swat)
            except discord.Forbidden:
                print(f"❌ Could not DM user {member.id} (Forbidden).")
            except discord.HTTPException as e:
                print(f"❌ HTTP error DMing user {member.id}: {e}")
    except discord.Forbidden:
        await interaction.followup.send("❌ Forbidden: Cannot assign roles or change nickname.", ephemeral=True)
    except discord.HTTPException as e:
        await interaction.followup.send(f"❌ HTTP Error during promotion: {e}", ephemeral=True)

@bot.tree.command(name="extend", description="Extend the current thread's voting period.")
@app_commands.describe(days="How many days to extend?")
async def extend_thread_command(interaction: discord.Interaction, days: int):
    """Extend the voting period for the currently open thread."""
    recruiter_role = interaction.guild.get_role(RECRUITER_ID)
    if not recruiter_role or (recruiter_role not in interaction.user.roles):
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
            old_end = datetime.strptime(str(data["endtime"]), "%Y-%m-%d %H:%M:%S.%f")
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

@bot.tree.command(name="resend_voting", description="Resends a voting embed!")
async def resend_voting_command(interaction: discord.Interaction):
    """Resend a voting embed for the current thread."""
    recruiter_role = interaction.guild.get_role(RECRUITER_ID)
    if not recruiter_role or (recruiter_role not in interaction.user.roles):
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
        await embed_msg.add_reaction("<:plus_one:1334498534187208714>")
        await embed_msg.add_reaction("❔")
        await embed_msg.add_reaction("<:minus_one:1334498485390544989>")

        await interaction.response.send_message("✅ Voting embed has been resent.", ephemeral=True)
    except Exception as e:
        await interaction.response.send_message(f"❌ Error occurred: {e}", ephemeral=True)


@bot.tree.command(name="early_vote", description="Resends a voting embed!")
async def early_vote(interaction: discord.Interaction):
    """Resend a voting embed for the current thread."""
    recruiter_role = interaction.guild.get_role(RECRUITER_ID)
    if not recruiter_role or (recruiter_role not in interaction.user.roles):
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
            thread = bot.get_channel(int(data["thread_id"])) if data["thread_id"].isdigit() else None

            if thread and isinstance(thread, discord.Thread):
                try:
                    if not isinstance(data["starttime"], datetime):
                        start_time = datetime.strptime(str(data["starttime"]), "%Y-%m-%d %H:%M:%S.%f")
                    else:
                        start_time = data["endtime"]
                except ValueError:
                    print(f"❌ Error parsing starttime: {data["starttime"]}")
                
                conn = sqlite3.connect(DATABASE_FILE)
                cursor = conn.cursor()
                now = datetime.now()

                if data["role_type"] == "cadet":
                    voting_embed = discord.Embed(
                        description=(
                            "SWAT, please express your vote below.\n"
                            "Use <:plus_one:1334498534187208714>, ❔, or <:minus_one:1334498485390544989> accordingly."
                        ),
                        color=0x000000
                    )
                    flags = {"EU": "🇪🇺 ", "NA": "🇺🇸 ", "SEA": "🇸🇬 "}
                    region_name = data["region"][:-1] if data["region"] and data["region"][-1].isdigit() else data["region"]
                    title = f"{flags.get(region_name, '')}{data["region"]}"
                    voting_embed.add_field(name="InGame Name:", value=data["ingame_name"], inline=True)
                    voting_embed.add_field(name="Region:", value=title, inline=True)
                    voting_embed.add_field(name="", value="", inline=False)
                    voting_embed.add_field(name="Voting started:", value=create_discord_timestamp(start_time), inline=True)
                    voting_embed.add_field(name="Voting has ended!", value="", inline=True)
                    voting_embed.add_field(name="", value="", inline=False)
                    voting_embed.add_field(name="Thread managed by:", value=f"<@{data["recruiter_id"]}>", inline=True)
                    voting_embed.add_field(name="Early voting issued by:", value=f"<@{interaction.user.id}>", inline=True)
                    await thread.send(f"<@&{SWAT_ROLE_ID}> It's time for another cadet voting!⌛")
                    embed_msg = await thread.send(embed=voting_embed)
                    await embed_msg.add_reaction("<:plus_one:1334498534187208714>")
                    await embed_msg.add_reaction("❔")
                    await embed_msg.add_reaction("<:minus_one:1334498485390544989>")

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
                    await interaction.followup.send(f"❌ Not a cadet thread!", ephemeral=True)
        else:
            await interaction.followup.send(f"❌ Reminder has already been sent!", ephemeral=True)
    except Exception as e:
        if interaction.response.is_done():
            await interaction.followup.send(f"❌ Error occurred: {e}", ephemeral=True)
        else:
            await interaction.response.send_message(f"❌ Error occurred: {e}", ephemeral=True)
        
# -----------------------
# COMMANDS
# -----------------------
@bot.tree.command(name="ticket_info", description="Show info about the current ticket thread.")
async def ticket_info(interaction: discord.Interaction):
    # Instead of interaction.response.defer(), we'll just respond once with a message
    if not isinstance(interaction.channel, discord.Thread):
        await interaction.response.send_message("❌ Use this command in the ticket thread.", ephemeral=True)
        return

    ticket_data = get_ticket_info(str(interaction.channel.id))
    if not ticket_data:
        await interaction.response.send_message("❌ This thread is not a registered ticket.", ephemeral=True)
        return

    thread_id, user_id, created_at, ticket_type = ticket_data
    embed = discord.Embed(title="Ticket Information", color=discord.Color.blue())
    embed.add_field(name="Thread ID", value=thread_id, inline=False)
    embed.add_field(name="User", value=f"<@{user_id}>", inline=False)
    embed.add_field(name="Created At (UTC)", value=created_at, inline=False)
    embed.add_field(name="Ticket Type", value=ticket_type, inline=False)
    await interaction.response.send_message(embed=embed, ephemeral=True)

@bot.tree.command(name="ticket_close", description="Close the current ticket.")
async def ticket_close(interaction: discord.Interaction):
    # Instead of interaction.response.defer(), we'll just respond once with a message
    if not isinstance(interaction.channel, discord.Thread):
        await interaction.response.send_message("❌ Use this command in the ticket thread.", ephemeral=True)
        return

    ticket_data = get_ticket_info(str(interaction.channel.id))
    if not ticket_data:
        await interaction.response.send_message("❌ This thread is not a registered ticket.", ephemeral=True)
        return

    # Remove from DB
    remove_ticket(str(interaction.channel.id))

    # Lock and archive
    embed = discord.Embed(title=f"Ticket closed by {interaction.user.nick}",
                colour=0xf51616)
    embed.set_footer(text="🔒This ticket is locked now!")
    await interaction.response.send_message(embed=embed)
    await interaction.channel.edit(locked=True, archived=True)


# --------------------------------------
#        SHUTDOWN AND BOT LAUNCH
# --------------------------------------
@bot.event
async def on_shutdown():
    """Handle graceful shutdown if implemented by yourself."""
    global embed_message_id
    if embed_message_id:
        try:
            with open(EMBED_ID_FILE, "w") as f:
                f.write(str(embed_message_id))
            print(f"✅ Saved embed_message_id: {embed_message_id} on shutdown")
        except IOError as e:
            print(f"❌ Error saving embed_message_id on shutdown: {e}")

    save_requests()

try:
    with open("token.txt", "r") as file:
        TOKEN = file.read().strip()
except IOError as e:
    print(f"❌ Error reading token.txt: {e}")
    TOKEN = None

if TOKEN:
    try:
        bot.run(TOKEN)
    except Exception as e:
        print(f"❌ Bot run error: {e}")
else:
    print("❌ No valid bot token found. Exiting.")
