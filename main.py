
import discord
from discord import app_commands
from discord.ext import commands, tasks
from discord import ui
from discord import ui
import asyncio
import os
import json
from datetime import datetime, timedelta
import sqlite3
from typing import Optional, Dict
import re

# Define the path to your SQLite database file
DATABASE_FILE = "data.db"

def initialize_database():
    """Initialize the SQLite database and create the entries table if it doesn't exist."""
    conn = sqlite3.connect(DATABASE_FILE)
    cursor = conn.cursor()
    
    # Create the entries table with 'thread_id' as the PRIMARY KEY
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS entries (
            thread_id TEXT PRIMARY KEY,
            recruiter_id TEXT NOT NULL,
            starttime TEXT NOT NULL,
            endtime TEXT,
            embed_id TEXT NOT NULL,
            ingame_name TEXT NOT NULL,
            user_id TEXT NOT NULL,
            role_type TEXT NOT NULL CHECK(role_type IN ('trainee', 'cadet'))
        )
    """)
    
    conn.commit()
    conn.close()

# Initialize the database when the module is run
initialize_database()

def add_entry(thread_id: str, recruiter_id: str, starttime: str, endtime: Optional[str], role_type: str, embed_id: str, ingame_name: str, user_id: str) -> bool:
    """
    Add a new entry to the database.

    :param thread_id: The ID of the thread.
    :param recruiter_id: The ID of the recruiter.
    :param starttime: The start time (ISO format string).
    :param endtime: The end time (ISO format string) or None.
    :param role_type: "trainee" or "cadet".
    :return: True if the entry was added successfully, False if it already exists or role_type is invalid.
    """
    if role_type not in ("trainee", "cadet"):
        raise ValueError("role_type must be either 'trainee' or 'cadet'.")

    try:
        conn = sqlite3.connect(DATABASE_FILE)
        cursor = conn.cursor()
        
        cursor.execute("""
            INSERT INTO entries (thread_id, recruiter_id, starttime, endtime, role_type, embed_id, ingame_name, user_id)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, (thread_id, recruiter_id, starttime, endtime, role_type, embed_id, ingame_name, user_id))
        
        conn.commit()
        return True
    except sqlite3.IntegrityError:
        # Entry with the same thread_id already exists or role_type is invalid
        return False
    finally:
        conn.close()

def remove_entry(thread_id: str) -> bool:
    """
    Remove an entry from the database based on thread_id.

    :param thread_id: The ID of the thread to remove.
    :return: True if an entry was removed, False otherwise.
    """
    conn = sqlite3.connect(DATABASE_FILE)
    cursor = conn.cursor()
    
    cursor.execute("""
        DELETE FROM entries
        WHERE thread_id = ?
    """, (thread_id,))
    
    conn.commit()
    rows_deleted = cursor.rowcount
    conn.close()
    
    return rows_deleted > 0

def update_endtime(thread_id: str, new_endtime: str) -> bool:
    """
    Update the endtime of an existing entry.

    :param thread_id: The ID of the thread to update.
    :param new_endtime: The new end time (ISO format string).
    :return: True if the entry was updated successfully, False otherwise.
    """
    conn = sqlite3.connect(DATABASE_FILE)
    cursor = conn.cursor()
    
    cursor.execute("""
        UPDATE entries
        SET endtime = ?
        WHERE thread_id = ?
    """, (new_endtime, thread_id))
    
    conn.commit()
    rows_updated = cursor.rowcount
    conn.close()
    
    return rows_updated > 0

def get_entry(thread_id: str) -> Optional[Dict]:
    """
    Retrieve an entry for a specific thread.

    :param thread_id: The ID of the thread.
    :return: A dictionary representing the entry or None if not found.
    """
    conn = sqlite3.connect(DATABASE_FILE)
    cursor = conn.cursor()
    
    cursor.execute("""
        SELECT recruiter_id, starttime, endtime, role_type, embed_id, ingame_name, user_id
        FROM entries
        WHERE thread_id = ?
    """, (thread_id,))
    
    row = cursor.fetchone()
    conn.close()
    
    if row:
        return {
            "thread_id": thread_id,
            "recruiter_id": row[0],
            "starttime": row[1],
            "endtime": row[2],
            "role_type": row[3],
            "embed_id": row[4],
            "ingame_name": row[5],
            "user_id": row[6]
        }
    else:
        return None

