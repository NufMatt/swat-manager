# cogs/player_list.py

import discord
from discord.ext import tasks, commands
import requests, json, asyncio, aiohttp, re, pytz, sqlite3
from datetime import datetime, timedelta
from config_testing import (
    USE_LOCAL_JSON, LOCAL_JSON_FILE, CHECK_INTERVAL, CACHE_UPDATE_INTERVAL,
    API_URLS, API_URLS_FIVEM, STATUS_CHANNEL_ID, GUILD_ID, MENTOR_ROLE_ID,
    CADET_ROLE, TRAINEE_ROLE, SWAT_ROLE_ID, RANK_HIERARCHY, ROLE_TO_RANK, EMBEDS_FILE, LEADERSHIP_ID, LEADERSHIP_EMOJI
)
from cogs.helpers import log, set_stored_embed, get_stored_embed


class PlayerListCog(commands.Cog):
    """Cog for updating an online player list embed based on external APIs,
    while logging playtime and name changes and adding leadership-only commands."""
    
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        # Cache for Discord members
        self.discord_cache = {"timestamp": None, "members": {}}
        # Dictionary to track server unreachable state for each region.
        self._server_unreachable = {}
        # Initialize database connection for playtime and name changes logging.
        self.db_conn = sqlite3.connect("player_logs.db")
        self.db_conn.row_factory = sqlite3.Row
        self.setup_database()
        # For playtime increment calculation.
        self.last_update_time = None
        self.update_game_status.start()

    def setup_database(self):
        """Creates the necessary tables if they do not exist."""
        cur = self.db_conn.cursor()
        cur.execute("""
            CREATE TABLE IF NOT EXISTS players_info (
                uid TEXT PRIMARY KEY,
                current_name TEXT,
                last_login TEXT,
                total_playtime REAL
            )
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS playtime_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                uid TEXT,
                log_time TEXT,
                seconds REAL
            )
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS name_changes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                uid TEXT,
                old_name TEXT,
                new_name TEXT,
                change_time TEXT
            )
        """)
        self.db_conn.commit()

    def cog_unload(self):
        self.update_game_status.cancel()
        # Close database connection when cog is unloaded.
        self.db_conn.close()

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
            return
        guild = self.bot.get_guild(GUILD_ID)
        if not guild:
            log(f"Bot not in guild with ID {GUILD_ID}.", level="error")
            return
        dc_members = {m.display_name: {"id": m.id, "roles": [r.id for r in m.roles]} for m in guild.members}
        self.discord_cache.update({"timestamp": now, "members": dc_members})

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
        embed_color = 0x28ef05
        if matching_players is None or (fivem_data and fivem_data.get(region) is None):
            offline = True
            embed_color = 0xf40006
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
            embed.set_footer(text="Refreshes every 60 seconds")
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
        embed.set_footer(text="Refreshes every 60 seconds")
        embed.timestamp = datetime.now()
        return embed

    def log_player_data(self, uid: str, username: str, observed_time: str, increment: float):
        """
        Logs playtime and name changes for a given player.
        Instead of using the API timestamp, it now uses the observed time.
        The increment is the actual elapsed time (in seconds) since the last update.
        """
        try:
            cur = self.db_conn.cursor()
            cur.execute("SELECT * FROM players_info WHERE uid = ?", (uid,))
            row = cur.fetchone()
            if row is None:
                cur.execute("""
                    INSERT INTO players_info (uid, current_name, last_login, total_playtime)
                    VALUES (?, ?, ?, ?)
                """, (uid, username, observed_time, increment))
            else:
                if row["current_name"].lower() != username.lower():
                    cur.execute("""
                        INSERT INTO name_changes (uid, old_name, new_name, change_time)
                        VALUES (?, ?, ?, ?)
                    """, (uid, row["current_name"], username, observed_time))
                    cur.execute("""
                        UPDATE players_info SET current_name = ?, last_login = ?
                        WHERE uid = ?
                    """, (username, observed_time, uid))
                else:
                    cur.execute("""
                        UPDATE players_info SET last_login = ?
                        WHERE uid = ?
                    """, (observed_time, uid))
                cur.execute("""
                    UPDATE players_info SET total_playtime = total_playtime + ?
                    WHERE uid = ?
                """, (increment, uid))
            cur.execute("""
                INSERT INTO playtime_log (uid, log_time, seconds)
                VALUES (?, ?, ?)
            """, (uid, observed_time, increment))
            self.db_conn.commit()
        except Exception as e:
            log(f"Error logging player data for uid {uid}: {e}", level="error")

    @tasks.loop(seconds=CHECK_INTERVAL)
    async def update_game_status(self):
        await self.bot.wait_until_ready()
        loop_start = datetime.utcnow()

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

        # Calculate the actual elapsed time since the last update.
        current_loop_time = datetime.utcnow()
        if self.last_update_time is not None:
            increment = (current_loop_time - self.last_update_time).total_seconds()
        else:
            increment = CHECK_INTERVAL
        self.last_update_time = current_loop_time
        observed_time = current_loop_time.isoformat()

        for region in regions:
            players = region_players_map[region]
            if players and isinstance(players, list):
                seen_uids = set()
                for pl in players:
                    uid = pl["Uid"]
                    if uid in seen_uids:
                        continue
                    seen_uids.add(uid)
                    username = pl["Username"]["Username"]
                    # Log with the observed time and actual elapsed increment.
                    self.log_player_data(uid, username, observed_time, increment)
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
                                # Check if the member has the leadership role and prepend the icon if so.
                                is_leader = LEADERSHIP_ID in details["roles"]
                                display_name = f"{LEADERSHIP_EMOJI} {username}" if is_leader else username
                                mtype = "mentor" if MENTOR_ROLE_ID in details["roles"] else "SWAT"
                                matching_players.append({
                                    "username": display_name,
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

    async def update_or_create_embed_for_region(self, channel, region, embed_pre):
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

    def format_playtime(self, seconds: float) -> str:
        m, s = divmod(int(seconds), 60)
        h, m = divmod(m, 60)
        parts = []
        if h:
            parts.append(f"{h}h")
        if m:
            parts.append(f"{m}m")
        if s or not parts:
            parts.append(f"{s}s")
        return " ".join(parts)

    @commands.hybrid_command(name="topplaytime", description="Shows top playtime for SWAT members in the given timeframe (days).")
    @commands.has_role(LEADERSHIP_ID)
    async def topplaytime(self, ctx: commands.Context, days: int):
        cutoff = datetime.utcnow() - timedelta(days=days)
        cutoff_iso = cutoff.isoformat()
        try:
            cur = self.db_conn.cursor()
            cur.execute("""
                SELECT p.uid, p.current_name, SUM(l.seconds) as playtime
                FROM playtime_log l
                JOIN players_info p ON l.uid = p.uid
                WHERE datetime(l.log_time) >= datetime(?)
                  AND p.current_name LIKE '[SWAT]%'
                GROUP BY p.uid
                ORDER BY playtime DESC
            """, (cutoff_iso,))
            results = cur.fetchall()
        except Exception as e:
            log(f"Error querying top playtime: {e}", level="error")
            await ctx.send("An error occurred while fetching top playtime data.", ephemeral=True)
            return

        if not results:
            await ctx.send(f"No playtime data for SWAT members in the last {days} day(s).", ephemeral=True)
            return

        description = f"Top SWAT playtime in the last {days} day(s):\n\n"
        for idx, row in enumerate(results, 1):
            playtime_formatted = self.format_playtime(row["playtime"])
            description += f"**{idx}. {row['current_name']}** – {playtime_formatted}\n"
        embed = discord.Embed(title="Top Playtime (SWAT Members)", description=description, color=0x28ef05)
        embed.set_footer(text="Data is a rough estimate and not 100% accurate")
        await ctx.send(embed=embed, ephemeral=True)

    @commands.hybrid_command(name="player", description="Shows playtime, last seen and past names for the specified player.")
    @commands.has_role(LEADERSHIP_ID)
    async def player(self, ctx: commands.Context, *, name: str):
        try:
            cur = self.db_conn.cursor()
            cur.execute("SELECT * FROM players_info WHERE lower(current_name)=lower(?)", (name,))
            player_info = cur.fetchone()
            if player_info is None:
                cur.execute("""
                    SELECT uid FROM name_changes
                    WHERE lower(old_name)=lower(?) OR lower(new_name)=lower(?)
                    LIMIT 1
                """, (name, name))
                row = cur.fetchone()
                if row:
                    cur.execute("SELECT * FROM players_info WHERE uid = ?", (row["uid"],))
                    player_info = cur.fetchone()
            if player_info is None:
                await ctx.send(f"Player `{name}` not found in the logs.", ephemeral=True)
                return

            uid = player_info["uid"]
            current_name = player_info["current_name"]
            total_playtime = player_info["total_playtime"]
            # Look up the last seen time from playtime_log.
            cur.execute("SELECT MAX(log_time) as last_seen FROM playtime_log WHERE uid = ?", (uid,))
            last_seen_row = cur.fetchone()
            if last_seen_row and last_seen_row["last_seen"]:
                try:
                    last_seen_dt = datetime.fromisoformat(last_seen_row["last_seen"])
                    now_dt = datetime.utcnow()
                    diff = now_dt - last_seen_dt
                    if diff < timedelta(hours=24):
                        total_seconds = diff.total_seconds()
                        if total_seconds < 3600:
                            minutes = int(total_seconds // 60)
                            last_seen = f"{minutes} min ago"
                        else:
                            hours = int(total_seconds // 3600)
                            last_seen = f"{hours} hour{'s' if hours != 1 else ''} ago"
                    else:
                        last_seen = last_seen_dt.strftime("%d.%m.%Y - %H:%M")
                except Exception as e:
                    last_seen = last_seen_row["last_seen"]
            else:
                last_seen = "Unknown"

            cur.execute("""
                SELECT old_name, new_name, change_time FROM name_changes
                WHERE uid = ?
                ORDER BY change_time DESC
            """, (uid,))
            name_changes = cur.fetchall()

            description = f"**UID:** {uid}\n"
            description += f"**Current Name:** {current_name}\n"
            description += f"**Last Seen:** {last_seen}\n"
            description += f"**Total Playtime:** {self.format_playtime(total_playtime)}\n\n"
            if name_changes:
                description += "**Past Name Changes:**\n"
                for change in name_changes:
                    description += f"- {change['old_name']} → {change['new_name']} (at {change['change_time']})\n"
            else:
                description += "No past name changes logged."

            embed = discord.Embed(title="Player Information", description=description, color=0x28ef05)
            embed.set_footer(text="Data is a rough estimate and not 100% accurate")
            await ctx.send(embed=embed, ephemeral=True)
        except Exception as e:
            log(f"Error in /player command: {e}", level="error")
            await ctx.send("An error occurred while fetching the player data.", ephemeral=True)

async def setup(bot: commands.Bot):
    await bot.add_cog(PlayerListCog(bot))
