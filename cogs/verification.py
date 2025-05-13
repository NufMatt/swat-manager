# cogs/verification.py

import discord
from discord.ext import commands
import asyncio, aiohttp, time
from datetime import datetime
from config_testing import *
from cogs.helpers import *

# -----------------------------------------------------------------------------
# 1) A small helper for doing the external CnR lookup
# -----------------------------------------------------------------------------

async def fetch_cnr_member(session: aiohttp.ClientSession, user_id: int, token: str):
    url = f"https://discord.com/api/v9/guilds/{CNR_ID}/members/{user_id}"
    headers = {
        "Authorization": token,
        "Content-Type": "application/json",
    }
    async with session.get(url, headers=headers) as resp:
        data = None
        try:
            data = await resp.json()
        except:
            pass
        return resp.status, data or {}


# -----------------------------------------------------------------------------
# 2) The “Verify” button view
# -----------------------------------------------------------------------------

class VerifyView(discord.ui.View):
    def __init__(self, cog: "VerificationCog"):
        super().__init__(timeout=None)
        self.cog = cog
        # user_id -> last timestamp
        self.cooldowns: dict[int, float] = {}

    @discord.ui.button(label="🔄 Verify", style=discord.ButtonStyle.primary, custom_id="manual_verify")
    async def verify(self, interaction: discord.Interaction, button: discord.ui.Button):
        user = interaction.user
        # 1) guild + roles from cache
        guild = interaction.guild
        verified = self.cog.verified_role
        guest     = self.cog.guest_role
        activity_ch = self.cog.activity_ch
        
        if verified in user.roles:
            return await interaction.response.send_message(
                "✅ You’re already verified!", ephemeral=True
            )
        
        now = time.time()
        last = self.cooldowns.get(user.id, 0)
        if now - last < 300:
            return await interaction.response.send_message(
                "⚠️ Please wait before retrying verification.", ephemeral=True
            )
        self.cooldowns[user.id] = now


        # 3) do the same external check
        await interaction.response.defer(ephemeral=True)
        async with aiohttp.ClientSession() as session:
            status, member_data = await fetch_cnr_member(session, user.id, self.cog.account_token)

        if status == 200 and str(CHECK_CNR_VERIFIED_ROLE) in member_data.get("roles", []):
            # success: give role, remove guest
            try:
                await user.add_roles(verified)
                if guest in user.roles:
                    await user.remove_roles(guest)
            except discord.Forbidden:
                log(f"⛔ Cannot assign roles to {user.id}", level="error")

            await interaction.followup.send("✅ Verification successful!", ephemeral=True)

            # log
            if activity_ch:
                e = create_user_activity_log_embed(
                    "verification", "Manual verify succeeded", user,
                    "User clicked Verify and passed."
                )
                await activity_ch.send(embed=e)

        else:
            await interaction.followup.send(
                "❌ Verification failed. Please make sure you have joined the CnR Discord and have the proper role there.",
                ephemeral=True
            )
            if activity_ch:
                e = create_user_activity_log_embed(
                    "verification",
                    "Manual verify failed",
                    user,
                    "User clicked Verify and failed."
                )
                await activity_ch.send(embed=e)

# -----------------------------------------------------------------------------
# 3) The VerificationCog itself
# -----------------------------------------------------------------------------

