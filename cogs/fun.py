import discord
from discord import app_commands
from discord.ext import commands, tasks
import random
from datetime import datetime, timedelta

class ExampleCog(commands.Cog):
    """An example cog showing how to use slash commands with fun embeds."""
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self._last_used: dict[int, datetime] = {}
        self.achievements = [
            "breached the break-room fortress under heavy snack fire",
            "neutralized the rogue paperclip insurgency",
            "executed a flawless SWAT-style coffee extraction",
            "disarmed the ticking time-bomb of expired yogurt",
            "led a hostage negotiation with the office plant",
            "sniped the last donut from enemy hands",
            "stormed the suggestion-box bunker unarmed",
            "took forward-operating position at the printer jam",
            "ran recon on the forbidden snack stash",
            "mounted a silent assault on the boss’s inbox",
            "deployed tactical sticky-note decoys",
            "exfiltrated intel from the supply-closet vault",
            "captured the crown jewel—CEO’s missing pen",
            "set up an ambush in the hallway of endless memos",
            "brewed a caffeinated flashbang of productivity",
            "led the SWAT squad of staplers on a raid",
            "rescued the USB hostage from the shark-infested cable pit",
            "hijacked the PA for tactical countdowns",
            "pinned down the rumor mill with precision truth rounds",
            "deployed the rubber-duck diversion tactic",
            "executed a carpet bombing of high-fives",
            "defused the glitter-bomb distraction",
            "ran a perfect breach-and-clear on the cookie jar",
            "coordinated a sniper punt of Nerf darts",
            "took point in the great ergonomics sweep",
            "planted a decoy donut box at enemy HQ",
            "neutralized the autocorrect saboteur",
            "secured the perimeter of the snack table",
            "wore night-vision goggles to beat the 3 AM meeting",
            "tackled a stack of TPS reports like a flashbang",
            "shot down phishing emails with surgical precision",
            "commandeered the swivel chair for tactical advantage",
            "stole the high-ground in the printer-room standoff",
            "barricaded the conference room with empty coffee cups",
            "ran silent like a ghost through the email thread",
            "placed motion sensors on the boss’s office door (aka Post-its)",
            "extracted the perfect GIF intel under fire",
            "led a midnight raid on the snack fridge",
            "disarmed a malfunctioning photocopier with calm under pressure",
            "held the line against the paperclip hailstorm",
            "flanked the HR overlord with kindness grenades",
            "shot-called a flawless tactical coffee run",
            "hacked the intercom to broadcast battle chants",
            "wore tactical gloves to handle the spicy salsa crisis",
            "took down the ‘reply all’ ambush squad",
            "stitched up morale wounds with dad-joke bandages",
            "defeated a horde of unread notifications",
            "ran perimeter checks in full SWAT kit (aka hoodie)",
            "mapped the enemy terrain of cluttered desktop icons",
            "cloaked yourself in stealth mode during mandatory fun",
            "wielded a keyboard katana to slice through deadlines",
            "spoofed the intercom with tactical cat-meow diversion",
            "pulled off a chair-rolling diversionary assault",
            "secured the final exit in the labyrinth of cubicles",
            "exfiltrated the secret coffee-blend recipe",
            "deployed a smoke-screen of bubble-wrap distractions",
            "shot down office gossip with fact grenades",
            "led a decapitation strike on the boss’s buzzword list",
            "survived the interrogation room (performance review)",
            "decrypted the ancient scrolls of company policy",
            "set booby-traps with harmless rubber-band landmines",
            "captured the intel-dump of last week’s meeting",
            "ran a covert op in the back-room supply vault",
            "flushed out the rogue memo operatives",
            "mounted a rescue mission for lost paperwork",
            "brewed a coffee strong enough to stop a raid",
        ]

    async def cog_app_command_check(self, interaction: discord.Interaction) -> bool:
        """Shared per-user cooldown: 1 hour between _any_ of these commands."""
        user_id = interaction.user.id
        now = datetime.utcnow()
        last = self._last_used.get(user_id)

        if last and (now - last) < timedelta(hours=1):
            retry_after = 3600 - (now - last).total_seconds()
            cooldown = app_commands.Cooldown(1, 3600)
            # raise the standard cooldown exception
            raise app_commands.CommandOnCooldown(cooldown, retry_after)

        # record this execution
        self._last_used[user_id] = now
        return True

    async def cog_app_command_error(self, interaction: discord.Interaction, error: app_commands.AppCommandError):
        """Catch our cooldown and send a friendly message."""
        if isinstance(error, app_commands.CommandOnCooldown):
            mins, secs = divmod(int(error.retry_after), 60)
            await interaction.response.send_message(
                f"⏳ Slow down! You can use any of these commands again in {mins}m {secs}s.",
                ephemeral=True
            )
        else:
            # re-raise other errors so they bubble up
            raise error

    def create_fun_embed(self, title: str, description: str, color: discord.Color) -> discord.Embed:
        """Utility function to create a fun embed."""
        embed = discord.Embed(title=title, description=description, color=color)
        embed.set_footer(text="Joke — not a real command")
        return embed

    @app_commands.command(
        name="remove_from_leadership",
        description="Remove someone from Leadership (fun!)"
    )
    @app_commands.describe(member="Who to remove")
    async def remove_from_leadership(
        self,
        interaction: discord.Interaction,
        member: discord.Member
    ):
        """“removes” the member from Leadership."""
        title = "🚫 Leadership Removal!"
        desc = f"**{member.display_name}** has been removed from Leadership."
        color = discord.Color.red()
        embed = self.create_fun_embed(title, desc, color)
        await interaction.response.send_message(embed=embed, ephemeral=False)

    @app_commands.command(
        name="remove_from_swat",
        description="Remove someone from SWAT (fun!)"
    )
    @app_commands.describe(member="Who to remove")
    async def remove_from_swat(
        self,
        interaction: discord.Interaction,
        member: discord.Member
    ):
        title = "💥 SWAT Removal!"
        desc = f"**{member.display_name}** has been removed from SWAT."
        color = discord.Color.red()
        embed = self.create_fun_embed(title, desc, color)
        await interaction.response.send_message(embed=embed, ephemeral=False)

    @app_commands.command(
        name="demote",
        description="Demote someone down to Officer (fun!)"
    )
    @app_commands.describe(member="Who to demote")
    async def demote(
        self,
        interaction: discord.Interaction,
        member: discord.Member
    ):
        title = "🔻 Demotion Time!"
        desc = f"**{member.display_name}** has been demoted to Officer."
        color = discord.Color.yellow()
        embed = self.create_fun_embed(title, desc, color)
        await interaction.response.send_message(embed=embed, ephemeral=False)

    @app_commands.command(
        name="back_to_trainee",
        description="Send someone back to Trainee status (fun!)"
    )
    @app_commands.describe(member="Who to send back")
    async def back_to_trainee(
        self,
        interaction: discord.Interaction,
        member: discord.Member
    ):
        title = "🔄 Back to Basics!"
        desc = f"**{member.display_name}** has been sent back to Trainee."
        color = discord.Color.yellow()
        embed = self.create_fun_embed(title, desc, color)
        await interaction.response.send_message(embed=embed, ephemeral=False)

    @app_commands.command(
        name="achievement",
        description="Unlock a random funny achievement!"
    )
    async def achievement(
        self,
        interaction: discord.Interaction
    ):
        """Grants the user a random achievement."""
        ach = random.choice(self.achievements)
        title = "🏆 Achievement Unlocked!"
        desc = f"**{interaction.user.display_name}** {ach}"
        color = discord.Color.orange()
        embed = self.create_fun_embed(title, desc, color)
        await interaction.response.send_message(embed=embed, ephemeral=False)

async def setup(bot: commands.Bot):
    await bot.add_cog(ExampleCog(bot))
