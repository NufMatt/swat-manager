# cogs/tickets.py

import discord
from discord import app_commands, ButtonStyle
from discord.ext import commands, tasks
import asyncio, os, json, sqlite3, re, traceback
from datetime import datetime, timedelta
from config_testing import (
    GUILD_ID, TICKET_CHANNEL_ID, TOKEN_FILE,
    LEADERSHIP_ID, RECRUITER_ID, LEAD_BOT_DEVELOPER_ID, SWAT_ROLE_ID,
    RECRUITER_EMOJI, LEADERSHIP_EMOJI, LEAD_BOT_DEVELOPER_EMOJI, ACTIVITY_CHANNEL_ID
)
from messages import OPEN_TICKET_EMBED_TEXT
from cogs.helpers import log, create_user_activity_log_embed, get_stored_embed, set_stored_embed

# -------------------------------
# Ticket Database Functions
# -------------------------------

def init_ticket_db():
    try:
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
        # Log success
        log("Ticket database initialized successfully.")
    except sqlite3.Error as e:
        log(f"Database init error for tickets.db: {e}", level="error")
        pass
    finally:
        conn.close()

active_tickets = {}

def add_ticket(thread_id: str, user_id: str, created_at: str, ticket_type: str):
    try:
        conn = sqlite3.connect("tickets.db")
        cur = conn.cursor()
        cur.execute("""
            INSERT OR IGNORE INTO tickets (thread_id, user_id, created_at, ticket_type)
            VALUES (?, ?, ?, ?)
        """, (thread_id, user_id, created_at, ticket_type))
        conn.commit()
        log(f"Added ticket: thread_id={thread_id}, user_id={user_id}, ticket_type={ticket_type}")
        active_tickets[thread_id] = {
            "user_id": user_id,
            "created_at": created_at,
            "ticket_type": ticket_type
        }
    except sqlite3.Error as e:
        log(f"Error adding ticket (thread_id={thread_id}): {e}", level="error")
        pass
    finally:
        conn.close()

def get_ticket_info(thread_id: str):
    try:
        conn = sqlite3.connect("tickets.db")
        cur = conn.cursor()
        cur.execute("""
            SELECT thread_id, user_id, created_at, ticket_type FROM tickets
            WHERE thread_id = ?
        """, (thread_id,))
        row = cur.fetchone()
        return row
    except sqlite3.Error as e:
        log(f"Error reading ticket_info for {thread_id}: {e}", level="error")
        return None
    finally:
        conn.close()

def remove_ticket(thread_id: str):
    try:
        conn = sqlite3.connect("tickets.db")
        cur = conn.cursor()
        cur.execute("DELETE FROM tickets WHERE thread_id = ?", (thread_id,))
        conn.commit()
        log(f"Removed ticket from DB: thread_id={thread_id}")
    except sqlite3.Error as e:
        log(f"Error removing ticket {thread_id} from DB: {e}", level="error")
        pass
    finally:
        conn.close()

init_ticket_db()

# -------------------------------
# Persistent Views and Modal(s)
# -------------------------------

class CloseThreadView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)
    
    @discord.ui.button(label="Close Thread", style=discord.ButtonStyle.danger, custom_id="close_thread")
    async def close_thread_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        thread = interaction.channel
        log(f"Attempting close_thread_button for thread_id={thread.id if thread else 'None'} by user {interaction.user.id}")
        if not interaction.guild or interaction.guild.id != GUILD_ID:
            await interaction.response.send_message("❌ This command can only be used in the specified guild.", ephemeral=True)
            return

        ticket_data = get_ticket_info(str(thread.id))
        if not ticket_data:
            await interaction.response.send_message("❌ No ticket data found for this thread.", ephemeral=True)
            log("No ticket data found for that thread_id in DB.")
            return

        # Determine which role is allowed to close the ticket.
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
            log(f"User {interaction.user.id} not allowed to close thread_id {thread.id}, required role missing.")
            return

        try:
            remove_ticket(str(thread.id))
            embed = discord.Embed(
                title=f"Ticket closed by {interaction.user.display_name}",
                colour=0xf51616
            )
            embed.set_footer(text="🔒This ticket is locked now!")
            await interaction.response.send_message(embed=embed)
            await thread.edit(locked=True, archived=True)
            log(f"Ticket {thread.id} closed successfully by user {interaction.user.id}.")
        except discord.Forbidden:
            await interaction.response.send_message("❌ I don't have permission to close this thread.", ephemeral=True)
            log(f"Forbidden error closing thread {thread.id}", level="error")
        except discord.HTTPException as e:
            await interaction.response.send_message(f"❌ Failed to close thread: {e}", ephemeral=True)
            log(f"HTTP error closing thread {thread.id}: {e}", level="error")

