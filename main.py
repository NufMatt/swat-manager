import discord
from discord.ext import commands
import asyncio
from config_testing import TOKEN_FILE
from cogs.helpers import log
with open(TOKEN_FILE, "r", encoding="utf-8") as file:
    TOKEN = file.read().strip()

intents = discord.Intents.default()
intents.message_content = True
intents.members = True
intents.guilds = True

bot = commands.Bot(command_prefix="!", intents=intents)

@bot.event
async def on_ready():
    print(f"✅ Logged in as {bot.user} (ID: {bot.user.id})")
    try:
        synced = await bot.tree.sync()
        print(f"✅ Synced {len(synced)} slash commands.")
    except Exception as e:
        print(f"❌ Failed to sync commands: {e}")

async def main():
    async with bot:
        # Load the cogs/extensions:
        await bot.load_extension("cogs.recruitment")
        # await bot.load_extension("cogs.tickets")
        # await bot.load_extension("cogs.playerlist")
        # await bot.load_extension("cogs.verification")
        # await bot.load_extension("cogs.example_cog")
        await bot.start(TOKEN)

if __name__ == "__main__":
    asyncio.run(main())
