# cogs/status.py
import discord
from discord import app_commands
from discord.ext import commands
from time import perf_counter, time
import aiohttp
import os
from pathlib import Path

from config import TICKET_CHANNEL_ID

class StatusCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        # Caches to avoid hitting APIs on every invocation
        creds_path = Path(__file__).parent / "pushover_creds.txt"
        try:
            with open(creds_path, "r") as f:
                lines = dict(line.strip().split("=", 1) for line in f if "=" in line)
            self.pushover_token = lines["API_TOKEN"]
            self.pushover_user  = lines["USER_KEY"]
        except Exception as e:
            raise RuntimeError(f"Could not load Pushover creds: {e}")
    # —————————————————————————————————————————————
        self._discord_status_cache = None  # dict with keys: description, ms, ts
        self._discord_status_ttl = 60      # seconds
        self._api_call_cache = None        # dict with keys: ok, ms, ts
        self._api_call_ttl = 10            # seconds

    @app_commands.command(name="status", description="Show bot health & latency metrics")
    async def status(self, interaction: discord.Interaction):
        await interaction.response.defer(thinking=True, ephemeral=True)
        overall_start = perf_counter()

        # 1) Heartbeat latency
        hb_ms = round(self.bot.latency * 1000)
        hb_emoji = "💚" if hb_ms < 100 else "💛" if hb_ms < 300 else "💔"

        now = time()

        # 2) Cached or fresh Discord API call
        if (self._api_call_cache is None or
            now - self._api_call_cache["ts"] > self._api_call_ttl):
            api_start = perf_counter()
            api_ok = True
            try:
                await self.bot.fetch_channel(TICKET_CHANNEL_ID)
            except Exception:
                api_ok = False
            api_ms = round((perf_counter() - api_start) * 1000)
            self._api_call_cache = {"ok": api_ok, "ms": api_ms, "ts": now}
        else:
            api_ok = self._api_call_cache["ok"]
            api_ms = self._api_call_cache["ms"]
        api_emoji = "📡❌" if not api_ok or api_ms >= 500 else "📡⚠️" if api_ms >= 200 else "📡✅"

        # 3) Cached or fresh Discord public status
        if (self._discord_status_cache is None or
            now - self._discord_status_cache["ts"] > self._discord_status_ttl):
            status_start = perf_counter()
            try:
                async with aiohttp.ClientSession() as sess:
                    async with sess.get("https://status.discord.com/api/v2/status.json") as resp:
                        j = await resp.json()
                        ds_desc = j["status"]["description"]
            except Exception:
                ds_desc = "Fetch Failed"
            status_ms = round((perf_counter() - status_start) * 1000)
            self._discord_status_cache = {
                "description": ds_desc,
                "ms": status_ms,
                "ts": now
            }
        else:
            ds_desc = self._discord_status_cache["description"]
            status_ms = self._discord_status_cache["ms"]

        # 4) Total command time
        total_ms = round((perf_counter() - overall_start) * 1000)

        # 5) Choose embed color by worst metric
        if hb_ms >= 300 or not api_ok:
            color = discord.Color.red()
        elif hb_ms >= 100 or api_ms >= 200:
            color = discord.Color.orange()
        else:
            color = discord.Color.green()

        # 6) Build embed with formatted code blocks
        embed = discord.Embed(title="🤖 Bot Status", color=color)
        embed.add_field(
            name=f"{hb_emoji} Heartbeat",
            value=f"```{hb_ms} ms```",
            inline=True
        )
        embed.add_field(
            name=f"{api_emoji} API Call",
            value=f"```{api_ms} ms```",
            inline=True
        )
        embed.add_field(
            name=f"📶 Discord Status ({status_ms} ms)",
            value=f"```{ds_desc}```",
            inline=False
        )
        embed.add_field(
            name="⏱ Total Time",
            value=f"```{total_ms} ms```",
            inline=True
        )

        await interaction.followup.send(embed=embed)

    @app_commands.command(
        name="contactmatt",
        description="📱 Alert Matt via Pushover with a reason"
    )
    @app_commands.describe(reason="Why are you contacting Matt?")
    async def contactmatt(
        self,
        interaction: discord.Interaction,
        reason: str
    ):
        CHIEF_ROLE_ID = 958272560905195521  # ← replace with your CHIEF role ID

        # 1) permission check
        if CHIEF_ROLE_ID not in (r.id for r in interaction.user.roles):
            return await interaction.response.send_message(
                "❌ You don’t have permission to use this.", ephemeral=True
            )

        # 2) defer so we can do the HTTP request
        await interaction.response.defer(ephemeral=True)

        # 3) build the Pushover message
        user_display = interaction.user.display_name
        pushover_msg = f"🐱‍💻 /contactmatt by **{user_display}**\n> {reason}"

        # 4) fire off to Pushover
        async with aiohttp.ClientSession() as session:
            payload = {
                "token": self.pushover_token,
                "user":  self.pushover_user,
                "message": pushover_msg
            }
            async with session.post(
                "https://api.pushover.net/1/messages.json",
                data=payload
            ) as resp:
                if resp.status == 200:
                    await interaction.followup.send(
                        "✅ Matt has been notified!", ephemeral=True
                    )
                else:
                    text = await resp.text()
                    await interaction.followup.send(
                        f"❌ Notification failed ({resp.status}):\n{text}",
                        ephemeral=True
                    )


async def setup(bot: commands.Bot):
    await bot.add_cog(StatusCog(bot))
