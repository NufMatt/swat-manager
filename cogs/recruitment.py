# cogs/recruitment.py

import discord
from discord import app_commands, ButtonStyle, Interaction, PartialMessage
from discord.ext import commands, tasks
import asyncio, os, json, re, traceback, random
import aiosqlite
from datetime import datetime, timedelta
from typing import Optional, Dict, Any, Callable
from functools import wraps
import requests
import threading

# Adjust the sys.path so that config_testing.py (in the root) is found.
import sys
import os
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from config import *

from messages import *
from cogs.helpers import *
from cogs.db_utils import *

def handle_interaction_errors(func: Callable[..., Any]) -> Callable[..., Any]:
    @wraps(func)
    async def wrapper(*args: Any, **kwargs: Any) -> Any:
        # Attempt to retrieve the interaction from kwargs or positional args.
        interaction = kwargs.get("interaction")
        if not interaction and len(args) >= 2:
            interaction = args[1]
        try:
            return await func(*args, **kwargs)
        except discord.Forbidden:
            await interaction.response.send_message("❌ Bot lacks permission to perform this action.", ephemeral=True)
            log(f"Forbidden error in {func.__name__}", level="error")
        except discord.HTTPException as e:
            await interaction.response.send_message(f"❌ HTTP error: {e}", ephemeral=True)
            log(f"HTTP error in {func.__name__}: {e}", level="error")
        except (ValueError, TypeError) as e:
            await interaction.response.send_message(f"❌ Input error: {e}", ephemeral=True)
            log(f"Input error in {func.__name__}: {e}", level="error")
        except Exception as e:
            await interaction.response.send_message(f"❌ Unexpected error: {e}", ephemeral=True)
            log(f"Unexpected error in {func.__name__}: {e}", level="error")
    return wrapper



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
        description=ROLE_REQUEST_MESSAGE, colour=0x008040
    )
    return embed

def format_status(status: str) -> str:
    status = status.upper()
    if status == "OPEN":
        return "✅ Open"
    elif status == "CLOSED":
        return "❌ Closed"
    else:
        return "UNKNOWN"

async def create_application_embed() -> discord.Embed:
    eu_status  = format_status((await get_region_status("EU"))  or "UNKNOWN")
    na_status  = format_status((await get_region_status("NA"))  or "UNKNOWN")
    sea_status = format_status((await get_region_status("SEA")) or "UNKNOWN")
    
    embed = discord.Embed(
        title="🚨 S.W.A.T Recruitment - Application Requirements 🚨",
        description=RECRUITMENT_MESSAGE, color=discord.Color.blue()
    )

    embed.add_field(name="🇪🇺 **EU**", value=f"```{eu_status or 'N/A'}```", inline=True)
    embed.add_field(name="🇺🇸 **NA**", value=f"```{na_status or 'N/A'}```", inline=True)
    embed.add_field(name="🇸🇬 **SEA**", value=f"```{sea_status or 'N/A'}```", inline=True)
    embed.set_footer(text="S.W.A.T. Application Manager")
    return embed

def is_in_correct_guild(interaction: discord.Interaction) -> bool:
    return interaction.guild_id == GUILD_ID

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
    result = await remove_entry(thread.id)
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
            color=discord.Color.dark_grey()
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
    except (ValueError, TypeError) as e:
        log(f"Error in create_voting_embed: {e}", level="error")
        return discord.Embed(description="❌ Error creating voting embed.", color=0xff0000)


# -------------------------------
# Persistent Views and Modals
# -------------------------------
class ApplicationControlView(discord.ui.View):
    """
    Updated view with four buttons in the following order:
    Withdraw (danger) → Accept (success) → Claim (primary) → History (secondary)
    """
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(
        label="Withdraw",
        style=discord.ButtonStyle.danger,
        custom_id="app_withdraw"
    )
    async def withdraw_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        # (Existing Withdraw logic remains unchanged)
        app_data = await get_application( str(interaction.channel.id))
        if not app_data:
            await interaction.response.send_message("❌ No application data found for this thread!", ephemeral=True)
            return
        if interaction.user.id != int(app_data["applicant_id"]):
            await interaction.response.send_message("❌ You are not the owner of this application!", ephemeral=True)
            return
        closed = await close_application( str(interaction.channel.id))
        if not closed:
            await interaction.response.send_message("❌ Could not close or already closed in DB!", ephemeral=True)
            return
        embed = discord.Embed(
            title=f"Application withdrawn by {interaction.user.display_name}",
            colour=0xf51616
        )
        embed.set_footer(text="🔒This application thread is locked now!")
        await interaction.response.send_message(embed=embed)
        await update_application_status( str(interaction.channel.id), 'withdrawn')
        activity_channel = interaction.client.resources.activity_ch
        if activity_channel:
            log_embed = create_user_activity_log_embed("recruitment", "Application Withdrawn", interaction.user, f"User has withdrawn an application. (Thread ID: <#{interaction.channel.id}>)")
            await activity_channel.send(embed=log_embed)
        try:
            await interaction.channel.edit(locked=True, archived=True)
        except discord.Forbidden:
            await interaction.response.send_message("❌ Bot lacks permission to lock/archive this thread!", ephemeral=True)

    @discord.ui.button(
        label="Accept",
        style=discord.ButtonStyle.success,
        custom_id="app_accept"
    )
    async def accept_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        """
        Calls the same logic as /app_accept.
        """
        cog = interaction.client.get_cog("RecruitmentCog")
        if not cog:
            await interaction.response.send_message("❌ Internal error: Cog not found!", ephemeral=True)
            return
        # Call the accept command's callback.
        await cog.app_accept_command.callback(cog, interaction)

    @discord.ui.button(
        label="Claim",
        style=discord.ButtonStyle.primary,
        custom_id="app_claim"
    )
    async def claim_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        # (Existing Claim logic remains unchanged)
        app_data = await get_application( str(interaction.channel.id))
        if not app_data:
            await interaction.response.send_message("❌ No application data found for this thread!", ephemeral=True)
            return
        recruiter_role = interaction.client.resources.recruiter_role
        if not recruiter_role or recruiter_role not in interaction.user.roles:
            await interaction.response.send_message("❌ Only recruiters can claim this application!", ephemeral=True)
            return
        updated = await update_application_recruiter( str(interaction.channel.id), str(interaction.user.id))
        if updated:
            await interaction.response.send_message(embed=discord.Embed(title=f"✅ {interaction.user.name} has claimed this application.", colour=0x23ef56))
        else:
            await interaction.response.send_message("❌ Failed to update recruiter in DB!", ephemeral=True)

    @discord.ui.button(
        label="History",
        style=discord.ButtonStyle.secondary,
        custom_id="app_history"
    )
    async def history_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        """
        Calls the same logic as /app_history.
        """
        cog = interaction.client.get_cog("RecruitmentCog")
        if not cog:
            await interaction.response.send_message("❌ Internal error: Cog not found!", ephemeral=True)
            return
        app_data = await get_application( str(interaction.channel.id))
        if not app_data:
            await interaction.response.send_message("❌ No application data found for this thread!", ephemeral=True)
            return
        user_id_str = app_data["applicant_id"]
        # Instead of calling the command directly, we use its callback.
        await cog.app_history.callback(cog, interaction, None, user_id_str)




class CloseThreadView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)
    
    @discord.ui.button(label="Close Thread", style=discord.ButtonStyle.danger, custom_id="close_thread")
    async def close_thread_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        thread = interaction.channel
        if not is_in_correct_guild(interaction):
            await interaction.response.send_message("❌ This command can only be used in the specified guild.", ephemeral=True)
            return
        ticket_data = await get_entry(str(thread.id))
        if not ticket_data:
            await interaction.response.send_message("❌ No ticket data found for this thread.", ephemeral=True)
            return

        # Determine which role can close this thread
        ticket_type = ticket_data[3]
        if ticket_type == "recruiters":
            closing_role = interaction.client.resources.recruiter_role
        elif ticket_type == "botdeveloper":
            closing_role = interaction.client.resources.lead_dev_role
        elif ticket_type == "loa":
            closing_role = interaction.client.resources.leadership_role
        else:
            closing_role = interaction.client.resources.leadership_role
            
        if closing_role not in interaction.user.roles and interaction.user.id != int(ticket_data[1]):
            await interaction.response.send_message("❌ You do not have permission to close this ticket.", ephemeral=True)
            return
        try:
            from cogs.tickets import remove_ticket  # If your tickets logic is separate
            await remove_ticket( str(thread.id))
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


class RoleRequestView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)
    @discord.ui.button(label="Request Name Change", style=discord.ButtonStyle.secondary, custom_id="request_name_change")
    async def request_name_change(self, interaction: discord.Interaction, button: discord.ui.Button):
        user_id_str = str(interaction.user.id)
        if not is_in_correct_guild(interaction):
            await interaction.response.send_message("❌ This command can only be used in the specified guild.", ephemeral=True)
            return
        if await get_role_request( user_id_str):  # DB lookup for pending request  # DB lookup for pending request
            await interaction.response.send_message("❌ You already have an open request.", ephemeral=True)
            return
        await interaction.response.send_modal(NameChangeModal(interaction.client.resources))

    @discord.ui.button(label="Request Other", style=discord.ButtonStyle.secondary, custom_id="request_other")
    async def request_other(self, interaction: discord.Interaction, button: discord.ui.Button):
        user_id_str = str(interaction.user.id)
        if not is_in_correct_guild(interaction):
            return await interaction.response.send_message("❌ This command can only be used in the specified guild.", ephemeral=True)

        if await get_role_request( user_id_str):  # DB lookup for pending request  # DB lookup for pending request
            await interaction.response.send_message("❌ You already have an open request.", ephemeral=True)
            return
        await interaction.response.send_modal(RequestOther())

###
### BUTTONS FOR APPLICATION
###

class ApplicationView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)
    
    @discord.ui.button(label="Open a Trainee Application", style=discord.ButtonStyle.primary, custom_id="request_trainee_role")
    async def request_trainee_role(self, interaction: discord.Interaction, button: discord.ui.Button):
        user_id_str = str(interaction.user.id)
        # Check if the user already has an application open (in the pending_applications dict)
        app_temp = await get_open_application(user_id_str)
        if app_temp:
            await interaction.response.send_message("❌ You already have an open application.", ephemeral=True)
            return
        if not is_in_correct_guild(interaction):
            await interaction.response.send_message("❌ This command can only be used in the specified guild.", ephemeral=True)
            return
        if any(r.id == SWAT_ROLE_ID for r in interaction.user.roles):
            await interaction.response.send_message("❌ You are already SWAT!", ephemeral=True)
            return
        if any(r.id in [TRAINEE_ROLE, CADET_ROLE] for r in interaction.user.roles):
            await interaction.response.send_message("❌ You already have a trainee/cadet role!", ephemeral=True)
            return
        if any(r.id == BLACKLISTED_ROLE_ID for r in interaction.user.roles):
            await interaction.response.send_message("❌ You are blacklisted from applying for SWAT! To appeal this, open a ticket with the recruiters!", ephemeral=True)
            return
        
        timeout_record = await get_timeout_record( str(interaction.user.id))
        if timeout_record and timeout_record["type"] == "timeout":
            expires_at = timeout_record["expires_at"]
            now = datetime.now()
            if expires_at and expires_at > now:
                days_remaining = (expires_at - now).days
                apply_date = expires_at.strftime("%d.%m.%Y")
                await interaction.response.send_message(
                    f"❌ You are temporarily timed out from applying for SWAT. Please try again in {days_remaining} day{'s' if days_remaining != 1 else ''} on {apply_date}.",
                    ephemeral=True
                )
                return
        
        # Prompt the user to select their region first.
        await interaction.response.send_message(
            "Please select your **Region** for your application:",
            view=RegionSelectionView(interaction.user.id),
            ephemeral=True
        )