###############
###############
###############


SWAT_ROLE_ID = 1321163290948145212

EMBED_ID_FILE = "embed.txt"
REQUESTS_FILE = "requests.json"  # <--- NEW: JSON file to store pending requests
GUILD_ID = 1300519755622383689
TRAINEE_NOTES_CHANNEL = 1334493226148691989
CADET_NOTES_CHANNEL = 1334493243018182699
TRAINEE_ROLE = 1321853549273157642
CADET_ROLE = 1321853586384093235
# SWAT_ROLE_ID = 91287123871623

embed_message_id = None  # Initialize as None
intents = discord.Intents.default()
intents.members = True  # Needed for on_member_join
intents.guilds = True

bot = commands.Bot(command_prefix="!", intents=intents)

# Replace with your channel ID
TARGET_CHANNEL_ID = 1334474489236557896

# Store the embed message ID for checking
embed_message_id = 1334481624427135018

# -----------------------------------------------------------
# NEW: Dictionary & Helpers to track user requests
# -----------------------------------------------------------
pending_requests = {}  # key: str(user_id), value: dict with request info
def safe_emoji(eid, default="⚫"):
        e = bot.get_emoji(eid)
        return str(e if e else default)

def get_rounded_time():
    now = datetime.now()
    # Calculate the number of minutes to add to round up to the nearest 15 minutes
    minutes_to_add = (15 - now.minute % 15) % 15
    rounded_time = now + timedelta(minutes=minutes_to_add)
    return rounded_time

def create_discord_timestamp(rounded_time):
    # Convert the rounded time to a Unix timestamp
    unix_timestamp = int(rounded_time.timestamp())
    # Create a Discord timestamp using the <t:...> format
    discord_timestamp = f"<t:{unix_timestamp}>"  # :t for short time format
    return discord_timestamp

def load_requests():
    """Load pending requests from the JSON file into memory."""
    global pending_requests
    if os.path.exists(REQUESTS_FILE):
        try:
            with open(REQUESTS_FILE, "r") as f:
                pending_requests = json.load(f)
        except (json.JSONDecodeError, IOError):
            pending_requests = {}
    else:
        pending_requests = {}

def save_requests():
    """Save current pending requests dictionary to disk."""
    with open(REQUESTS_FILE, "w") as f:
        json.dump(pending_requests, f)

def create_embed():
    """Create the main management embed with buttons."""
    embed = discord.Embed(
        title="Trainee Management",
        description="Please select an option below:",
        color=discord.Color.blue()
    )
    return embed

class TraineeView(ui.View):
    """Persistent view for the main management embed buttons."""
    def __init__(self):
        super().__init__(timeout=None)

    @ui.button(label="Request Trainee Role", style=discord.ButtonStyle.primary, custom_id="request_trainee_role")
    async def request_trainee_role(self, interaction: discord.Interaction, button: ui.Button):
        # Check if user already has a pending request
        if str(interaction.user.id) in pending_requests:
            await interaction.response.send_message(
                "You already have an open request. Wait until it is accepted or ignored.",
                ephemeral=True
            )
            return
        elif str(SWAT_ROLE_ID) in str(interaction.user.roles):
            await interaction.response.send_message("You are already in SWAT you dummy!", ephemeral=True)
            return
        elif str(CADET_ROLE) in str(interaction.user.roles) or str(TRAINEE_ROLE) in str(interaction.user.roles):
            await interaction.response.send_message("You are already a trainee you dummy!", ephemeral=True)
            return
        await interaction.response.send_modal(TraineeRoleModal())

    @ui.button(label="Request Name Change", style=discord.ButtonStyle.secondary, custom_id="request_name_change")
    async def request_name_change(self, interaction: discord.Interaction, button: ui.Button):
        # Check if user already has a pending request
        if str(interaction.user.id) in pending_requests:
            await interaction.response.send_message(
                "You already have an open request. Wait until it is accepted or ignored.",
                ephemeral=True
            )
            return
        await interaction.response.send_modal(NameChangeModal())

