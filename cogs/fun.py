import discord
from discord import app_commands
from discord.ext import commands, tasks

class ExampleCog(commands.Cog):
    """An example cog showing how to use slash commands, listeners, and tasks."""
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        # Start a background task (runs every 30 seconds)

    def cog_unload(self):
        # Cancel the task when the cog is unloaded
        pass

    @app_commands.command(name="remove_from_leadership", description="An example slash command")
    async def example_command(self, interaction: discord.Interaction):
        """A simple greeting command."""
        await interaction.response.send_message("Hello from the Example Cog!", ephemeral=True)

async def setup(bot: commands.Bot):
    await bot.add_cog(ExampleCog(bot))