class RequestActionView(discord.ui.View):
    def __init__(self, user_id: str = None):
        """
        user_id is optional for persistent startup.
        When a new request is created, pass the proper user ID so that the buttons’ custom IDs are updated.
        """
        super().__init__(timeout=None)
        self.user_id = user_id  # May be None if not provided
        # Replace placeholder {uid} in button custom_ids if user_id is provided.
        if self.user_id:
            for child in self.children:
                if hasattr(child, "custom_id") and "{uid}" in child.custom_id:
                    child.custom_id = child.custom_id.replace("{uid}", self.user_id)

    @discord.ui.button(label="Accept", style=discord.ButtonStyle.success, custom_id="request_accept:{uid}")
    async def accept_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer(ephemeral=True, thinking=True)
        # Attempt to extract the original user id from the custom_id; if not found, fallback to self.user_id.
        try:
            parts = button.custom_id.split(":")
            original_user_id = parts[1] if len(parts) > 1 else self.user_id
        except Exception:
            original_user_id = self.user_id

        leadership_role = interaction.client.resources.leadership_role
        if not is_in_correct_guild(interaction):
            await interaction.followup.send(
                "❌ This command can only be used in the specified guild.",
                ephemeral=True
            )
            return

        if not leadership_role or leadership_role not in interaction.user.roles:
            await interaction.followup.send(
                "❌ You do not have permission to use this command.",
                ephemeral=True
            )
            return

        # Fetch the request data from the database using the original user id.
        request_data = await get_role_request( str(original_user_id))
        if not request_data:
            await interaction.followup.send("❌ No pending request found for the specified user.", ephemeral=True)
            return

        try:
            embed = interaction.message.embeds[0]
            embed.color = discord.Color.green()
            req_type = request_data.get("request_type", "")
            # Append text to the title so it’s clear that it’s been accepted.
            if req_type in ["name_change", "other"]:
                embed.title += " (Done)"
            else:
                embed.title += " (Accepted)"

            embed.add_field(
                name="Handled by:",
                value=f"<@{interaction.user.id}>",
                inline=False
            )

            # Update the original request message.
            await interaction.message.edit(embed=embed, view=None)

            # Remove it from the pending list in the database.
            if not await remove_role_request( str(original_user_id)):
                await interaction.followup.send("⚠ Warning: Could not remove the request from the database.", ephemeral=True)

            # DM the user about the acceptance.
            user = interaction.client.get_user(int(original_user_id))
            if user is not None:
                if req_type == "name_change":
                    dm_embed = discord.Embed(
                        title="Your Name Change Request has been Accepted",
                        color=discord.Color.green()
                    )
                elif req_type == "other":
                    dm_embed = discord.Embed(
                        title="Your Other Request has been Accepted",
                        color=discord.Color.green()
                    )
                else:
                    dm_embed = discord.Embed(
                        title=f"Your {req_type.capitalize()} Request has been Accepted",
                        color=discord.Color.green()
                    )

                detail = request_data.get("details", "Unknown")
                if req_type == "name_change":
                    dm_embed.add_field(
                        name="New Name Requested",
                        value=detail,
                        inline=False
                    )
                else:
                    dm_embed.add_field(
                        name="Request Details",
                        value=detail,
                        inline=False
                    )

                timestamp = request_data.get("timestamp")
                if timestamp:
                    dm_embed.add_field(
                        name="Opened At",
                        value=d_timestamp(timestamp),
                        inline=False
                    )

                try:
                    await user.send(embed=dm_embed)
                except discord.Forbidden:
                    await interaction.followup.send(
                        f"⚠ Could not send a DM to <@{original_user_id}> (they may have DMs blocked).",
                        ephemeral=True
                    )
            else:
                await interaction.followup.send(
                    f"⚠ Could not find the user (ID: {original_user_id}) in cache. No DM was sent.",
                    ephemeral=True
                )

            await interaction.followup.send("✅ The request has been accepted.", ephemeral=True)

        except IndexError:
            await interaction.followup.send("❌ No embed found on this message.", ephemeral=True)
        except Exception as e:
            await interaction.followup.send(f"❌ Error accepting request: {e}", ephemeral=True)

    @discord.ui.button(label="Ignore", style=discord.ButtonStyle.danger, custom_id="request_ignore:{uid}")
    async def ignore_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        try:
            parts = button.custom_id.split(":")
            original_user_id = parts[1] if len(parts) > 1 else self.user_id
        except Exception:
            original_user_id = self.user_id

        if not is_in_correct_guild(interaction):
            await interaction.response.send_message("❌ This command can only be used in the specified guild.", ephemeral=True)
            return

        try:
            request_data = await get_role_request( str(original_user_id))
            if not request_data:
                await interaction.response.send_message("❌ No pending request found.", ephemeral=True)
                return

            leadership_role = interaction.client.resources.leadership_role
            if not leadership_role or (leadership_role not in interaction.user.roles):
                await interaction.response.send_message("❌ You do not have permission to ignore this request.", ephemeral=True)
                return

            updated_embed = interaction.message.embeds[0]
            updated_embed.color = discord.Color.red()
            updated_embed.title += " (Ignored)"
            updated_embed.add_field(name="Ignored by:", value=f"<@{interaction.user.id}>", inline=False)
            await interaction.message.edit(embed=updated_embed, view=None)
            await remove_role_request( str(original_user_id))
            
        except Exception as e:
            await interaction.response.send_message(f"❌ Error ignoring request: {e}", ephemeral=True)

    @discord.ui.button(label="Deny w/Reason", style=discord.ButtonStyle.danger, custom_id="request_deny_reason:{uid}")
    async def deny_with_reason(self, interaction: discord.Interaction, button: discord.ui.Button):
        try:
            parts = button.custom_id.split(":")
            original_user_id = parts[1] if len(parts) > 1 else self.user_id
        except Exception:
            original_user_id = self.user_id

        if not is_in_correct_guild(interaction):
            await interaction.response.send_message("❌ This command can only be used in the specified guild.", ephemeral=True)
            return

        recruiter_role = interaction.client.resources.recruiter_role
        leadership_role = interaction.client.resources.leadership_role
        request_data = await get_role_request( str(original_user_id))
        if not request_data:
            await interaction.response.send_message("❌ No pending request found.", ephemeral=True)
            return

        req_type = request_data.get("request_type", "")
        if not leadership_role or (leadership_role not in interaction.user.roles):
            await interaction.response.send_message("❌ You do not have permission to deny this request.", ephemeral=True)
            return

        modal = DenyReasonModal(
            user_id=original_user_id,
            original_message=interaction.message,
            request_type=req_type,
            timestamp=request_data.get("timestamp")
        )
        await interaction.response.send_modal(modal)

class DenyReasonModal(discord.ui.Modal):
    def __init__(self, user_id: int, original_message: discord.Message, request_type: str = None, timestamp: str = None):
        super().__init__(title="Denial Reason")
        self.user_id = str(user_id)
        self.original_message = original_message
        self.request_type = request_type
        self.timestamp = timestamp

    reason = discord.ui.TextInput(
        label="Reason for Denial",
        style=discord.TextStyle.long,
        placeholder="Explain why this request is denied...",
        required=True
    )
    
    @handle_interaction_errors
    async def on_submit(self, interaction: discord.Interaction):
        reason_text = self.reason.value
        user = interaction.client.get_user(int(self.user_id))
        dm_sent = False
        try:
            if self.request_type == "name_change":
                dm_embed = discord.Embed(
                    title="Your Name Change Request has been Denied",
                    color=discord.Color.red()
                )
            elif self.request_type == "other":
                dm_embed = discord.Embed(
                    title="Your Other Request has been Denied",
                    color=discord.Color.red()
                )
            else:
                dm_embed = discord.Embed(
                    title=f"Your {self.request_type.capitalize()} Request has been Denied",
                    color=discord.Color.red()
                )
            dm_embed.add_field(name="Reason for Denial", value=reason_text, inline=False)
            if self.timestamp:
                dm_embed.add_field(name="Opened At", value=d_timestamp(self.timestamp), inline=False)
            await user.send(embed=dm_embed)
            dm_sent = True
        except discord.Forbidden:
            dm_sent = False
        except discord.HTTPException as e:
            log(f"HTTP error sending DM in DenyReasonModal: {e}", level="error")
            dm_sent = False

        try:
            if self.original_message.embeds:
                updated_embed = self.original_message.embeds[0]
            else:
                updated_embed = discord.Embed(title="Denied", color=discord.Color.red())
            updated_embed.color = discord.Color.red()
            updated_embed.title += " (Denied with reason)"
            updated_embed.add_field(name="Reason:", value=f"```\n{reason_text}\n```", inline=False)
            updated_embed.add_field(name="Denied by:", value=f"<@{interaction.user.id}>", inline=False)
            await self.original_message.edit(embed=updated_embed, view=None)
        except Exception as e:
            await interaction.response.send_message(f"❌ Error updating the message: {e}", ephemeral=True)
            return

        if not await remove_role_request( self.user_id):
            await interaction.followup.send("⚠ Warning: Could not remove the request from the database.", ephemeral=True)
        
        final_msg = (
            "✅ Denial reason submitted. " +
            ("User has been notified via DM." if dm_sent else "Could not DM the user (they may have DMs blocked).")
        )
        if not interaction.response.is_done():
            await interaction.response.send_message(final_msg, ephemeral=True)
        else:
            await interaction.followup.send(final_msg, ephemeral=True)

class RegionSelectionView(discord.ui.View):
    def __init__(self, user_id: int):
        super().__init__(timeout=None)
        self.user_id = user_id
        self.add_item(RegionSelection())

class RegionSelection(discord.ui.Select):
    def __init__(self):
        options = [
            discord.SelectOption(label="EU",  description="Europe"),
            discord.SelectOption(label="NA",  description="North America"),
            discord.SelectOption(label="SEA", description="Southeast Asia"),
        ]
        super().__init__(
            placeholder="Select the region you play in",
            min_values=1,
            max_values=1,
            options=options
        )
    
    async def callback(self, interaction: discord.Interaction):
        selected_region = self.values[0]
        if await get_region_status( selected_region) == "CLOSED":
            guild = interaction.client.get_guild(GUILD_ID)
            if guild:
                activity_channel = interaction.client.resources.activity_ch
                if activity_channel:
                    embed = create_user_activity_log_embed(
                        "recruitment",
                        "Closed Region Application Attempt",
                        interaction.user,
                        f"User attempted to apply for {selected_region} which is closed."
                    )
                    attempt_msg = await activity_channel.send(embed=embed)
                    # Save the log URL (jump_url) for future reference.
                    await add_application_attempt( interaction.user.id, selected_region, "closed_region_attempt", attempt_msg.jump_url)
            await interaction.response.send_message(
                f"❌ Applications for {selected_region} are currently closed.",
                ephemeral=True
            )
            return
        
        # If region is open, proceed to show the modal for further details.
        modal = TraineeDetailsModal(selected_region)
        await interaction.response.send_modal(modal)


class TraineeDetailsModal(discord.ui.Modal, title="Trainee Application Details"):
    def __init__(self, region: str):
        super().__init__()
        self.region = region

    ingame_name = discord.ui.TextInput(
        label="In-Game Name (WITHOUT CREWTAGS)",
        placeholder="Enter your in-game name"
    )
    age = discord.ui.TextInput(
        label="Your Age",
        placeholder="Enter your age",
        required=True,
        max_length=3
    )
    level = discord.ui.TextInput(
        label="In-Game Level",
        placeholder="e.g., 22",
        required=True,
        max_length=3
    )
    join_reason = discord.ui.TextInput(
        label="Why do you want to join?",
        style=discord.TextStyle.long,
        placeholder="Tell us why you want to join S.W.A.T.",
        required=True
    )
    previous_crews = discord.ui.TextInput(
        label="Previous Crews & why you left",
        style=discord.TextStyle.long,
        placeholder="List any previous crews and why you left, if applicable.",
        required=False
    )
    @handle_interaction_errors
    async def on_submit(self, interaction: discord.Interaction):
        user_id_str = str(interaction.user.id)
        # Validate that age and level are numeric
        if not (self.age.value.isdigit() and self.level.value.isdigit()):
            await interaction.response.send_message("❌ Age and level must be numbers.", ephemeral=True)
            return
        
        age_int = int(self.age.value)
        if age_int < 16:
            guild = interaction.guild
            if guild:
                member = guild.get_member(interaction.user.id)
                # Add a blacklist record (using your existing function)
                await add_timeout_record( str(interaction.user.id), "blacklist")
                if member:
                    blacklist_role = interaction.client.resources.blacklist_role
                    if blacklist_role and blacklist_role not in member.roles:
                        try:
                            await member.add_roles(blacklist_role)
                        except Exception as e:
                            log(f"Error adding blacklist role for underage user: {e}", level="error")
            
            
            activity_channel = interaction.client.resources.activity_ch
            if activity_channel:
                embed = create_user_activity_log_embed("recruitment", "Blacklist User", interaction.user,
                                                    f"User {member.display_name} has been blacklisted, due to being underage.")
                await activity_channel.send(embed=embed)
            
            await interaction.response.send_message(
                f"❌ You have been blacklisted because you are underage (under 16). If you wish to appeal, please open a <#{TICKET_CHANNEL_ID}> with the recruiters.",
                ephemeral=True
            )
            return
        
        # Save only the five fields in our pending_applications dict.
        data = {
            "request_type": "trainee_role",
            "ingame_name": self.ingame_name.value,
            "age": self.age.value,
            "level": self.level.value,
            "join_reason": self.join_reason.value,
            "previous_crews": self.previous_crews.value,
            "region": self.region
        }
        await add_application_request( str(interaction.user.id), data)

        await finalize_trainee_request(interaction, user_id_str)



class NameChangeModal(discord.ui.Modal, title="Request Name Change"):
    def __init__(self, resources):
        super().__init__()      # important!
        self.resources = resources

    new_name = discord.ui.TextInput(label="New Name", placeholder="Enter your new name")
    @handle_interaction_errors
    async def on_submit(self, interaction: discord.Interaction):
        if await add_role_request( str(interaction.user.id), "name_change", self.new_name.value):
            # Proceed to send the request to the role requests channel
            guild = interaction.client.get_guild(GUILD_ID)
            if not guild:
                await interaction.response.send_message("❌ Guild not found.", ephemeral=True)
                return
            channel = self.resources.requests_ch
            if not channel:
                await interaction.response.send_message("❌ Requests channel not found.", ephemeral=True)
                return
            base_nick = interaction.user.nick if interaction.user.nick else interaction.user.name
            new_name_cleaned = re.sub(
                r'^(?:\[(CADET|TRAINEE|SWAT)\]\s*)?|(?:\s*\[(CADET|TRAINEE|SWAT)\])+$',
                '', self.new_name.value, flags=re.IGNORECASE)
            suffix_match = re.search(r'\[(CADET|TRAINEE|SWAT)\]', base_nick, flags=re.IGNORECASE)
            suffix = suffix_match.group(0) if suffix_match else ""
            new_name_final = new_name_cleaned + (" " + suffix if suffix else "")
            embed = discord.Embed(
                title="New Name Change Request:",
                description=f"User <@{interaction.user.id}> has requested a name change!",
                colour=0x298ecb
            )
            embed.add_field(name="New Name:", value=f"```{new_name_final or 'N/A'}```", inline=True)
            embed.add_field(name="Make sure to actually change the name BEFORE clicking accept!", value="", inline=False)
            view = RequestActionView(user_id=str(interaction.user.id))
            await channel.send(embed=embed, view=view)
            await interaction.response.send_message("✅ Submitting successful!", ephemeral=True)
        else:
            await interaction.response.send_message("❌ Failed to submit your request.", ephemeral=True)

class RequestOther(discord.ui.Modal, title="RequestOther"):
    other = discord.ui.TextInput(label="Requesting:", placeholder="What do you want to request?")
    @handle_interaction_errors
    async def on_submit(self, interaction: discord.Interaction):
        user_id_str = str(interaction.user.id)
        if await add_role_request( user_id_str, "other", self.other.value):
            guild = interaction.client.get_guild(GUILD_ID)
            if not guild:
                await interaction.response.send_message("❌ Guild not found.", ephemeral=True)
                return
            channel = interaction.client.resources.requests_ch
            if not channel:
                await interaction.response.send_message("❌ Requests channel not found.", ephemeral=True)
                return
            embed = discord.Embed(
                title="New Other Request:",
                description=f"User <@{interaction.user.id}> has requested Other!",
                colour=0x298ecb
            )
            embed.add_field(name="Request:", value=f"```{self.other.value or 'N/A'}```", inline=True)
            embed.add_field(name="Make sure to actually ADD the ROLE BEFORE clicking accept!", value="", inline=False)
            view = RequestActionView(user_id=str(interaction.user.id))
            await channel.send(embed=embed, view=view)
            await interaction.response.send_message("✅ Submitting successful!", ephemeral=True)
        else:
            await interaction.response.send_message(f"❌ Failed to submit your request", ephemeral=True)