# --- NEW CLASS: Accept/Ignore Buttons ---
class RequestActionView(ui.View):
    """View that provides Accept/Ignore buttons for a newly created request embed."""
    def __init__(self, user_id: int, request_type: str, ingame_name: str = None, accepted_by: str = None, new_name: str = None):
        """
        Store the data so you can use it when Accept is pressed or ignored:
        - user_id: The Discord user ID who made the request
        - request_type: "trainee_role" or "name_change" (just an example)
        - ingame_name / accepted_by: For trainee role requests
        - new_name: For name change requests
        """
        super().__init__(timeout=None)
        self.user_id = user_id
        self.request_type = request_type
        self.ingame_name = ingame_name
        self.accepted_by = accepted_by
        self.new_name = new_name

    @ui.button(label="Accept", style=discord.ButtonStyle.success, custom_id="request_accept")
    async def accept_button(self, interaction: discord.Interaction, button: ui.Button):
        
        # Change embed color to green and update title
        guild = bot.get_guild(GUILD_ID)
        if guild:
            member = guild.get_member(self.user_id)
            temp_name = re.sub(r'(?:\s*\[(?:CADET|TRAINEE|SWAT)\])+$', '', member.nick, flags=re.IGNORECASE)
            new_nickname = f"{temp_name} [TRAINEE]"
            await member.edit(nick=new_nickname)
            temp_trainee_role = guild.get_role(TRAINEE_ROLE)
            await member.add_roles(temp_trainee_role)
            channel = guild.get_channel(TRAINEE_NOTES_CHANNEL)
            if channel:
                start_time = get_rounded_time()
                end_time = start_time + timedelta(days=7)
                thread = await channel.create_thread(name=str(self.ingame_name) + " | TRAINEE Notes", message=None, type=discord.ChannelType.public_thread, reason="New Trainee accepted", invitable=False, slowmode_delay=None)
                embed = await create_voting_embed(start_time, end_time, interaction.user.id)

                embed_msg = await thread.send(embed=embed)
                await embed_msg.add_reaction("<:plus_one:1334498534187208714>")
                await embed_msg.add_reaction("❔")
                await embed_msg.add_reaction("<:minus_one:1334498485390544989>")
                add_entry(thread.id, interaction.user.id, start_time, end_time, "trainee", embed_msg.id, self.ingame_name, self.user_id)
        
        embed = interaction.message.embeds[0]
        embed.color = discord.Color.green()
        embed.title += " (Accepted)"
        embed.add_field(name="Trainee file created by:", value="<@" + str(interaction.user.id) + ">", inline=False)
        await interaction.message.edit(embed=embed, view=None)

        # Remove user from pending requests and save
        user_id_str = str(self.user_id)
        if user_id_str in pending_requests:
            del pending_requests[user_id_str]
            save_requests()

            # await interaction.response.send_message("An error occured!", ephemeral=True)
        # -----------------------------------------
        # CODE WHERE YOU CAN CONTINUE IF THE REQUEST GOT ACCEPTED
        # You have access to:
        #   self.user_id
        #   self.ingame_name
        #   self.accepted_by
        #   self.new_name
        #   self.request_type
        # Example: Grant a role, log to DB, etc.
        # -----------------------------------------

  
    @ui.button(label="Ignore", style=discord.ButtonStyle.danger, custom_id="request_ignore")
    async def ignore_button(self, interaction: discord.Interaction, button: ui.Button):
        embed = interaction.message.embeds[0]
        # Change embed color to red and update title
        embed.color = discord.Color.red()
        embed.title += " (Ignored)"
        embed.add_field(name="Ignored by:", value="<@" + str(interaction.user.id) + ">", inline=False)
        await interaction.message.edit(embed=embed, view=None)
        await interaction.response.send_message("Request has been **ignored**.", ephemeral=True)

        # Remove user from pending requests and save
        user_id_str = str(self.user_id)
        if user_id_str in pending_requests:
            del pending_requests[user_id_str]
            save_requests()

