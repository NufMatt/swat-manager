import discord
from discord import app_commands, ButtonStyle, Interaction
from discord.ext import commands, tasks
import asyncio
from cogs.helpers import *
import os, threading
from sqlite_web.sqlite_web import initialize_app, app
import asyncio
import platform
from cogs.db_utils import *
from cogs.guild_resources import GuildResources

from config_testing import TOKEN_FILE

if platform.system() != "Windows":
    try:
        import uvloop
        asyncio.set_event_loop_policy(uvloop.EventLoopPolicy())
    except ImportError:
        pass  # uvloop isn’t installed or not available

with open(TOKEN_FILE, "r", encoding="utf-8") as file:
    TOKEN = file.read().strip()

def start_sqlite_web():
    # Initialisiere das sqlite-web‑Interface
    #   args: (datenbank-file, read_only=False, password=None, url_prefix=None)
    initialize_app('data.db', False, None, None)  
    # Starte den eingebauten Flask‑Server
    app.run(host='0.0.0.0', port=8080, debug=False)

# In Deinem main() noch vor bot.run() starten
threading.Thread(target=start_sqlite_web, daemon=True).start()

intents = discord.Intents.default()
intents.message_content = True
intents.members = True
intents.guilds = True

bot = commands.AutoShardedBot(command_prefix="!", intents=intents)

@bot.tree.command(name="reload_cog", description="Reload a specified cog. (Owner only)")
async def reload_cog_command(interaction: discord.Interaction, cog_name: str):
    """Reload a given cog file without restarting the bot."""
    # 1) Optional: restrict usage to the correct guild
    if not is_in_correct_guild(interaction):
        await interaction.response.send_message(
            "❌ This command can only be used in the specified guild.",
            ephemeral=True
        )
        return

    # 2) Check if the user is the bot owner
    #    (bot.is_owner() is async; we do `interaction.client` which is your bot.)
    if not await interaction.client.is_owner(interaction.user):
        await interaction.response.send_message(
            "❌ You do not have permission to reload cogs.",
            ephemeral=True
        )
        return

    # 3) Attempt to reload the cog
    try:
        # Attempt to reload the cog.
        await interaction.client.reload_extension(cog_name)
        # After reloading, resync the command tree.
        synced = await interaction.client.tree.sync()
        await interaction.response.send_message(
            f"✅ Successfully reloaded `{cog_name}` and resynced commands ({len(synced)} commands synced).",
            ephemeral=True
        )
    except Exception as e:
        await interaction.response.send_message(
            f"❌ Failed to reload `{cog_name}`:\n```\n{e}\n```",
            ephemeral=True
        )

@bot.tree.command(name="shardinfo", description="Display the current shard ID.")
async def shardinfo(interaction: discord.Interaction):
    await interaction.response.send_message(f"This interaction is on shard {interaction.client.shard_id}.", ephemeral=True)

@bot.event
async def on_ready():
        # -------------------------------
    # Initialize databases
    # -------------------------------
    await initialize_database()
    await init_role_requests_db()
    await init_application_requests_db()
    await init_applications_db()
    await init_application_attempts_db()
    await init_region_status()
    await init_timeouts_db()
    await init_stored_embeds_db()

    print(f"✅ Logged in as {bot.user} (ID: {bot.user.id})")
    try:
        synced = await bot.tree.sync()
        print(f"✅ Synced {len(synced)} slash commands.")
    except Exception as e:
        print(f"❌ Failed to sync commands: {e}")

async def main():
    async with bot:
        bot.resources = GuildResources(bot)
        # Load the cogs/extensions:
        # await bot.load_extension("cogs.recruitment")
        #await bot.load_extension("cogs.tickets")
        #await bot.load_extension("cogs.status")
        await bot.load_extension("cogs.playerlist-new")
        #await bot.load_extension("cogs.verification")
        # await bot.load_extension("cogs.example_cog")
        await bot.start(TOKEN)

if __name__ == "__main__":
    asyncio.run(main())
