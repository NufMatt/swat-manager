# cogs/status.py
import discord
from discord import app_commands
from discord.ext import commands
from time import perf_counter
import aiohttp

from config import GUILD_ID, TICKET_CHANNEL_ID

class StatusCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(name="status", description="Show bot health & latency metrics")
    async def status(self, interaction: discord.Interaction):
        # give the user immediate feedback and extra time
        await interaction.response.defer(thinking=True, ephemeral=True)
        overall_start = perf_counter()

        # 1) Heartbeat latency
        hb_ms = round(self.bot.latency * 1000)
        if hb_ms < 100:
            hb_emoji = "💚"
        elif hb_ms < 300:
            hb_emoji = "💛"
        else:
            hb_emoji = "💔"

        # 2) Sample Discord API call
        api_start = perf_counter()
        api_ok = True
        try:
            await self.bot.fetch_channel(TICKET_CHANNEL_ID)
        except Exception:
            api_ok = False
        api_ms = round((perf_counter() - api_start) * 1000)
        if not api_ok:
            api_emoji = "📡❌"
        elif api_ms < 200:
            api_emoji = "📡✅"
        elif api_ms < 500:
            api_emoji = "📡⚠️"
        else:
            api_emoji = "📡❌"

        # 3) Check Discord’s public status endpoint
        status_start = perf_counter()
        try:
            async with aiohttp.ClientSession() as sess:
                async with sess.get("https://status.discord.com/api/v2/status.json") as resp:
                    j = await resp.json()
                    ds = j["status"]["description"]
        except Exception:
            ds = "Fetch Failed"
        status_ms = round((perf_counter() - status_start) * 1000)

        # 4) Total command time
        total_ms = round((perf_counter() - overall_start) * 1000)

        # 5) Choose embed color by worst metric
        if hb_ms >= 300 or not api_ok:
            color = discord.Color.red()
        elif hb_ms >= 100 or api_ms >= 200:
            color = discord.Color.orange()
        else:
            color = discord.Color.green()

        # 6) Build a clean, code-formatted embed
        embed = discord.Embed(title="🤖 Bot Status", color=color)
        embed.add_field(
            name="💓 Heartbeat",
            value=f"```{hb_ms} ms``` {hb_emoji}",
            inline=True
        )
        embed.add_field(
            name="📡 API Call",
            value=f"```{api_ms} ms``` {api_emoji}",
            inline=True
        )
        embed.add_field(
            name="📶 Discord Status",
            value=f"```{ds}``` ({status_ms} ms)",
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