# --- Trainee Role Modal ---
class TraineeRoleModal(ui.Modal, title="Request Trainee Role"):
    ingame_name = ui.TextInput(label="In-Game Name", placeholder="Enter your in-game name")
    accepted_by = ui.TextInput(label="Accepted By", placeholder="Enter the name of the person who accepted you")

    async def on_submit(self, interaction: discord.Interaction):
        ingame_name = self.ingame_name.value
        accepted_by = self.accepted_by.value
        user_id_str = str(interaction.user.id)

        # Immediately store as "pending" to block additional requests
        pending_requests[user_id_str] = {
            "request_type": "trainee_role",
            "ingame_name": ingame_name,
            "accepted_by": accepted_by
        }
        save_requests()

        guild = bot.get_guild(GUILD_ID)
        if guild:
            # Channel where request is posted
            channel = guild.get_channel(1334474601668804638)
            if channel:
                embed = discord.Embed(
                    title="New Trainee Role Request:",
                    description=f"User <@{interaction.user.id}> has requested a trainee role!",
                    colour=0x0080c0
                )
                embed.add_field(
                    name="In-Game Name:",
                    value=f"```{ingame_name}```",
                    inline=True
                )
                embed.add_field(
                    name="Accepted By:",
                    value=f"```{accepted_by}```",
                    inline=True
                )
                # Attach the Accept/Ignore buttons with data
                view = RequestActionView(
                    user_id=interaction.user.id,
                    request_type="trainee_role",
                    ingame_name=ingame_name,
                    accepted_by=accepted_by
                )
                await channel.send(embed=embed, view=view)

        # Send a success message to the user who filled out the form
        await interaction.response.send_message("Submitting successful!", ephemeral=True)

# --- Name Change Modal ---
class NameChangeModal(ui.Modal, title="Request Name Change"):
    new_name = ui.TextInput(label="New Name", placeholder="Enter your new name")

    async def on_submit(self, interaction: discord.Interaction):
        new_name = self.new_name.value
        user_id_str = str(interaction.user.id)

        # Immediately store as "pending" to block additional requests
        pending_requests[user_id_str] = {
            "request_type": "name_change",
            "new_name": new_name
        }
        save_requests()

        guild = bot.get_guild(GUILD_ID)
        if guild:
            # Channel where request is posted
            channel = guild.get_channel(1334474601668804638)
            if channel:
                embed = discord.Embed(
                    title="New Name Change Request:",
                    description=f"User <@{interaction.user.id}> has requested a name change!",
                    colour=0x298ecb
                )
                embed.add_field(
                    name="New Name:",
                    value=f"```{new_name}```",
                    inline=True
                )
                # Attach the Accept/Ignore buttons with data
                view = RequestActionView(
                    user_id=interaction.user.id,
                    request_type="name_change",
                    new_name=new_name
                )
                await channel.send(embed=embed, view=view)

        # Send a success message to the user
        await interaction.response.send_message("Submitting successful!", ephemeral=True)

@bot.event
async def on_ready():
    print(f'Logged in as {bot.user.name}')
    try:
        synced = await bot.tree.sync()  # Syncs all commands globally
        print(f"Synced {len(synced)} commands.")
    except Exception as e:
        print(f"Failed to sync commands: {e}")
        
    # Load the pending requests from file so we remember them on restart
    load_requests()

    # Register the persistent view for the main Trainee Management embed
    bot.add_view(TraineeView())

    # Load the embed_message_id from file
    global embed_message_id
    if os.path.exists(EMBED_ID_FILE):
        try:
            with open(EMBED_ID_FILE, "r") as f:
                embed_message_id = int(f.read().strip())
                print(f"Loaded embed_message_id: {embed_message_id}")
        except (ValueError, IOError) as e:
            print(f"Error reading {EMBED_ID_FILE}: {e}")
            embed_message_id = None
    else:
        embed_message_id = None

    # Start checking the main embed
    check_embed.start()

    # Ensure the main embed with TraineeView is present
    channel = bot.get_channel(TARGET_CHANNEL_ID)
    if channel:
        if embed_message_id:
            try:
                await channel.fetch_message(embed_message_id)
                print("Embed message exists. No need to send a new one.")
            except discord.NotFound:
                embed = create_embed()
                view = TraineeView()
                message = await channel.send(embed=embed, view=view)
                embed_message_id = message.id
                with open(EMBED_ID_FILE, "w") as f:
                    f.write(str(embed_message_id))
                print(f"Sent new embed and saved embed_message_id: {embed_message_id}")
        else:
            embed = create_embed()
            view = TraineeView()
            message = await channel.send(embed=embed, view=view)
            embed_message_id = message.id
            with open(EMBED_ID_FILE, "w") as f:
                f.write(str(embed_message_id))
            print(f"Sent new embed and saved embed_message_id: {embed_message_id}")

