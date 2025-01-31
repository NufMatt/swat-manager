import discord
from discord import app_commands
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

# --------------------------------------
#               CONSTANTS
# --------------------------------------
DATABASE_FILE = "data.db"
EMBED_ID_FILE = "embed.txt"
REQUESTS_FILE = "requests.json"
GUILD_ID = 1300519755622383689

TRAINEE_NOTES_CHANNEL = 1334493226148691989
CADET_NOTES_CHANNEL   = 1334493243018182699
TRAINEE_CHAT_CHANNEL = 1334534670330761389
SWAT_CHAT_CHANNEL = 1324733745919692800

TRAINEE_ROLE = 1321853549273157642
CADET_ROLE   = 1321853586384093235
SWAT_ROLE_ID = 1321163290948145212
RECRUITER_ID = 1334600500448067707
LEADERSHIP_ID = 1300539048225673226

TARGET_CHANNEL_ID      = 1334474489236557896  # Channel for main embed
REQUESTS_CHANNEL_ID    = 1334474601668804638  # Where requests are posted

# --------------------------------------
#            DATABASE SETUP
# --------------------------------------
def initialize_database():
    """Initialize the SQLite database and create the entries table if it doesn't exist."""
    conn = sqlite3.connect(DATABASE_FILE)
    cursor = conn.cursor()
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS entries (
            thread_id TEXT PRIMARY KEY,
            recruiter_id TEXT NOT NULL,
            starttime TEXT NOT NULL,
            endtime TEXT,
            embed_id TEXT NOT NULL,
            ingame_name TEXT NOT NULL,
            user_id TEXT NOT NULL,
            region TEXT NOT NULL,
            reminder_sent INTEGER DEFAULT 0,
            role_type TEXT NOT NULL CHECK(role_type IN ('trainee', 'cadet'))
        )
    """)
    conn.commit()
    conn.close()

initialize_database()

def add_entry(thread_id: str, recruiter_id: str, starttime: datetime, endtime: datetime, 
              role_type: str, embed_id: str, ingame_name: str, user_id: str, region: str) -> bool:
    """Add a new entry to the database."""
    if role_type not in ("trainee", "cadet"):
        raise ValueError("role_type must be either 'trainee' or 'cadet'.")

    start_str = str(starttime)
    end_str   = str(endtime) if endtime else None

    try:
        conn = sqlite3.connect(DATABASE_FILE)
        cursor = conn.cursor()
        cursor.execute(
            """INSERT INTO entries (thread_id, recruiter_id, starttime, endtime, role_type, embed_id, ingame_name, user_id, region)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (thread_id, recruiter_id, start_str, end_str, role_type, embed_id, ingame_name, user_id, region)
        )
        conn.commit()
        return True
    except sqlite3.IntegrityError:
        return False
    finally:
        conn.close()

def remove_entry(thread_id: str) -> bool:
    """Remove an entry from the database based on thread_id."""
    conn = sqlite3.connect(DATABASE_FILE)
    cursor = conn.cursor()
    cursor.execute("DELETE FROM entries WHERE thread_id = ?", (thread_id,))
    conn.commit()
    rows_deleted = cursor.rowcount
    conn.close()
    return rows_deleted > 0

def update_endtime(thread_id: str, new_endtime: datetime) -> bool:
    """Update the endtime of an existing entry."""
    conn = sqlite3.connect(DATABASE_FILE)
    cursor = conn.cursor()
    cursor.execute("UPDATE entries SET endtime = ? WHERE thread_id = ?", (str(new_endtime), thread_id))
    conn.commit()
    rows_updated = cursor.rowcount
    conn.close()
    return rows_updated > 0

def get_entry(thread_id: str) -> Optional[Dict]:
    """Retrieve an entry for a specific thread."""
    conn = sqlite3.connect(DATABASE_FILE)
    cursor = conn.cursor()
    cursor.execute(
        """SELECT recruiter_id, starttime, endtime, role_type, embed_id, ingame_name, user_id, region
           FROM entries
           WHERE thread_id = ?""",
        (thread_id,)
    )
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
            "user_id": row[6],
            "region": row[7]
        }
    return None

def is_user_in_database(user_id: int) -> bool:
    try:
        # Connect to the SQLite database
        conn = sqlite3.connect(DATABASE_FILE)
        cursor = conn.cursor()
        
        # Execute the query to check for the user and role
        cursor.execute("""
            SELECT 1 FROM entries 
            WHERE user_id = ?
            LIMIT 1
        """, (str(user_id)))
        
        # Fetch one record
        result = cursor.fetchone()
        
        return result is not None
    except sqlite3.Error as e:
        print(f"Database error: {e}")
        return False
    finally:
        if conn:
            conn.close()

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
        except (json.JSONDecodeError, IOError):
            pending_requests = {}
    else:
        pending_requests = {}