class LOAModal(discord.ui.Modal, title="Leave of Absence (LOA)"):
    reason = discord.ui.TextInput(
        label="Reason for LOA",
        style=discord.TextStyle.long,
        placeholder="Explain why you need a leave of absence...",
        required=True
    )
    end_date = discord.ui.TextInput(
        label="End Date (YYYY-MM-DD)",
        placeholder="Enter the date you plan to return (e.g., 2023-12-31)",
        required=True
    )

    async def on_submit(self, interaction: discord.Interaction):
        log(f"LOAModal submitted by {interaction.user.id}.")
        try:
            # Validate date format
            datetime.strptime(self.end_date.value, "%Y-%m-%d")
        except ValueError:
            await interaction.response.send_message("❌ Invalid date format. Please use YYYY-MM-DD.", ephemeral=True)
            log("User provided invalid date format in LOAModal.")
            return

        now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S UTC")
        channel = interaction.channel
        thread_name = f"[LOA] - {interaction.user.display_name}"
        thread = await channel.create_thread(
            name=thread_name,
            type=discord.ChannelType.private_thread,
            invitable=False
        )

        try:
            embed = discord.Embed(
                title="🎟️ LOA Request",
                description=f"**User:** <@{interaction.user.id}>\n**Reason:** {self.reason.value}\n**End Date:** {self.end_date.value}",
                color=0x158225
            )
            await thread.send(f"<@&{LEADERSHIP_ID}> <@{interaction.user.id}>")
            await thread.send(embed=embed, view=CloseThreadView())
            add_ticket(str(thread.id), str(interaction.user.id), now_str, "loa")
            await interaction.response.send_message("✅ Your LOA request has been submitted!", ephemeral=True)
            log(f"LOA ticket created for user {interaction.user.id}, thread_id={thread.id}")
        except discord.Forbidden:
            await interaction.response.send_message("❌ Forbidden: Cannot send messages in the thread.", ephemeral=True)
            log(f"Forbidden error sending LOA messages in thread {thread.id if thread else 'None'}", level="error")
        except discord.HTTPException as e:
            await interaction.response.send_message(f"❌ HTTP Error sending messages: {e}", ephemeral=True)
            log(f"HTTP error sending LOA embed in thread {thread.id if thread else 'None'}: {e}", level="error")

