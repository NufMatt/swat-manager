import discord
from discord.ext import commands
import asyncio
import aiohttp
import sys, os
from cogs.helpers import create_user_activity_log_embed
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
from config import GUILD_ID, VERIFIED_ROLE, GUEST_ROLE, CHECK_CNR_VERIFIED_ROLE, CNR_ID, ACTIVITY_CHANNEL_ID

try:
    with open("account_token.txt", "r") as f:
        ACCOUNT_TOKEN = f.read().strip()
except Exception:
    ACCOUNT_TOKEN = ""

from cogs.helpers import log

class VerificationCog(commands.Cog):
    """Cog for automatically verifying new members via an external API."""
    
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        log("VerificationCog loaded.")

    async def send_dm(self, member: discord.Member, embed: discord.Embed, log_msg: str):
        try:
            await member.send(embed=embed)
            log(log_msg)
        except discord.Forbidden:
            log(f"Forbidden: Could not send DM to member {member.id}.", level="error")

    def create_embed(self, title: str, description: str, colour: int) -> discord.Embed:
        embed = discord.Embed(title=title, description=description, colour=colour)
        embed.set_author(name="S.W.A.T. Verification Bot")
        return embed

    @commands.Cog.listener()
    async def on_ready(self):
        user_id_info = f"(ID: {self.bot.user.id})" if self.bot.user else "(unknown bot ID)"
        log(f"Logged in as {self.bot.user} {user_id_info} - VerificationCog is ready.")

    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member):
        if not member:
            log("on_member_join called with None as member! Skipping verification.", level="error")
            return

        log(f"Member {member} (ID: {member.id}) joined. Starting verification check.")
        await asyncio.sleep(2)

        guild = self.bot.get_guild(GUILD_ID)
        if not guild:
            log(f"Guild with ID {GUILD_ID} not found! Aborting on_member_join.", level="error")
            return

        current_member = guild.get_member(member.id)
        if not current_member:
            log(f"Member {member} (ID: {member.id}) is not found in guild. User might not be in Discord anymore.", level="error")
            return

        url = f"https://discord.com/api/v9/guilds/{CNR_ID}/members/{member.id}"
        headers = {
            "Authorization": ACCOUNT_TOKEN,
            "Content-Type": "application/json"
        }

        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(url, headers=headers) as response:
                    status = response.status
                    text = await response.text()
                    log(f"External API request sent for member {member.id}. Response code: {status}")
                    log(f"Response: {text}")

                    if status == 200:
                        member_data = await response.json()
                        user_roles = member_data.get("roles", [])
                        if str(CHECK_CNR_VERIFIED_ROLE) in user_roles:
                            log(f"Member {member.id} externally verified (role {CHECK_CNR_VERIFIED_ROLE} found).")
                            
                            role = guild.get_role(VERIFIED_ROLE)
                            if role:
                                try:
                                    await current_member.add_roles(role)
                                    log(f"Role '{role.name}' assigned to member {member.id}.")
                                except discord.Forbidden:
                                    log(f"Forbidden: Unable to modify roles for member {member.id}.", level="error")
                                except discord.HTTPException as e:
                                    log(f"HTTPException while modifying roles for member {member.id}: {e}", level="error")
                            else:
                                log(f"Role with ID {VERIFIED_ROLE} not found in the guild.", level="error")

                            embed = self.create_embed(
                                "✅ Automatic Verification Successful",
                                f"Hey {member_data.get('nick', member.name)}, you have been successfully verified via our CnR database!",
                                0x1cd946
                            )
                            await self.send_dm(current_member, embed, f"Verification DM sent to member {member.id}.")
                            activity_channel = self.bot.get_channel(ACTIVITY_CHANNEL_ID)
                            if activity_channel:
                                embed = create_user_activity_log_embed("verification", "Successful verification", member, "User has been successfully verified.")
                                await activity_channel.send(embed=embed)
                        else:
                            log(f"Member {member.id} not verified (external role {CHECK_CNR_VERIFIED_ROLE} missing).", level="error")
                            embed = self.create_embed(
                                "❌ Automatic Verification Failed",
                                (
                                    f"Hey {member.name}, we could **not** verify you in our CnR database. "
                                    "Please ensure you are verified on the CnR Discord, and open a ticket "
                                    "with the leadership here: <#1303104817228677150>"
                                ),
                                0xf40000
                            )
                            await self.send_dm(current_member, embed, f"Failure DM (not verified) sent to member {member.id}.")
                            activity_channel = self.bot.get_channel(ACTIVITY_CHANNEL_ID)
                            if activity_channel:
                                embed = create_user_activity_log_embed("verification", "Failed verification", member, "User isn't verified in the CnR Discord")
                                await activity_channel.send(embed=embed)
                            
                            # Add guest role
                            guest_role = guild.get_role(GUEST_ROLE)
                            if guest_role:
                                try:
                                    await current_member.add_roles(guest_role)
                                    log(f"Guest role added to member {member.id}.")
                                except discord.Forbidden:
                                    log(f"Forbidden: Unable to add guest role to member {member.id}.", level="error")
                                except discord.HTTPException as e:
                                    log(f"HTTPException while adding guest role to member {member.id}: {e}", level="error")
                            else:
                                log(f"Guest role with ID {GUEST_ROLE} not found.", level="error")
                    else:
                        if status == 404:
                            reason = "User can't be found in the CnR Discord"
                        else:
                            reason = f"API error (Status code: {status})"
                        
                        log(f"External API request for member {member.id} returned response code {status}.", level="error")
                        embed = self.create_embed(
                            "❌ Automatic Verification Failed",
                            (
                                f"Hey {member.name}, we could **not** verify you in our CnR database. "
                                "Please ensure you are verified on the CnR Discord, and open a ticket "
                                "with the leadership here: <#1303104817228677150>"
                            ),
                            0xf40000
                        )
                        await self.send_dm(current_member, embed, f"Failure DM due to API response sent to member {member.id}.")
                        activity_channel = self.bot.get_channel(ACTIVITY_CHANNEL_ID)
                        if activity_channel:
                            embed = create_user_activity_log_embed("verification", "Failed verification", member, reason)
                            await activity_channel.send(embed=embed)
                        
                        # Add guest role
                        guest_role = guild.get_role(GUEST_ROLE)
                        if guest_role:
                            try:
                                await current_member.add_roles(guest_role)
                                log(f"Guest role added to member {member.id}.")
                            except discord.Forbidden:
                                log(f"Forbidden: Unable to add guest role to member {member.id}.", level="error")
                            except discord.HTTPException as e:
                                log(f"HTTPException while adding guest role to member {member.id}: {e}", level="error")
                        else:
                            log(f"Guest role with ID {GUEST_ROLE} not found.", level="error")
                    
        except aiohttp.ClientError as e:
            log(f"API request error for member {member.id}: {e}", level="error")
            embed = self.create_embed(
                "❌ Automatic Verification Failed",
                "An error occurred while checking your verification. Please try again later.",
                0xf40000
            )
            await self.send_dm(current_member, embed, f"Error DM (ClientError) sent to member {member.id}.")
            activity_channel = self.bot.get_channel(ACTIVITY_CHANNEL_ID)
            if activity_channel:
                reason = f"API request failed: {e}"
                embed = create_user_activity_log_embed("verification", "Failed verification", member, reason)
                await activity_channel.send(embed=embed)
            
            # Add guest role
            guest_role = guild.get_role(GUEST_ROLE)
            if guest_role:
                try:
                    await current_member.add_roles(guest_role)
                    log(f"Guest role added to member {member.id}.")
                except discord.Forbidden:
                    log(f"Forbidden: Unable to add guest role to member {member.id}.", level="error")
                except discord.HTTPException as e:
                    log(f"HTTPException while adding guest role to member {member.id}: {e}", level="error")
            else:
                log(f"Guest role with ID {GUEST_ROLE} not found.", level="error")
        except Exception as e:
            log(f"Unexpected error in on_member_join for member {member.id}: {e}", level="error")
            embed = self.create_embed(
                "❌ Automatic Verification Failed",
                f"An error occurred while checking your verification. Please contact Matt.\n**Error:** ```{e}```",
                0xf40000
            )
            await self.send_dm(current_member, embed, f"Error DM (Exception) sent to member {member.id}.")
            activity_channel = self.bot.get_channel(ACTIVITY_CHANNEL_ID)
            if activity_channel:
                reason = f"Unexpected error: {e}"
                embed = create_user_activity_log_embed("verification", "Failed verification", member, reason)
                await activity_channel.send(embed=embed)
            
            # Add guest role
            guest_role = guild.get_role(GUEST_ROLE)
            if guest_role:
                try:
                    await current_member.add_roles(guest_role)
                    log(f"Guest role added to member {member.id}.")
                except discord.Forbidden:
                    log(f"Forbidden: Unable to add guest role to member {member.id}.", level="error")
                except discord.HTTPException as e:
                    log(f"HTTPException while adding guest role to member {member.id}: {e}", level="error")
            else:
                log(f"Guest role with ID {GUEST_ROLE} not found.", level="error")

async def setup(bot: commands.Bot):
    await bot.add_cog(VerificationCog(bot))