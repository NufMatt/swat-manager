# cogs/status.py
import discord
from discord import app_commands
from discord.ext import commands
from time import perf_counter, time
import aiohttp

from config import TICKET_CHANNEL_ID

class StatusCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        # Caches to avoid hitting APIs on every invocation
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

async def setup(bot: commands.Bot):
    await bot.add_cog(StatusCog(bot))
