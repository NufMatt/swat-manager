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
# Ticket & LOA-Reminder Database Initialization
# -------------------------------

def init_ticket_db():
    try:
        conn = sqlite3.connect("data.db")
        cur = conn.cursor()
        cur.execute("""
            CREATE TABLE IF NOT EXISTS tickets (
                thread_id   TEXT PRIMARY KEY,
                user_id     TEXT NOT NULL,
                created_at  TEXT NOT NULL,
                ticket_type TEXT NOT NULL
            )
        """)
        conn.commit()
        log("Ticket database initialized successfully.")
    except sqlite3.Error as e:
        log(f"Database init error for tickets: {e}", level="error")
    finally:
        conn.close()

def init_loa_db():
    try:
        conn = sqlite3.connect("data.db")
        cur = conn.cursor()
        cur.execute("""
            CREATE TABLE IF NOT EXISTS loa_reminders (
                thread_id     TEXT PRIMARY KEY,
                user_id       TEXT NOT NULL,
                end_date      TEXT NOT NULL,  -- ISO YYYY-MM-DD
                reminder_sent INTEGER NOT NULL -- 0 = not sent, 1 = sent
            )
        """)
        conn.commit()
        log("LOA reminder database initialized successfully.")
    except sqlite3.Error as e:
        log(f"Database init error for LOA reminders: {e}", level="error")
    finally:
        conn.close()

init_ticket_db()
init_loa_db()

# -------------------------------
# Ticket Database Operations
# -------------------------------

active_tickets = {}

def add_ticket(thread_id: str, user_id: str, created_at: str, ticket_type: str):
    try:
        conn = sqlite3.connect("data.db")
        cur = conn.cursor()
        cur.execute("""
            INSERT OR IGNORE INTO tickets (thread_id, user_id, created_at, ticket_type)
            VALUES (?, ?, ?, ?)
        """, (thread_id, user_id, created_at, ticket_type))
        conn.commit()
        active_tickets[thread_id] = {
            "user_id": user_id,
            "created_at": created_at,
            "ticket_type": ticket_type
        }
        log(f"Added ticket: thread_id={thread_id}, user_id={user_id}, type={ticket_type}")
    except sqlite3.Error as e:
        log(f"Error adding ticket (thread_id={thread_id}): {e}", level="error")
    finally:
        conn.close()

def get_ticket_info(thread_id: str):
    try:
        conn = sqlite3.connect("data.db")
        cur = conn.cursor()
        cur.execute("""
            SELECT thread_id, user_id, created_at, ticket_type
            FROM tickets WHERE thread_id = ?
        """, (thread_id,))
        return cur.fetchone()
    except sqlite3.Error as e:
        log(f"Error reading ticket_info for {thread_id}: {e}", level="error")
        return None
    finally:
        conn.close()

def remove_ticket(thread_id: str):
    try:
        conn = sqlite3.connect("data.db")
        cur = conn.cursor()
        cur.execute("DELETE FROM tickets WHERE thread_id = ?", (thread_id,))
        conn.commit()
        active_tickets.pop(thread_id, None)
        log(f"Removed ticket from DB: thread_id={thread_id}")
    except sqlite3.Error as e:
        log(f"Error removing ticket {thread_id} from DB: {e}", level="error")
    finally:
        conn.close()

# -------------------------------
# LOA Reminder Database Operations
# -------------------------------

def add_loa_reminder(thread_id: str, user_id: str, end_date_iso: str):
    try:
        conn = sqlite3.connect("data.db")
        cur = conn.cursor()
        cur.execute("""
            INSERT OR REPLACE INTO loa_reminders (thread_id, user_id, end_date, reminder_sent)
            VALUES (?, ?, ?, 0)
        """, (thread_id, user_id, end_date_iso))
        conn.commit()
        log(f"LOA reminder added: thread_id={thread_id}, end_date={end_date_iso}")
    except sqlite3.Error as e:
        log(f"Error adding LOA reminder {thread_id}: {e}", level="error")
    finally:
        conn.close()