# Define finalize_trainee_request as a module-level function.
async def finalize_trainee_request(interaction: discord.Interaction, user_id_str: str):
    try:
        if not interaction.response.is_done():
            await interaction.response.defer(ephemeral=True)

        request = await get_application_request( user_id_str)
        if not request:
            try:
                await interaction.followup.send("❌ No pending request found to finalize.", ephemeral=True)
            except discord.NotFound:
                log("Webhook not found when sending pending request not found message.")
            return

        region = request.get("region")
        age     = request.get("age")
        level   = request.get("level")
        # Removed ban_history and languages
        ign     = request.get("ingame_name")
        join_reason = request.get("join_reason")
        previous_crews = request.get("previous_crews")

        if not region:
            try:
                await interaction.followup.send("❌ Please complete the region selection first.", ephemeral=True)
            except discord.NotFound:
                log("Webhook not found when sending region selection message.")
            return

        guild = interaction.client.get_guild(GUILD_ID)
        if not guild:
            try:
                await interaction.followup.send("❌ Guild not found.", ephemeral=True)
            except discord.NotFound:
                log("Webhook not found when sending guild not found message.")
            return

        # Log the application submission
        activity_channel = guild.get_channel(ACTIVITY_CHANNEL_ID)
        if activity_channel:
            embed = create_user_activity_log_embed(
                "recruitment",
                "Application Opened",
                interaction.user,
                f"User has opened an application for {region}."
            )
            await activity_channel.send(embed=embed)

        # Create a private thread for the application.
        apply_channel = guild.get_channel(APPLY_CHANNEL_ID)
        if not apply_channel:
            try:
                await interaction.followup.send("❌ The application channel was not found!", ephemeral=True)
            except discord.NotFound:
                log("Webhook not found when sending application channel not found message.")
            return

        thread = await apply_channel.create_thread(
            name=f"{ign} - Trainee Application",
            message=None,
            type=discord.ChannelType.private_thread,
            reason=f"Trainee application from user {user_id_str}",
            invitable=False
        )

        # Build the application overview embed.
        history = await get_application_history( str(interaction.user.id))
        has_history = len(history) > 0
        recent_attempts = await get_recent_closed_attempts( str(interaction.user.id))

        embed = discord.Embed(
            title="📋 Application Overview",
            description=f"**Applicant:** <@{interaction.user.id}>",
            color=0x07ed13
        )
        embed.add_field(name="🎮 In-Game Name", value=f"```{ign or 'N/A'}```", inline=True)
        embed.add_field(name="🌍 Region", value=f"```{region or 'N/A'}```", inline=True)
        embed.add_field(name="", value="", inline=False)  # Empty field for spacing
        embed.add_field(name="🔞 Age", value=f"```{age or 'N/A'}```", inline=True)
        embed.add_field(name="💪 Level", value=f"```{level or 'N/A'}```", inline=True)
        embed.add_field(name="❓ Why Join?", value=f"```{join_reason or 'N/A'}```", inline=False)
        embed.add_field(name="🚪 Previous Crews", value=f"```{previous_crews or 'N/A'}```", inline=True)
        # Exclude the current application from history if needed
        # (Assuming you have a way to identify the current application's record)
        # Exclude the current application from history if needed
        filtered_history = [entry for entry in history if entry.get("thread_id") != str(thread.id)]
        has_history = len(filtered_history) > 0
        complete_history_count = has_history + len(recent_attempts)

        # 1) Exclude the current application
        filtered_history = [
            entry for entry in history
            if entry.get("thread_id") != str(thread.id)
        ]
        # 2) Build unified list of events
        events = []
        for entry in filtered_history:
            ts = entry["timestamp"]
            url = f"https://discord.com/channels/{GUILD_ID}/{entry['thread_id']}"
            events.append((ts, f"- [Application]({url})"))
        for att in recent_attempts:
            ts = att["timestamp"]
            url = att["log_url"]
            events.append((ts, f"- [Log Entry]({url})"))

        # 3) Sort chronologically (oldest first)
        events.sort(key=lambda e: e[0])

        # 4) Prep header counts
        app_count = len(filtered_history)
        attempt_count = len(recent_attempts)
        lines = [f"- {app_count} previous Application(s)", f"- {attempt_count} Closed Region Attempt(s)"]
        # 5) Append event lines until limit
        MAX = 1024
        field = ""
        for _, line in events:
            candidate = field + line + "\n"
            if len(candidate) > MAX:
                field += "..."
                break
            field = candidate

        # 6) Put header + events into the field
        value = "\n".join(lines)
        if field:
            value += "\n" + field
        else:
            value += "\nNone"
        if complete_history_count > 0:
            embed.add_field(
                name="⚠️ Internal Refs:",
                value=value,
                inline=False
            )

        embed.add_field(
            name="⏳ Next Steps",
            value=(
                
                "- Please provide your full ban history. You can request it by opening a ticket in the CnR Discord. Once you have it, post a screenshot in this thread.\n"
                "- After that, a recruiter will review your application and let you know the decision."
                "- If you have any questions, feel free to ask in this thread."
            ),
            inline=False
        )

        control_view = ApplicationControlView()
        await thread.send(
            content=f"<@{interaction.user.id}> <@&{RECRUITER_ID}>",
            embed=embed,
            view=control_view,
            silent=True
        )
        
        await remove_application_request( user_id_str)
        
        # In your add_application call, you can now omit ban_history (pass an empty string, if your DB still expects it)
        await add_application( 
            thread_id=str(thread.id),
            applicant_id=str(interaction.user.id),
            recruiter_id=None,
            starttime=datetime.now(),
            ingame_name=ign,
            region=region,
            age=age,
            level=level,
            join_reason=join_reason,
            previous_crews=previous_crews,
        )

        try:
            await interaction.followup.send(
                "✅ Your trainee role application has been submitted via private thread. A recruiter will review your application soon.",
                ephemeral=True
            )
        except discord.NotFound:
            log("Webhook not found when sending submission confirmation.")
    except Exception as e:
        try:
            await interaction.followup.send(f"❌ Error finalizing trainee request: {e}", ephemeral=True)
        except discord.NotFound:
            log(f"Webhook not found when sending error message: {e}")





# -------------------------------
# Recruitment Cog
# -------------------------------
class RecruitmentCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.resources = bot.resources
        self.ready = False
        self.ban_history_reminded = set()  # to track threads that already got a reminder
        self.claimed_reminders = {}
        self.ban_history_submitted = set() 
        self.embed_message_id = None
        self.application_embed_message_id = None
        self.bot.loop.create_task(self._wait_and_start())


    async def _wait_and_start(self):
        await self.bot.wait_until_ready()
        self.bot.add_view(RoleRequestView())
        self.bot.add_view(RequestActionView())
        self.bot.add_view(ApplicationView())
        self.bot.add_view(CloseThreadView())
        self.bot.add_view(ApplicationControlView())

        # Load embed message ID from file
        stored = await get_stored_embed("main_embed")
        self.embed_message_id = int(stored["message_id"]) if stored else None
        
        # Load embed message ID from file
        stored = await get_stored_embed("application_embed")
        self.application_embed_message_id = int(stored["message_id"]) if stored else None
        
        # Start tasks
        self.check_embed_task.start()
        self.check_application_embed_task.start()
        self.check_expired_endtimes_task.start()
        self.check_ban_history_and_application_reminders.start()
        self.check_open_requests_reminder.start()
        self.check_timeouts_task.start()
        await self.load_existing_tickets()
        log("RecruitmentCog setup complete. All tasks started.")

    def cog_unload(self):
        self.check_embed_task.cancel()
        self.check_application_embed_task.cancel()
        self.check_expired_endtimes_task.cancel()
        self.check_timeouts_task.cancel()
        self.check_ban_history_and_application_reminders.cancel()
        self.check_open_requests_reminder.cancel()

    @tasks.loop(minutes=5)
    async def check_embed_task(self):
        try:
            channel = self.resources.request_ch
            if channel:
                if self.embed_message_id:
                    msg = PartialMessage(channel=channel, id=self.embed_message_id)
                    try:
                        # if it still exists, we assume it’s fine—no fetch
                        await msg.edit(embed=create_embed(), view=RoleRequestView())
                    except discord.NotFound:
                        # it vanished—create a brand new one
                        sent = await channel.send(embed=create_embed(), view=RoleRequestView())
                        self.embed_message_id = sent.id
                        await set_stored_embed("main_embed", sent.id, channel.id)
                else:
                    embed = create_embed()
                    view = RoleRequestView()
                    msg = await channel.send(embed=embed, view=view)
                    self.embed_message_id = msg.id
                    await set_stored_embed("main_embed", str(msg.id), str(channel.id))
                    log(f"Created new embed with ID: {self.embed_message_id}")
        except (discord.DiscordException, Exception) as e:
            log(f"Error in check_embed_task: {e}", level="error")

    
    @tasks.loop(minutes=5)
    async def check_application_embed_task(self):
        channel = self.resources.apply_ch
        if not channel:
            return

        if self.application_embed_message_id:
            msg = PartialMessage(channel=channel, id=self.application_embed_message_id)
            try:
                await msg.edit(embed=await create_application_embed(),
                               view=ApplicationView())
            except discord.NotFound:
                # it vanished—send and store a new one
                sent = await channel.send(embed=await create_application_embed(),
                                          view=ApplicationView())
                self.application_embed_message_id = sent.id
                await set_stored_embed("application_embed", sent.id, channel.id)
        else:
            sent = await channel.send(embed=await create_application_embed(),
                                      view=ApplicationView())
            self.application_embed_message_id = sent.id
            await set_stored_embed("application_embed", sent.id, channel.id)

    @tasks.loop(minutes=1)
    async def check_expired_endtimes_task(self):
        await self.bot.wait_until_ready()
        now = datetime.now()

        async with aiosqlite.connect(DATABASE_FILE) as db:
            # fetch all un‑sent reminders
            async with db.execute("""
                SELECT thread_id, recruiter_id, starttime, endtime, role_type, region, ingame_name
                FROM entries
                WHERE reminder_sent = 0
            """) as cursor:
                rows = await cursor.fetchall()

            for thread_id, recruiter_id, start_iso, end_iso, role_type, region, ign in rows:
                if not end_iso:
                    continue

                end_dt = datetime.fromisoformat(end_iso)
                if end_dt <= now:
                    # Calculate days open if needed
                    start_dt = datetime.fromisoformat(start_iso)
                    days_open = (now - start_dt).days

                    # Build and send your embed
                    embed = discord.Embed(
                        description=f"**Reminder:** This thread has been open for **{days_open} days**.",
                        color=0x008040
                    )
                    thread = self.bot.get_channel(int(thread_id))
                    if thread and isinstance(thread, discord.Thread):
                        if role_type == "trainee":
                            await thread.send(f"<@{recruiter_id}>", embed=embed)
                        else:  # cadet
                            voting_embed = await create_voting_embed(start_dt, now, int(recruiter_id), region, ign)
                            msg = await thread.send(f"<@&{SWAT_ROLE_ID}> Time for another cadet vote!⌛", embed=voting_embed)
                            await asyncio.gather(*(msg.add_reaction(e) for e in (PLUS_ONE_EMOJI, "❔", MINUS_ONE_EMOJI)))

                    # Mark reminder sent
                    await db.execute(
                        "UPDATE entries SET reminder_sent = 1 WHERE thread_id = ?",
                        (thread_id,)
                    )
            await db.commit()

    async def load_existing_tickets(self):
        # For recruitment, if you need to load active requests, do so here.
        pass


    @tasks.loop(hours=1)
    async def check_ban_history_and_application_reminders(self):
        await self.bot.wait_until_ready()
        now = datetime.now()

        # initialize reminder_times dict once
        if not hasattr(self, "reminder_times"):
            self.reminder_times = {}

        async with aiosqlite.connect(DATABASE_FILE) as db:
            # Fetch all open application threads
            async with db.execute("""
                SELECT thread_id, applicant_id, recruiter_id, starttime,
                       ban_history_sent, ban_history_reminder_count
                FROM application_threads
                WHERE is_closed = 0
            """) as cursor:
                rows = await cursor.fetchall()

            for thread_id, applicant_id, recruiter_id, start_iso, ban_history_sent, reminder_count in rows:
                # skip if we’ve sent a reminder for this thread in the last 24 h
                last = self.reminder_times.get(thread_id)
                if last and (now - last) < timedelta(hours=24):
                    continue

                # respect “silenced” flag
                if await is_application_silenced(thread_id):
                    log(f"Thread {thread_id} is silenced; skipping notification.", level="info")
                    continue

                # only start reminding after 24 h have elapsed
                try:
                    start_dt = datetime.fromisoformat(start_iso)
                except (TypeError, ValueError):
                    continue
                if (now - start_dt) <= timedelta(hours=24):
                    continue

                # ensure the thread still exists
                thread = self.bot.get_channel(int(thread_id))
                if not isinstance(thread, discord.Thread):
                    continue

                # build the correct embed + mention
                if ban_history_sent:
                    embed = discord.Embed(
                        title="⏰ Reminder: This application is still open and awaiting review.",
                        colour=0xEFE410
                    )
                    mention = f"<@{recruiter_id}>" if recruiter_id else f"<@&{RECRUITER_ID}>"

                else:
                    # first two reminders ping only the applicant
                    if reminder_count < 2:
                        embed = discord.Embed(
                            title="⏰ Reminder: Please post your ban history as a picture in this thread!",
                            colour=0xEFE410
                        )
                        mention = f"<@{applicant_id}>"
                    # third reminder pings applicant + recruiters
                    else:
                        embed = discord.Embed(
                            title="⏰ Final Reminder: User has not provided a ban history after elapsed time.",
                            colour=0xEFE410
                        )
                        if recruiter_id:
                            mention = f"<@{applicant_id}> <@{recruiter_id}>"
                        else:
                            mention = f"<@{applicant_id}> <@&{RECRUITER_ID}>"

                    # bump the reminder counter
                    new_count = reminder_count + 1
                    await db.execute(
                        "UPDATE application_threads SET ban_history_reminder_count = ? WHERE thread_id = ?",
                        (new_count, thread_id)
                    )

                # send the reminder
                try:
                    await thread.send(content=mention, embed=embed)
                except Exception as e:
                    log(f"Error sending reminder in thread {thread_id}: {e}", level="error")

                # record that we just sent one
                self.reminder_times[thread_id] = now

            # commit any counter-updates
            await db.commit()

    @tasks.loop(minutes=30)
    async def check_timeouts_task(self):
        now = datetime.now()
        guild = self.bot.get_guild(GUILD_ID)
        if not guild:
            return
        # Cache all guild members in a dictionary (requires the members intent to be enabled)
        members_dict = {member.id: member for member in guild.members}
        activity_channel = self.resources.activity_ch
        
        for record in await get_all_timeouts( ):
            user_id = int(record["user_id"])
            member = members_dict.get(user_id)
            if not member:
                continue  # Skip if the member isn't found
            
            if record["type"] == "timeout":
                timeout_role = self.resources.timeout_role
                if record["expires_at"] and record["expires_at"] <= now:
                    if timeout_role in member.roles:
                        try:
                            await member.remove_roles(timeout_role)
                            log(f"Removed expired timeout role from user {record['user_id']}")
                        except Exception as e:
                            log(f"Error removing expired timeout role from user {record['user_id']}: {e}", level="error")
                    await remove_timeout_record( record["user_id"])
                    if activity_channel:
                        log_embed = create_user_activity_log_embed(
                            "recruitment", "Timeout Expired", member,
                            f"Timeout expired on {record['expires_at'].strftime('%Y-%m-%d %H:%M:%S')}"
                        )
                        await activity_channel.send(embed=log_embed)
                else:
                    if timeout_role not in member.roles:
                        try:
                            await member.add_roles(timeout_role)
                            log(f"Re-added timeout role to user {record['user_id']}")
                        except Exception as e:
                            log(f"Error re-adding timeout role to user {record['user_id']}: {e}", level="error")
            elif record["type"] == "blacklist":
                blacklist_role = self.resources.blacklist_role
                if blacklist_role not in member.roles:
                    try:
                        await member.add_roles(blacklist_role)
                        log(f"Re-added blacklist role to user {record['user_id']}")
                    except Exception as e:
                        log(f"Error re-adding blacklist role to user {record['user_id']}: {e}", level="error")
        log("Completed check_timeouts_task cycle.")

    @tasks.loop(hours=1)
    async def check_open_requests_reminder(self):
        """
        Checks every 30 minutes for role requests that have been open for over 24 hours and
        have not yet been reminded. Sends an embed to the activity channel and pings leadership and recruiters.
        """
        pending_requests = await get_pending_role_requests_no_reminder( )
        now = datetime.now()
        for req in pending_requests:
            try:
                req_time = datetime.fromisoformat(req["timestamp"])
            except Exception as e:
                log(f"Error parsing timestamp for role request from user {req['user_id']}: {e}", level="error")
                continue

            if now - req_time > timedelta(hours=24):
                
                try:
                    dt = datetime.fromisoformat(req['timestamp'])
                    formatted_time = dt.strftime("%Y-%m-%d %H:%M")
                except Exception:
                    formatted_time = req['timestamp']
                embed = discord.Embed(title="⏰ Open Request Reminder",
                      description=f"Role request from <@{req['user_id']}> has been open for over 24 hours.",
                      colour=0x8d8d8d,
                      timestamp=datetime.now())

                embed.add_field(name="Request Type:",
                                value=f"```{req['request_type'] or 'N/A'}```",
                                inline=True)
                embed.add_field(name="Request Details:",
                                value=f"```{req['details'] or 'N/A'}```",
                                inline=True)
                embed.add_field(name="Request TIme:",
                                value=f"```{formatted_time or 'N/A'}```",
                                inline=False)
                embed.add_field(name="",
                                value="Please check /list_requests if there are no open requests in the channel.",
                                inline=False)

                embed.set_footer(text="🔒 This reminder is visible only to team members.")
                activity_channel = self.resources.activity_ch
                if activity_channel:
                    await activity_channel.send(content=f"<@&{LEADERSHIP_ID}>", embed=embed)
                    log(f"Sent reminder for open request from user {req['user_id']}")
                # Mark this request as reminded so it isn’t processed again.
                await mark_role_request_reminder_sent( req["user_id"])