def save_requests():
    """Save current pending requests dictionary to disk."""
    with open(REQUESTS_FILE, "w") as f:
        json.dump(pending_requests, f)

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
    embed = discord.Embed(title="**Welcome to the SWAT Community!** 🎉🚔",
                      description="📌 **Select the appropriate button below:**  \n\n🔹 **Request Trainee Role** – If you applied through the website and got accepted **and received a DM from a recruiter**, press this button! Fill in your **EXACT** in-game name, select the region you play in, and choose the recruiter who accepted you. If everything checks out, you’ll receive a message in the trainee chat!  \n\n🔹 **Request Name Change** – Need to update your name? Press this button and enter your new name **without any SWAT tags!** 🚨 **Make sure your IGN and Discord name match at all times!** If they don’t, request a name change here!  \n\n🔹 **Request Other** – Want a guest role or a friends role? Click here and type your request! We’ll handle the rest.  \n\n⚠️ **Important:** Follow the instructions carefully to avoid delays. Let’s get you set up and ready to roll! 🚀",
                      colour=0x008040)
    return embed

async def update_recruiters():
    guild = bot.get_guild(GUILD_ID)
    if not guild:
        print("Guild not found.")
        return

    recruiter_role = guild.get_role(RECRUITER_ID)
    if not recruiter_role:
        print("Recruiter role not found.")
        return

    recruiters = []
    for member in guild.members:
        if recruiter_role in member.roles:
            recruiters.append({
                "name": member.display_name,
                "id": member.id
            })

    # Update the global RECRUITERS list
    global RECRUITERS
    RECRUITERS = recruiters
    print("Updated recruiters list:", RECRUITERS)

async def set_user_nickname(member: discord.Member, role_label: str):
    """Remove any trailing [TRAINEE/Cadet/SWAT] bracketed text and set the new bracket."""
    base_nick = member.nick if member.nick else member.name
    temp_name = re.sub(r'(?:\s*\[(?:CADET|TRAINEE|SWAT)\])+$', '', base_nick, flags=re.IGNORECASE)
    await member.edit(nick=f"{temp_name} [{role_label.upper()}]")

async def close_thread(interaction: discord.Interaction, thread: discord.Thread) -> None:
    """Remove DB entry for the thread, lock & archive it."""
    try:
        if remove_entry(thread.id):
            await thread.edit(locked=True, archived=True)
        else:
            await interaction.followup.send("Not a registered voting thread!", ephemeral=True)
    except discord.Forbidden:
        await interaction.followup.send("Bot lacks permission to lock/archive this thread.", ephemeral=True)
    except discord.HTTPException as e:
        await interaction.followup.send(f"An error occurred: {e}", ephemeral=True)

async def create_voting_embed(start_time, end_time, recruiter: int, region, ingame_name, extended: bool = False) -> discord.Embed:
    """Create the standard voting embed with plus/minus/uncertain reactions."""
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
    region_name = region[:-1] if region[-1].isdigit() else region
    title = f"{flags.get(region_name, '')}{region}"
    embed.add_field(name="InGame Name:", value=ingame_name, inline=True)
    embed.add_field(name="Region:", value=title, inline=True)
    embed.add_field(name="", value="", inline=False)
    embed.add_field(name="Voting started:", value=create_discord_timestamp(start_time), inline=True)
    end_title = "Voting will end: (Extended)" if extended else "Voting will end:"
    embed.add_field(name=end_title, value=create_discord_timestamp(end_time), inline=True)
    embed.add_field(name="Thread managed by:", value=f"<@{recruiter}>", inline=False)
    return embed

# --------------------------------------
#   PERSISTENT VIEW & RELATED CLASSES
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
            await interaction.response.send_message("You already have an open request.", ephemeral=True)
            return
        if any(r.id == SWAT_ROLE_ID for r in interaction.user.roles):
            await interaction.response.send_message("You are already SWAT!", ephemeral=True)
            return
        if any(r.id in [TRAINEE_ROLE, CADET_ROLE] for r in interaction.user.roles):
            await interaction.response.send_message("You already have a trainee/cadet role!", ephemeral=True)
            return

        await interaction.response.send_modal(TraineeRoleModal())

    @discord.ui.button(label="Request Name Change", style=discord.ButtonStyle.secondary, custom_id="request_name_change")
    async def request_name_change(self, interaction: discord.Interaction, button: discord.ui.Button):
        user_id_str = str(interaction.user.id)
        
        if user_id_str in pending_requests:
            await interaction.response.send_message("You already have an open request.", ephemeral=True)
            return
        
        await interaction.response.send_modal(NameChangeModal())
    
    @discord.ui.button(label="Request Other", style=discord.ButtonStyle.secondary, custom_id="request_other")
    async def request_other(self, interaction: discord.Interaction, button: discord.ui.Button):
        user_id_str = str(interaction.user.id)
        
        if user_id_str in pending_requests:
            await interaction.response.send_message("You already have an open request.", ephemeral=True)
            return
        
        await interaction.response.send_modal(RequestOther())