@bot.tree.command(name="hello", description="Say hello to the bot")
async def hello(interaction: discord.Interaction):
    await interaction.response.send_message(f"Hello, {interaction.user.mention}!")

@tasks.loop(minutes=5)
async def check_embed():
    """Periodically check if the main Trainee Management embed exists."""
    global embed_message_id
    channel = bot.get_channel(TARGET_CHANNEL_ID)
    if channel:
        try:
            await channel.fetch_message(embed_message_id)
        except discord.NotFound:
            # If the message is not found, resend it
            embed = create_embed()
            view = TraineeView()
            message = await channel.send(embed=embed, view=view)
            embed_message_id = message.id
            with open(EMBED_ID_FILE, "w") as f:
                f.write(str(embed_message_id))
            print(f"Embed not found. Sent new embed and updated embed_message_id: {embed_message_id}")
        except discord.Forbidden:
            print("Bot does not have permission to fetch messages in the channel.")
        except discord.HTTPException as e:
            print(f"Failed to fetch message: {e}")


######################################################################################### THREAD VOTING

async def create_voting_embed(start_time, end_time, recruiter, extended: bool = False):
    embed = discord.Embed(description="SWAT, please express your vote down below. Use <:plus_one:1334498534187208714>,❔ or <:minus_one:1334498485390544989> accordingly.", color=0x000000)
    if not isinstance(start_time, datetime):
        start_time = datetime.strptime(start_time, "%Y-%m-%d %H:%M:%S.%f")
    if not isinstance(end_time, datetime):
        end_time = datetime.strptime(end_time, "%Y-%m-%d %H:%M:%S.%f")
    
    

    embed.add_field(name="Voting started:",
                    value=create_discord_timestamp(start_time),
                    inline=True)
    if extended:
        embed.add_field(name="Voting will end: (Extended)",
            value=create_discord_timestamp(end_time),
            inline=True)
    else:
        embed.add_field(name="Voting will end:",
            value=create_discord_timestamp(end_time),
            inline=True)
    embed.add_field(name="Thread managed by:",
                    value="<@" + str(recruiter) + ">",
                    inline=False)
    return embed
 
@bot.tree.command(name="add_trainee", description="Manually add user as a trainee")
@app_commands.describe(user_id="What is the users id?", ingame_name="What is the users EXACT ingame name?")
async def thread_info(interaction: discord.Interaction, user_id: int, ingame_name: str):
    guild = bot.get_guild(GUILD_ID)

    if guild:
        member = guild.get_member(user_id)
        temp_name = re.sub(r'(?:\s*\[(?:CADET|TRAINEE|SWAT)\])+$', '', member.nick, flags=re.IGNORECASE)
        new_nickname = f"{temp_name} [TRAINEE]"
        await member.edit(nick=new_nickname)
        temp_trainee_role = guild.get_role(TRAINEE_ROLE)
        await member.add_roles(temp_trainee_role)
        channel = guild.get_channel(TRAINEE_NOTES_CHANNEL)
        if channel:
            start_time = get_rounded_time()
            end_time = start_time + timedelta(days=7)
            thread = await channel.create_thread(name=str(ingame_name) + " | TRAINEE Notes", message=None, type=discord.ChannelType.public_thread, reason="New Trainee accepted", invitable=False, slowmode_delay=None)
            embed = await create_voting_embed(start_time, end_time, interaction.user.id)
            embed_msg = await thread.send(embed=embed)
            await embed_msg.add_reaction("<:plus_one:1334498534187208714>")
            await embed_msg.add_reaction("❔")
            await embed_msg.add_reaction("<:minus_one:1334498485390544989>")
            add_entry(thread.id, interaction.user.id, start_time, end_time, "trainee", embed_msg.id, ingame_name, int(user_id))
            
    await interaction.response.send_message("Trainee added", ephemeral=True)