class VerificationCog(commands.Cog):
    """Cog for automatically verifying new members via an external API,
       and providing a manual-retry button in a dedicated channel."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        # grab your resources object
        self.resources = bot.resources
        # config
        self.account_token = open("account_token.txt", "r").read().strip()
        # placeholders to fill in on_ready
        # will hold our “Click to Verify” message ID
        self.verify_msg_id: int | None = None
        log("VerificationCog loaded.")

    def create_embed(self, title: str, description: str, colour: int) -> discord.Embed:
        embed = discord.Embed(title=title, description=description, colour=colour)
        embed.set_author(name="S.W.A.T. Verification Bot")
        return embed
    
    @commands.Cog.listener()
    async def on_ready(self):
        # 1) wait for guild_resources to populate & cache roles/channels
        await self._wait_for_resources()
        res = await self.resources.ready()
        self.verified_role = res.verified_role
        self.guest_role    = res.guest_role
        self.activity_ch   = res.activity_ch
        self.verify_ch     = res.verify_ch
        self.guild = self.bot.get_guild(GUILD_ID) or await self.bot.fetch_guild(GUILD_ID)
        
        # 2) make sure our embed‐storage table exists
        await self._ensure_embed_db()

        # 3) restore—or create—the manual-verify embed (and register its view)
        await self._ensure_manual_verify_embed()

        log("VerificationCog is fully initialized.")

    async def _wait_for_resources(self):
        # wait until roles/channels from guild_resources are populated
        resources = await self.bot.resources.ready()
        self.verify_ch = resources.verify_ch
        if self.verify_ch is None:
            log(f"❌ verify_ch not found (ID: {resources.verify_ch}).", level="error")
            raise RuntimeError("verify channel missing")

    async def _ensure_embed_db(self):
        # make sure our table exists
        await init_stored_embeds_db()

    async def _ensure_manual_verify_embed(self):
        stored = await get_stored_embed("verification_embed")

        if stored:
            try:
                ch  = self.bot.get_channel(int(stored["channel_id"]))
                msg = await ch.fetch_message(int(stored["message_id"]))
                # re-attach your custom VerifyView
                self.bot.add_view(VerifyView(self), message_id=msg.id)
                self.verify_msg_id = msg.id
                log(f"Restored verify embed {msg.id}")
                return
            except discord.NotFound:
                log("Stored verify embed not found, recreating.", level="warning")

        # no valid stored message → send new
        view = VerifyView(self)
        embed = self.create_embed(
            "🔒 Verification Required",
            "If you’re seeing this channel, it means your verification has **not been completed.**\n\n"
            "✅ To fix this:\n"
            "1. **Verify yourself in the CNR Discord here:**  <#937110452738084934>\n"
            "2. Once verified, **click the button below to request verification!**",
            0x3ec62f
        )
        embed.set_footer(text="S.W.A.T Verification Manager")
        msg = await self.verify_ch.send(embed=embed, view=view)
        self.bot.add_view(view, message_id=msg.id)
        await set_stored_embed("verification_embed", msg.id, self.verify_ch.id)
        self.verify_msg_id = msg.id
        log(f"Posted new verify embed {msg.id}")

    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member):
        await asyncio.sleep(2)  # let Discord finish onboarding
        res = await self.resources.ready()

        # try to get them from the cache
        m = self.guild.get_member(member.id)
        if not m:
            log(f"Member {member.id} left before we could verify.", level="warning")
            return

        # perform the same external check
        async with aiohttp.ClientSession() as session:
            status, data = await fetch_cnr_member(session, member.id, self.account_token)

        # reference your cached roles & channel
        ver = self.verified_role
        gue = self.guest_role
        act = self.activity_ch

        # handle success
        if status == 200 and str(CHECK_CNR_VERIFIED_ROLE) in data.get("roles", []):
            try:
                await m.add_roles(ver)
            except:
                log(f"Cannot assign verified to {member.id}", level="error")
            # DM
            embed = self.create_embed(
                "✅ Automatic Verification Successful",
                f"Hey {data.get('nick', member.name)}, you have been successfully verified via our CnR database!",
                0x1cd946
            )
            await self._safe_dm(m, embed)
            # log
            if act:
                e = create_user_activity_log_embed(
                    "verification", "Successful verification", member,
                    "Automatically verified on join."
                )
                await act.send(embed=e)

        # all failures funnel through here
        else:
            # DM fail
            embed = self.create_embed(
                "❌ Automatic Verification Failed",
                (
                    f"Hey {member.name}, we could **not** verify you in our CnR database. "
                    "Please ensure you are verified on the CnR Discord, and then click "
                    "the verify button here: <#1370260376276697140>"
                ),
                0xf40000
            )
            await self._safe_dm(m, embed)
            # give guest
            try:
                await m.add_roles(gue)
            except:
                log(f"Cannot assign guest to {member.id}", level="error")
           
            if act:
                # choose a human-readable reason
                reason = (
                    "User not found in the CnR Discord"
                    if status == 404 else
                    "Missing CnR-verified role"
                    if status == 200 else
                    f"API error (status {status})"
                )
                e = create_user_activity_log_embed(
                    "verification",
                    "Failed verification",
                    member,
                    reason
                )
                await act.send(embed=e)

    async def _safe_dm(self, member: discord.Member, embed: discord.Embed):
        try:
            await member.send(embed=embed)
        except discord.Forbidden:
            log(f"Cannot DM member {member.id}", level="error")

async def setup(bot: commands.Bot):
    await bot.add_cog(VerificationCog(bot))
