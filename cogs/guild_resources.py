# cogs/guild_resources.py

import asyncio
from config_testing import *

class GuildResources:
    """Holds cached Role & Channel objects for quick access."""
    def __init__(self, bot):
        self.bot    = bot
        self._ready = asyncio.Event()

        # placeholders for channels
        self.trainee_notes_ch = None
        self.cadet_notes_ch   = None
        self.trainee_chat_ch  = None
        self.swat_chat_ch     = None
        self.apply_ch         = None
        self.requests_ch      = None
        self.activity_ch      = None
        
        # … add any other channels you need …

        # placeholders for roles
        self.trainee_role     = None
        self.cadet_role       = None
        self.swat_role        = None
        self.officer_role     = None
        self.recruiter_role   = None
        self.leadership_role  = None
        self.eu_role          = None
        self.na_role          = None
        self.sea_role         = None
        self.blacklist_role   = None
        self.timeout_role     = None
        # … add any other roles you need …

        # register _init to run once on ready
        bot.add_listener(self._init, "on_ready")

    async def _init(self):
        # ensure this only runs once
        if self._ready.is_set():
            return

        # wait until bot is fully ready and guild is available
        await self.bot.wait_until_ready()

        guild = self.bot.get_guild(GUILD_ID)
        if guild is None:
            # fallback to fetching if not in cache
            guild = await self.bot.fetch_guild(GUILD_ID)
        if guild is None:
            # give up if still missing
            return

        # --------------------
        # Channels
        # --------------------
        self.trainee_notes_ch = guild.get_channel(TRAINEE_NOTES_CHANNEL)
        self.cadet_notes_ch   = guild.get_channel(CADET_NOTES_CHANNEL)
        self.trainee_chat_ch  = guild.get_channel(TRAINEE_CHAT_CHANNEL)
        self.swat_chat_ch     = guild.get_channel(SWAT_CHAT_CHANNEL)
        self.apply_ch         = guild.get_channel(APPLY_CHANNEL_ID)
        self.requests_ch      = guild.get_channel(REQUESTS_CHANNEL_ID)
        self.activity_ch      = guild.get_channel(ACTIVITY_CHANNEL_ID)
        self.request_ch       = guild.get_channel(TARGET_CHANNEL_ID)
        self.status_ch        = guild.get_channel(STATUS_CHANNEL_ID)
        self.ticket_ch        = guild.get_channel(TICKET_CHANNEL_ID)
        self.trainee_notes_ch = guild.get_channel(TRAINEE_NOTES_CHANNEL)
        self.cadet_notes_ch   = guild.get_channel(CADET_NOTES_CHANNEL)
        

        # --------------------
        # Roles
        # --------------------
        self.trainee_role    = guild.get_role(TRAINEE_ROLE)
        self.cadet_role      = guild.get_role(CADET_ROLE)
        self.swat_role       = guild.get_role(SWAT_ROLE_ID)
        self.officer_role    = guild.get_role(OFFICER_ROLE_ID)
        self.recruiter_role  = guild.get_role(RECRUITER_ID)
        self.leadership_role = guild.get_role(LEADERSHIP_ID)
        self.eu_role         = guild.get_role(EU_ROLE_ID)
        self.na_role         = guild.get_role(NA_ROLE_ID)
        self.sea_role        = guild.get_role(SEA_ROLE_ID)
        self.blacklist_role  = guild.get_role(BLACKLISTED_ROLE_ID)
        self.timeout_role    = guild.get_role(TIMEOUT_ROLE_ID)
        self.mentor_role    = guild.get_role(MENTOR_ROLE_ID)
        self.guest_role     = guild.get_role(GUEST_ROLE)
        self.verified_role  = guild.get_role(VERIFIED_ROLE)
        

        # signal that resources are ready
        self._ready.set()

    async def ready(self):
        """Await until all roles & channels are fetched."""
        await self._ready.wait()
        return self

# In your bot startup file (e.g. main.py):
#
# from cogs.guild_resources import GuildResources
#
# bot = commands.Bot(...)
# bot.resources = GuildResources(bot)
# bot.run(TOKEN)
