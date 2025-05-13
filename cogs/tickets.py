# cogs/tickets.py

from typing import Any
import discord
from discord import app_commands, ButtonStyle
from discord.ext import commands, tasks
import re
import aiosqlite
from datetime import datetime, timedelta
from config_testing import *
from messages import OPEN_TICKET_EMBED_TEXT
from cogs.helpers import *
from cogs.db_utils import *

# -------------------------------
# Persistent Views and Modals
# -------------------------------

class CloseThreadView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="Close Ticket", style=ButtonStyle.danger, custom_id="ticket_close_button")
    async def close_thread_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        thread = interaction.channel
        log(f"User {interaction.user.id} issued ticket_close in thread {thread.id if thread else 'None'}.")
        if not interaction.guild or interaction.guild.id != GUILD_ID:
            return await interaction.response.send_message("❌ This command can only be used in the specified guild.", ephemeral=True)

        ticket_data = await get_ticket_info(str(thread.id))
        if not ticket_data:
            return await interaction.response.send_message("❌ No ticket data found for this thread.", ephemeral=True)

        # Block closing if LOA active
        if ticket_data[3] == "loa" and await get_loa_reminder(str(thread.id)):
            return await interaction.response.send_message(
                "❌ You must remove the active LOA first with `/loa_remove` before closing.",
                ephemeral=True
            )

        if ticket_data[3] == "recruiters":
            closing_role = interaction.client.resources.recruiter_role
        elif ticket_data[3] == "botdeveloper":
            closing_role = interaction.client.resources.lead_dev_role
        else:
            closing_role = interaction.client.resources.leadership_role

        if closing_role not in interaction.user.roles and interaction.user.id != int(ticket_data[1]):
            return await interaction.response.send_message("❌ You do not have permission to close this ticket.", ephemeral=True)

        try:
            await remove_ticket(str(thread.id))
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

        await interaction.response.defer(ephemeral=True)
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
            await add_ticket(str(thread.id), str(interaction.user.id), now_str, "loa")
            await interaction.followup.send("✅ Your LOA request has been submitted!", ephemeral=True)
            log(f"LOA ticket created for user {interaction.user.id}, thread_id={thread.id}")
        except discord.Forbidden:
            await interaction.followup.send("❌ Forbidden: Cannot send messages in the thread.", ephemeral=True)
            log(f"Forbidden error sending LOA messages in thread {thread.id}", level="error")
        except discord.HTTPException as e:
            await interaction.followup.send(f"❌ HTTP Error sending messages: {e}", ephemeral=True)
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
        # **1)** Acknowledge the interaction immediately
        await interaction.response.defer(ephemeral=True)
        log(f"Attempting create_ticket of type {ticket_type} by user {interaction.user.id}.")

        # **2)** Guild check
        if not interaction.guild or interaction.guild.id != GUILD_ID:
            return await interaction.followup.send("❌ This command can only be used in the specified guild.", ephemeral=True)

        # **3)** Pick the ping role
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
            # **4)** Send the ping + welcome embed inside the new thread
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
            return await interaction.followup.send("❌ Forbidden: Cannot send messages in the thread.", ephemeral=True)
        except discord.HTTPException as e:
            return await interaction.followup.send(f"❌ HTTP Error sending messages: {e}", ephemeral=True)

        # **5)** Persist it and let the user know
        await add_ticket(str(thread.id), str(interaction.user.id), now_str, ticket_type)
        await interaction.followup.send("✅ Your ticket has been created!", ephemeral=True)

# -------------------------------
# Ticket Cog
# -------------------------------

class TicketCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.active_tickets: Dict[str, Any] = {}
        self.ticket_embed_id: Optional[int] = None
        # kick off DB/init once ready
        self.bot.loop.create_task(self._init_dbs())

    async def _init_dbs(self):
        await self.bot.wait_until_ready()
        # ensure the two tables exist
        await init_ticket_db()
        await init_loa_db()

        # load existing tickets into memory
        await self.load_existing_tickets()

        # grab the stored embed ID just once
        stored = await get_stored_embed("tickets_embed")
        if stored and stored.get("message_id"):
            self.ticket_embed_id = int(stored["message_id"])

        # register our views & start loops
        self.bot.add_view(TicketView())
        self.bot.add_view(CloseThreadView())
        self.ensure_ticket_embed_task.start()
        self.loa_reminder_task.start()
        self.ticket_done_task.start()
        log("Tickets cog fully initialized")

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
        ch = self.bot.resources.ticket_ch
        if ch is None:
            log(f"Ticket channel {TICKET_CHANNEL_ID} not found.", level="error")
            return

        # If we _haven't_ loaded an ID yet, or if the message is missing, recreate it:
        if not self.ticket_embed_id:
            return await self._create_ticket_embed(ch)

        try:
            # single fetch to verify it still exists
            await ch.fetch_message(self.ticket_embed_id)
        except discord.NotFound:
            # if it's gone, create a new one
            await self._create_ticket_embed(ch)
        except Exception as e:
            log(f"Error fetching stored ticket embed {self.ticket_embed_id}: {e}", level="warning")

    async def _create_ticket_embed(self, ch: discord.TextChannel):
        description = (
            OPEN_TICKET_EMBED_TEXT
            .replace("{leadership_emoji}", LEADERSHIP_EMOJI)
            .replace("{recruiter_emoji}", RECRUITER_EMOJI)
            .replace("{leaddeveloper_emoji}", LEAD_BOT_DEVELOPER_EMOJI)
        )
        embed = discord.Embed(title="🎟️ Open a Ticket", description=description, colour=0x28afcc)
        msg = await ch.send(embed=embed, view=TicketView())
        # cache and persist
        self.ticket_embed_id = msg.id
        await set_stored_embed("tickets_embed", str(msg.id), str(ch.id))
        log(f"New ticket embed created and stored: {msg.id}")

    # -------------------------------
    # Load Existing Tickets on Start
    # -------------------------------
    async def load_existing_tickets(self):
        await self.bot.wait_until_ready()
        self.active_tickets.clear()
        rows = await get_all_tickets()
        for rec in rows:
            tid = rec["thread_id"]
            thread = self.bot.get_channel(int(tid))
            if isinstance(thread, discord.Thread):
                self.active_tickets[tid] = {
                    "user_id":     rec["user_id"],
                    "created_at":  rec["created_at"],
                    "ticket_type": rec["ticket_type"]
                }
                log(f"Re-registered ticket: {tid}")


    # -------------------------------
    # LOA Management Commands
    # -------------------------------

    @app_commands.command(name="loa_accept", description="Accept the LOA request on this thread.")
    async def loa_accept(self, interaction: discord.Interaction):
        thread = interaction.channel
        if not isinstance(thread, discord.Thread):
            return await interaction.response.send_message("❌ Use this inside a LOA thread.", ephemeral=True)

        leadership_role = interaction.client.resources.leadership_role
        if leadership_role not in interaction.user.roles:
            return await interaction.response.send_message("❌ Only leadership can accept LOA.", ephemeral=True)

        ticket = await get_ticket_info(str(thread.id))
        if not ticket or ticket[3] != "loa":
            return await interaction.response.send_message("❌ This is not a LOA ticket.", ephemeral=True)

        # enforce one per user
        if await has_active_loa_for_user(ticket[1]):
            return await interaction.response.send_message(
                "❌ That user already has an active LOA. Remove it first with `/loa_remove`.",
                ephemeral=True
            )

        # pull the date out of the embed
        async for msg in thread.history(limit=10):
            if msg.embeds:
                embed = msg.embeds[0]
                break
        else:
            return await interaction.response.send_message("❌ Could not find LOA embed.", ephemeral=True)

        m = re.search(r"(\d{2}-\d{2}-\d{4})", embed.description)
        if not m:
            return await interaction.response.send_message("❌ Could not parse end date.", ephemeral=True)

        # convert to YYYY-MM-DD
        end_iso = datetime.strptime(m.group(1), "%d-%m-%Y").date().isoformat()

        # **only here** do we write the LOA reminder
        await add_loa_reminder(str(thread.id), ticket[1], end_iso)

        await interaction.response.send_message(
            embed=discord.Embed(
                title="✅ LOA accepted and reminder scheduled.",
                colour=0x1dcb2e
            )
        )
        await thread.edit(archived=True, locked=False)



    @app_commands.command(name="loa_extend", description="Extend an existing LOA by days or until a given date.")
    @app_commands.describe(days="Number of days to extend", until="New end date DD-MM-YYYY")
    async def loa_extend(self, interaction: discord.Interaction, days: int = None, until: str = None):
        thread = interaction.channel
        if not isinstance(thread, discord.Thread):
            return await interaction.response.send_message("❌ Use this inside a LOA thread.", ephemeral=True)

        # Only leadership may run this
        leadership_role = interaction.client.resources.leadership_role
        if leadership_role not in interaction.user.roles:
            return await interaction.response.send_message("❌ Only leadership can extend LOA.", ephemeral=True)

        # Must have an active LOA reminder
        loa = await get_loa_reminder(str(thread.id))
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

        await update_loa_end_date(str(thread.id), new_date)
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
        leadership_role = interaction.client.resources.leadership_role
        if leadership_role not in interaction.user.roles:
            return await interaction.response.send_message("❌ Only leadership can remove LOA.", ephemeral=True)

        if not await get_loa_reminder(str(thread.id)):
            return await interaction.response.send_message("❌ No LOA to remove.", ephemeral=True)

        await remove_loa_reminder(str(thread.id))
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
        leadership_role = interaction.client.resources.leadership_role
        if leadership_role not in interaction.user.roles:
            return await interaction.response.send_message(
                "❌ Only leadership can view active LOAs.", ephemeral=True
            )
        
        # Get all active LOAs
        rows = await get_active_loa_reminders()

        if not rows:
            return await interaction.response.send_message(
                "✅ There are currently no active LOAs.", ephemeral=True
            )

        embed = discord.Embed(title="Active LOAs", color=discord.Color.blue())
        for rec in rows:
            # pull out each field by its key
            thread_id     = rec["thread_id"]
            user_id       = rec["user_id"]
            end_date_iso  = rec["end_date"]

            # Resolve user display name
            member = interaction.guild.get_member(int(user_id))
            user_display = member.display_name if member else f"<@{user_id}>"

            # Resolve (and mention) the thread
            thread = self.bot.get_channel(int(thread_id))
            thread_mention = thread.mention if thread else f"<#{thread_id}>"

            # Format end date
            end_date_str = datetime.fromisoformat(end_date_iso).strftime("%d-%m-%Y")

            embed.add_field(
                name=user_display,
                value=f"Ends: {end_date_str}\nThread: {thread_mention}",
                inline=False
            )

        await interaction.response.send_message(embed=embed, ephemeral=True)

    @app_commands.command(
        name="loa_custom",
        description="Override the LOA end date, accept the request, schedule the reminder, and close the thread."
    )
    @app_commands.describe(
        date="New end date in DD-MM-YYYY format"
    )
    async def loa_custom(self, interaction: discord.Interaction, date: str):
        thread = interaction.channel
        # 1) Must be in a thread
        if not isinstance(thread, discord.Thread):
            return await interaction.response.send_message(
                "❌ Use this inside a LOA thread.", ephemeral=True
            )

        # 2) Only leadership
        leadership = interaction.client.resources.leadership_role
        if leadership not in interaction.user.roles:
            return await interaction.response.send_message(
                "❌ Only leadership can override an LOA.", ephemeral=True
            )

        # 3) Must be a LOA ticket
        ticket = await get_ticket_info(str(thread.id))
        if not ticket or ticket[3] != "loa":
            return await interaction.response.send_message(
                "❌ This is not a LOA ticket.", ephemeral=True
            )

        # 4) Parse the new date
        from datetime import datetime
        try:
            new_dt = datetime.strptime(date, "%d-%m-%Y").date()
        except ValueError:
            return await interaction.response.send_message(
                "❌ Invalid date format. Please use DD-MM-YYYY.", ephemeral=True
            )
        if new_dt < datetime.utcnow().date():
            return await interaction.response.send_message(
                "❌ End date cannot be in the past.", ephemeral=True
            )

        # 5) Update the embed in the thread to show the new date
        import re
        async for msg in thread.history(limit=10):
            if msg.author == interaction.client.user and msg.embeds:
                emb = msg.embeds[0]
                # Replace the line that starts with "**End Date:**"
                new_description = re.sub(
                    r"(\*\*End Date:\*\*\s*)\d{2}-\d{2}-\d{4}",
                    rf"\1{date}",
                    emb.description or ""
                )
                emb.description = new_description
                await msg.edit(embed=emb)
                break

        # 6) Schedule the LOA reminder (same as loa_accept)
        new_iso = new_dt.isoformat()
        await add_loa_reminder(str(thread.id), ticket[1], new_iso)

        # 7) Confirm+close thread
        await interaction.response.send_message(
            embed=discord.Embed(
                title=f"✅ LOA end date updated to {date} and reminder scheduled.",
                colour=0x1dcb2e
            ),
            ephemeral=False
        )
        await thread.edit(archived=True, locked=False)

    # -------------------------------
    # LOA Expiration Background Task
    # -------------------------------

    @tasks.loop(hours=1)
    async def loa_reminder_task(self):
        await self.bot.wait_until_ready()
        expired = await get_expired_loa()
        for thread_id, user_id in expired:
            # Try to fetch the thread even if it's archived
            thread = self.bot.get_channel(int(thread_id))
            if thread is None:
                try:
                    thread = await self.bot.fetch_channel(int(thread_id))
                except discord.HTTPException:
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
            await mark_reminder_sent(thread_id)

    @tasks.loop(minutes=5) ## CHANGE FOR PRODUCTION
    async def ticket_done_task(self):
        await self.bot.wait_until_ready()
        for tid in await get_tickets_to_lock():
            try:
                # fetch or load the thread
                thread = (
                    self.bot.get_channel(int(tid))
                    or await self.bot.fetch_channel(int(tid))
                )
                if not thread:
                    continue

                # 1) remove from tickets table (just like /ticket_close)
                await remove_ticket(tid)

                # 2) send the close embed
                embed = discord.Embed(
                    title="Ticket closed automatically",
                    colour=0xf51616
                )
                embed.set_footer(text="🔒This ticket is locked now!")
                await thread.send(embed=embed)

                # 3) lock & archive thread
                await thread.edit(locked=True, archived=True)
                log(f"Ticket {tid} auto-closed and locked.")

                # 4) clear the done flag so we don’t run again
                await clear_ticket_done(tid)

            except Exception as e:
                log(f"Error auto-closing ticket {tid}: {e}", level="error")

    # -------------------------------
    # Existing Commands (unchanged)
    # -------------------------------

    @app_commands.command(name="ticket_internal", description="Creates a ticket without pinging anybody!")
    async def ticket_internal(self, interaction: discord.Interaction):
        now_str = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC")
        if not interaction.guild or interaction.guild.id != GUILD_ID:
            return await interaction.response.send_message("❌ This command can only be used in the specified guild.", ephemeral=True)

        leadership_role = interaction.client.resources.leadership_role
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

        await add_ticket(str(thread.id), str(interaction.user.id), now_str, "other")
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
        thread = interaction.channel
        # must be a thread in the right guild
        if not isinstance(thread, discord.Thread):
            return await interaction.response.send_message(
                "❌ Use this command in a ticket thread.", ephemeral=True
            )
        if not interaction.guild or interaction.guild.id != GUILD_ID:
            return await interaction.response.send_message(
                "❌ This command can only be used in the specified guild.", ephemeral=True
            )

        # pull directly from your tickets table
        ticket_data = await get_ticket_info(str(thread.id))
        if not ticket_data:
            return await interaction.response.send_message(
                "❌ This thread is not a registered ticket.", ephemeral=True
            )

        # ticket_data == (thread_id, user_id, created_at, ticket_type)
        _, user_id, created_at, ticket_type = ticket_data

        embed = discord.Embed(title="Ticket Information", color=discord.Color.blue())
        embed.add_field(name="Thread ID",        value=str(thread.id), inline=False)
        embed.add_field(name="User",             value=f"<@{user_id}>", inline=False)
        embed.add_field(name="Created At (UTC)", value=created_at,     inline=False)
        embed.add_field(name="Ticket Type",      value=ticket_type,   inline=False)

        await interaction.response.send_message(embed=embed, ephemeral=True)

    @app_commands.command(name="ticket_close", description="Close the current ticket.")
    async def ticket_close(self, interaction: discord.Interaction):
        thread = interaction.channel
        log(f"User {interaction.user.id} issued ticket_close in thread {thread.id if thread else 'None'}.")
        if not interaction.guild or interaction.guild.id != GUILD_ID:
            return await interaction.response.send_message("❌ This command can only be used in the specified guild.", ephemeral=True)

        ticket_data = await get_ticket_info(str(thread.id))
        if not ticket_data:
            return await interaction.response.send_message("❌ No ticket data found for this thread.", ephemeral=True)

        # Block closing if LOA active
        if ticket_data[3] == "loa" and await get_loa_reminder(str(thread.id)):
            return await interaction.response.send_message(
                "❌ You must remove the active LOA first with `/loa_remove` before closing.",
                ephemeral=True
            )

        if ticket_data[3] == "recruiters":
            closing_role = interaction.client.resources.recruiter_role
        elif ticket_data[3] == "botdeveloper":
            closing_role = interaction.client.resources.lead_dev_role
        else:
            closing_role = interaction.client.resources.leadership_role

        if closing_role not in interaction.user.roles and interaction.user.id != int(ticket_data[1]):
            return await interaction.response.send_message("❌ You do not have permission to close this ticket.", ephemeral=True)

        try:
            await remove_ticket(str(thread.id))
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

    @app_commands.command(
        name="ticket_done",
        description="Mark this ticket as done; it will auto-lock in 24 hours."
    )
    async def ticket_done(self, interaction: discord.Interaction):
        thread = interaction.channel
        if not isinstance(thread, discord.Thread):
            return await interaction.response.send_message(
                "❌ Use this inside a ticket thread.", ephemeral=True
            )
        if not interaction.guild or interaction.guild.id != GUILD_ID:
            return await interaction.response.send_message(
                "❌ This command can only be used here.", ephemeral=True
            )

        ticket_data = await get_ticket_info(str(thread.id))
        if not ticket_data:
            return await interaction.response.send_message(
                "❌ This thread isn’t a registered ticket.", ephemeral=True
            )

        opener_id = ticket_data[1]
        leadership = interaction.client.resources.leadership_role
        if interaction.user.id != int(opener_id) and leadership not in interaction.user.roles:
            return await interaction.response.send_message(
                "❌ You don’t have permission to mark this done.", ephemeral=True
            )

        # schedule lock 24 h from now
        done_iso = datetime.utcnow().isoformat()
        await update_ticket_done(str(thread.id), done_iso)
        embed = discord.Embed(title="✅ Ticket marked as done!",
                            description="🔒This ticket will auto-lock in 24 hours.\nYou can still close it immediately with the button below.",
                            colour=0x00d500)
        embed.set_footer(text="⌨️Writing in this ticket will cancel the automatic closing!")
        await interaction.response.send_message(embed=embed, view=CloseThreadView())



    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        # ignore bots & non-threads
        if message.author.bot or not isinstance(message.channel, discord.Thread):
            return

        tid = str(message.channel.id)
        if await get_ticket_done(tid):
            await clear_ticket_done(tid)
            embed = discord.Embed(title="❌ Ticket done canceled due to activity in the thread!",
                                colour=0xff0000)
            await message.channel.send(embed=embed)

            
    
async def setup(bot: commands.Bot):
    await bot.add_cog(TicketCog(bot))
    log("Tickets cog loaded.")