#
# DETECT IF PICTURE IS SENT
#
    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        # Ignore bot messages
        if message.author.bot:
            return

        # Only process if the message is sent in a thread channel
        if not isinstance(message.channel, discord.Thread):
            return

        # Check if this thread is an application thread
        app_data = await get_application( str(message.channel.id))
        if not app_data:
            return

        # Auto-claim if the message author is a recruiter and the application is unclaimed:
        if not app_data.get("recruiter_id") and any(role.id == RECRUITER_ID for role in message.author.roles):
            await update_application_recruiter( str(message.channel.id), str(message.author.id))
            embed = discord.Embed(title=f"ℹ️ Application automatically claimed by *{message.author.name}*.", colour=0xc0c0c0)
            await message.channel.send(embed=embed)
            app_data["recruiter_id"] = str(message.author.id)

        # Only check messages from the applicant
        if message.author.id != int(app_data["applicant_id"]):
            return

        # Check each attachment for an image and update DB accordingly
        for att in message.attachments:
            is_image = False
            if att.content_type and att.content_type.startswith("image/"):
                is_image = True
            elif att.filename.lower().endswith(('.png', '.jpg', '.jpeg', '.gif', '.webp')):
                is_image = True
            if is_image:
                try:
                    async with aiosqlite.connect(DATABASE_FILE) as db:
                        await db.execute(
                            "UPDATE application_threads SET ban_history_sent = 1 WHERE thread_id = ?",
                            (message.channel.id,)
                        )
                        await db.commit()
                except Exception as e:
                    log(f"DB update error in on_message for thread {message.channel.id}: {e}", level="error")
                    
                if message.channel.id not in self.ban_history_submitted:
                    confirmation = discord.Embed(
                        title="✅ Ban History Submitted!",
                        description="Your ban history has been received. A recruiter will review your application shortly.",
                        color=discord.Color.green()
                    )
                    await message.channel.send(embed=confirmation)
                    self.ban_history_submitted.add(message.channel.id)
                break


    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member):
        record = await get_timeout_record(str(member.id))
        if not record:
            return
        # Pull your roles from the Cog’s resources, not member.self
        if record["type"] == "timeout":
            role = self.resources.timeout_role
        else:
            role = self.resources.blacklist_role

        if role and role not in member.roles:
            try:
                await member.add_roles(role)
                log(f"Re-added {record['type']} role to rejoined member {member.id}")
            except Exception as e:
                log(f"Error re-adding role for {member.id}: {e}", level="error")


    @commands.Cog.listener()
    async def on_member_remove(self, member: discord.Member):
            # 1) Fetch all open application threads
        async with aiosqlite.connect(DATABASE_FILE) as db:
            cursor = await db.execute(
                """
                SELECT thread_id, recruiter_id, starttime, ingame_name, region
                FROM application_threads
                WHERE applicant_id = ? AND is_closed = 0 AND status = 'open'
                """,
                (str(member.id),)
            )
            rows = await cursor.fetchall()

            for thread_id, recruiter_id, starttime, ingame_name, region in rows:
                thread = self.bot.get_channel(int(thread_id))
                if not isinstance(thread, discord.Thread):
                    continue

                # 2) Mark ban_history_sent
                await db.execute(
                    "UPDATE application_threads SET ban_history_sent = 1 WHERE thread_id = ?",
                    (thread_id,)
                )

                # 3) Send alert embed
                embed = discord.Embed(
                    title="🛫 User has left the discord!",
                    colour=discord.Color.red()
                )
                mention = f"<@{recruiter_id}>" if recruiter_id else ""
                try:
                    await thread.send(content=mention, embed=embed)
                except Exception as e:
                    log(f"Error sending reminder in thread {thread_id}: {e}", level="error")

                # 4) Log attempt in history
                await add_application_attempt(
                    applicant_id=str(member.id),
                    region=region,
                    status="left_with_open_application",
                    log_url=f"https://discord.com/channels/{GUILD_ID}/{thread_id}"
                )

            # Commit all UPDATEs at once
            await db.commit()


        # ----- Process Accepted Trainee/Cadet Threads -----
        async with aiosqlite.connect(DATABASE_FILE) as db:
            cursor = await db.execute(
                """
                SELECT thread_id, recruiter_id, starttime, ingame_name, region, reminder_sent
                FROM entries
                WHERE user_id = ?
                """,
                (str(member.id),)
            )
            accepted_threads = await cursor.fetchall()

        if accepted_threads:
            for thread_id, recruiter_id, starttime, ingame_name, region, reminder_sent in accepted_threads:
                thread = self.bot.get_channel(int(thread_id))
                if thread and isinstance(thread, discord.Thread):
                    embed = discord.Embed(
                        title="🛫 User has left the discord!",
                        description="",
                        colour=discord.Color.red()
                    )
                    # Update reminder_sent in the entries table if not already set
                    if reminder_sent == 0:
                        async with aiosqlite.connect(DATABASE_FILE) as db:
                            await db.execute(
                                "UPDATE entries SET reminder_sent = ? WHERE thread_id = ?",
                                (1, thread_id)
                            )
                            await db.commit()
                    if recruiter_id:
                        content = f"<@{recruiter_id}>"
                    else:
                        content = ""
                    try:
                        await thread.send(content=content, embed=embed)
                    except Exception as e:
                        log(f"Error sending reminder in accepted thread {thread_id}: {e}", level="error")
                    # Log the event for accepted threads


    @app_commands.command(name="hello", description="Say hello to the bot")
    @handle_interaction_errors
    async def hello_command(self, interaction: discord.Interaction):
        if not is_in_correct_guild(interaction):
            await interaction.response.send_message("❌ This command can only be used in the specified guild.", ephemeral=True)
            return
        await interaction.response.send_message(f"✅ Hello, {interaction.user.mention}!", ephemeral=True)

    @app_commands.command(name="force_add", description="Manually add an existing trainee / cadet thread to the database!")
    @handle_interaction_errors
    async def force_add(self, interaction: discord.Interaction, user_id: str, ingame_name: str, region: app_commands.Choice[str], role_type: app_commands.Choice[str]):
        if not is_in_correct_guild(interaction):
            await interaction.response.send_message("❌ This command can only be used in the specified guild.", ephemeral=True)
            return
        thread = interaction.channel
        user_id_int = int(user_id)
        guild = interaction.client.get_guild(GUILD_ID)
        
        leadership_role = self.bot.resources.leadership_role if guild else None
        if not leadership_role or leadership_role not in interaction.user.roles:
            await interaction.response.send_message("❌ You do not have permission to use this command.", ephemeral=True)
            return
        
        selected_region = region.value
        selected_role = role_type.value
        start_time = get_rounded_time()
        end_time = start_time + timedelta(days=7)
        validate_entry = await add_entry(
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
            activity_channel = self.resources.activity_ch
            if activity_channel:
                embed = create_user_activity_log_embed("recruitment", f"Manually added trainee", interaction.user, f"User has added <@{user_id_int}> as a trainee.")
                await activity_channel.send(embed=embed)
        else:
            await interaction.response.send_message(
                f"❌ Error adding user ID `{user_id_int}` to the database. Possibly a duplicate or DB issue.",
                ephemeral=True
            )

    @app_commands.command(name="list_requests", description="Lists the currently stored pending requests.")
    @handle_interaction_errors
    async def list_requests(self, interaction: discord.Interaction):
        if not is_in_correct_guild(interaction):
            await interaction.response.send_message("❌ This command can only be used in the specified guild.", ephemeral=True)
            return
        
        guild = interaction.client.get_guild(GUILD_ID)
        leadership_role = self.bot.resources.leadership_role if guild else None
        recruiter_role = self.resources.recruiter_role if guild else None

        # If the user has neither role, deny access
        if not (
            (leadership_role and leadership_role in interaction.user.roles)
            or
            (recruiter_role and recruiter_role in interaction.user.roles)
        ):
            await interaction.response.send_message(
                "❌ You do not have permission to list requests.",
                ephemeral=True
            )
            return
        
        await interaction.response.defer(ephemeral=True)
        
        pending_requests = await get_role_requests( )  # Returns a list of dicts
        if not pending_requests:
            await interaction.followup.send("There are **no** pending requests at the moment.", ephemeral=True)
            return
        
        lines = []
        
        # Iterate directly over the list of request dicts
        for request_data in pending_requests:
            user_id_str = request_data.get("user_id", "N/A")
            req_type = request_data.get("request_type", "N/A")
            timestamp = request_data.get("timestamp", "Unknown")
            
            # Build out the 'details' portion based on request_type
            if req_type == "trainee_role":
                detail = (
                    f"InGame Name: {request_data.get('ingame_name', 'Unknown')}, "
                    f"Region: {request_data.get('region', 'Not Selected')}"
                )
            elif req_type == "name_change":
                detail = f"New Name: {request_data.get('new_name', 'Unknown')}"
            elif req_type == "other":
                detail = f"Request: {request_data.get('other', 'No details')}"
            else:
                detail = "N/A"
            
            # Include the timestamp in the output line
            lines.append(
                f"• **User ID**: {user_id_str} | **Type**: `{req_type}` | {detail} | **Time**: {timestamp}"
            )
        
        reply_text = "\n".join(lines)
        await interaction.followup.send(f"**Current Pending Requests:**\n\n{reply_text}", ephemeral=True)

    @app_commands.command(name="list_applications", description="List all current open applications with their status.")
    @handle_interaction_errors
    async def list_applications(self, interaction: discord.Interaction):
        if not is_in_correct_guild(interaction):
            await interaction.response.send_message("❌ This command can only be used in the specified guild.", ephemeral=True)
            return

        # (Optional) Restrict this command to recruiters or leadership.
        guild = interaction.client.get_guild(GUILD_ID)
        leadership_role = self.bot.resources.leadership_role if guild else None
        recruiter_role = self.resources.recruiter_role if guild else None

        # If the user has neither role, deny access
        if not (
            (leadership_role and leadership_role in interaction.user.roles)
            or
            (recruiter_role and recruiter_role in interaction.user.roles)
        ):
            await interaction.response.send_message(
                "❌ You do not have permission to list requests.",
                ephemeral=True
            )
            return

        apps = await get_open_applications( )
        if not apps:
            await interaction.response.send_message("There are no open applications at the moment.", ephemeral=True)
            return

        sorted_apps = sort_applications(apps)
        description_lines = []
        for app in sorted_apps:
            if not app["recruiter_id"]:
                if app["ban_history_sent"] == 0:
                    status = "Unclaimed, No Ban History"
                else:
                    status = "Unclaimed, Ban History Sent"
            else:
                status = "Claimed"
            # You can also include a formatted timestamp if desired.
            description_lines.append(
                f"**{app['ingame_name']}** (Thread: `{app['thread_id']}`) | Region: {app['region']} | Status: {status}"
            )
        description = "\n".join(description_lines)
        embed = discord.Embed(
            title="📋 Open Applications",
            description=description,
            color=discord.Color.blue()
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)


    @app_commands.command(name="clear_requests", description="Clears the entire pending requests list.")
    @handle_interaction_errors
    async def clear_requests(self, interaction: discord.Interaction):
        if not is_in_correct_guild(interaction):
            await interaction.response.send_message("❌ This command can only be used in the specified guild.", ephemeral=True)
            return
        guild = interaction.client.get_guild(GUILD_ID)
        leadership_role = self.bot.resources.leadership_role if guild else None
        if not leadership_role or leadership_role not in interaction.user.roles:
            await interaction.response.send_message("❌ You do not have permission to clear requests.", ephemeral=True)
            return
        await clear_role_requests( )
        await interaction.response.send_message("✅ All pending requests have been **cleared**!", ephemeral=True)
        activity_channel = self.resources.activity_ch
        if activity_channel:
            embed = create_user_activity_log_embed("recruitment", f"Cleared Requests", interaction.user, f"User has cleared all requests.")
            await activity_channel.send(embed=embed)

    @app_commands.command(name="votinginfo", description="Show info about the current voting thread")
    @handle_interaction_errors
    async def votinginfo_command(self, interaction: discord.Interaction):
        if not is_in_correct_guild(interaction):
            await interaction.response.send_message("❌ This command can only be used in the specified guild.", ephemeral=True)
            return
        if not isinstance(interaction.channel, discord.Thread):
            await interaction.response.send_message("❌ Use this command inside a thread.", ephemeral=True)
            return
        data = await get_entry(str(interaction.channel.id))
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
        embed.add_field(name="Reminder?",  value=data['reminder_sent'], inline=False)
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @app_commands.command(name="remove", description="Remove a user from trainee / cadet program and close thread!")
    @handle_interaction_errors
    async def lock_thread_command(self, interaction: discord.Interaction, days: int):
        if not is_in_correct_guild(interaction):
            await interaction.response.send_message("❌ This command can only be used in the specified guild.", ephemeral=True)
            return
        guild = interaction.client.get_guild(GUILD_ID)
        recruiter_role = self.resources.recruiter_role if guild else None
        if not recruiter_role or recruiter_role not in interaction.user.roles:
            await interaction.response.send_message("❌ You do not have permission to use this command.", ephemeral=True)
            return
        if not isinstance(interaction.channel, discord.Thread):
            await interaction.response.send_message("❌ This is not a thread.", ephemeral=True)
            return
        data = await get_entry(str(interaction.channel.id))
        if not data:
            await interaction.response.send_message("❌ No DB entry for this thread!", ephemeral=True)
            return

        await interaction.response.defer()
        
        # Archive/close thread
        new_name = "❌ " + str(interaction.channel.name)
        try:
            await interaction.channel.edit(name=new_name)
        except Exception:
            log("Renaming thread failed", level="warning")
        await close_thread(interaction, interaction.channel)
        
        # Remove nickname tag and roles if the member exists
        try:
            member = guild.get_member(int(data["user_id"])) or await guild.fetch_member(int(data["user_id"]))
        except discord.NotFound:
            log(f"Couldn’t find member {int(data["user_id"])} in guild.", level="warning")
            member = None
        
        if member:
            try:
                temp_name = re.sub(r'(?:\s*\[(?:CADET|TRAINEE|SWAT)\])+$', '', member.nick if member.nick else member.name, flags=re.IGNORECASE)
                await member.edit(nick=temp_name)
            except Exception as e:
                log(f"Error updating nickname for {data['user_id']}: {e}", level="error")
            t_role = self.resources.trainee_role
            c_role = self.resources.cadet_role
            try:
                to_remove = [r for r in (t_role, c_role) if r in member.roles]
                if to_remove:
                    await member.remove_roles(*to_remove, reason="Exited cadet/trainee program")
            except Exception as e:
                log(f"Error removing roles for {data['user_id']}: {e}", level="error")
        else:
            log(f"Member {data['user_id']} not found in guild. Proceeding with removal restrictions only.", level="warning")
        
        # Apply blacklist/timeout restriction regardless of member presence
        now = datetime.now()
        reapply_info = ""
        if days == -1:
            reapply_info = "No additional restrictions."
        elif days == 0:
            await add_timeout_record( data["user_id"], "blacklist")
            if member:
                blacklist_role = self.resources.blacklist_role
                if blacklist_role and blacklist_role not in member.roles:
                    try:
                        await member.add_roles(blacklist_role)
                    except Exception as e:
                        log(f"Error assigning blacklist role to {data['user_id']}: {e}", level="error")
            log(f"User {data['user_id']} has been blacklisted.")
            create_user_activity_log_embed("recruitment", "Blacklisted User", interaction.user,
                                        f"User {data['user_id']} has been blacklisted. (Thread ID: <#{interaction.channel.id}>)")
            reapply_info = "User has been blacklisted."
        elif days >= 1:
            expires = now + timedelta(days=days)
            await add_timeout_record( data["user_id"], "timeout", expires)
            if member:
                timeout_role = self.resources.timeout_role
                if timeout_role and timeout_role not in member.roles:
                    try:
                        await member.add_roles(timeout_role)
                    except Exception as e:
                        log(f"Error assigning timeout role to {data['user_id']}: {e}", level="error")
            log(f"User {data['user_id']} has been timed out until {expires}.")
            create_user_activity_log_embed("recruitment", "Timed Out User", interaction.user,
                                        f"User {data['user_id']} has been timed out until {expires}. (Thread ID: <#{interaction.channel.id}>)")
            reapply_info = f"User is timed out until {expires.strftime('%d-%m-%Y')}."
        
        embed = discord.Embed(
            title=f"❌ {data['ingame_name']} has been removed!",
            colour=discord.Color.red()
        )
        if reapply_info:
            embed.add_field(name="Status", value=reapply_info, inline=False)
        embed.set_footer(text="🔒This thread is locked now!")
        await interaction.followup.send(embed=embed)
        
        activity_channel = self.resources.activity_ch
        if activity_channel:
            log_embed = create_user_activity_log_embed("recruitment", "Removed Trainee/Cadet", interaction.user,
                                                        f"User {data['user_id']} removed. (Thread ID: <#{interaction.channel.id}>)")
            await activity_channel.send(embed=log_embed)


    @app_commands.command(name="rename", description="Rename the trainee/cadet thread and update the in-game name in the voting embed.")
    @handle_interaction_errors
    async def rename(self, interaction: discord.Interaction, new_name: str):
        # Check the command is used in the correct guild.
        if not is_in_correct_guild(interaction):
            await interaction.response.send_message("❌ This command can only be used in the specified guild.", ephemeral=True)
            return
        # Make sure it's run inside a thread.
        if not isinstance(interaction.channel, discord.Thread):
            await interaction.response.send_message("❌ This command must be used inside a thread.", ephemeral=True)
            return
        
        # Check if the user issuing command is a Recruiter
        recruiter_role = self.bot.resources.recruiter_role
        if not recruiter_role or recruiter_role not in interaction.user.roles:
            await interaction.response.send_message("❌ You do not have permission to accept this application.", ephemeral=True)
            return
        
        # Ensure the thread is a trainee or cadet notes thread.
        if interaction.channel.parent_id not in [TRAINEE_NOTES_CHANNEL, CADET_NOTES_CHANNEL]:
            await interaction.response.send_message("❌ This command can only be used in a trainee or cadet notes thread.", ephemeral=True)
            return

        # Defer the response so we have extra time and avoid multiple responses.
        await interaction.response.defer(ephemeral=False)

        thread_id = str(interaction.channel.id)
        app_entry = await get_entry(thread_id)
        if not app_entry:
            await interaction.followup.send("❌ No application entry found for this thread.", ephemeral=True)
            return

        # Update the in-game name in the database.
        if not await update_application_ingame_name( thread_id, new_name):
            await interaction.followup.send("❌ Failed to update the application name in the database.", ephemeral=True)
            return

        # Determine the role suffix based on the role type.
        role_suffix = "Trainee Notes" if app_entry["role_type"] == "trainee" else "Cadet Notes"
        new_thread_name = f"{new_name} - {role_suffix}"
        await interaction.channel.edit(name=new_thread_name)

        # Regenerate the voting embed so the new name appears correctly.
        # Use the stored start time, end time (or default to +7 days), recruiter (if claimed), and region.
        start_time = app_entry["starttime"]
        end_time = app_entry["endtime"] if app_entry["endtime"] else (start_time + timedelta(days=7))
        recruiter = int(app_entry["recruiter_id"]) if app_entry["recruiter_id"] else 0
        region = app_entry["region"]
        try:
            new_embed = await create_voting_embed(start_time, end_time, recruiter, region, new_name)
        except Exception as e:
            await interaction.followup.send(f"❌ Failed to generate new embed: {e}", ephemeral=True)
            return

        # Update the embed message if an embed_id is stored.
        if app_entry.get("embed_id"):
            try:
                embed_msg = await interaction.channel.fetch_message(int(app_entry["embed_id"]))
                await embed_msg.edit(embed=new_embed)
            except Exception as e:
                await interaction.followup.send(f"⚠️ Warning: Failed to update the voting embed: {e}", ephemeral=True)

        # Send a public confirmation message.
        embed = discord.Embed(title=f"📇Ingame Name has been changed to *{new_name}*", colour=0xda65ba)
        await interaction.followup.send(embed=embed)

    @app_commands.command(name="promote", description="Promote the user in the current voting thread (Trainee->Cadet or Cadet->SWAT).")
    @handle_interaction_errors
    async def promote_user_command(self, interaction: discord.Interaction):
        if not is_in_correct_guild(interaction):
            await interaction.response.send_message("❌ This command can only be used in the specified guild.", ephemeral=True)
            return
        guild = interaction.client.get_guild(GUILD_ID)
        recruiter_role = self.resources.recruiter_role if guild else None
        if not recruiter_role or recruiter_role not in interaction.user.roles:
            await interaction.response.send_message("❌ You do not have permission to use this command.", ephemeral=True)
            return
        if not isinstance(interaction.channel, discord.Thread):
            await interaction.response.send_message("❌ This command must be used in a thread.", ephemeral=True)
            return
        data = await get_entry(str(interaction.channel.id))
        if not data:
            await interaction.response.send_message("❌ No DB entry for this thread!", ephemeral=True)
            return
        await interaction.response.defer()
        removed = await remove_entry(str(interaction.channel.id))
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
                # remove old, add new in two batch calls
                roles_to_remove = [self.resources.trainee_role] if self.resources.trainee_role in member.roles else []
                roles_to_add    = [self.resources.cadet_role]
                if roles_to_remove:
                    await member.remove_roles(*roles_to_remove)
                
                await member.add_roles(*roles_to_add)
        
                channel_obj = self.resources.cadet_notes_ch
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
                    msg = await thread.send(embed=voting_embed)
                    
                    await asyncio.gather(*(msg.add_reaction(e) for e in (PLUS_ONE_EMOJI, "❔", MINUS_ONE_EMOJI)))
                    
                    swat_chat = self.resources.swat_chat_ch
                    if swat_chat:
                        message_text = random.choice(cadet_messages).replace("{username}", f"<@{data['user_id']}>")
                        cadet_embed = discord.Embed(description=message_text, colour=0x008000)
                        embed_msg = await swat_chat.send(f"<@{data['user_id']}>", embed=cadet_embed)
                    await add_entry(
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
                roles_to_remove = [self.resources.cadet_role] if self.resources.cadet_role in member.roles else []
                roles_to_add    = [self.resources.swat_role, self.resources.officer_role]

                if roles_to_remove:
                    await member.remove_roles(*roles_to_remove)

                await member.add_roles(*roles_to_add)
                try:
                    await member.send(welcome_to_swat)
                except discord.Forbidden:
                    log(f"Could not DM user {member.id} (Forbidden).", level="warning")
                except discord.HTTPException as e:
                    log(f"HTTP error DMing user {member.id}: {e}", level="warning")
                activity_channel = self.resources.activity_ch
                if activity_channel:
                    embed = create_user_activity_log_embed("recruitment", f"Promotion", interaction.user, f"User has promoted to SWAT Officer. (Thread ID: <#{interaction.channel.id}>)")
                    await activity_channel.send(embed=embed)
        except discord.Forbidden:
            await interaction.followup.send("❌ Forbidden: Cannot assign roles or change nickname.", ephemeral=True)
        except discord.HTTPException as e:
            await interaction.followup.send(f"❌ HTTP Error during promotion: {e}", ephemeral=True)

    @app_commands.command(name="extend", description="Extend the current thread's voting period.")
    @app_commands.describe(days="How many days to extend?")
    @handle_interaction_errors
    async def extend_thread_command(self, interaction: discord.Interaction, days: int):
        if not is_in_correct_guild(interaction):
            await interaction.response.send_message("❌ This command can only be used in the specified guild.", ephemeral=True)
            return
        recruiter_role = self.bot.resources.recruiter_role
        if not recruiter_role or recruiter_role not in interaction.user.roles:
            await interaction.response.send_message("❌ You do not have permission to use this command.", ephemeral=True)
            return
        if not isinstance(interaction.channel, discord.Thread):
            await interaction.response.send_message("❌ Use this in a thread channel.", ephemeral=True)
            return
        data = await get_entry(str(interaction.channel.id))
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
        if await update_endtime(str(interaction.channel.id), new_end):
            if data["embed_id"]:
                try:
                    msg = await interaction.channel.fetch_message(int(data["embed_id"]))
                    new_embed = await create_voting_embed(data["starttime"], new_end, int(data["recruiter_id"]), data["region"], data["ingame_name"], extended=True)
                    await msg.edit(embed=new_embed)
                    async with aiosqlite.connect(DATABASE_FILE) as db:
                        await db.execute(
                            """
                            UPDATE entries 
                            SET reminder_sent = 0
                            WHERE thread_id = ?
                            """,
                            (str(interaction.channel.id),)
                        )
                        await db.commit()

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
    @handle_interaction_errors
    async def resend_voting_command(self, interaction: discord.Interaction):
        if not is_in_correct_guild(interaction):
            await interaction.response.send_message("❌ This command can only be used in the specified guild.", ephemeral=True)
            return
        recruiter_role = self.bot.resources.recruiter_role
        if not recruiter_role or recruiter_role not in interaction.user.roles:
            await interaction.response.send_message("❌ You do not have permission to use this command.", ephemeral=True)
            return
        if not isinstance(interaction.channel, discord.Thread):
            await interaction.response.send_message("❌ This command must be used in a thread.", ephemeral=True)
            return
        try:
            data = await get_entry(str(interaction.channel.id))
            if not data:
                await interaction.response.send_message("❌ No DB entry for this thread!", ephemeral=True)
                return
            voting_embed = await create_voting_embed(data["starttime"], data["endtime"], data["recruiter_id"], data["region"], data["ingame_name"])
            embed_msg = await interaction.channel.send(embed=voting_embed)
            await asyncio.gather(*(embed_msg.add_reaction(e) for e in (PLUS_ONE_EMOJI, "❔", MINUS_ONE_EMOJI)))
            
            await interaction.response.send_message("✅ Voting embed has been resent.", ephemeral=True)
        except Exception as e:
            await interaction.response.send_message(f"❌ Error occurred: {e}", ephemeral=True)

    @app_commands.command(name="early_vote", description="Resends a voting embed!")
    @handle_interaction_errors
    async def early_vote(self, interaction: discord.Interaction):
        if not is_in_correct_guild(interaction):
            await interaction.response.send_message("❌ This command can only be used in the specified guild.", ephemeral=True)
            return
        recruiter_role = self.bot.resources.recruiter_role
        if not recruiter_role or recruiter_role not in interaction.user.roles:
            await interaction.response.send_message("❌ You do not have permission to use this command.", ephemeral=True)
            return
        if not isinstance(interaction.channel, discord.Thread):
            await interaction.response.send_message("❌ This command must be used in a thread.", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        try:
            data = await get_entry(str(interaction.channel.id))
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
                        embed_msg = await thread.send(f"<@&{SWAT_ROLE_ID}> It's time for another cadet voting!⌛", embed=voting_embed)
                        await asyncio.gather(*(embed_msg.add_reaction(e) for e in (PLUS_ONE_EMOJI, "❔", MINUS_ONE_EMOJI)))
                        async with aiosqlite.connect(DATABASE_FILE) as db:
                            await db.execute(
                                """
                                UPDATE entries 
                                SET reminder_sent = 1 
                                WHERE thread_id = ?
                                """,
                                (str(interaction.channel.id),)
                            )
                            await db.commit()
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


#
# APPLICATION COMMANDS
#
    @app_commands.command(name="app_info", description="Show info about the current application thread.")
    @handle_interaction_errors
    async def app_info_command(self, interaction: discord.Interaction):
        if not is_in_correct_guild(interaction):
            await interaction.response.send_message("❌ Wrong guild!", ephemeral=True)
            return
        if not isinstance(interaction.channel, discord.Thread):
            await interaction.response.send_message("❌ This must be used in a thread!", ephemeral=True)
            return

        app_data = await get_application( str(interaction.channel.id))
        if not app_data:
            await interaction.response.send_message("❌ No application data found for this thread!", ephemeral=True)
            return

        embed = discord.Embed(title="Application Info", color=discord.Color.blue())
        embed.add_field(name="Thread_ID", value=f"<#{app_data['thread_id']}>", inline=False)
        embed.add_field(name="Applicant", value=f"<@{app_data['applicant_id']}>", inline=False)
        embed.add_field(
            name="Recruiter", 
            value=(f"<@{app_data['recruiter_id']}>" if app_data['recruiter_id'] else "No one claimed yet"), 
            inline=False
        )
        embed.add_field(name="Started", value=str(app_data["starttime"]), inline=False)
        embed.add_field(name="IGN", value=app_data["ingame_name"], inline=True)
        embed.add_field(name="Region", value=app_data["region"], inline=True)
        embed.add_field(name="Age", value=app_data["age"], inline=True)
        embed.add_field(name="Level", value=app_data["level"], inline=True)
        embed.add_field(name="Join Reason", value=app_data["join_reason"], inline=False)
        embed.add_field(name="Previous Crews", value=app_data["previous_crews"], inline=False)
        embed.add_field(name="Closed?", value=("Yes" if app_data["is_closed"] else "No"), inline=False)
        embed.add_field(name="Silenced?", value=("Yes" if app_data["silenced"] else "No"), inline=False)

        await interaction.response.send_message(embed=embed, ephemeral=True)


    @app_commands.command(name="app_remove", description="Remove this application and lock/archive the thread.")
    @handle_interaction_errors
    async def app_remove_command(self, interaction: discord.Interaction, days: int):
        if not is_in_correct_guild(interaction):
            await interaction.response.send_message("❌ Wrong guild!", ephemeral=True)
            return
        
        if not isinstance(interaction.channel, discord.Thread):
            await interaction.response.send_message("❌ Must be used in a thread!", ephemeral=True)
            return

        # Check if the user issuing command is a Recruiter
        recruiter_role = self.bot.resources.recruiter_role
        if not recruiter_role or recruiter_role not in interaction.user.roles:
            await interaction.response.send_message("❌ You do not have permission to accept this application.", ephemeral=True)
            return


        app_data = await get_application( str(interaction.channel.id))
        if not app_data:
            await interaction.response.send_message("❌ No application data found or already removed!", ephemeral=True)
            return

        # Mark the application as removed in the database.
        removed = await mark_application_removed( str(interaction.channel.id))
        if not removed:
            await interaction.response.send_message("❌ Failed to mark the application as removed.", ephemeral=True)
            return

        guild = interaction.guild
        member = guild.get_member(int(app_data["applicant_id"])) if guild else None
        now = datetime.now()
        reapply_info = ""
        if days == -1:
            reapply_info = "No restrictions applied. The user may reapply immediately."
        elif days == 0:
            await add_timeout_record( app_data["applicant_id"], "blacklist")
            if member:
                blacklist_role = self.resources.blacklist_role
                if blacklist_role and blacklist_role not in member.roles:
                    try:
                        await member.add_roles(blacklist_role)
                    except Exception as e:
                        log(f"Error assigning blacklist role to {app_data['applicant_id']}: {e}", level="error")
            log(f"User {app_data['applicant_id']} has been blacklisted.")
            reapply_info = "User has been blacklisted and cannot reapply."
        elif days >= 1:
            expires = now + timedelta(days=days)
            await add_timeout_record( app_data["applicant_id"], "timeout", expires)
            if member:
                timeout_role = self.resources.timeout_role
                if timeout_role and timeout_role not in member.roles:
                    try:
                        await member.add_roles(timeout_role)
                    except Exception as e:
                        log(f"Error assigning timeout role to {app_data['applicant_id']}: {e}", level="error")
            log(f"User {app_data['applicant_id']} has been timed out until {expires}.")
            reapply_info = f"User is timed out until {expires.strftime('%d-%m-%Y')} and may reapply after that date."

        embed = discord.Embed(
            title="❌ This application has been removed!",
            colour=discord.Color.red()
        )
        embed.add_field(name="Recruiter:", value=f"<@{interaction.user.id}>", inline=False)
        if reapply_info:
            embed.add_field(name="Reapply Info", value=reapply_info, inline=False)
        embed.set_footer(text="🔒This thread is locked now!")
        await interaction.response.send_message(embed=embed)

        activity_channel = self.resources.activity_ch
        if activity_channel:
            log_embed = create_user_activity_log_embed("recruitment", "Application Removed", interaction.user,
                                                    f"User has removed this application. (Thread ID: <#{interaction.channel.id}>)")
            await activity_channel.send(embed=log_embed)

        try:
            await interaction.channel.edit(locked=True, archived=True)
        except discord.Forbidden:
            await interaction.response.send_message("❌ Forbidden: Cannot lock/archive the thread!", ephemeral=True)



    @app_commands.command(name="app_accept", description="Accept this application, awarding the Trainee role to the applicant.")
    @handle_interaction_errors
    async def app_accept_command(self, interaction: discord.Interaction):
        # Immediately defer so we can use followup responses
        await interaction.response.defer(thinking=True)
        
        # Use followup.send for error responses (ephemeral)
        if not is_in_correct_guild(interaction):
            await interaction.followup.send("❌ Wrong guild!", ephemeral=True)
            return

        # Must be used in a thread
        if not isinstance(interaction.channel, discord.Thread):
            await interaction.followup.send("❌ Must be used in a thread!", ephemeral=True)
            return

        # Check if the user issuing command is a Recruiter
        recruiter_role = self.bot.resources.recruiter_role
        if not recruiter_role or recruiter_role not in interaction.user.roles:
            await interaction.followup.send("❌ You do not have permission to accept this application.", ephemeral=True)
            return

        app_data = await get_application( str(interaction.channel.id))
        if not app_data:
            await interaction.followup.send("❌ No application data found for this thread!", ephemeral=True)
            return

        if app_data["is_closed"] == 1:
            await interaction.followup.send("❌ This application is already closed!", ephemeral=True)
            return

        # If the application is not claimed, automatically claim it:
        if not app_data.get("recruiter_id"):
            await update_application_recruiter( str(interaction.channel.id), str(interaction.user.id))
            app_data["recruiter_id"] = str(interaction.user.id)
            # await interaction.followup.send("ℹ️ Application was unclaimed. It has now been claimed by you. \n *Processing the command, please wait*")

        # Now do the "Trainee add" logic:
        applicant_id = int(app_data["applicant_id"])
        if await is_user_in_database( applicant_id):
            await interaction.followup.send("❌ That user is already in the voting database!", ephemeral=True)
            return

        guild = interaction.guild
        member = guild.get_member(applicant_id)
        if not member:
            await interaction.followup.send("❌ That user is no longer in the guild!", ephemeral=True)
            return

        # 1) Adjust nickname to include [TRAINEE]
        await set_user_nickname(member, "trainee", app_data["ingame_name"])

        roles_to_add = []
        if trainee_role_obj := self.resources.trainee_role:
            roles_to_add.append(trainee_role_obj)

        region_map = {
            "EU": self.resources.eu_role,
            "NA": self.resources.na_role,
            "SEA": self.resources.sea_role,
        }
        if region_role := region_map.get(app_data["region"].upper()):
            roles_to_add.append(region_role)

        if roles_to_add:
            try:
                await member.add_roles(*roles_to_add)
            except discord.Forbidden:
                await interaction.followup.send("❌ Bot lacks permission to assign roles.", ephemeral=True)
                return
        
        # 4) Create new thread in the Trainee Notes channel with a voting embed
        notes_channel = self.resources.trainee_notes_ch
        if not notes_channel:
            await interaction.followup.send("❌ Trainee notes channel not found.", ephemeral=True)
            return

        start_time = get_rounded_time()
        end_time   = start_time + timedelta(days=7)
        thread_title = f"{app_data['ingame_name']} | TRAINEE Notes"
        try:
            trainee_thread = await notes_channel.create_thread(
                name=thread_title,
                type=discord.ChannelType.public_thread,
                invitable=False,
                reason="New Trainee accepted"
            )
        except discord.Forbidden:
            await interaction.followup.send("❌ Cannot create new thread in Trainee notes channel.", ephemeral=True)
            return

        # 5) Send the voting embed and add reactions
        voting_embed = await create_voting_embed(start_time, end_time, app_data["recruiter_id"], app_data["region"], app_data["ingame_name"])
        msg = await trainee_thread.send(embed=voting_embed)
        await asyncio.gather(*(msg.add_reaction(e) for e in (PLUS_ONE_EMOJI, "❔", MINUS_ONE_EMOJI)))

        # 6) Insert into the "entries" table for tracking
        inserted = await add_entry(
            thread_id=str(trainee_thread.id),
            recruiter_id=app_data["recruiter_id"],
            starttime=start_time,
            endtime=end_time,
            role_type="trainee",
            embed_id=str(msg.id),
            ingame_name=app_data["ingame_name"],
            user_id=str(applicant_id),
            region=app_data["region"]
        )
        if not inserted:
            # Optionally log the DB insertion failure
            pass

        # 7) Post a welcome message in the trainee chat
        trainee_chat = self.resources.trainee_chat_ch
        if trainee_chat:
            import random
            message_text = random.choice(trainee_messages).replace("{username}", f"<@{applicant_id}>")
            welcome_embed = discord.Embed(description=message_text, colour=0x008000)
            await trainee_chat.send(f"<@{applicant_id}>", embed=welcome_embed)

        await update_application_status( str(interaction.channel.id), 'accepted')
        # Mark application as closed in your DB and lock/archive the application thread
        await close_application( str(interaction.channel.id))
        acceptance_embed = discord.Embed(
            title="✅ This application has been **ACCEPTED**!",
            description=f"<@{applicant_id}> is now a Trainee.",
            colour=discord.Color.green()
        )
        acceptance_embed.add_field(name="Recruiter: ", value=f"<@{interaction.user.id}>", inline=False)
        acceptance_embed.set_footer(text="🔒 This thread is locked now.")
        
        dm_embed = discord.Embed(title=":white_check_mark: Your application as a S.W.A.T Trainee has been accepted!", description=":tada: Congratulations!\nYou’ve automatically received your Trainee role — the first step is complete!\n\n:pushpin: All additional information can be found in the #「:pushpin:」trainee-info channel.\nPlease make sure to carefully read through everything to get started on the right foot.\n\nWelcome aboard, and good luck on your journey!\n\n", colour=discord.Color.green())
        dm_embed.add_field(name="📝 Help Us Improve – Application Feedback Form", value=f"We’d love to hear your thoughts on the application process! Your feedback helps us improve the experience for everyone.\n\n👉 [Click here to fill out the feedback form]({FEEDBACK_FORM_LINK})\n\nIt only takes a minute, and your input is greatly appreciated. Thank you!", inline=False)
        
        # Send a DM to the applicant
        dm_sent = True
        try:
            await member.send(embed=dm_embed)
        except discord.Forbidden:
            dm_sent = False
        except discord.HTTPException:
            dm_sent = False

        # build your “accepted” embed…
        if not dm_sent:
            acceptance_embed.add_field(
                name="⚠️ DM Failed",
                value="Could not send acceptance DM (they may have DMs blocked).",
                inline=False
            )

        await interaction.followup.send(embed=acceptance_embed)
        # (optionally also an ephemeral: “⚠ Could not DM the user…”)

        activity_channel = self.resources.activity_ch
        if activity_channel:
            embed = create_user_activity_log_embed("recruitment", f"Application Accepted", interaction.user, f"User has accepted this application. (Thread ID: <#{interaction.channel.id}>)")
            await activity_channel.send(embed=embed)
        try:
            await interaction.channel.edit(locked=True, archived=True)
        except discord.Forbidden:
            pass

    @app_commands.command(name="app_accept_cadet", description="Accept this application, and get the person to cadet immediatly.")
    @handle_interaction_errors
    async def app_accept_cadet_command(self, interaction: discord.Interaction):
        # Immediately defer so we can use followup responses
        await interaction.response.defer(ephemeral=False)

        # Use followup.send for error responses (ephemeral)
        if not is_in_correct_guild(interaction):
            await interaction.followup.send("❌ Wrong guild!", ephemeral=True)
            return

        # Must be used in a thread
        if not isinstance(interaction.channel, discord.Thread):
            await interaction.followup.send("❌ Must be used in a thread!", ephemeral=True)
            return

        # Check if the user issuing command is a Recruiter
        recruiter_role = self.bot.resources.recruiter_role
        if not recruiter_role or recruiter_role not in interaction.user.roles:
            await interaction.followup.send("❌ You do not have permission to accept this application.", ephemeral=True)
            return

        app_data = await get_application( str(interaction.channel.id))
        if not app_data:
            await interaction.followup.send("❌ No application data found for this thread!", ephemeral=True)
            return

        if app_data["is_closed"] == 1:
            await interaction.followup.send("❌ This application is already closed!", ephemeral=True)
            return

        # If the application is not claimed, automatically claim it:
        if not app_data.get("recruiter_id"):
            await update_application_recruiter( str(interaction.channel.id), str(interaction.user.id))
            app_data["recruiter_id"] = str(interaction.user.id)
            await interaction.followup.send("ℹ️ Application was unclaimed. It has now been claimed by you. \n *Processing the command, please wait*", ephemeral=True)

        # Now do the "Trainee add" logic:
        applicant_id = int(app_data["applicant_id"])
        if await is_user_in_database( applicant_id):
            await interaction.followup.send("❌ That user is already in the voting database!", ephemeral=True)
            return

        guild = interaction.guild
        member = guild.get_member(applicant_id)
        if not member:
            await interaction.followup.send("❌ That user is no longer in the guild!", ephemeral=True)
            return

        # 1) Adjust nickname to include [CADET]
        await set_user_nickname(member, "cadet", app_data["ingame_name"])

        # 2) Add the Cadet role
        cadet_role_obj = self.resources.cadet_role
        if cadet_role_obj:
            try:
                await member.add_roles(cadet_role_obj)
            except discord.Forbidden:
                await interaction.followup.send("❌ Bot lacks permission to assign the Cadet role.", ephemeral=True)
                return
        else:
            await interaction.followup.send("❌ Cadet role not found.", ephemeral=True)
            return

        # 3) Add region role (EU, NA, SEA)
        # 3) Add region role (EU, NA, SEA)
        region = app_data["region"]
        region_role = None
        if region.upper() == "EU":
            region_role = self.resources.eu_role
        elif region.upper() == "NA":
            region_role = self.resources.na_role
        elif region.upper() == "SEA":
            region_role = self.resources.sea_role
        
        if region_role:
            try:
                await member.add_roles(region_role)
            except discord.Forbidden:
                await interaction.followup.send("❌ Bot lacks permission to assign region role.", ephemeral=True)
                return

        # 4) Create new thread in the Trainee Notes channel with a voting embed
        notes_channel = self.resources.cadet_notes_ch
        if not notes_channel:
            await interaction.followup.send("❌ Cadet notes channel not found.", ephemeral=True)
            return

        start_time = get_rounded_time()
        end_time   = start_time + timedelta(days=7)
        thread_title = f"{app_data['ingame_name']} | CADET Notes"
        try:
            trainee_thread = await notes_channel.create_thread(
                name=thread_title,
                type=discord.ChannelType.public_thread,
                invitable=False,
                reason="New Cadet accepted"
            )
        except discord.Forbidden:
            await interaction.followup.send("❌ Cannot create new thread in Cadet notes channel.", ephemeral=True)
            return

        # 5) Send the voting embed and add reactions
        voting_embed = await create_voting_embed(start_time, end_time, app_data["recruiter_id"], region, app_data["ingame_name"])
        msg = await trainee_thread.send(embed=voting_embed)
        await asyncio.gather(*(msg.add_reaction(e) for e in (PLUS_ONE_EMOJI, "❔", MINUS_ONE_EMOJI)))

        
        # 6) Insert into the "entries" table for tracking
        inserted = await add_entry(
            thread_id=str(trainee_thread.id),
            recruiter_id=app_data["recruiter_id"],
            starttime=start_time,
            endtime=end_time,
            role_type="cadet",
            embed_id=str(msg.id),
            ingame_name=app_data["ingame_name"],
            user_id=str(applicant_id),
            region=region
        )
        if not inserted:
            # Optionally log the DB insertion failure
            pass

        # 7) Post a welcome message in the trainee chat
        swat_chat = self.resources.swat_chat_ch
        if swat_chat:
            message_text = random.choice(cadet_messages).replace("{username}", f"<@{applicant_id}>")
            cadet_embed = discord.Embed(description=message_text, colour=0x008000)
            await swat_chat.send(f"<@{applicant_id}>", embed=cadet_embed)

        await update_application_status( str(interaction.channel.id), 'accepted_cadet')
        # Mark application as closed in your DB and lock/archive the application thread
        await close_application( str(interaction.channel.id))
        acceptance_embed = discord.Embed(
            title="✅ This application has been **ACCEPTED**!",
            description=f"<@{applicant_id}> is now a Cadet.",
            colour=discord.Color.green()
        )
        acceptance_embed.set_footer(text="🔒 This thread is locked now.")
        await interaction.followup.send(embed=acceptance_embed, ephemeral=False)
        activity_channel = self.resources.activity_ch
        if activity_channel:
            embed = create_user_activity_log_embed("recruitment", f"Application Accepted (Cadet)", interaction.user, f"User has accepted this application to Cadet. (Thread ID: <#{interaction.channel.id}>)")
            await activity_channel.send(embed=embed)
        try:
            await interaction.channel.edit(locked=True, archived=True)
        except discord.Forbidden:
            pass

    @app_commands.command(
        name="app_deny",
        description="Deny the application with a reason and a note about reapplying."
    )
    @app_commands.describe(
        reason="Why is this application being denied?",
        can_reapply="Enter -1 for no timeout, 0 for blacklist, or number of days for timeout."
    )
    @handle_interaction_errors
    async def app_deny_command(self, interaction: discord.Interaction, reason: str, can_reapply: int):
        await interaction.response.defer(ephemeral=False)
        if not is_in_correct_guild(interaction):
            await interaction.followup.send("❌ Wrong guild!", ephemeral=True)
            return
        if not isinstance(interaction.channel, discord.Thread):
            await interaction.followup.send("❌ Must be used inside a thread!", ephemeral=True)
            return
        
        # Check if the user issuing command is a Recruiter
        recruiter_role = self.bot.resources.recruiter_role
        if not recruiter_role or recruiter_role not in interaction.user.roles:
            await interaction.followup.send("❌ You do not have permission to accept this application.", ephemeral=True)
            return

        app_data = await get_application( str(interaction.channel.id))
        if not app_data:
            await interaction.followup.send("❌ No application data found for this thread!", ephemeral=True)
            return
        if app_data["is_closed"] == 1:
            await interaction.followup.send("❌ This application is already closed!", ephemeral=True)
            return

        guild = interaction.guild
        member = guild.get_member(int(app_data["applicant_id"])) if guild else None
        now = datetime.now()
        reapply_info = ""
        if can_reapply == -1:
            reapply_info = "No additional restrictions."
        elif can_reapply == 0:
            await add_timeout_record( app_data["applicant_id"], "blacklist")
            if member:
                blacklist_role = self.resources.blacklist_role
                if blacklist_role and blacklist_role not in member.roles:
                    try:
                        await member.add_roles(blacklist_role)
                    except Exception as e:
                        log(f"Error assigning blacklist role to {app_data['applicant_id']}: {e}", level="error")
            log(f"User {app_data['applicant_id']} has been blacklisted.")
            create_user_activity_log_embed("recruitment", "Blacklist User", interaction.user,
                                        f"User {app_data['applicant_id']} has been blacklisted.")
            reapply_info = "User has been blacklisted."
        elif can_reapply >= 1:
            expires = now + timedelta(days=can_reapply)
            await add_timeout_record( app_data["applicant_id"], "timeout", expires)
            if member:
                timeout_role = self.resources.timeout_role
                if timeout_role and timeout_role not in member.roles:
                    try:
                        await member.add_roles(timeout_role)
                    except Exception as e:
                        log(f"Error assigning timeout role to {app_data['applicant_id']}: {e}", level="error")
            log(f"User {app_data['applicant_id']} has been timed out until {expires}.")
            create_user_activity_log_embed("recruitment", "Timeout User", interaction.user,
                                        f"User {app_data['applicant_id']} has been timed out until {expires}.")
            reapply_info = f"User can reapply on {expires.strftime('%d-%m-%Y')}."
        
        # Mark the application as closed and update status
        await close_application( str(interaction.channel.id))
        await update_application_status( str(interaction.channel.id), 'denied')

        applicant_id = int(app_data["applicant_id"])
        applicant_user = interaction.client.get_user(applicant_id)
        dm_embed = discord.Embed(title="❌ Your application as a S.W.A.T Trainee has been denied.", description="We are sorry to inform you that your SWAT application has been denied.", colour=discord.color.red())
        if can_reapply == -1:
            dm_embed.add_field(name="Reason:", value=f"```{reason}```\nYou are free to reapply immediatly. Please ensure any issues mentioned above are addressed before reapplying.", inline=False)
        elif can_reapply == 0:
            dm_embed.add_field(name="Reason:", value=f"```{reason}```\nYou have been blacklisted from applying for SWAT. Please contact a recruiter via ticket to appeal.", inline=False)
        elif can_reapply >= 1:
            dm_embed.add_field(name="Reason:", value=f"```{reason}```\nYou are restricted from applying for {can_reapply} days. You can reapply on {expires.strftime('%d-%m-%Y')}.", inline=False)
        dm_embed.add_field(name="", value="Thank you for your interest in joining SWAT and taking the time to apply.", inline=False)
        dm_embed.add_field(name="", value="", inline=False)
        dm_embed.add_field(name="📝 Help Us Improve – Application Feedback Form", value=f"We’d love to hear your thoughts on the application process! Your feedback helps us improve the experience for everyone.\n\n👉 [Click here to fill out the feedback form]({FEEDBACK_FORM_LINK})\n\nIt only takes a minute, and your input is greatly appreciated. Thank you!", inline=False)
        
          # after
        dm_sent = True
        try:
            await applicant_user.send(embed=dm_embed)
        except discord.Forbidden:
            dm_sent = False

        denied_embed = discord.Embed(
            title="❌ Application Denied",
            description=f"**Reason:** {reason}\n**Reapply Info:** {reapply_info}",
            color=discord.Color.red()
        )
        # if DM failed, add a note to the embed itself
        if not dm_sent:
            denied_embed.add_field(
                name="⚠️ DM Failed",
                value="Could not send a DM to the applicant; they may have DMs blocked.",
                inline=False
            )
        denied_embed.set_footer(text="🔒 This thread is locked now!")
        await interaction.followup.send(embed=denied_embed, ephemeral=False)

        activity_channel = self.resources.activity_ch
        if activity_channel:
            log_embed = create_user_activity_log_embed("recruitment", "Application Denied", interaction.user,
                                                    f"Application denied for thread ID: <#{interaction.channel.id}>")
            await activity_channel.send(embed=log_embed)
        try:
            await interaction.channel.edit(locked=True, archived=True)
        except discord.Forbidden:
            pass

    @app_deny_command.autocomplete("reason")
    async def reason_autocomplete(self, interaction: discord.Interaction, current: str):
        # Define a list of predefined reason options
        options = [
            "Excessive ban history",
            "Recent ban(s) within the last 30 days",
            "Level 20+ requirement not met"
        ]
        # Filter the options based on the current input (ignoring case)
        return [
            app_commands.Choice(name=option, value=option)
            for option in options if current.lower() in option.lower()
        ]

    @app_commands.command(name="app_claim", description="Claim this application.")
    @handle_interaction_errors
    async def app_claim_command(self, interaction: discord.Interaction):
        # Retrieve application data for the current thread.
        app_data = await get_application( str(interaction.channel.id))
        if not app_data:
            await interaction.response.send_message("❌ No application data found for this thread!", ephemeral=True)
            return

        # Check if the user has the Recruiter role.
        recruiter_role = self.bot.resources.recruiter_role
        if not recruiter_role or recruiter_role not in interaction.user.roles:
            await interaction.response.send_message("❌ Only recruiters can claim this application!", ephemeral=True)
            return

        # **New Check:** Ensure the application is not already claimed.
        if app_data.get("recruiter_id"):
            await interaction.response.send_message(
                "❌ This application has already been claimed!",
                ephemeral=True
            )
            return

        # If unclaimed, update the DB to mark the current user as the recruiter.
        updated = await update_application_recruiter( str(interaction.channel.id), str(interaction.user.id))
        if updated:
            await interaction.response.send_message(embed=discord.Embed(title=f"✅ {interaction.user.name} has claimed this application.", colour=0x23ef56))
        else:
            await interaction.response.send_message("❌ Failed to update recruiter in DB!", ephemeral=True)


    @app_commands.command(
        name="blacklist",
        description="Manually blacklist a user by mention or by user ID."
    )
    @app_commands.describe(
        target="The user to blacklist (mention)",
        user_id="The user ID to blacklist"
    )
    @handle_interaction_errors
    async def blacklist_command(
        self,
        interaction: discord.Interaction,
        target: discord.Member = None,
        user_id: str = None
    ):
        # exactly one of target or user_id must be provided
        if (target is None and user_id is None) or (target is not None and user_id is not None):
            return await interaction.response.send_message(
                "❌ Please specify exactly one of `target` (mention) or `user_id`.",
                ephemeral=True
            )

        # resolve member_id and optional Member object
        if target:
            member_id = target.id
            member_obj = target
        else:
            try:
                member_id = int(user_id)
            except ValueError:
                return await interaction.response.send_message(
                    "❌ `user_id` must be a valid integer.",
                    ephemeral=True
                )
            member_obj = interaction.guild.get_member(member_id)

        # permission check
        recruiter_role = self.bot.resources.recruiter_role
        if not recruiter_role or recruiter_role not in interaction.user.roles:
            return await interaction.response.send_message(
                "❌ You do not have permission to blacklist users.",
                ephemeral=True
            )

        # apply blacklist in your DB
        await add_timeout_record( str(member_id), "blacklist")

        # if they’re still in guild, add the Discord role
        if member_obj:
            blacklist_role = self.resources.blacklist_role
            if blacklist_role and blacklist_role not in member_obj.roles:
                await member_obj.add_roles(blacklist_role)

        log(f"User {member_id} has been blacklisted.")
        activity_channel = self.resources.activity_ch
        if activity_channel:
            embed = create_user_activity_log_embed("recruitment", "Blacklist User", interaction.user,
                                                f"User {member_obj.display_name} has been blacklisted.")
            await activity_channel.send(embed=embed)
        
        embed = discord.Embed(
            title="User Blacklisted",
            description=f"<@{member_id}> has been blacklisted.",
            colour=discord.Colour.red()
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)



    @app_commands.command(
        name="show_restrictions",
        description="Lists all active blacklists and timeouts."
    )
    @handle_interaction_errors
    async def show_restrictions(self, interaction: discord.Interaction):
        # 1) Permission check
        recruiter = self.bot.resources.recruiter_role
        if not recruiter or recruiter not in interaction.user.roles:
            return await interaction.response.send_message(
                "❌ You do not have permission to view this.", ephemeral=True
            )

        # 2) Fetch records
        records = await get_all_timeouts()
        if not records:
            return await interaction.response.send_message(
                "✅ There are no active blacklists or timeouts.", ephemeral=True
            )

        # 3) Build the embed
        embed = discord.Embed(
            title="🚨 Active Blacklists & Timeouts",
            color=discord.Color.dark_red()
        )
        guild = interaction.guild

        for rec in records:
            user_id = int(rec["user_id"])
            # Try to get Member, fallback to User
            member = guild.get_member(user_id) or await interaction.client.fetch_user(user_id)
            name    = member.display_name if hasattr(member, "display_name") else member.name

            if rec["type"] == "timeout":
                # Parse expiration
                exp = rec["expires_at"]
                if isinstance(exp, str):
                    exp = datetime.fromisoformat(exp) if exp else None
                if exp:
                    ts = d_timestamp(exp.isoformat(), "f")
                    value = f"Timeout until {ts}"
                    icon  = "⏰"
                else:
                    value = "Timeout until: N/A"
                    icon  = "⏰"
            else:
                value = "User is blacklisted"
                icon  = "🚫"

            embed.add_field(
                name=f"{icon} {user_id} — {name}",
                value=value,
                inline=False
            )

        # 4) Send
        await interaction.response.send_message(embed=embed, ephemeral=True)
    @app_commands.command(
        name="remove_restriction",
        description="Remove blacklist/timeout from a user by mention or by user ID."
    )
    @app_commands.describe(
        target="The user to un-restrict (mention)",
        user_id="The user ID to un-restrict"
    )
    @handle_interaction_errors
    async def remove_restriction_command(
        self,
        interaction: discord.Interaction,
        target: discord.Member = None,
        user_id: str = None
    ):
        # exactly one of target or user_id must be provided
        if (target is None and user_id is None) or (target is not None and user_id is not None):
            return await interaction.response.send_message(
                "❌ Please specify exactly one of `target` (mention) or `user_id`.",
                ephemeral=True
            )

        # resolve member_id and optional Member object
        if target:
            member_id = target.id
            member_obj = target
        else:
            try:
                member_id = int(user_id)
            except ValueError:
                return await interaction.response.send_message(
                    "❌ `user_id` must be a valid integer.",
                    ephemeral=True
                )
            member_obj = interaction.guild.get_member(member_id)

        # permission check
        recruiter_role = self.bot.resources.recruiter_role
        if not recruiter_role or recruiter_role not in interaction.user.roles:
            return await interaction.response.send_message(
                "❌ You do not have permission to remove restrictions.",
                ephemeral=True
            )

        # remove from your DB
        removed = await remove_timeout_record( str(member_id))
        if not removed:
            return await interaction.response.send_message(
                "❌ No active blacklist or timeout found for that user.",
                ephemeral=True
            )

        # clean up Discord roles if they’re still here
        timeout_role   = self.resources.timeout_role
        blacklist_role = self.resources.blacklist_role
        if member_obj:
            roles_to_strip = []
            if timeout_role and timeout_role in member_obj.roles:
                roles_to_strip.append(timeout_role)
            if blacklist_role and blacklist_role in member_obj.roles:
                roles_to_strip.append(blacklist_role)

            if roles_to_strip:
                try:
                    await member_obj.remove_roles(*roles_to_strip)
                except discord.Forbidden:
                    pass

        log(f"User {member_id} restriction removed.", level="info")
        activity_channel = self.resources.activity_ch
        if activity_channel:
            embed = create_user_activity_log_embed("recruitment", "Remove Restriction", interaction.user,
                                                f"Removed restrictions from user {member_obj.display_name}.")
            await activity_channel.send(embed=embed)
        
        embed = discord.Embed(
            title="Restriction Removed",
            description=f"Blacklist/timeout removed from <@{member_id}>.",
            colour=discord.Colour.green()
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)


#
# Toggle Application Status Command
#


    @app_commands.command(
        name="toggle_applications",
        description="Toggle applications for a region as OPEN or CLOSED."
    )
    @app_commands.describe(
        region="Select a region",
        status="Select the new status"
    )
    @app_commands.choices(
        region=[
            app_commands.Choice(name="EU",  value="EU"),
            app_commands.Choice(name="NA", value="NA"),
            app_commands.Choice(name="SEA", value="SEA"),
        ],
        status=[
            app_commands.Choice(name="Open",   value="OPEN"),
            app_commands.Choice(name="Closed", value="CLOSED"),
        ]
    )
    @handle_interaction_errors
    async def toggle_applications(
        self,
        interaction: discord.Interaction,
        region: str = "EU",
        status: str = "OPEN"
    ):
        # Immediately defer so we can take our time
        await interaction.response.defer(ephemeral=True)

        # 1) Permission check
        recruiter_role = self.bot.resources.recruiter_role
        if not recruiter_role or recruiter_role not in interaction.user.roles:
            return await interaction.followup.send(
                "❌ You do not have permission to toggle applications.",
                ephemeral=True
            )

        region_val = region.upper()   # "EU", "NA", "SEA"
        status_val = status.upper()   # "OPEN" or "CLOSED"

        # 2) Update local DB
        if not await update_region_status(region_val, status_val):
            return await interaction.followup.send(
                "❌ Failed to update region status in the database.",
                ephemeral=True
            )

        # 3) Refresh the local embed
        new_embed = await create_application_embed()
        channel   = self.resources.apply_ch
        try:
            msg = await channel.fetch_message(self.application_embed_message_id)
            await msg.edit(embed=new_embed)
        except Exception as e:
            log(f"Error editing local application embed: {e}", level="error")

        # 4) Log locally
        activity_channel = self.resources.activity_ch
        if activity_channel:
            log_embed = create_user_activity_log_embed(
                "recruitment",
                "Application Status Change",
                interaction.user,
                f"{region_val} → {status_val}"
            )
            await activity_channel.send(embed=log_embed)

        # 5) Read API token
        try:
            with open(SWAT_WEBSITE_TOKEN_FILE, "r") as f:
                website_api_token = f.read().strip()
        except Exception as e:
            log(f"Failed to read API token file '{SWAT_WEBSITE_TOKEN_FILE}': {e}", level="error")
            return await interaction.followup.send(
                "❌ Internal error reading API token.",
                ephemeral=True
            )

        # 6) Call external API with correct header name
        if SEND_API_DATA:
            url     = f"{SWAT_WEBSITE_URL}/api/application/status"
            payload = {"server": region_val.lower(), "status": status_val.lower()}
            headers = {
                "X-Api-Token":    website_api_token,
                "Content-Type":   "application/json",
                "User-Agent":     (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/114.0.0.0 Safari/537.36"
                ),
                "Accept":         "application/json, text/javascript, */*; q=0.01",
                "Accept-Language":"en-US,en;q=0.9",
            }

            try:
                resp = requests.post(url, json=payload, headers=headers, timeout=10)
                resp.raise_for_status()
            except requests.exceptions.HTTPError as e:
                body = getattr(e.response, "text", "")
                log(f"API HTTP error {e.response.status_code}", level="error")
                return await interaction.followup.send(
                    f"⚠ Website API returned {e.response.status_code} -> Data not updated",
                    ephemeral=True
                )
            except requests.exceptions.RequestException as e:
                log(f"Error calling external API: {e}", level="error")
                return await interaction.followup.send(
                    "⚠ Could not reach website application API.",
                    ephemeral=True
                )

            # 7) Parse JSON
            try:
                data = resp.json()
            except ValueError:
                log(f"Invalid JSON from API: {resp.text}", level="error")
                return await interaction.followup.send(
                    "⚠ Received invalid response from website API.",
                    ephemeral=True
                )

            if not data.get("success"):
                err = data.get("error", "Unknown error")
                log(f"External API error response: {err}", level="error")
                return await interaction.followup.send(
                    f"⚠ API error: {err}",
                    ephemeral=True
                )

            log(f"External API status update succeeded: {region_val}→{status_val}", level="info")

        # 8) Final confirmation
        await interaction.followup.send(
            f"✅ Applications for **{region_val}** set to **{status_val}**.",
            ephemeral=True
        )

    @app_commands.command(name="app_stats", description="Show application statistics.")
    @handle_interaction_errors
    async def app_stats(self, interaction: discord.Interaction, days: int = 0):
        guild = interaction.client.get_guild(GUILD_ID)
        recruiter_role = self.resources.recruiter_role if guild else None
        if not recruiter_role or recruiter_role not in interaction.user.roles:
            await interaction.response.send_message("❌ You do not have permission to use this command.", ephemeral=True)
            return

        stats = await get_application_stats( days)
        if days > 0:
            title = f"Application Statistics (Last {days} days)"
        else:
            title = "Application Statistics (All Time)"
            
        embed = discord.Embed(title=title, color=discord.Color.blue())
        embed.add_field(name="✅ Accepted Applications", value=str(stats["accepted"]), inline=False)
        embed.add_field(name="❌ Denied Applications", value=str(stats["denied"]), inline=False)
        embed.add_field(name="⚠️ Withdrawn Applications", value=str(stats["withdrawn"]), inline=False)
        embed.add_field(name="🟢 Current Open Applications", value=str(stats["open"]), inline=False)
        await interaction.response.send_message(embed=embed, ephemeral=True)



    @app_commands.command(
        name="app_history",
        description="Show all application attempts for a user (by mention or by user ID)."
    )
    @app_commands.describe(
        target="The user to look up (mention)",
        user_id="The user ID to look up"
    )
    @handle_interaction_errors
    async def app_history(
        self,
        interaction: discord.Interaction,
        target: discord.User = None,
        user_id: str = None
    ):
        # 1) Validate exactly one of target or user_id
        if (target is None) == (user_id is None):
            return await interaction.response.send_message(
                "❌ Please specify **exactly one** of `target` (mention) or `user_id`.",
                ephemeral=True
            )

        # 2) Resolve lookup_id and user object
        if target:
            lookup_id = target.id
            lookup_user = target
        else:
            try:
                lookup_id = int(user_id)
            except ValueError:
                return await interaction.response.send_message(
                    "❌ `user_id` must be a valid integer.",
                    ephemeral=True
                )
            lookup_user = interaction.client.get_user(lookup_id) or await interaction.client.fetch_user(lookup_id)

        # 3) Permission check
        guild = interaction.client.get_guild(GUILD_ID)
        recruiter_role = self.resources.recruiter_role if guild else None
        if not recruiter_role or recruiter_role not in interaction.user.roles:
            return await interaction.response.send_message(
                "❌ You do not have permission to use this command.",
                ephemeral=True
            )

        # 4) Fetch history
        history = await get_application_history(str(lookup_id))
        if not history:
            return await interaction.response.send_message(
                f"No application history found for {lookup_user.mention}.",
                ephemeral=True
            )

        # 5) Build lines
        type_icons   = {"submission": "📥", "attempt": "🔍"}
        status_icons = {"accepted": "✅", "denied": "❌", "withdrawn": "🔁", "open": "📁"}
        lines = []
        # assume submissions now include entry["thread_id"], attempts have entry["details"] with a [Log Entry](url)
        for entry in sorted(history, key=lambda e: e["timestamp"]):
            # timestamp
            ts_tag = d_timestamp(entry["timestamp"], "f")
            t_icon = type_icons.get(entry["type"], "")
            s_icon = status_icons.get(entry["status"].lower(), "")
            if entry["type"] == "submission":
                thread_id = entry["thread_id"]
                thread_url = f"https://discord.com/channels/{GUILD_ID}/{thread_id}"
                detail = entry["details"]  # "IGN: X, Region: Y"
                line = f"{t_icon} {ts_tag} • Application • {s_icon} • [Thread]({thread_url}) • `{detail}`"
            else:
                # entry["details"] already contains "Region: X[, [Log Entry](url)]"
                line = f"{t_icon} {ts_tag} • Attempt • {s_icon} • `{entry['details']}`"
            lines.append(line)

        # 6) Truncate if over Discord's 4096 limit
        desc = ""
        for ln in lines:
            candidate = desc + ln + "\n"
            if len(candidate) > 4090:
                desc += "…\n"
                break
            desc = candidate

        # 7) Send embed
        embed = discord.Embed(
            title=f"📜 Application History for {lookup_user.display_name}",
            description=desc or "No entries.",
            color=discord.Color.green()
        )
        embed.set_footer(text="Application: 📥, Attempt: 🔍; Accepted: ✅, Denied: ❌, Withdrawn: 🔁, Open: 📁")
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @app_commands.command(name="app_silence", description="Toggle silence for notifications in this application thread.")
    @handle_interaction_errors
    async def app_silence(self, interaction: discord.Interaction):
        if not is_in_correct_guild(interaction):
            await interaction.response.send_message("❌ This command can only be used in the specified guild.", ephemeral=True)
            return
        if not isinstance(interaction.channel, discord.Thread):
            await interaction.response.send_message("❌ This command must be used in an application thread.", ephemeral=True)
            return
    
        # Check if the user has the Recruiter role
        recruiter_role = self.bot.resources.recruiter_role
        if not recruiter_role or recruiter_role not in interaction.user.roles:
            await interaction.response.send_message("❌ You do not have permission to toggle silence.", ephemeral=True)
            return

        thread_id = str(interaction.channel.id)
        # Check current silence status
        current_silence = await is_application_silenced( thread_id)
        # Toggle the silence status
        new_state = not current_silence
        result = await set_application_silence( thread_id, new_state)
        if result:
            if new_state:
                embed = discord.Embed(title="🔇 Notifications have been silenced for this application thread.", colour=0xc0c0c0)
                await interaction.response.send_message(embed=embed, ephemeral=False)
            else:
                embed = discord.Embed(title="🔊 Notifications have been resumed for this application thread.", colour=0xc0c0c0)
                await interaction.response.send_message(embed=embed, ephemeral=False)
        else:
            await interaction.response.send_message("❌ Failed to update the silence status.", ephemeral=True)

async def setup(bot: commands.Bot):
    await bot.add_cog(RecruitmentCog(bot))
