# cogs/player_list.py

import discord
from discord.ext import tasks, commands
import requests, json, asyncio, aiohttp, re, pytz
from datetime import datetime, timedelta
from config_testing import (
    USE_LOCAL_JSON, LOCAL_JSON_FILE, CHECK_INTERVAL, CACHE_UPDATE_INTERVAL,
    API_URLS, API_URLS_FIVEM, STATUS_CHANNEL_ID, GUILD_ID, MENTOR_ROLE_ID,
    CADET_ROLE, TRAINEE_ROLE, SWAT_ROLE_ID, RANK_HIERARCHY, ROLE_TO_RANK, EMBEDS_FILE
)
from cogs.helpers import log, set_stored_embed, get_stored_embed

class PlayerListCog(commands.Cog):
    """Cog for updating an online player list embed based on external APIs."""
    
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        # Cache for Discord members
        self.discord_cache = {"timestamp": None, "members": {}}
        # Removed file-based embed storage; unified embed storage now via helpers.
        # New: Dictionary to track server unreachable state for each region.
        self._server_unreachable = {}
        self.update_game_status.start()

    def cog_unload(self):
        self.update_game_status.cancel()

    async def fetch_players(self, region):
        if USE_LOCAL_JSON:
            try:
                with open(LOCAL_JSON_FILE, "r", encoding="utf-8") as file:
                    return json.load(file)
            except Exception as e:
                log(f"Error reading local JSON for region {region}: {e}", level="error")
                return []
        url = API_URLS.get(region)
        if not url:
            log(f"No API URL defined for region {region}.", level="error")
            return []
        try:
            await asyncio.sleep(1)
            timeout = aiohttp.ClientTimeout(total=5)
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.get(url) as resp:
                    resp.raise_for_status()
                    text = await resp.text(encoding='utf-8')
                    data = json.loads(text)
                    return data
        except asyncio.TimeoutError:
            log(f"Timeout fetching API data for region {region}", level="error")
            return None
        except aiohttp.ClientError as e:
            log(f"Client error fetching API data for region {region}: {e}", level="error")
            return None

    async def getqueuedata(self):
        try:
            r = requests.get("https://api.gtacnr.net/cnr/servers", timeout=3)
            r.encoding = 'utf-8'
            r.raise_for_status()
            data = json.loads(r.text)
            queue_info = {entry["Id"]: entry for entry in data}
            if "US1" in queue_info:
                queue_info["NA1"] = queue_info.pop("US1")
            if "US2" in queue_info:
                queue_info["NA2"] = queue_info.pop("US2")
            if "US3" in queue_info:
                queue_info["NA3"] = queue_info.pop("US3")
            return queue_info
        except requests.Timeout:
            log("Timeout fetching queue data.", level="error")
            return None
        except requests.RequestException as e:
            log(f"Error fetching queue data: {e}", level="error")
            return None

    async def get_fivem_data(self):
        fivem_data = {}
        async with aiohttp.ClientSession() as session:
            for region, url in API_URLS_FIVEM.items():
                try:
                    async with session.get(url, ssl=False, timeout=aiohttp.ClientTimeout(total=5)) as response:
                        response.raise_for_status()
                        text = await response.text(encoding='utf-8')
                        fivem_data[region] = json.loads(text)
                        await asyncio.sleep(1.5)
                except Exception as e:
                    log(f"Warning fetching FiveM data for {region}: {e}", level="warning")
                    fivem_data[region] = None
        return fivem_data

    async def update_discord_cache(self):
        now = datetime.now()
        if self.discord_cache["timestamp"] and now - self.discord_cache["timestamp"] < timedelta(seconds=CACHE_UPDATE_INTERVAL):
            # Cache is current; no need to update.
            return
        guild = self.bot.get_guild(GUILD_ID)
        if not guild:
            log(f"Bot not in guild with ID {GUILD_ID}.", level="error")
            return
        dc_members = {m.display_name: {"id": m.id, "roles": [r.id for r in m.roles]} for m in guild.members}
        self.discord_cache.update({"timestamp": now, "members": dc_members})
        # Cache updated successfully.

    def time_convert(self, time_string):
        m = re.match(r'^(.+) (\d{2}):(\d{2})$', time_string)
        if not m: 
            return "*Restarting now*"
        d, hh, mm = m.groups()
        hh, mm = int(hh), int(mm)
        days = ['Saturday','Friday','Thursday','Wednesday','Tuesday','Monday','Sunday']
        total_hours = (days.index(d)*24*60 + (24-hh-1)*60 + (60-mm))//60
        if not total_hours:
            return "*Restarting now*"
        h, r = divmod(total_hours, 60)
        hs = f"{h} hour{'s'*(h!=1)}" if h else ""
        rs = f"{r} minute{'s'*(r!=1)}" if r else ""
        return f"*Next restart in ~{hs+' and '+rs if hs and rs else hs or rs}*"

    def get_rank_from_roles(self, roles):
        for r_id, rank in ROLE_TO_RANK.items():
            if r_id in roles:
                return rank
        return None

    async def create_embed(self, region, matching_players, queue_data, fivem_data):
        offline = False
        embed_color = 0x28ef05  # default green
        if matching_players is None or (fivem_data and fivem_data.get(region) is None):
            offline = True
            embed_color = 0xf40006  # red
        if queue_data and region in queue_data and not offline:
            try:
                last_heartbeat = datetime.fromisoformat(
                    queue_data[region]["LastHeartbeatDateTime"].replace("Z", "+00:00")
                )
                if datetime.now(pytz.UTC) - last_heartbeat > timedelta(minutes=10):
                    offline = True
                    embed_color = 0xf40006
            except Exception as e:
                log(f"Error parsing last heartbeat for region {region}: {e}", level="error")
        else:
            offline = True
            embed_color = 0xf40006
        flags = {"EU": "🇪🇺 ", "NA": "🇺🇸 ", "SEA": "🇸🇬 "}
        region_name = region[:-1] if region[-1].isdigit() else region
        title = f"{flags.get(region_name, '')}{region}"
        def safe_emoji(eid, default="⚫"):
            e = self.bot.get_emoji(eid)
            return str(e if e else default)
        embed = discord.Embed(title=title, colour=embed_color)
        if offline:
            embed.add_field(name="Server or API down?", value="No Data for this server!", inline=False)
            embed.add_field(name="🎮Players:", value="```no data```", inline=True)
            embed.add_field(name="⌛Queue:", value="```no data```", inline=True)
            embed.set_footer(text="Refreshes every 30 seconds")
            embed.timestamp = datetime.now()
            return embed
        if matching_players is not None and not offline:
            swat_count = sum(p["type"] in ("unknown", "SWAT", "mentor") for p in matching_players)
            mentor_count = sum(p["type"] == "mentor" for p in matching_players)
            trainee_count = sum(p["type"] in ("trainee", "cadet") for p in matching_players)
            try:
                restart_timer = self.time_convert(fivem_data[region]["vars"]["Time"])
            except Exception as e:
                log(f"Error fetching restart timer for region {region}: {e}", level="warning")
                restart_timer = "*No restart data available!*"
            if mentor_count:
                val = ""
                for mp in matching_players:
                    if mp["type"] == "mentor":
                        val += f"\n - {mp['username']} (<@{mp['discord_id']}>)" if mp['discord_id'] else f"\n - {mp['username']} (❔)"
                embed.add_field(name=f"{safe_emoji(1305249069463113818)}Mentors Online:", value=val, inline=False)
            if swat_count - mentor_count > 0:
                val = ""
                for mp in matching_players:
                    if mp["type"] in ("SWAT", "unknown"):
                        val += f"\n - {mp['username']} (<@{mp['discord_id']}>)" if mp['discord_id'] else f"\n - {mp['username']} (❔)"
                embed.add_field(name="SWAT Online:", value=val, inline=False)
            if trainee_count:
                val = ""
                for mp in matching_players:
                    if mp["type"] == "trainee":
                        val += f"\n{safe_emoji(1305496951642390579)} {mp['username']} (<@{mp['discord_id']}>)"
                    elif mp["type"] == "cadet":
                        val += f"\n{safe_emoji(1305496985582698607)} {mp['username']} (<@{mp['discord_id']}>)"
                embed.add_field(name="Cadets / Trainees Online:", value=val, inline=False)
            if all(p["type"] not in ("SWAT", "mentor", "trainee", "cadet", "unknown") for p in matching_players):
                embed.add_field(name="\n*Nobody is online*\n", value="", inline=False)
            if queue_data and region in queue_data:
                p = queue_data[region]
                embed.add_field(name=f"{safe_emoji(1196404423874854992)}SWAT:", value=f"``` {swat_count} ```", inline=True)
                embed.add_field(name="🎮Players:", value=f"```{p['Players']}/{p['MaxPlayers']}```", inline=True)
                embed.add_field(name="⌛Queue:", value=f"```{p['QueuedPlayers']}```", inline=True)
                embed.add_field(name="", value=restart_timer, inline=False)
            else:
                embed.add_field(name=f"{safe_emoji(1196404423874854992)}SWAT:", value=f"```{swat_count}```", inline=True)
                embed.add_field(name="🎮Players:", value="```no data```", inline=True)
                embed.add_field(name="⌛Queue:", value="```no data```", inline=True)
        else:
            embed.add_field(name="Server or API down?", value="No Data for this server!", inline=False)
            embed.add_field(name="🎮Players:", value="```no data```", inline=True)
            embed.add_field(name="⌛Queue:", value="```no data```", inline=True)
        embed.set_footer(text="Refreshes every 30 seconds")
        embed.timestamp = datetime.now()
        return embed

    async def update_or_create_embed_for_region(self, channel, region, embed_pre):
        # Initialize the flag for this region if not already done.
        if region not in self._server_unreachable:
            self._server_unreachable[region] = False

        stored = get_stored_embed(region)
        if stored:
            MAX_RETRIES = 3
            for attempt in range(1, MAX_RETRIES + 1):
                try:
                    msg = await channel.fetch_message(stored["message_id"])
                    await msg.edit(embed=embed_pre)
                    await asyncio.sleep(2)
                    if self._server_unreachable.get(region, False):
                        log(f"Server is reachable again for region {region}.", level="info")
                        self._server_unreachable[region] = False
                    break
                except discord.HTTPException as e:
                    if e.status == 503:
                        if not self._server_unreachable.get(region, False):
                            log(f"Discord 503 on attempt {attempt} for region {region} while editing embed: {e}", level="error")
                            self._server_unreachable[region] = True
                        if attempt == MAX_RETRIES:
                            log(f"Max retries reached for region {region} (edit failed)", level="error")
                    else:
                        log(f"HTTPException while editing embed for region {region}: {e}", level="error")
                        break
                except Exception as ex:
                    log(f"Unexpected error editing embed for region {region}: {ex}", level="error")
                    break
        else:
            MAX_RETRIES = 3
            for attempt in range(1, MAX_RETRIES + 1):
                try:
                    msg_send = await channel.send(embed=embed_pre)
                    set_stored_embed(region, str(msg_send.id), str(msg_send.channel.id))
                    await asyncio.sleep(1)
                    if self._server_unreachable.get(region, False):
                        log(f"Server is reachable again for region {region}.", level="info")
                        self._server_unreachable[region] = False
                    break
                except discord.HTTPException as e:
                    if e.status == 503:
                        if not self._server_unreachable.get(region, False):
                            log(f"Discord 503 on attempt {attempt} for region {region} while sending embed: {e}", level="error")
                            self._server_unreachable[region] = True
                        if attempt == MAX_RETRIES:
                            log(f"Max retries reached for region {region} (send failed)", level="error")
                        else:
                            await asyncio.sleep(5)
                    else:
                        log(f"HTTPException while sending embed for region {region}: {e}", level="error")
                        break
                except Exception as ex:
                    log(f"Unexpected error sending embed for region {region}: {ex}", level="error")
                    break

    @tasks.loop(seconds=CHECK_INTERVAL)
    async def update_game_status(self):
        await self.bot.wait_until_ready()
        await self.update_discord_cache()
        queue_data = await self.getqueuedata()
        fivem_data = await self.get_fivem_data()
        regions = list(API_URLS.keys())
        results = []
        for region in regions:
            players = await self.fetch_players(region)
            results.append(players)
            await asyncio.sleep(1)
        region_players_map = dict(zip(regions, results))
        channel = self.bot.get_channel(STATUS_CHANNEL_ID)
        if not channel:
            log(f"Status channel {STATUS_CHANNEL_ID} not found.", level="error")
            return
        for region in regions:
            players = region_players_map[region]
            matching_players = [] if players else None
            if isinstance(players, list):
                matching_players = []
                for pl in players:
                    username = pl["Username"]["Username"]
                    if any(mp["username"] == username for mp in matching_players):
                        continue
                    if username.startswith("[SWAT] "):
                        cleaned_name = re.sub(r'^\[SWAT\]\s*', '', username, flags=re.IGNORECASE)
                        discord_found = False
                        for discord_name, details in self.discord_cache["members"].items():
                            compare_dn = re.sub(r'\s*\[SWAT\]$', '', discord_name, flags=re.IGNORECASE)
                            if cleaned_name.lower() == compare_dn.lower():
                                discord_found = True
                                mtype = "mentor" if MENTOR_ROLE_ID in details["roles"] else "SWAT"
                                matching_players.append({
                                    "username": username,
                                    "type": mtype,
                                    "discord_id": details["id"],
                                    "rank": self.get_rank_from_roles(details["roles"])
                                })
                                break
                        if not discord_found:
                            matching_players.append({
                                "username": username,
                                "type": "SWAT",
                                "discord_id": None,
                                "rank": None
                            })
                    else:
                        for discord_name, details in self.discord_cache["members"].items():
                            tmp_dn = re.sub(r'\s*\[(CADET|TRAINEE|SWAT)\]$', '', discord_name, flags=re.IGNORECASE)
                            if username.lower() == tmp_dn.lower():
                                if CADET_ROLE in details["roles"]:
                                    matching_players.append({
                                        "username": username,
                                        "type": "cadet",
                                        "discord_id": details["id"],
                                        "rank": self.get_rank_from_roles(details["roles"])
                                    })
                                elif TRAINEE_ROLE in details["roles"]:
                                    matching_players.append({
                                        "username": username,
                                        "type": "trainee",
                                        "discord_id": details["id"],
                                        "rank": self.get_rank_from_roles(details["roles"])
                                    })
                                elif SWAT_ROLE_ID in details["roles"]:
                                    matching_players.append({
                                        "username": username,
                                        "type": "SWAT",
                                        "discord_id": details["id"],
                                        "rank": self.get_rank_from_roles(details["roles"])
                                    })
                                break
            if matching_players is not None:
                matching_players.sort(key=lambda x: RANK_HIERARCHY.index(x["rank"]) if x["rank"] in RANK_HIERARCHY else len(RANK_HIERARCHY))
            embed_pre = await self.create_embed(region, matching_players, queue_data, fivem_data)
            await asyncio.sleep(1)
            await self.update_or_create_embed_for_region(channel, region, embed_pre)
        # Removed file-based storage write; unified embed storage handles persistence.

async def setup(bot: commands.Bot):
    await bot.add_cog(PlayerListCog(bot))