class RequestActionView(discord.ui.View):
    """View with Accept/Ignore buttons for new request embed."""
    def __init__(self, user_id: int, request_type: str, ingame_name: str = None, recruiter: str = None, new_name: str = None, region: str = None):
        super().__init__(timeout=None)
        self.user_id     = user_id
        self.request_type= request_type
        self.ingame_name = ingame_name
        self.new_name    = new_name
        self.recruiter   = recruiter
        self.region      = region

    @discord.ui.button(label="Accept", style=discord.ButtonStyle.success, custom_id="request_accept")
    async def accept_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        embed = interaction.message.embeds[0]
        embed.color = discord.Color.green()
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
            if is_user_in_database(self.user_id):
                await interaction.response.send_message(
                    "There is already a user with this id in the database.",
                    ephemeral=True
                )
                return
            if guild:
                member = guild.get_member(self.user_id)
                if member:
                    await set_user_nickname(member, "trainee")
                    trainee_role_obj = guild.get_role(TRAINEE_ROLE)
                    if trainee_role_obj:
                        await member.add_roles(trainee_role_obj)
                    channel = guild.get_channel(TRAINEE_NOTES_CHANNEL)
                    if channel:
                        start_time = get_rounded_time()
                        # end_time   = start_time + timedelta(days=7)
                        end_time   = start_time + timedelta(minutes=1)
                        thread_name= f"{self.ingame_name} | TRAINEE Notes"
                        thread = await channel.create_thread(
                            name=thread_name,
                            message=None,
                            type=discord.ChannelType.public_thread,
                            reason="New Trainee accepted",
                            invitable=False
                        )
                        voting_embed = await create_voting_embed(start_time, end_time, interaction.user.id, self.region, self.ingame_name)
                        embed_msg = await thread.send(embed=voting_embed)
                        await embed_msg.add_reaction("<:plus_one:1334498534187208714>")
                        await embed_msg.add_reaction("❔")
                        await embed_msg.add_reaction("<:minus_one:1334498485390544989>")
                        
                        add_entry(
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
                        
                        ### SENDTRAINEEMESSAGE
                        trainee_channel = guild.get_channel(TRAINEE_CHAT_CHANNEL)
                        if trainee_channel:
                            message = random.choice(trainee_messages).replace("{username}", "<@" + str(self.user_id) + ">")
                            trainee_embed = discord.Embed(description=message, colour=0x008000)
                            await trainee_channel.send("<@" + str(self.user_id) + ">")
                            await trainee_channel.send(embed=trainee_embed)

        # If it's a name change request:
        elif self.request_type == "name_change":
            guild = bot.get_guild(GUILD_ID)
            if guild:
                member = guild.get_member(self.user_id)
                leadership_role = interaction.guild.get_role(LEADERSHIP_ID)
                if leadership_role not in interaction.user.roles:
                    await interaction.response.send_message("You do not have permission to use this embed.", ephemeral=True)
                    return
    
                if member:
                    base_nick = member.nick if member.nick else member.name
                    
                    # Remove any role tag from the new name (if present)
                    new_name_cleaned = re.sub(r'(?:\s*\[(?:CADET|TRAINEE|SWAT)\])+$', '', str(self.new_name), flags=re.IGNORECASE)
                    
                    # Keep bracket suffix if user had one in the original nickname
                    suffix_match = re.search(r'\[(CADET|TRAINEE|SWAT)\]', base_nick, flags=re.IGNORECASE)
                    suffix = suffix_match.group(0) if suffix_match else ""
                    changing_name = new_name_cleaned + (" " + suffix if suffix else "")
                try:
                    await member.edit(nick=changing_name)
                except Exception as e:
                    await interaction.response.send_message(f"Error occurred while updating the nickname: {e}", ephemeral=True)
                    return
                        
        await interaction.message.edit(embed=embed, view=None)

    @discord.ui.button(label="Ignore", style=discord.ButtonStyle.danger, custom_id="request_ignore")
    async def ignore_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if self.request_type == "name_change" or self.request_type == "other":
            leadership_role = interaction.guild.get_role(LEADERSHIP_ID)
            if leadership_role not in interaction.user.roles:
                await interaction.response.send_message("You do not have permission to use this embed.", ephemeral=True)
                return
        else:
            recruiter_role = interaction.guild.get_role(RECRUITER_ID)
            if recruiter_role not in interaction.user.roles:
                await interaction.response.send_message("You do not have permission to use this embed.", ephemeral=True)
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

# --------------------------------------
#            MODAL CLASSES
# --------------------------------------
async def finalize_trainee_request(interaction: discord.Interaction, user_id_str: str):
    """Finalize the trainee request after selections."""
    request = pending_requests.get(user_id_str)
    if not request:
        await interaction.followup.send("No pending request found to finalize.", ephemeral=True)
        return
    
    recruiter_role = interaction.guild.get_role(RECRUITER_ID)
    if recruiter_role not in interaction.user.roles:
        await interaction.response.send_message("You do not have permission to use this embed.", ephemeral=True)
        return
    
    region = request.get("region")
    recruiter_name = request.get("selected_recruiter_name")
    recruiter_id = request.get("selected_recruiter_id")  # Access the recruiter's ID
    
    if not region or not recruiter_name or not recruiter_id:
        await interaction.followup.send("Please complete all selections.", ephemeral=True)
        return
    
    # Proceed to create the request embed with all data
    guild = bot.get_guild(GUILD_ID)
    if guild:
        channel = guild.get_channel(REQUESTS_CHANNEL_ID)
        if channel:
            embed = discord.Embed(
                title="New Trainee Role Request:",
                description=f"User <@{interaction.user.id}> has requested a trainee role!",
                color=0x0080c0
            )
            embed.add_field(name="In-Game Name:", value=f"```{request['ingame_name']}```", inline=True)
            embed.add_field(name="Accepted By:", value=f"```{recruiter_name}```", inline=True)  # Use recruiter's ID and name
            embed.add_field(name="Region:", value=f"```{region}```", inline=True)
    
            view = RequestActionView(
                user_id=interaction.user.id,
                request_type="trainee_role",
                ingame_name=request['ingame_name'],
                region=region,
                recruiter=recruiter_name  # Pass the recruiter's name (or ID if needed)
            )
            await channel.send(f"<@{recruiter_id}>")
            await channel.send(embed=embed, view=view)
    
    # Optionally, notify the user
    await interaction.followup.send("Your trainee role request has been submitted!", ephemeral=True)

# Define your list of recruiters
RECRUITERS = ["Bain", "Arcadia", "Happy"]  # Replace with actual recruiter names or IDs

class RegionSelect(discord.ui.Select):
    def __init__(self):
        options = [
            discord.SelectOption(label="EU", description="Europe"),
            discord.SelectOption(label="NA", description="North America"),
            discord.SelectOption(label="SEA", description="Southeast Asia"),
        ]
        super().__init__(
            placeholder="Select what region you play the most!",
            min_values=1,
            max_values=1,
            options=options
        )
    
    async def callback(self, interaction: discord.Interaction):
        user_id_str = str(self.view.user_id)
        selected_region = self.values[0]
        
        # Update the pending request with the selected region
        if user_id_str in pending_requests:
            pending_requests[user_id_str]["region"] = selected_region
            save_requests()
            await interaction.response.send_message(f"Region selected: {selected_region}", ephemeral=True)
        else:
            await interaction.response.send_message("No pending request found.", ephemeral=True)

class RecruiterSelect(discord.ui.Select):
    def __init__(self):
        options = [
            discord.SelectOption(label=rec["name"], description=f"Recruiter: {rec['name']}", value=str(rec["id"]))
            for rec in RECRUITERS
        ]
        super().__init__(
            placeholder="Select the person which accepted you...",
            min_values=1,
            max_values=1,
            options=options
        )
    
    async def callback(self, interaction: discord.Interaction):
        user_id_str = str(self.view.user_id)
        selected_recruiter_id = self.values[0]
        
        # Find the selected recruiter's name and ID
        selected_recruiter = next((rec for rec in RECRUITERS if str(rec["id"]) == selected_recruiter_id), None)
        
        if selected_recruiter:
            # Update the pending request with the selected recruiter's name and ID
            if user_id_str in pending_requests:
                pending_requests[user_id_str]["selected_recruiter_name"] = selected_recruiter["name"]
                pending_requests[user_id_str]["selected_recruiter_id"] = selected_recruiter["id"]
                save_requests()
                await interaction.response.send_message(f"Recruiter selected: {selected_recruiter['name']}", ephemeral=True)
                
                # Finalize the request after recruiter selection
                await finalize_trainee_request(interaction, user_id_str)
            else:
                await interaction.response.send_message("No pending request found.", ephemeral=True)
        else:
            await interaction.response.send_message("Selected recruiter not found.", ephemeral=True)

class TraineeDropdownView(discord.ui.View):
    def __init__(self, user_id: int):
        super().__init__(timeout=None)
        self.user_id = user_id
        self.add_item(RegionSelect())
        self.add_item(RecruiterSelect())

class TraineeRoleModal(discord.ui.Modal, title="Request Trainee Role"):
    ingame_name = discord.ui.TextInput(label="In-Game Name", placeholder="Enter your in-game name")
    
    async def on_submit(self, interaction: discord.Interaction):
        user_id_str = str(interaction.user.id)
        
        # Store initial modal data
        pending_requests[user_id_str] = {
            "request_type": "trainee_role",
            "ingame_name": self.ingame_name.value
            # 'region' and 'selected_recruiter' will be added after dropdown selection
        }
        save_requests()
    
        # Send the dropdown view
        view = TraineeDropdownView(user_id=interaction.user.id)
        await interaction.response.send_message(
            "Please select your **Region** and **Recruiter** below:",
            view=view,
            ephemeral=True
        )
        
class NameChangeModal(discord.ui.Modal, title="Request Name Change"):
    new_name = discord.ui.TextInput(label="New Name", placeholder="Enter your new name")

    async def on_submit(self, interaction: discord.Interaction):
        user_id_str = str(interaction.user.id)
        pending_requests[user_id_str] = {
            "request_type": "name_change",
            "new_name": self.new_name.value
        }
        save_requests()

        guild = bot.get_guild(GUILD_ID)
        if guild:
            channel = guild.get_channel(REQUESTS_CHANNEL_ID)
            if channel:
                base_nick = interaction.user.nick if interaction.user.nick else interaction.user.name
                new_name_cleaned = re.sub(r'(?:\s*\[(?:CADET|TRAINEE|SWAT)\])+$', '', self.new_name.value, flags=re.IGNORECASE)
                suffix_match = re.search(r'\[(CADET|TRAINEE|SWAT)\]', base_nick, flags=re.IGNORECASE)
                suffix = suffix_match.group(0) if suffix_match else ""
                new_name = new_name_cleaned + (" " + suffix if suffix else "")
            
                embed = discord.Embed(
                    title="New Name Change Request:",
                    description=f"User <@{interaction.user.id}> has requested a name change!",
                    colour=0x298ecb
                )
                embed.add_field(name="New Name:", value=f"```{new_name}```", inline=True)

                view = RequestActionView(
                    user_id=interaction.user.id,
                    request_type="name_change",
                    new_name=self.new_name.value
                )
                await channel.send(embed=embed, view=view)

        await interaction.response.send_message("Submitting successful!", ephemeral=True)

class RequestOther(discord.ui.Modal, title="RequestOther"):
    other = discord.ui.TextInput(label="Requesting:", placeholder="What do you want to request?")

    async def on_submit(self, interaction: discord.Interaction):
        user_id_str = str(interaction.user.id)
        pending_requests[user_id_str] = {
            "request_type": "other",
            "other": self.other.value
        }
        save_requests()

        guild = bot.get_guild(GUILD_ID)
        if guild:
            channel = guild.get_channel(REQUESTS_CHANNEL_ID)
            if channel:
                embed = discord.Embed(
                    title="New Other Request:",
                    description=f"User <@{interaction.user.id}> has requested Other!",
                    colour=0x298ecb
                )
                embed.add_field(name="New Name:", value=f"```{self.other.value}```", inline=True)

                view = RequestActionView(
                    user_id=interaction.user.id,
                    request_type="other",
                    new_name=self.other.value
                )
                await channel.send(embed=embed, view=view)

        await interaction.response.send_message("Submitting successful!", ephemeral=True)
# --------------------------------------
#         BOT EVENTS & COMMANDS
# --------------------------------------
@bot.event
async def on_ready():
    print(f'Logged in as {bot.user.name}')
    try:
        synced = await bot.tree.sync()
        print(f"Synced {len(synced)} commands.")
    except Exception as e:
        print(f"Failed to sync commands: {e}")
    
    load_requests()  # Load any pending requests from disk
    bot.add_view(TraineeView())  # Register the persistent view

    global embed_message_id
    if os.path.exists(EMBED_ID_FILE):
        try:
            with open(EMBED_ID_FILE, "r") as f:
                embed_message_id = int(f.read().strip())
                print(f"Loaded embed_message_id: {embed_message_id}")
        except (ValueError, IOError) as e:
            print(f"Error reading {EMBED_ID_FILE}: {e}")
            embed_message_id = None

    check_embed.start()  # Start the periodic check task
    update_recruiters_task.start()
    check_expired_endtimes.start()

    channel = bot.get_channel(TARGET_CHANNEL_ID)

@bot.tree.command(name="hello", description="Say hello to the bot")
async def hello_command(interaction: discord.Interaction):
    await interaction.response.send_message(f"Hello, {interaction.user.mention}!")

@tasks.loop(minutes=5)
async def check_embed():
    """Periodically ensure the main Trainee Management embed is present."""
    global embed_message_id
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
            print(f"Embed not found; sent new embed and updated embed_message_id: {embed_message_id}")
        except discord.Forbidden:
            print("Bot lacks permission to fetch messages in this channel.")
        except discord.HTTPException as e:
            print(f"Failed to fetch message: {e}")

@tasks.loop(minutes=10)
async def update_recruiters_task():
    await update_recruiters()

@tasks.loop(minutes=1)
async def check_expired_endtimes():
    # Connect to the SQLite database
    conn = sqlite3.connect(DATABASE_FILE)
    cursor = conn.cursor()
    
    # Get the current UTC time in ISO format
    now = datetime.now()
    
    # Query for entries where endtime has passed and reminder_sent is False
    cursor.execute("""
        SELECT thread_id, recruiter_id, starttime, role_type , region, ingame_name
        FROM entries 
        WHERE endtime <= ? AND reminder_sent = 0
    """, (now,))
    
    expired_entries = cursor.fetchall()
    
    for thread_id, recruiter_id, starttime, role_type, region, ingame_name in expired_entries:
        # Fetch the thread object
        thread = bot.get_channel(int(thread_id))
        
        if thread and isinstance(thread, discord.Thread):
            # Calculate the number of days since the thread was opened
            start_time = datetime.strptime(starttime, "%Y-%m-%d %H:%M:%S.%f")
            days_open = (now - start_time).days
            
            # Create the embed
            embed = discord.Embed(
                description=f"**Reminder:** This thread has been open for **{days_open} days**.",
                color=0x008040
            )
            
            # For trainee threads, ping the recruiter
            if role_type == "trainee":
                recruiter = bot.get_user(int(recruiter_id))
                if recruiter:
                    await thread.send(f"<@{recruiter_id}>", embed=embed)
                else:
                    await thread.send(embed=embed)
            
            # For cadet threads, just send the embed
            elif role_type == "cadet":
                if not isinstance(start_time, datetime):
                    start_time = datetime.strptime(str(start_time), "%Y-%m-%d %H:%M:%S.%f")

                voting_embed = discord.Embed(
                    description=(
                        "SWAT, please express your vote below.\n"
                        "Use <:plus_one:1334498534187208714>, ❔, or <:minus_one:1334498485390544989> accordingly."
                    ),
                    color=0x000000
                )
                flags = {"EU": "🇪🇺 ", "NA": "🇺🇸 ", "SEA": "🇸🇬 "}
                region_name = region[:-1] if region[-1].isdigit() else region
                title = f"{flags.get(region_name, '')}{region}"
                voting_embed.add_field(name="InGame Name:", value=ingame_name, inline=True)
                voting_embed.add_field(name="Region:", value=title, inline=True)
                voting_embed.add_field(name="", value="", inline=False)
                voting_embed.add_field(name="Voting started:", value=create_discord_timestamp(start_time), inline=True)
                voting_embed.add_field(name="Voting has ended!", value="", inline=True)
                voting_embed.add_field(name="Thread managed by:", value=f"<@{recruiter_id}>", inline=False)
                await thread.send("<@&" + str(SWAT_ROLE_ID) + ">  It's time for another cadet voting!⌛")
                embed_msg = await thread.send(embed=voting_embed)
                await embed_msg.add_reaction("<:plus_one:1334498534187208714>")
                await embed_msg.add_reaction("❔")
                await embed_msg.add_reaction("<:minus_one:1334498485390544989>")

            
            # Mark the reminder as sent in the database
            cursor.execute("""
                UPDATE entries 
                SET reminder_sent = 1 
                WHERE thread_id = ?
            """, (thread_id,))
            conn.commit()
        else:
            print(f"Thread with ID {thread_id} not found or is not a thread.")
    
    conn.close()

# --------------------------------------
#     STAFF / MANAGEMENT COMMANDS
# --------------------------------------
# Command to add a trainee
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
async def add_trainee_command(interaction: discord.Interaction, user_id: str, ingame_name: str, region: app_commands.Choice[str]):
    """Forcibly add a user as trainee and create a voting thread."""
    # Check if the user has the recruiter role
    user_id = int(user_id)
    recruiter_role = interaction.guild.get_role(RECRUITER_ID)
    if recruiter_role not in interaction.user.roles:
        await interaction.response.send_message("You do not have permission to use this command.", ephemeral=True)
        return

    if is_user_in_database(user_id):
        await interaction.response.send_message(
            "This trainee is already in the database.",
            ephemeral=True
        )
        return

    guild = bot.get_guild(GUILD_ID)
    if not guild:
        await interaction.response.send_message("Guild not found.", ephemeral=True)
        return
    

    member = guild.get_member(user_id)
    if not member:
        await interaction.response.send_message("User not found in guild!", ephemeral=True)
        return

    await interaction.response.defer(ephemeral=True)
    await set_user_nickname(member, "trainee")
    role_obj = guild.get_role(TRAINEE_ROLE)
    if role_obj:
        await member.add_roles(role_obj)

    channel = guild.get_channel(TRAINEE_NOTES_CHANNEL)
    if channel:
        start_time = get_rounded_time()
        end_time = start_time + timedelta(days=7)  # Or use timedelta(minutes=1) for testing
        thread_name = f"{ingame_name} | TRAINEE Notes"
        thread = await channel.create_thread(
            name=thread_name,
            message=None,
            type=discord.ChannelType.public_thread,
            reason="New Trainee accepted",
            invitable=False
        )
        voting_embed = await create_voting_embed(start_time, end_time, interaction.user.id, region.value, ingame_name)
        embed_msg = await thread.send(embed=voting_embed)
        await embed_msg.add_reaction("<:plus_one:1334498534187208714>")
        await embed_msg.add_reaction("❔")
        await embed_msg.add_reaction("<:minus_one:1334498485390544989>")

        add_entry(
            thread_id=thread.id,
            recruiter_id=str(interaction.user.id),
            starttime=start_time,
            endtime=end_time,
            role_type="trainee",
            embed_id=str(embed_msg.id),
            ingame_name=ingame_name,
            user_id=str(user_id),
            region=region.value
        )
        await interaction.followup.send("Trainee added successfully!", ephemeral=True)
    else:
        await interaction.followup.send("Cannot find the trainee notes channel.", ephemeral=True)

@bot.tree.command(name="votinginfo", description="Show info about the current voting thread")
async def votinginfo_command(interaction: discord.Interaction):
    """Display info about the currently used thread, if it exists in DB."""
    channel = interaction.channel
    if not isinstance(channel, discord.Thread):
        await interaction.response.send_message("Use this command inside a thread.", ephemeral=True)
        return

    data = get_entry(channel.id)
    if not data:
        await interaction.response.send_message("This thread is not associated with any trainee/cadet voting!", ephemeral=True)
        return

    embed = discord.Embed(title="Voting Information", color=discord.Color.blue())
    embed.add_field(name="Thread Name", value=channel.name, inline=False)
    embed.add_field(name="Thread ID",  value=channel.id, inline=False)
    embed.add_field(name="Start Time", value=str(data["starttime"]), inline=False)
    embed.add_field(name="End Time",   value=str(data["endtime"]),   inline=False)
    embed.add_field(name="Type",       value=data["role_type"],      inline=False)
    embed.add_field(name="Recruiter",  value=f"<@{data['recruiter_id']}>", inline=False)
    embed.add_field(name="Embed ID",   value=str(data["embed_id"]),  inline=False)
    embed.add_field(name="InGame Name",value=data["ingame_name"],    inline=False)
    embed.add_field(name="User ID",    value=f"<@{data['user_id']}>",inline=False)
    embed.add_field(name="Region",    value=data['region'],inline=False)
    await interaction.response.send_message(embed=embed, ephemeral=True)

@bot.tree.command(name="remove", description="Removes them from trainee / cadet programm and closes thread!")
async def lock_thread_command(interaction: discord.Interaction):
    """Close the thread if it's a valid voting thread."""
    recruiter_role = interaction.guild.get_role(RECRUITER_ID)
    if recruiter_role not in interaction.user.roles:
        await interaction.response.send_message("You do not have permission to use this command.", ephemeral=True)
        return

    if not isinstance(interaction.channel, discord.Thread):
        await interaction.response.send_message("This is not a thread.", ephemeral=True)
        return

    data = get_entry(interaction.channel.id)
    if not data:
        await interaction.response.send_message("No DB entry for this thread!", ephemeral=True)
        return
    
    await interaction.response.defer()
    await close_thread(interaction, interaction.channel)

    guild = bot.get_guild(GUILD_ID)
    if not guild:
        return

    member = guild.get_member(int(data["user_id"]))
    if not member:
        await interaction.followup.send("User not found in guild!", ephemeral=True)
        return
    
    temp_name = re.sub(r'(?:\s*\[(?:CADET|TRAINEE|SWAT)\])+$', '', member.nick, flags=re.IGNORECASE)
    await member.edit(nick=temp_name)
    t_role = guild.get_role(TRAINEE_ROLE)
    c_role = guild.get_role(CADET_ROLE)
    if t_role in member.roles:
        await member.remove_roles(t_role)
    elif c_role in member.roles:
        await member.remove_roles(c_role)
    
    
    embed = discord.Embed(title="❌ " + str(data["ingame_name"]) + " has been removed!", colour=0xf94144)
    embed.set_footer(text="🔒This thread is locked now!")
    
    await interaction.followup.send(embed=embed)

@bot.tree.command(name="promote", description="Promote the user in the current voting thread (Trainee->Cadet or Cadet->SWAT).")
async def promote_user_command(interaction: discord.Interaction):
    """Promote a user from Trainee->Cadet or Cadet->SWAT, closing the old thread and creating a new one if needed."""
    recruiter_role = interaction.guild.get_role(RECRUITER_ID)
    if recruiter_role not in interaction.user.roles:
        await interaction.response.send_message("You do not have permission to use this command.", ephemeral=True)
        return

    if not isinstance(interaction.channel, discord.Thread):
        await interaction.response.send_message("This command must be used in a thread.", ephemeral=True)
        return

    data = get_entry(interaction.channel.id)
    if not data:
        await interaction.response.send_message("No DB entry for this thread!", ephemeral=True)
        return

    await interaction.response.defer()
    removed = remove_entry(interaction.channel.id)
    if removed:
        await interaction.channel.edit(locked=True, archived=True)
        if data["role_type"] == "trainee":
            promotion = "Cadet"
        elif data["role_type"] == "cadet":
            promotion = "SWAT Officer"
        embed = discord.Embed(title="🏅 " + str(data["ingame_name"]) + " has been promoted to " + str(promotion) + "!🎉", colour=0x43bccd)
        embed.set_footer(text="🔒This thread is locked now!")
        await interaction.followup.send(embed=embed)
    else:
        await interaction.followup.send("Not a registered voting thread!", ephemeral=True)
        return

    guild = bot.get_guild(GUILD_ID)
    if not guild:
        return

    member = guild.get_member(int(data["user_id"]))
    if not member:
        await interaction.followup.send("User not found in guild!", ephemeral=True)
        return

    old_role_type = data["role_type"]
    ingame_name   = data["ingame_name"]

    if old_role_type == "trainee":
        # Promote to CADET
        await set_user_nickname(member, "cadet")
        t_role = guild.get_role(TRAINEE_ROLE)
        c_role = guild.get_role(CADET_ROLE)
        if t_role in member.roles:
            await member.remove_roles(t_role)
        await member.add_roles(c_role)

        # New CADET thread
        channel_obj = guild.get_channel(CADET_NOTES_CHANNEL)
        if channel_obj:
            start_time = get_rounded_time()
            # end_time   = start_time + timedelta(days=7)
            end_time   = start_time + timedelta(minutes=1)
            thread = await channel_obj.create_thread(
                name=f"{ingame_name} | CADET Notes",
                message=None,
                type=discord.ChannelType.public_thread,
                reason="Promoted to cadet!",
                invitable=False
            )
            voting_embed = await create_voting_embed(start_time, end_time, interaction.user.id, data["region"], ingame_name)
            embed_msg = await thread.send(embed=voting_embed)
            await embed_msg.add_reaction("<:plus_one:1334498534187208714>")
            await embed_msg.add_reaction("❔")
            await embed_msg.add_reaction("<:minus_one:1334498485390544989>")

            ### SENDTRAINEEMESSAGE
            swat_chat = guild.get_channel(SWAT_CHAT_CHANNEL)
            
            if swat_chat:
                message = random.choice(cadet_messages).replace("{username}", "<@" + str(data["user_id"]) + ">")
                embed = discord.Embed(description=message, colour=0x008000)
                await swat_chat.send("<@" + str(data["user_id"]) + ">")
                await swat_chat.send(embed=embed)

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
        await member.send(welcome_to_swat)

@bot.tree.command(name="extend", description="Extend the current thread's voting period.")
@app_commands.describe(days="How many days to extend?")
async def extend_thread_command(interaction: discord.Interaction, days: int):
    """Extend the voting period for the currently open thread."""
    recruiter_role = interaction.guild.get_role(RECRUITER_ID)
    if recruiter_role not in interaction.user.roles:
        await interaction.response.send_message("You do not have permission to use this command.", ephemeral=True)
        return

    if not isinstance(interaction.channel, discord.Thread):
        await interaction.response.send_message("Use this in a thread channel.", ephemeral=True)
        return

    data = get_entry(interaction.channel.id)
    if not data:
        await interaction.response.send_message("No DB entry for this thread!", ephemeral=True)
        return

    if days < 1 or days > 50:
        await interaction.response.send_message("You can only extend from 1 to 50 days!", ephemeral=True)
        return

    old_end = datetime.strptime(data["endtime"], "%Y-%m-%d %H:%M:%S.%f")
    new_end = old_end + timedelta(days=days)
    if update_endtime(interaction.channel.id, new_end):
        msg = await interaction.channel.fetch_message(int(data["embed_id"]))
        new_embed = await create_voting_embed(data["starttime"], new_end, int(data["recruiter_id"]), data["region"], data["ingame_name"], extended=True)
        await msg.edit(embed=new_embed)

        embed = discord.Embed(description="This " + str(data['role_type']) + " voting has been extended by " + str(days) + " days!", colour=0xf9c74f)
        await interaction.response.send_message(embed=embed)
    else:
        await interaction.response.send_message("Failed to update endtime.", ephemeral=True)

# --------------------------------------
#        SHUTDOWN AND BOT LAUNCH
# --------------------------------------
@bot.event
async def on_shutdown():
    # Save the embed_message_id on shutdown
    global embed_message_id
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
