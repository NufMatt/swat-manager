# cogs/recruitment.py

import discord
from discord import app_commands, ButtonStyle
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
    INTEGRATIONS_MANAGER, RECRUITER_EMOJI, LEADERSHIP_EMOJI
)
from messages import trainee_messages, cadet_messages, welcome_to_swat, OPEN_TICKET_EMBED_TEXT
from cogs.helpers import is_in_correct_guild, log  # if you have additional helpers

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

# -------------------------------
# Recruitment Requests Management
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

class TraineeView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)
    @discord.ui.button(label="Request Trainee Role", style=discord.ButtonStyle.primary, custom_id="request_trainee_role")
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
        await interaction.response.send_modal(TraineeRoleModal())

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
        if user_id_str in pending_requests:
            del pending_requests[user_id_str]
            save_requests()
        await interaction.followup.send("✅ Denial reason submitted. User has been notified.", ephemeral=True)

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
                    pending_requests[user_id_str]["selected_recruiter_id"] = selected_recruiter["id"]
                    save_requests()
                    await interaction.response.send_message(f"✅ Recruiter selected: {selected_recruiter['name']}", ephemeral=True)
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
        request = pending_requests.get(user_id_str)
        if not request:
            await interaction.followup.send("❌ No pending request found to finalize.", ephemeral=True)
            return
        region = request.get("region")
        recruiter_name = request.get("selected_recruiter_name")
        recruiter_id = request.get("selected_recruiter_id")
        if not region or not recruiter_name or not recruiter_id:
            await interaction.followup.send("❌ Please complete all selections.", ephemeral=True)
            return
        guild = interaction.client.get_guild(GUILD_ID)
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
        await channel.send(f"<@{recruiter_id}>")
        await channel.send(embed=embed, view=view)
        await interaction.followup.send("✅ Your trainee role request has been submitted! Please allow us some time to accept this request.", ephemeral=True)
    except Exception as e:
        await interaction.followup.send(f"❌ Error finalizing trainee request: {e}", ephemeral=True)

# -------------------------------
# Recruitment Cog
# -------------------------------
class RecruitmentCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        # Delay any action until the bot is fully ready.
        self.bot.loop.create_task(self._wait_and_start())

    async def _wait_and_start(self):
        await self.bot.wait_until_ready()
        load_requests()
        self.bot.add_view(TraineeView())
        self.bot.add_view(RequestActionView())
        self.bot.add_view(CloseThreadView())
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
        # Start tasks
        self.check_embed_task.start()
        self.update_recruiters_task.start()
        self.check_expired_endtimes_task.start()
        await self.load_existing_tickets()
        log("RecruitmentCog setup complete. All tasks started.")

    def cog_unload(self):
        self.check_embed_task.cancel()
        self.update_recruiters_task.cancel()
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
                        view = TraineeView()
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
                    view = TraineeView()
                    msg = await channel.send(embed=embed, view=view)
                    embed_message_id = msg.id
                    with open(EMBED_ID_FILE, "w") as f:
                        f.write(str(embed_message_id))
                    log(f"Created new embed with ID: {embed_message_id}")
        except Exception as e:
            log(f"Error in check_embed_task: {e}", level="error")

    @tasks.loop(minutes=10)
    async def update_recruiters_task(self):
        await update_recruiters(self.bot)

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
            log(f"Member with ID {data['user_id']} not found in guild (they may have left). Skipping nickname and role removal.", level="warning")
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

async def setup(bot: commands.Bot):
    await bot.add_cog(RecruitmentCog(bot))