@bot.tree.command(name="votinginfo", description="Get information about the current voting thread")
async def thread_info(interaction: discord.Interaction):
    channel = interaction.channel  # The channel where the command was used

    if isinstance(channel, discord.Thread):
        thread = channel
        data = get_entry(thread.id)
        if data == None:
            await interaction.response.send_message("This thread is not associated with and trainee or cadet voting!", ephemeral=True)
        else:
            embed = discord.Embed(title="Voting Information", color=discord.Color.blue())
            embed.add_field(name="Name", value=thread.name, inline=False)
            embed.add_field(name="ID", value=thread.id, inline=False)
            embed.add_field(name="StartTime:", value=str(data["starttime"]), inline=False)
            embed.add_field(name="EndTime:", value=str(data["endtime"]), inline=False)
            embed.add_field(name="Type:", value=str(data["role_type"]), inline=False)
            embed.add_field(name="Recruiter:", value="<@" + str(data["recruiter_id"]) + ">", inline=False)
            embed.add_field(name="Embed ID:", value=str(data["embed_id"]), inline=False)
            embed.add_field(name="InGame Name:", value=str(data["ingame_name"]), inline=False)
            embed.add_field(name="User ID:", value="<@" + str(data["user_id"]) + ">", inline=False)
            await interaction.response.send_message(embed=embed, ephemeral=True)
    else:
        await interaction.response.send_message("This command was not used inside a thread.", ephemeral=True)
    
@bot.tree.command(name="lock", description="Lock and archive the current thread!")
async def thread_info(interaction: discord.Interaction):
    thread = interaction.channel
    await interaction.response.defer()
    try:
        if remove_entry(thread.id):
            await thread.edit(locked=True, archived=True)
            await interaction.followup.send("Thread has been locked and closed!", ephemeral=True)
            # await interaction.response.send_message("This thread has been closed!", ephemeral=True)
        else:
            await interaction.followup.send("Not a registered voting thread!", ephemeral=True)
    except discord.Forbidden:
        await interaction.followup.send("The bot doesn't have permissions to lock this thread!", ephemeral=True)
    except discord.HTTPException as e:
        await interaction.followup.send(f"An error occurred: {e}", ephemeral=True)

