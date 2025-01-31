import discord
from discord import app_commands
from discord.ext import commands
import sqlite3
from datetime import datetime
import json
import os

# -----------------------
# CONFIGURATION
# -----------------------
# IDs of the roles to mention
LEADERSHIP_ROLE_ID = 1300539048225673226
RECRUITER_ROLE_ID  = 1334600500448067707

# ID of the channel where the ticket-embed will be posted
TICKET_CHANNEL_ID   = 1334880226089500732
EMBED_FILE   = "tickets_embed.json"
EMBED_TITLE  = "Open a Ticket Here"

# -----------------------
# DATABASE SETUP
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

# -----------------------
# BOT SETUP
# -----------------------
intents = discord.Intents.default()
intents.guilds = True
bot = commands.Bot(command_prefix="!", intents=intents)

init_db()  # Initialize the database

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
        role_id = LEADERSHIP_ROLE_ID if ticket_type == "leadership" else RECRUITER_ROLE_ID
        now_str = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC")
        
        # Create a private thread in the same channel
        channel = interaction.channel
        thread_name = f"[{ticket_type.capitalize()}] - {interaction.user.display_name}"
        thread = await channel.create_thread(
            name=thread_name,
            type=discord.ChannelType.private_thread,
            invitable=False
        )

        # Ping the appropriate role, then send an embed
        await thread.send(f"<@&{role_id}> <@{interaction.user.id}>")
        embed = discord.Embed(
            title="What can we do for you?",
            description="Please describe your issue or request below.",
            color=discord.Color.blue()
        )
        await thread.send(embed=embed)

        # Save the ticket info
        add_ticket(
            thread_id=str(thread.id),
            user_id=str(interaction.user.id),
            created_at=now_str,
            ticket_type=ticket_type
        )

        # Acknowledge to the user
        await interaction.response.send_message("✅ Your ticket has been created!", ephemeral=True)

# -----------------------
# BOT EVENTS
# -----------------------
@bot.event
async def on_ready():
    print(f"Logged in as {bot.user}.")
    try:
        synced = await bot.tree.sync()
        print(f"Synced {len(synced)} slash commands.")
    except Exception as e:
        print(f"Failed to sync commands: {e}")

    # Add the view so it's persistent across restarts
    bot.add_view(TicketView())

    # Start checking for the embed every 5 minutes
    ensure_ticket_embed.start()

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
    embed = discord.Embed(
        title="Ticket Closed",
        description=f"<@{interaction.user.id}> has closed this ticket. No more messages can be sent.",
        color=discord.Color.red()
    )
    await interaction.response.send_message(embed=embed)
    await interaction.channel.edit(locked=True, archived=True)



# -----------------------
# TASKS
# -----------------------

from discord.ext import tasks

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
    embed = discord.Embed(
        title=EMBED_TITLE,
        description="Click one of the buttons below to open a ticket.",
        color=discord.Color.green()
    )
    sent_msg = await channel.send(embed=embed, view=TicketView())

    # Save the new embed ID
    with open(EMBED_FILE, "w") as f:
        json.dump({"embed_id": sent_msg.id}, f)


# -----------------------
# RUN THE BOT
# -----------------------
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
