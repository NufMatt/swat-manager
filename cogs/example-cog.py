import discord
from discord import app_commands
from discord.ext import commands, tasks

class ExampleCog(commands.Cog):
    """An example cog showing how to use slash commands, listeners, and tasks."""
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        # Start a background task (runs every 30 seconds)
        self.example_task.start()

    def cog_unload(self):
        # Cancel the task when the cog is unloaded
        self.example_task.cancel()

    @app_commands.command(name="example", description="An example slash command")
    async def example_command(self, interaction: discord.Interaction):
        """A simple greeting command."""
        await interaction.response.send_message("Hello from the Example Cog!", ephemeral=True)

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        """A listener that replies if someone says 'hello'."""
        if message.author == self.bot.user:
            return
        if "hello" in message.content.lower():
            await message.channel.send(f"Hello, {message.author.mention}!")

    @commands.Cog.listener()
    async def on_ready(self):
        print("ExampleCog is ready!")

    @tasks.loop(seconds=30)
    async def example_task(self):
        print("Example task is running every 30 seconds.")

async def setup(bot: commands.Bot):
    await bot.add_cog(ExampleCog(bot))