@bot.tree.command(name="promote", description="Promoting the user either to cadet or swat!")
async def thread_info(interaction: discord.Interaction):
    thread = interaction.channel
    data = get_entry(thread.id)
    if data == None:
        await interaction.response.send_message("This thread is not associated with and trainee or cadet voting!", ephemeral=True)
    elif data["role_type"] == "trainee":
        ## PROMOTING TO CADET
        thread = interaction.channel
        await interaction.response.defer()
        try:
            if remove_entry(thread.id):
                await thread.edit(locked=True, archived=True)
                await interaction.followup.send("Thread has been locked and closed!", ephemeral=True)
                # await interaction.response.send_message("This thread has been closed!", ephemeral=True)
            else:
                await interaction.followup.send("Not a registered voting thread!", ephemeral=True)
        except discord.Forbidden:
            await interaction.followup.send("The bot doesn't have permissions to lock this thread!", ephemeral=True)
        except discord.HTTPException as e:
            await interaction.followup.send(f"An error occurred: {e}", ephemeral=True)
        
        guild = bot.get_guild(GUILD_ID)
        if guild:
            member = guild.get_member(int(data["user_id"]))
            if member:
                temp_name = re.sub(r'(?:\s*\[(?:CADET|TRAINEE|SWAT)\])+$', '', member.nick, flags=re.IGNORECASE)
                new_nickname = f"{temp_name} [CADET]"
                await member.edit(nick=new_nickname)
                temp_trainee_role = guild.get_role(TRAINEE_ROLE)
                temp_cadet_role = guild.get_role(CADET_ROLE)
                await member.remove_roles(temp_trainee_role)
                await member.add_roles(temp_cadet_role)
                channel = guild.get_channel(CADET_NOTES_CHANNEL)
                if channel:
                    start_time = get_rounded_time()
                    end_time = start_time + timedelta(days=7)
                    thread = await channel.create_thread(name=str(data["ingame_name"]) + " | CADET Notes", message=None, type=discord.ChannelType.public_thread, reason="Promoted to cadet!", invitable=False, slowmode_delay=None)
                    embed = await create_voting_embed(start_time, end_time, interaction.user.id)
                    
                    embed_msg = await thread.send(embed=embed)
                    await embed_msg.add_reaction("<:plus_one:1334498534187208714>")
                    await embed_msg.add_reaction("❔")
                    await embed_msg.add_reaction("<:minus_one:1334498485390544989>")
                    add_entry(thread.id, data["recruiter_id"], start_time, end_time, "cadet", embed_msg.id, data["ingame_name"], data["user_id"])
            else:
                await interaction.followup.send("An error occured: User not found", ephemeral=True)
                
    elif data["role_type"] == "cadet":
        thread = interaction.channel
        await interaction.response.defer()
        try:
            if remove_entry(thread.id):
                await thread.edit(locked=True, archived=True)
                await interaction.followup.send("Thread has been locked and closed!", ephemeral=True)
                # await interaction.response.send_message("This thread has been closed!", ephemeral=True)
            else:
                await interaction.followup.send("Not a registered voting thread!", ephemeral=True)
        except discord.Forbidden:
            await interaction.followup.send("The bot doesn't have permissions to lock this thread!", ephemeral=True)
        except discord.HTTPException as e:
            await interaction.followup.send(f"An error occurred: {e}", ephemeral=True)
        
        guild = bot.get_guild(GUILD_ID)
        if guild:
            member = guild.get_member(int(data["user_id"]))
            temp_name = re.sub(r'(?:\s*\[(?:CADET|TRAINEE|SWAT)\])+$', '', member.nick, flags=re.IGNORECASE)
            new_nickname = f"{temp_name} [SWAT]"
            await member.edit(nick=new_nickname)
            temp_cadet_role = guild.get_role(CADET_ROLE)
            temp_swat_role = guild.get_role(SWAT_ROLE_ID)
            await member.remove_roles(temp_cadet_role)
            await member.add_roles(temp_swat_role)

@bot.tree.command(name="extend", description="Lock and archive the current thread!")
@app_commands.describe(days="How long should the thread be extended?")
async def thread_info(interaction: discord.Interaction, days: int):
    thread = interaction.channel
    data = get_entry(thread.id)
    if data == None:
        await interaction.response.send_message("This thread is not associated with and trainee or cadet voting!", ephemeral=True)
    else:
        if days > 0 and days < 50:
            new_end_time = datetime.strptime(data["endtime"], "%Y-%m-%d %H:%M:%S.%f") + timedelta(days=days)
            if update_endtime(thread.id, new_end_time):
                new_voting_message = await interaction.channel.fetch_message(int(data["embed_id"]))
                await new_voting_message.edit(embed=await create_voting_embed(data["starttime"], new_end_time, data["recruiter_id"], extended=True))
                embed = discord.Embed(description="This " + str(data["role_type"]) + " voting has been extended by " + str(days) + " days!",
                      colour=0x5b0edc)
                await interaction.response.send_message(embed=embed)
            else:
                await interaction.response.send_message("No thread id has been found!", ephemeral=True)
    
        else:
            await interaction.response.send_message("A thread can only be extended for 1 day up to 50 days!", ephemeral=True)

##################################################################### THREAD VOTING END



@bot.event
async def on_shutdown():
    # Save the embed_message_id on shutdown
    if embed_message_id:
        try:
            with open(EMBED_ID_FILE, "w") as f:
                f.write(str(embed_message_id))
            print(f"Saved embed_message_id: {embed_message_id} on shutdown")
        except IOError as e:
            print(f"Error saving embed_message_id on shutdown: {e}")

    # Also save current pending requests
    save_requests()

# Read token and run bot
with open("token.txt", "r") as file:
    TOKEN = file.read().strip()

bot.run(TOKEN)