def get_loa_reminder(thread_id: str):
    try:
        conn = sqlite3.connect("data.db")
        cur = conn.cursor()
        cur.execute("""
            SELECT thread_id, user_id, end_date, reminder_sent
            FROM loa_reminders WHERE thread_id = ?
        """, (thread_id,))
        return cur.fetchone()
    except sqlite3.Error as e:
        log(f"Error reading LOA reminder for {thread_id}: {e}", level="error")
        return None
    finally:
        conn.close()

def remove_loa_reminder(thread_id: str):
    try:
        conn = sqlite3.connect("data.db")
        cur = conn.cursor()
        cur.execute("DELETE FROM loa_reminders WHERE thread_id = ?", (thread_id,))
        conn.commit()
        log(f"LOA reminder removed: thread_id={thread_id}")
    except sqlite3.Error as e:
        log(f"Error removing LOA reminder {thread_id}: {e}", level="error")
    finally:
        conn.close()

def update_loa_end_date(thread_id: str, new_end_date_iso: str):
    try:
        conn = sqlite3.connect("data.db")
        cur = conn.cursor()
        cur.execute("""
            UPDATE loa_reminders
            SET end_date = ?, reminder_sent = 0
            WHERE thread_id = ?
        """, (new_end_date_iso, thread_id))
        conn.commit()
        log(f"LOA reminder extended: thread_id={thread_id}, new_end_date={new_end_date_iso}")
    except sqlite3.Error as e:
        log(f"Error updating LOA reminder {thread_id}: {e}", level="error")
    finally:
        conn.close()

def mark_reminder_sent(thread_id: str):
    try:
        conn = sqlite3.connect("data.db")
        cur = conn.cursor()
        cur.execute("""
            UPDATE loa_reminders
            SET reminder_sent = 1
            WHERE thread_id = ?
        """, (thread_id,))
        conn.commit()
        log(f"LOA reminder marked sent: thread_id={thread_id}")
    except sqlite3.Error as e:
        log(f"Error marking reminder sent for {thread_id}: {e}", level="error")
    finally:
        conn.close()

def get_expired_loa():
    today_iso = datetime.utcnow().date().isoformat()
    try:
        conn = sqlite3.connect("data.db")
        cur = conn.cursor()
        cur.execute("""
            SELECT thread_id, user_id
            FROM loa_reminders
            WHERE end_date < ? AND reminder_sent = 0
        """, (today_iso,))
        return cur.fetchall()
    except sqlite3.Error as e:
        log(f"Error fetching expired LOA: {e}", level="error")
        return []
    finally:
        conn.close()

# -------------------------------
# Persistent Views and Modals
# -------------------------------

class CloseThreadView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="Close Thread", style=ButtonStyle.danger, custom_id="close_thread")
    async def close_thread_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        thread = interaction.channel
        log(f"Attempting close_thread_button for thread_id={thread.id if thread else 'None'} by user {interaction.user.id}")

        # Only in the configured guild
        if not interaction.guild or interaction.guild.id != GUILD_ID:
            return await interaction.response.send_message(
                "❌ This command can only be used in the specified guild.", ephemeral=True
            )

        # Pull from SQLite instead of the in-memory dict
        ticket_data = get_ticket_info(str(thread.id))
        if not ticket_data:
            return await interaction.response.send_message(
                "❌ No ticket data found for this thread.", ephemeral=True
            )

        # ticket_data is a tuple: (thread_id, user_id, created_at, ticket_type)
        thread_id, user_id, created_at, ticket_type = ticket_data

        # Block closing if there's an active LOA reminder
        if ticket_type == "loa" and get_loa_reminder(thread_id):
            return await interaction.response.send_message(
                "❌ You must remove the active LOA first with `/loa_remove` before closing this thread.",
                ephemeral=True
            )

        # Determine which role can close this type
        if ticket_type == "recruiters":
            closing_role = interaction.guild.get_role(RECRUITER_ID)
        elif ticket_type == "botdeveloper":
            closing_role = interaction.guild.get_role(LEAD_BOT_DEVELOPER_ID)
        else:
            closing_role = interaction.guild.get_role(LEADERSHIP_ID)

        # Only the opener or the role may close
        if closing_role not in interaction.user.roles and interaction.user.id != int(user_id):
            return await interaction.response.send_message(
                "❌ You do not have permission to close this ticket.", ephemeral=True
            )

        # Perform the close
        try:
            remove_ticket(thread_id)
            embed = discord.Embed(
                title=f"Ticket closed by {interaction.user.display_name}",
                colour=0xf51616
            )
            embed.set_footer(text="🔒This ticket is locked now!")
            await interaction.response.send_message(embed=embed)
            await thread.edit(locked=True, archived=True)
            log(f"Ticket {thread_id} closed successfully by user {interaction.user.id}.")
        except discord.Forbidden:
            await interaction.response.send_message(
                "❌ I don't have permission to close this thread.", ephemeral=True
            )
            log(f"Forbidden error closing thread {thread_id}", level="error")
        except discord.HTTPException as e:
            await interaction.response.send_message(f"❌ Failed to close thread: {e}", ephemeral=True)
            log(f"HTTP error closing thread {thread_id}: {e}", level="error")