class TicketView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)
    
    @discord.ui.button(label="Leadership", style=discord.ButtonStyle.primary, custom_id="leadership_ticket")
    async def leadership_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        log(f"Leadership ticket button pressed by user {interaction.user.id}.")
        await self.create_ticket(interaction, "leadership")
    
    @discord.ui.button(label="Recruiters", style=discord.ButtonStyle.secondary, custom_id="recruiter_ticket")
    async def recruiter_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        log(f"Recruiter ticket button pressed by user {interaction.user.id}.")
        await self.create_ticket(interaction, "recruiters")
    
    @discord.ui.button(label="Lead Bot Developer", style=discord.ButtonStyle.secondary, custom_id="botdeveloper_ticket")
    async def botdeveloper_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        log(f"Botdeveloper ticket button pressed by user {interaction.user.id}.")
        await self.create_ticket(interaction, "botdeveloper")
    
    @discord.ui.button(label="LOA", style=discord.ButtonStyle.secondary, custom_id="loa_ticket")
    async def loa_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        log(f"LOA ticket button pressed by user {interaction.user.id}.")
        await interaction.response.send_modal(LOAModal())
    
    async def create_ticket(self, interaction: discord.Interaction, ticket_type: str):
        log(f"Attempting create_ticket of type {ticket_type} by user {interaction.user.id}.")
        if not interaction.guild or interaction.guild.id != GUILD_ID:
            await interaction.response.send_message("❌ This command can only be used in the specified guild.", ephemeral=True)
            log("User tried to create ticket in wrong guild or no guild.")
            return
        
        # Choose the role to ping based on ticket type.
        if ticket_type == "leadership":
            role_id = LEADERSHIP_ID
        elif ticket_type == "botdeveloper":
            role_id = LEAD_BOT_DEVELOPER_ID
        else:
            role_id = RECRUITER_ID

        now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S UTC")
        channel = interaction.channel
        thread_name = f"[{ticket_type.capitalize()}] - {interaction.user.display_name}"
        thread = await channel.create_thread(
            name=thread_name,
            type=discord.ChannelType.private_thread,
            invitable=False
        )

        try:
            if ticket_type == "botdeveloper":
                await thread.send(f"<@&{role_id}> <@294842627017408512> <@{interaction.user.id}>")
            else:
                await thread.send(f"<@&{role_id}> <@{interaction.user.id}>")
            
            embed = discord.Embed(
                title="🎟️ Ticket Opened",
                description=("Thank you for reaching out! Our team will assist you shortly.\n\n"
                             "📌 In the meantime, please provide more details about your issue.\n"
                             "⏳ Please be patient – we’ll be with you soon!"),
                colour=0x158225
            )
            await thread.send(embed=embed, view=CloseThreadView())
            log(f"Created ticket thread {thread.id} for user {interaction.user.id}, type={ticket_type}")
        except discord.Forbidden:
            await interaction.response.send_message("❌ Forbidden: Cannot send messages in the thread.", ephemeral=True)
            log(f"Forbidden: can't send messages in thread {thread.id if thread else 'None'}.", level="error")
            return
        except discord.HTTPException as e:
            await interaction.response.send_message(f"❌ HTTP Error sending messages: {e}", ephemeral=True)
            log(f"HTTP error sending ticket embed: {e}", level="error")
            return

        add_ticket(str(thread.id), str(interaction.user.id), now_str, ticket_type)
        await interaction.response.send_message("✅ Your ticket has been created!", ephemeral=True)

# -------------------------------
# Ticket Cog
# -------------------------------

class TicketCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        # Register persistent views.
        bot.add_view(TicketView())
        bot.add_view(CloseThreadView())
        # Start a task to ensure the ticket embed is present.
        self.ensure_ticket_embed_task.start()
        # Load existing tickets from DB.
        self.bot.loop.create_task(self.load_existing_tickets())

    def cog_unload(self):
        self.ensure_ticket_embed_task.cancel()
        log("TicketCog has been unloaded; ensure_ticket_embed_task canceled.")

    async def _run_embed_check_on_start(self):
        # Wait until bot is ready, then run the embed check once:
        await self.bot.wait_until_ready()
        await self._ensure_ticket_embed_check()
        log("Initial ticket embed check completed after startup.")

    @tasks.loop(minutes=5)
    async def ensure_ticket_embed_task(self):
        await self.bot.wait_until_ready()
        await self._ensure_ticket_embed_check()

    async def _ensure_ticket_embed_check(self):
        channel = self.bot.get_channel(TICKET_CHANNEL_ID)
        if not channel:
            log(f"Ticket channel {TICKET_CHANNEL_ID} not found, cannot ensure ticket embed.", level="error")
            return
        stored_embed_id = None
        stored = get_stored_embed("tickets_embed")
        if stored:
            stored_embed_id = stored.get("message_id")
            log(f"Loaded stored ticket embed id from DB: {stored_embed_id}")
        if stored_embed_id:
            try:
                await channel.fetch_message(stored_embed_id)
                log(f"Ticket embed {stored_embed_id} found, no action needed.")
                return
            except (discord.NotFound, discord.Forbidden, discord.HTTPException):
                log(f"Stored ticket embed {stored_embed_id} not found or cannot be accessed; creating new one.", level="warning")
                pass
        description = OPEN_TICKET_EMBED_TEXT\
            .replace("{leadership_emoji}", LEADERSHIP_EMOJI)\
            .replace("{recruiter_emoji}", RECRUITER_EMOJI)\
            .replace("{leaddeveloper_emoji}", LEAD_BOT_DEVELOPER_EMOJI)
        embed = discord.Embed(title="🎟️ Open a Ticket", description=description, colour=0x28afcc)
        sent_msg = await channel.send(embed=embed, view=TicketView())
        set_stored_embed("tickets_embed", str(sent_msg.id), str(channel.id))
        log(f"New ticket embed created with id {sent_msg.id} in channel {channel.id}.")

    async def load_existing_tickets(self):
        conn = sqlite3.connect("tickets.db")
        cur = conn.cursor()
        cur.execute("SELECT thread_id, user_id, created_at, ticket_type FROM tickets")
        rows = cur.fetchall()
        conn.close()
        for row in rows:
            thread_id, user_id, created_at, ticket_type = row
            thread = self.bot.get_channel(int(thread_id))
            if thread and isinstance(thread, discord.Thread):
                add_ticket(thread_id, user_id, created_at, ticket_type)
                log(f"Re-registered ticket from DB: thread_id={thread_id}, user_id={user_id}")
            else:
                print(f"❌ Could not find thread with ID: {thread_id}")
                log(f"Thread {thread_id} from DB not found as a valid channel. Possibly archived or missing.")

    @app_commands.command(name="ticket_internal", description="Creates a ticket without pinging anybody!")
    async def ticket_internal(self, interaction: discord.Interaction):
        now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S UTC")
        if not interaction.guild or interaction.guild.id != GUILD_ID:
            await interaction.response.send_message("❌ This command can only be used in the specified guild.", ephemeral=True)
            return
        leadership_role = interaction.guild.get_role(LEADERSHIP_ID)
        if not leadership_role or leadership_role not in interaction.user.roles:
            await interaction.response.send_message("❌ You do not have permission to open a private ticket.", ephemeral=True)
            return
        channel = self.bot.get_channel(TICKET_CHANNEL_ID)
        if channel:
            thread_name = f"[INT] - {interaction.user.display_name}"
            thread = await channel.create_thread(
                name=thread_name,
                type=discord.ChannelType.private_thread,
                invitable=False
            )
            try:
                await thread.send(f"<@{interaction.user.id}>")
                embed = discord.Embed(
                    title="🔒 Private Ticket Opened",
                    description=("This ticket is private. To invite someone, please **tag them** in this thread.\n\n"
                                 "📌 Only tagged members will be able to see and respond."),
                    colour=0xe9ee1e
                )
                await thread.send(embed=embed, view=CloseThreadView())
                log(f"Private (INT) ticket created by user {interaction.user.id}, thread_id={thread.id}")
            except discord.Forbidden:
                await interaction.response.send_message("❌ Forbidden: Cannot send messages in the thread.", ephemeral=True)
                log(f"Forbidden error sending private ticket messages in {thread.id if thread else 'None'}.", level="error")
                return
            except discord.HTTPException as e:
                await interaction.response.send_message(f"❌ HTTP Error sending messages: {e}", ephemeral=True)
                log(f"HTTP error sending private ticket embed: {e}", level="error")
                return
            add_ticket(str(thread.id), str(interaction.user.id), now_str, "other")
            await interaction.response.send_message("✅ Your ticket has been created!", ephemeral=True)
            activity_channel = self.bot.get_channel(ACTIVITY_CHANNEL_ID)
            if activity_channel:
                embed = create_user_activity_log_embed("tickets", f"Internal Ticket opened", interaction.user, f"User has opened an internal ticket. (Thread ID: <#{thread.id}>)")
                await activity_channel.send(embed=embed)
        else:
            await interaction.response.send_message("❌ Ticket channel not found", ephemeral=True)
            log(f"Ticket channel {TICKET_CHANNEL_ID} not found to create private ticket.", level="error")

    @app_commands.command(name="ticket_info", description="Show info about the current ticket thread.")
    async def ticket_info(self, interaction: discord.Interaction):
        if not isinstance(interaction.channel, discord.Thread):
            await interaction.response.send_message("❌ Use this command in a ticket thread.", ephemeral=True)
            return
        if not interaction.guild or interaction.guild.id != GUILD_ID:
            await interaction.response.send_message("❌ This command can only be used in the specified guild.", ephemeral=True)
            return
        thread_id = str(interaction.channel.id)
        ticket_data = active_tickets.get(thread_id)
        if not ticket_data:
            await interaction.response.send_message("❌ This thread is not a registered ticket.", ephemeral=True)
            return
        embed = discord.Embed(title="Ticket Information", color=discord.Color.blue())
        embed.add_field(name="Thread ID", value=thread_id, inline=False)
        embed.add_field(name="User", value=f"<@{ticket_data['user_id']}>", inline=False)
        embed.add_field(name="Created At (UTC)", value=ticket_data["created_at"], inline=False)
        embed.add_field(name="Ticket Type", value=ticket_data["ticket_type"], inline=False)
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @app_commands.command(name="ticket_close", description="Close the current ticket.")
    async def ticket_close(self, interaction: discord.Interaction):
        thread = interaction.channel
        log(f"User {interaction.user.id} issued ticket_close in thread {thread.id if thread else 'None'}.")
        if not interaction.guild or interaction.guild.id != GUILD_ID:
            await interaction.response.send_message("❌ This command can only be used in the specified guild.", ephemeral=True)
            return
        ticket_data = get_ticket_info(str(thread.id))
        if not ticket_data:
            await interaction.response.send_message("❌ No ticket data found for this thread.", ephemeral=True)
            log("No ticket data in DB for that thread. Canceling close.")
            return
        if ticket_data[3] == "recruiters":
            closing_role = interaction.guild.get_role(RECRUITER_ID)
        elif ticket_data[3] == "botdeveloper":
            closing_role = interaction.guild.get_role(LEAD_BOT_DEVELOPER_ID)
        elif ticket_data[3] == "loa":
            closing_role = interaction.guild.get_role(LEADERSHIP_ID)
        else:
            closing_role = interaction.guild.get_role(LEADERSHIP_ID)
        if not closing_role or (closing_role not in interaction.user.roles and interaction.user.id != int(ticket_data[1])):
            await interaction.response.send_message("❌ You do not have permission to close this ticket.", ephemeral=True)
            log(f"User {interaction.user.id} not authorized to close thread {thread.id}. Required role missing.")
            return
        try:
            data_check = get_ticket_info(str(interaction.channel.id))
            if not data_check:
                await interaction.response.send_message("❌ This thread is not a registered ticket.", ephemeral=True)
                log("Data check reveals no DB entry for thread, cannot close.")
                return
            remove_ticket(str(thread.id))
            embed = discord.Embed(title=f"Ticket closed by {interaction.user.display_name}", colour=0xf51616)
            embed.set_footer(text="🔒This ticket is locked now!")
            await interaction.response.send_message(embed=embed)
            await interaction.channel.edit(locked=True, archived=True)
            log(f"Ticket {thread.id} closed successfully by user {interaction.user.id}.")
        except discord.Forbidden:
            await interaction.response.send_message("❌ I don't have permission to close this thread.", ephemeral=True)
            log(f"Forbidden error closing thread {thread.id}", level="error")
        except discord.HTTPException as e:
            await interaction.response.send_message(f"❌ Failed to close thread: {e}", ephemeral=True)
            log(f"HTTP error closing thread {thread.id}: {e}", level="error")
        except Exception as e:
            await interaction.response.send_message(f"❌ An unexpected error occurred: {e}", ephemeral=True)
            log(f"Unexpected error closing ticket {thread.id}: {e}", level="error")

async def setup(bot: commands.Bot):
    await bot.add_cog(TicketCog(bot))
    log("Tickets cog loaded.")