class LOAModal(discord.ui.Modal, title="Leave of Absence (LOA)"):
    reason = discord.ui.TextInput(
        label="Reason for LOA",
        style=discord.TextStyle.long,
        placeholder="Explain why you need a leave of absence...",
        required=True
    )
    end_date = discord.ui.TextInput(
        label="End Date (DD-MM-YYYY)",
        placeholder="Enter return date, e.g., 31-12-2025",
        required=True
    )

    async def on_submit(self, interaction: discord.Interaction):
        log(f"LOAModal submitted by {interaction.user.id}.")
        # Parse DD-MM-YYYY
        try:
            end_date_obj = datetime.strptime(self.end_date.value, "%d-%m-%Y")
        except ValueError:
            await interaction.response.send_message("❌ Invalid date format. Please use DD-MM-YYYY.", ephemeral=True)
            return

        if end_date_obj.date() < datetime.utcnow().date():
            await interaction.response.send_message("❌ End date cannot be in the past.", ephemeral=True)
            return

        now_str = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC")
        channel = interaction.channel
        thread = await channel.create_thread(
            name=f"[LOA] - {interaction.user.display_name}",
            type=discord.ChannelType.private_thread,
            invitable=False
        )

        try:
            embed = discord.Embed(
                title="🎟️ LOA Request",
                description=(
                    f"**User:** <@{interaction.user.id}>\n"
                    f"**Reason:** {self.reason.value}\n"
                    f"**End Date:** {self.end_date.value}"
                ),
                color=0x158225
            )
            embed.set_footer(text="Please add LOA role before accepting the Leave of Absence!")
            await thread.send(f"<@&{LEADERSHIP_ID}> <@{interaction.user.id}>")
            await thread.send(embed=embed, view=CloseThreadView())
            add_ticket(str(thread.id), str(interaction.user.id), now_str, "loa")
            await interaction.response.send_message("✅ Your LOA request has been submitted!", ephemeral=True)
            log(f"LOA ticket created for user {interaction.user.id}, thread_id={thread.id}")
        except discord.Forbidden:
            await interaction.response.send_message("❌ Forbidden: Cannot send messages in the thread.", ephemeral=True)
            log(f"Forbidden error sending LOA messages in thread {thread.id}", level="error")
        except discord.HTTPException as e:
            await interaction.response.send_message(f"❌ HTTP Error sending messages: {e}", ephemeral=True)
            log(f"HTTP error sending LOA embed in thread {thread.id}: {e}", level="error")

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

    @discord.ui.button(label="Bot Developer", style=discord.ButtonStyle.secondary, custom_id="botdeveloper_ticket")
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
            return

        # Choose role to ping based on ticket type
        if ticket_type == "leadership":
            role_id = LEADERSHIP_ID
        elif ticket_type == "botdeveloper":
            role_id = LEAD_BOT_DEVELOPER_ID
        else:
            role_id = RECRUITER_ID

        now_str = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC")
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
                description=(
                    "Thank you for reaching out! Our team will assist you shortly.\n\n"
                    "📌 In the meantime, please provide more details about your issue.\n"
                    "⏳ Please be patient – we’ll be with you soon!"
                ),
                colour=0x158225
            )
            await thread.send(embed=embed, view=CloseThreadView())
            log(f"Created ticket thread {thread.id} for user {interaction.user.id}, type={ticket_type}")
        except discord.Forbidden:
            await interaction.response.send_message("❌ Forbidden: Cannot send messages in the thread.", ephemeral=True)
            log(f"Forbidden: can't send messages in thread {thread.id}.", level="error")
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
        # Register persistent views
        bot.add_view(TicketView())
        bot.add_view(CloseThreadView())
        # Start background tasks
        self.ensure_ticket_embed_task.start()
        self.loa_reminder_task.start()
        # Load DB tickets into memory
        self.bot.loop.create_task(self.load_existing_tickets())

    def cog_unload(self):
        self.ensure_ticket_embed_task.cancel()
        self.loa_reminder_task.cancel()
        log("TicketCog unloaded; tasks canceled.")

    # -------------------------------
    # Ensure Ticket Embed in Channel
    # -------------------------------

    @tasks.loop(minutes=5)
    async def ensure_ticket_embed_task(self):
        await self.bot.wait_until_ready()
        await self._ensure_ticket_embed_check()

    async def _ensure_ticket_embed_check(self):
        channel = self.bot.get_channel(TICKET_CHANNEL_ID)
        if not channel:
            log(f"Ticket channel {TICKET_CHANNEL_ID} not found.", level="error")
            return

        stored = get_stored_embed("tickets_embed")
        stored_id = stored.get("message_id") if stored else None

        if stored_id:
            try:
                await channel.fetch_message(int(stored_id))
                return  # embed exists
            except Exception:
                log(f"Stored embed {stored_id} missing; recreating.", level="warning")

        description = (OPEN_TICKET_EMBED_TEXT
                       .replace("{leadership_emoji}", LEADERSHIP_EMOJI)
                       .replace("{recruiter_emoji}", RECRUITER_EMOJI)
                       .replace("{leaddeveloper_emoji}", LEAD_BOT_DEVELOPER_EMOJI))
        embed = discord.Embed(title="🎟️ Open a Ticket", description=description, colour=0x28afcc)
        sent = await channel.send(embed=embed, view=TicketView())
        set_stored_embed("tickets_embed", str(sent.id), str(channel.id))
        log(f"New ticket embed created: {sent.id}")

    # -------------------------------
    # Load Existing Tickets on Start
    # -------------------------------

    async def load_existing_tickets(self):
        await self.bot.wait_until_ready()
        conn = sqlite3.connect("data.db")
        cur = conn.cursor()
        cur.execute("SELECT thread_id, user_id, created_at, ticket_type FROM tickets")
        rows = cur.fetchall()
        conn.close()
        for thread_id, user_id, created_at, ticket_type in rows:
            try:
                thread = self.bot.get_channel(int(thread_id))
                if thread and isinstance(thread, discord.Thread):
                    active_tickets[thread_id] = {
                        "user_id": user_id,
                        "created_at": created_at,
                        "ticket_type": ticket_type
                    }
                    log(f"Re-registered ticket: {thread_id}")
            except Exception:
                log(f"Failed to re-register thread {thread_id}", level="error")

    # -------------------------------
    # LOA Management Commands
    # -------------------------------

    @app_commands.command(name="loa_accept", description="Accept the LOA request on this thread.")
    async def loa_accept(self, interaction: discord.Interaction):
        thread = interaction.channel
        # Only usable inside a LOA thread
        if not isinstance(thread, discord.Thread):
            return await interaction.response.send_message("❌ Use this inside a LOA thread.", ephemeral=True)

        # Only leadership may run this
        leadership_role = interaction.guild.get_role(LEADERSHIP_ID)
        if leadership_role not in interaction.user.roles:
            return await interaction.response.send_message("❌ Only leadership can accept LOA.", ephemeral=True)

        # Must be an LOA ticket
        ticket = get_ticket_info(str(thread.id))
        if not ticket or ticket[3] != "loa":
            return await interaction.response.send_message("❌ This is not a LOA ticket.", ephemeral=True)

        # Prevent multiple active LOAs for the same user
        conn = sqlite3.connect("data.db")
        cur = conn.cursor()
        cur.execute(
            "SELECT 1 FROM loa_reminders WHERE user_id = ? AND reminder_sent = 0",
            (ticket[1],)
        )
        if cur.fetchone():
            conn.close()
            return await interaction.response.send_message(
                "❌ That user already has an active LOA. Remove it first with `/loa_remove`.",
                ephemeral=True
            )
        conn.close()

        # Extract the LOA end date from the embed
        async for msg in thread.history(limit=10):
            if msg.embeds:
                embed = msg.embeds[0]
                break
        else:
            return await interaction.response.send_message("❌ Could not find LOA embed.", ephemeral=True)

        match = re.search(r"(\d{2}-\d{2}-\d{4})", embed.description)
        if not match:
            return await interaction.response.send_message("❌ Could not parse end date.", ephemeral=True)

        dd_mm_yyyy = match.group(1)
        end_iso = datetime.strptime(dd_mm_yyyy, "%d-%m-%Y").date().isoformat()

        # Record the reminder
        add_loa_reminder(str(thread.id), ticket[1], end_iso)

        # Confirm then archive
        await interaction.response.send_message(
            embed=discord.Embed(
                title="✅ LOA accepted and reminder scheduled.",
                colour=0x1dcb2e
            ),
            ephemeral=False
        )
        await thread.edit(archived=True, locked=False)


    @app_commands.command(name="loa_extend", description="Extend an existing LOA by days or until a given date.")
    @app_commands.describe(days="Number of days to extend", until="New end date DD-MM-YYYY")
    async def loa_extend(self, interaction: discord.Interaction, days: int = None, until: str = None):
        thread = interaction.channel
        if not isinstance(thread, discord.Thread):
            return await interaction.response.send_message("❌ Use this inside a LOA thread.", ephemeral=True)

        # Only leadership may run this
        leadership_role = interaction.guild.get_role(LEADERSHIP_ID)
        if leadership_role not in interaction.user.roles:
            return await interaction.response.send_message("❌ Only leadership can extend LOA.", ephemeral=True)

        # Must have an active LOA reminder
        loa = get_loa_reminder(str(thread.id))
        if not loa:
            return await interaction.response.send_message("❌ No active LOA to extend.", ephemeral=True)

        if (days and until) or (not days and not until):
            return await interaction.response.send_message(
                "❌ Provide exactly one of `days` or `until`.", ephemeral=True
            )

        if days:
            old_date = datetime.fromisoformat(loa[2]).date()
            new_date = (old_date + timedelta(days=days)).isoformat()
            human = (old_date + timedelta(days=days)).strftime("%d-%m-%Y")
        else:
            try:
                dt = datetime.strptime(until, "%d-%m-%Y").date()
            except ValueError:
                return await interaction.response.send_message("❌ Invalid date format. Use DD-MM-YYYY.", ephemeral=True)
            if dt <= datetime.utcnow().date():
                return await interaction.response.send_message("❌ New end date must be in the future.", ephemeral=True)
            new_date = dt.isoformat()
            human = until

        update_loa_end_date(str(thread.id), new_date)
        await interaction.response.send_message(
            embed=discord.Embed(
                title=f"✅ LOA extended until {human}.",
                colour=0x1dcb2e
            ),
            ephemeral=False
        )
        await thread.edit(archived=True, locked=False)


    @app_commands.command(name="loa_remove", description="Remove the LOA reminder for this thread.")
    async def loa_remove(self, interaction: discord.Interaction):
        thread = interaction.channel
        if not isinstance(thread, discord.Thread):
            return await interaction.response.send_message("❌ Use this inside a LOA thread.", ephemeral=True)

        # Only leadership may run this
        leadership_role = interaction.guild.get_role(LEADERSHIP_ID)
        if leadership_role not in interaction.user.roles:
            return await interaction.response.send_message("❌ Only leadership can remove LOA.", ephemeral=True)

        if not get_loa_reminder(str(thread.id)):
            return await interaction.response.send_message("❌ No LOA to remove.", ephemeral=True)

        remove_loa_reminder(str(thread.id))
        await interaction.response.send_message(
            embed=discord.Embed(
                title="✅ LOA reminder removed. You may now close the thread.",
                colour=0x1dcb2e
            ),
            ephemeral=False
        )

    @app_commands.command(name="loa_list", description="List all active LOAs with user, end date, and thread.")
    async def loa_list(self, interaction: discord.Interaction):
        # Only leadership may run this
        leadership_role = interaction.guild.get_role(LEADERSHIP_ID)
        if leadership_role not in interaction.user.roles:
            return await interaction.response.send_message(
                "❌ Only leadership can view active LOAs.", ephemeral=True
            )

        # Fetch active LOA reminders
        conn = sqlite3.connect("data.db")
        cur = conn.cursor()
        cur.execute(
            "SELECT thread_id, user_id, end_date FROM loa_reminders WHERE reminder_sent = 0"
        )
        rows = cur.fetchall()
        conn.close()

        if not rows:
            return await interaction.response.send_message(
                "✅ There are currently no active LOAs.", ephemeral=True
            )

        embed = discord.Embed(title="Active LOAs", color=discord.Color.blue())
        for thread_id, user_id, end_date_iso in rows:
            # Resolve user display name
            user_obj = interaction.guild.get_member(int(user_id))
            user_display = user_obj.display_name if user_obj else f"<@{user_id}>"
            # Resolve thread mention
            thread = self.bot.get_channel(int(thread_id))
            thread_mention = thread.mention if thread else thread_id
            # Format end date
            end_date_str = datetime.fromisoformat(end_date_iso).strftime("%d-%m-%Y")
            embed.add_field(
                name=user_display,
                value=f"Ends: {end_date_str}\nThread: <#{thread_mention}>",
                inline=False
            )

        await interaction.response.send_message(embed=embed, ephemeral=True)

    # -------------------------------
    # LOA Expiration Background Task
    # -------------------------------

    @tasks.loop(minutes=1)  # adjust frequency for production
    async def loa_reminder_task(self):
        await self.bot.wait_until_ready()
        expired = get_expired_loa()
        for thread_id, user_id in expired:
            # Try to fetch the thread even if it's archived
            try:
                thread = await self.bot.fetch_channel(int(thread_id))
            except discord.HTTPException:
                log(f"Could not fetch thread {thread_id}; skipping LOA reminder.", level="warning")
                continue

            # Attempt to unarchive, send the reminder, then re‑archive
            try:
                await thread.edit(archived=False, locked=False)
                embed = discord.Embed(
                    title="❗ This LOA has expired. Please follow up.",
                    color=0xe80000
                )
                await thread.send(f"<@&{LEADERSHIP_ID}> <@{user_id}>", embed=embed)
                # await thread.edit(archived=True)
            except Exception as e:
                log(f"Failed to send LOA expiry ping in thread {thread_id}: {e}", level="error")
                # Do not mark as sent; we'll retry next loop
                continue

            # Only mark as sent after a successful send
            mark_reminder_sent(thread_id)

    # -------------------------------
    # Existing Commands (unchanged)
    # -------------------------------

    @app_commands.command(name="ticket_internal", description="Creates a ticket without pinging anybody!")
    async def ticket_internal(self, interaction: discord.Interaction):
        now_str = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC")
        if not interaction.guild or interaction.guild.id != GUILD_ID:
            return await interaction.response.send_message("❌ This command can only be used in the specified guild.", ephemeral=True)

        leadership_role = interaction.guild.get_role(LEADERSHIP_ID)
        if not leadership_role or leadership_role not in interaction.user.roles:
            return await interaction.response.send_message("❌ You do not have permission to open a private ticket.", ephemeral=True)

        channel = self.bot.get_channel(TICKET_CHANNEL_ID)
        if not channel:
            return await interaction.response.send_message("❌ Ticket channel not found", ephemeral=True)

        thread = await channel.create_thread(
            name=f"[INT] - {interaction.user.display_name}",
            type=discord.ChannelType.private_thread,
            invitable=False
        )
        try:
            await thread.send(f"<@{interaction.user.id}>")
            embed = discord.Embed(
                title="🔒 Private Ticket Opened",
                description=(
                    "This ticket is private. To invite someone, please **tag them** in this thread.\n\n"
                    "📌 Only tagged members will be able to see and respond."
                ),
                colour=0xe9ee1e
            )
            await thread.send(embed=embed, view=CloseThreadView())
            log(f"Private (INT) ticket created by {interaction.user.id}, thread_id={thread.id}")
        except Exception as e:
            await interaction.response.send_message(f"❌ Error creating private ticket: {e}", ephemeral=True)
            return

        add_ticket(str(thread.id), str(interaction.user.id), now_str, "other")
        await interaction.response.send_message("✅ Your ticket has been created!", ephemeral=True)

        activity_channel = self.bot.get_channel(ACTIVITY_CHANNEL_ID)
        if activity_channel:
            embed = create_user_activity_log_embed(
                "tickets", "Internal Ticket opened",
                interaction.user,
                f"User has opened an internal ticket. (Thread ID: <#{thread.id}>)"
            )
            await activity_channel.send(embed=embed)

    @app_commands.command(name="ticket_info", description="Show info about the current ticket thread.")
    async def ticket_info(self, interaction: discord.Interaction):
        if not isinstance(interaction.channel, discord.Thread):
            return await interaction.response.send_message("❌ Use this command in a ticket thread.", ephemeral=True)
        if not interaction.guild or interaction.guild.id != GUILD_ID:
            return await interaction.response.send_message("❌ This command can only be used in the specified guild.", ephemeral=True)

        thread_id = str(interaction.channel.id)
        ticket_data = active_tickets.get(thread_id)
        if not ticket_data:
            return await interaction.response.send_message("❌ This thread is not a registered ticket.", ephemeral=True)

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
            return await interaction.response.send_message("❌ This command can only be used in the specified guild.", ephemeral=True)

        ticket_data = get_ticket_info(str(thread.id))
        if not ticket_data:
            return await interaction.response.send_message("❌ No ticket data found for this thread.", ephemeral=True)

        # Block closing if LOA active
        if ticket_data[3] == "loa" and get_loa_reminder(str(thread.id)):
            return await interaction.response.send_message(
                "❌ You must remove the active LOA first with `/loa_remove` before closing.",
                ephemeral=True
            )

        if ticket_data[3] == "recruiters":
            closing_role = interaction.guild.get_role(RECRUITER_ID)
        elif ticket_data[3] == "botdeveloper":
            closing_role = interaction.guild.get_role(LEAD_BOT_DEVELOPER_ID)
        else:
            closing_role = interaction.guild.get_role(LEADERSHIP_ID)

        if closing_role not in interaction.user.roles and interaction.user.id != int(ticket_data[1]):
            return await interaction.response.send_message("❌ You do not have permission to close this ticket.", ephemeral=True)

        try:
            remove_ticket(str(thread.id))
            embed = discord.Embed(
                title=f"Ticket closed by {interaction.user.display_name}",
                colour=0xf51616
            )
            embed.set_footer(text="🔒This ticket is locked now!")
            await interaction.response.send_message(embed=embed)
            await thread.edit(locked=True, archived=True)
            log(f"Ticket {thread.id} closed by {interaction.user.id}.")
        except Exception as e:
            await interaction.response.send_message(f"❌ Failed to close thread: {e}", ephemeral=True)
            log(f"Error closing thread: {e}", level="error")

async def setup(bot: commands.Bot):
    await bot.add_cog(TicketCog(bot))
    log("Tickets cog loaded.")
