# cogs/player_list.py

import discord
from discord.ext import tasks, commands
import requests, json, asyncio, aiohttp, re, pytz, aiosqlite
from datetime import datetime, timedelta
import io
from config_testing import *
from cogs.helpers import log, set_stored_embed, get_stored_embed


class PlayerListCog(commands.Cog):
    """Cog for updating an online player list embed based on external APIs,
    while logging playtime and name changes and adding leadership-only commands."""
    
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.discord_cache = {"timestamp": None, "members": {}}
        self._server_unreachable = {}
        self.db_conn = None
        self.last_update_time = None
        self.current_sessions = {}

    async def cog_load(self):
        self.db_conn = await aiosqlite.connect("player_logs.db")
        self.db_conn.row_factory = aiosqlite.Row
        await self.setup_database()
        if not self.update_game_status.is_running():
            self.update_game_status.start()
        if not self.send_unique_count.is_running():
            self.send_unique_count.start()

    async def setup_database(self):
        """Creates the necessary tables if they do not exist."""
        await self.db_conn.execute("""
            CREATE TABLE IF NOT EXISTS players_info (
                uid TEXT PRIMARY KEY,
                current_name TEXT,
                last_login TEXT,
                total_playtime REAL
            )
        """)
        await self.db_conn.execute("""
            CREATE TABLE IF NOT EXISTS playtime_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                uid TEXT,
                log_time TEXT,
                seconds REAL
            )
        """)
        await self.db_conn.execute("""
            CREATE TABLE IF NOT EXISTS name_changes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                uid TEXT,
                old_name TEXT,
                new_name TEXT,
                change_time TEXT
            )
        """)
        await self.db_conn.execute("""
            CREATE TABLE IF NOT EXISTS player_sessions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                uid TEXT,
                server TEXT,
                start_time TEXT,
                end_time TEXT,
                duration REAL
            )
        """)
        await self.db_conn.commit()

    async def cog_unload(self):
        self.update_game_status.cancel()
        await self.db_conn.close()

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
                if datetime.now(pytz.UTC) - last_heartbeat > timedelta(minutes=1):
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
                embed.add_field(name=f"{MENTOR_EMOJI} {mentor_count} Mentors Online:", value=val, inline=False)
            if swat_count - mentor_count > 0:
                val = ""
                for mp in matching_players:
                    if mp["type"] in ("SWAT", "unknown"):
                        val += f"\n - {mp['username']} (<@{mp['discord_id']}>)" if mp['discord_id'] else f"\n - {mp['username']} (❔)"
                embed.add_field(name=f"{SWAT_LOGO_EMOJI} {swat_count} SWAT Online:", value=val, inline=False)
            if trainee_count:
                val = ""
                for mp in matching_players:
                    if mp["type"] == "trainee":
                        val += f"\n{TRAINEE_EMOJI} {mp['username']} (<@{mp['discord_id']}>)"
                    elif mp["type"] == "cadet":
                        val += f"\n{CADET_EMOJI} {mp['username']} (<@{mp['discord_id']}>)"
                embed.add_field(name=f"{trainee_count} Cadets / Trainees Online:", value=val, inline=False)
            
            if all(p["type"] not in ("SWAT", "mentor", "trainee", "cadet", "unknown") for p in matching_players):
                embed.add_field(name="\n*Nobody is online*\n", value="", inline=False)
            if queue_data and region in queue_data:
                p = queue_data[region]
                # embed.add_field(name=f"{SWAT_LOGO_EMOJI}SWAT:", value=f"``` {swat_count} ```", inline=True)
                embed.add_field(name="🎮Players:", value=f"```{p['Players']}/{p['MaxPlayers']}```", inline=True)
                embed.add_field(name="⌛Queue:", value=f"```{p['QueuedPlayers']}```", inline=True)
                embed.add_field(name="", value=restart_timer, inline=False)
            else:
                # embed.add_field(name=f"{SWAT_LOGO_EMOJI}SWAT:", value=f"```{swat_count}```", inline=True)
                embed.add_field(name="🎮Players:", value="```no data```", inline=True)
                embed.add_field(name="⌛Queue:", value="```no data```", inline=True)
        else:
            embed.add_field(name="Server or API down?", value="No Data for this server!", inline=False)
            embed.add_field(name="🎮Players:", value="```no data```", inline=True)
            embed.add_field(name="⌛Queue:", value="```no data```", inline=True)
        embed.set_footer(text="Refreshes every 60 seconds")
        embed.timestamp = datetime.now()
        return embed

    async def log_name_change(self, uid, old_name, new_name, change_time):
        try:
            await self.db_conn.execute("""
                INSERT INTO name_changes (uid, old_name, new_name, change_time)
                VALUES (?, ?, ?, ?)
            """, (uid, old_name, new_name, change_time))
            await self.db_conn.commit()
        except Exception as e:
            log(f"Error logging name change for uid {uid}: {e}", level="error")

    async def log_session(self, uid, server, start_time, end_time, duration, username):
        try:
            # Update players_info
            await self.db_conn.execute("""
                UPDATE players_info 
                SET total_playtime = total_playtime + ?, last_login = ?
                WHERE uid = ?
            """, (duration, end_time.isoformat(), uid))
            await self.db_conn.commit()
            
            # Insert into playtime_log
            await self.db_conn.execute("""
                INSERT INTO playtime_log (uid, log_time, seconds)
                VALUES (?, ?, ?)
            """, (uid, end_time.isoformat(), duration))
            await self.db_conn.commit()
            
            # Insert into player_sessions
            await self.db_conn.execute("""
                INSERT INTO player_sessions (uid, server, start_time, end_time, duration)
                VALUES (?, ?, ?, ?, ?)
            """, (uid, server, start_time.isoformat(), end_time.isoformat(), duration))
            
            await self.db_conn.commit()
        except Exception as e:
            log(f"Error logging session for uid {uid}: {e}", level="error")

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

        # Process session tracking
        current_time = datetime.utcnow()
        current_players = {}
        
        # Collect current players
        for region in regions:
            players = region_players_map.get(region)
            if isinstance(players, list):
                for pl in players:
                    uid = pl["Uid"]
                    username = pl["Username"]["Username"]
                    if uid not in current_players:
                        current_players[uid] = {
                            "server": region,
                            "username": username
                        }

        # Process new and existing players
        for uid, data in current_players.items():
            if uid not in self.current_sessions:
                # New session
                self.current_sessions[uid] = {
                    "start_time": current_time,
                    "server": data["server"],
                    "username": data["username"]
                }
                # Check for existing player info
                cursor = await self.db_conn.execute("SELECT * FROM players_info WHERE uid = ?", (uid,))
                existing_info = await cursor.fetchone()
                await cursor.close()
                if existing_info:
                    if existing_info["current_name"] != data["username"]:
                        await self.log_name_change(uid, existing_info["current_name"], data["username"], current_time.isoformat())
                        await self.db_conn.execute("""
                            UPDATE players_info SET current_name = ?, last_login = ?
                            WHERE uid = ?
                        """, (data["username"], current_time.isoformat(), uid))
                else:
                    await self.db_conn.execute("""
                        INSERT INTO players_info (uid, current_name, last_login, total_playtime)
                        VALUES (?, ?, ?, 0)
                    """, (uid, data["username"], current_time.isoformat()))
                await self.db_conn.commit()
            else:
                session = self.current_sessions[uid]
                if session["server"] != data["server"]:
                    # Server changed, log old session
                    duration = (current_time - session["start_time"]).total_seconds()
                    await self.log_session(
                        uid, session["server"], session["start_time"], current_time, duration, session["username"]
                    )
                    # Start new session
                    self.current_sessions[uid] = {
                        "start_time": current_time,
                        "server": data["server"],
                        "username": data["username"]
                    }
                elif session["username"] != data["username"]:
                    # Username changed
                    await self.log_name_change(uid, session["username"], data["username"], current_time.isoformat())
                    session["username"] = data["username"]
                    await self.db_conn.execute("""
                        UPDATE players_info SET current_name = ?, last_login = ?
                        WHERE uid = ?
                    """, (data["username"], current_time.isoformat(), uid))
                    await self.db_conn.commit()

        # Process disconnected players
        for uid in list(self.current_sessions.keys()):
            if uid not in current_players:
                session = self.current_sessions.pop(uid)
                duration = (current_time - session["start_time"]).total_seconds()
                await self.log_session(
                    uid, session["server"], session["start_time"], current_time, duration, session["username"]
                )

        # Update embeds
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

        stored = await get_stored_embed(region)  # Added await here
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

    def format_playtime(self, seconds: int) -> str:
        hours, rem = divmod(seconds, 3600)
        mins, secs = divmod(rem, 60)
        return f"{int(hours)}h {int(mins)}m"

    @tasks.loop(hours=1)
    async def send_unique_count(self):
        await self.bot.wait_until_ready()
        cutoff = datetime.utcnow() - timedelta(hours=24)
        cutoff_iso = cutoff.isoformat()

        try:
            async with self.db_conn.execute("""
                SELECT COUNT(DISTINCT l.uid) AS cnt
                FROM playtime_log l
                JOIN players_info p ON p.uid = l.uid
                WHERE datetime(l.log_time) >= datetime(?)
                AND p.current_name LIKE '[SWAT]%'
            """, (cutoff_iso,)) as cursor:
                row = await cursor.fetchone()
                count = row["cnt"] if row else 0  # Now works with aiosqlite.Row
        except Exception as e:
            log(f"Error in send_unique_count: {e}", level="error")
            raise

        try:
            with open(SWAT_WEBSITE_TOKEN_FILE, "r") as f:
                api_token = f.read().strip()
        except Exception as e:
            log(f"Could not read SWAT_WEBSITE_TOKEN_FILE: {e}", level="error")
            return

        if SEND_API_DATA:
            url = f"{SWAT_WEBSITE_URL}/api/players/count"
            headers = {"X-Api-Token": api_token, "Content-Type": "application/json",
                       "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/114.0.0.0 Safari/537.36",
                       "Accept": "application/json, text/javascript, */*; q=0.01",
                       "Accept-Language": "en-US,en;q=0.9"}
            payload = {"playerCount": str(count)}
            log(f"Sending unique SWAT count={count} to {url}", level="info")

            try:
                resp = requests.post(url, json=payload, headers=headers, timeout=10)
                resp.raise_for_status()
            except requests.exceptions.HTTPError as e:
                log(f"Players count API HTTP {e.response.status_code}", level="error")
            except requests.exceptions.RequestException as e:
                log(f"Error sending players count: {e}", level="error")

    # @commands.has_role(LEADERSHIP_ID)
    @commands.hybrid_command(
        name="topplaytime",
        description="Shows top playtime for SWAT members in the given timeframe (days)."
    )
    async def topplaytime(self, ctx: commands.Context, days: int):
        await ctx.defer(ephemeral=True)
        cutoff = datetime.utcnow() - timedelta(days=days)
        cutoff_iso = cutoff.isoformat()
        try:
            cur = await self.db_conn.execute("""
                SELECT p.uid, p.current_name, SUM(l.seconds) as playtime
                FROM playtime_log l
                JOIN players_info p ON l.uid = p.uid
                WHERE datetime(l.log_time) >= datetime(?)
                AND p.current_name LIKE '[SWAT]%'
                GROUP BY p.uid
                ORDER BY playtime DESC
            """, (cutoff_iso,))
            results = await cur.fetchall()
            await cur.close()
        except Exception as e:
            log(f"Error querying top playtime: {e}", level="error")
            await ctx.send("❌ An error occurred while fetching top playtime data.", ephemeral=True)
            return

        if not results:
            await ctx.send(f"No playtime data for SWAT members in the last {days} day(s).", ephemeral=True)
            return

        lines = [f"Top SWAT playtime in the last {days} day(s):", ""]
        for idx, row in enumerate(results, 1):
            playtime_formatted = self.format_playtime(row["playtime"])
            lines.append(f"{idx:>2}. {row['current_name']:<25} – {playtime_formatted}")

        report = "\n".join(lines)

        if len(report) > 4000:
            fp = io.StringIO(report)
            fp.seek(0)
            await ctx.send(
                "⚠️ Result is too long for an embed, here's a text file instead:",
                file=discord.File(fp, filename="topplaytime.txt"),
                ephemeral=True
            )
            return

        embed = discord.Embed(
            title="Top Playtime (SWAT Members)",
            description=report,
            color=0x28ef05
        )
        embed.set_footer(text="Data is a rough estimate and not 100% accurate")
        await ctx.send(embed=embed, ephemeral=True)

    @topplaytime.error
    async def topplaytime_error(self, ctx: commands.Context, error):
        if isinstance(error, commands.MissingRole):
            await ctx.send("❌ You must have the Leadership role to use `/topplaytime`.", ephemeral=True)
        else:
            raise error

    class SessionHistoryView(discord.ui.View):
        def __init__(self, uid: str, cog: commands.Cog):
            super().__init__(timeout=60)
            self.uid = uid
            self.cog = cog
            self.page = 0

        async def fetch_sessions(self):
            cursor = await self.cog.db_conn.execute("""
                SELECT server, start_time, end_time, duration
                FROM player_sessions
                WHERE uid = ?
                ORDER BY end_time DESC
                LIMIT 5 OFFSET ?
            """, (self.uid, self.page * 5))
            sessions = await cursor.fetchall()
            await cursor.close()
            return sessions

        async def update_embed(self, interaction: discord.Interaction):
            sessions = await self.fetch_sessions()
            cursor = await self.cog.db_conn.execute("""
                SELECT COUNT(*) as total FROM player_sessions WHERE uid = ?
            """, (self.uid,))
            total = await cursor.fetchone()
            await cursor.close()
            total_sessions = total["total"] if total else 0

            embed = discord.Embed(title="Session History", color=0x28ef05)
            if sessions:
                desc = []
                for idx, session in enumerate(sessions, start=self.page * 5 + 1):
                    start = datetime.fromisoformat(session["start_time"]).strftime("%m/%d %H:%M")
                    end = datetime.fromisoformat(session["end_time"]).strftime("%H:%M")
                    duration = self.cog.format_playtime(session["duration"])
                    desc.append(f"{idx}. {session['server']} - {start} to {end} ({duration})")
                embed.description = "\n".join(desc)
            else:
                embed.description = "No sessions found."
            
            embed.set_footer(text=f"Page {self.page +1} of {total_sessions //5 +1} | Total sessions: {total_sessions}")
            self.previous_button.disabled = self.page == 0
            self.next_button.disabled = (self.page +1) *5 >= total_sessions
            await interaction.response.edit_message(embed=embed, view=self)

        @discord.ui.button(label="◀️", style=discord.ButtonStyle.primary)
        async def previous_button(self, interaction: discord.Interaction, button: discord.ui.Button):
            self.page -= 1
            await self.update_embed(interaction)

        @discord.ui.button(label="▶️", style=discord.ButtonStyle.primary)
        async def next_button(self, interaction: discord.Interaction, button: discord.ui.Button):
            self.page += 1
            await self.update_embed(interaction)

    # @commands.has_role(LEADERSHIP_ID)
    @commands.hybrid_command(
        name="player",
        description="Shows playtime, last seen, past names, and session history for the specified player."
    )
    async def player(self, ctx: commands.Context, *, name: str):
        await ctx.defer(ephemeral=True)
        try:
            # Existing player info lookup
            cursor = await self.db_conn.execute("SELECT * FROM players_info WHERE lower(current_name)=lower(?)", (name,))
            player_info = await cursor.fetchone()
            await cursor.close()

            if not player_info:
                cursor = await self.db_conn.execute("""
                    SELECT uid FROM name_changes
                    WHERE lower(old_name)=lower(?) OR lower(new_name)=lower(?)
                    LIMIT 1
                """, (name, name))
                row = await cursor.fetchone()
                await cursor.close()
                if row:
                    cursor = await self.db_conn.execute("SELECT * FROM players_info WHERE uid = ?", (row["uid"],))
                    player_info = await cursor.fetchone()
                    await cursor.close()

            if not player_info:
                await ctx.send(f"Player `{name}` not found in the logs.", ephemeral=True)
                return

            uid = player_info["uid"]
            current_name = player_info["current_name"]
            total_playtime = player_info["total_playtime"]

            # Last seen
            cursor = await self.db_conn.execute("SELECT MAX(end_time) as last_seen FROM player_sessions WHERE uid = ?", (uid,))
            last_seen_row = await cursor.fetchone()
            await cursor.close()
            last_seen = "Unknown"
            if last_seen_row and last_seen_row["last_seen"]:
                last_seen_dt = datetime.fromisoformat(last_seen_row["last_seen"])
                last_seen = last_seen_dt.strftime("%Y-%m-%d %H:%M")

            # Name changes
            cursor = await self.db_conn.execute("""
                SELECT old_name, new_name, change_time 
                FROM name_changes
                WHERE uid = ?
                ORDER BY change_time DESC
            """, (uid,))
            name_changes = await cursor.fetchall()
            await cursor.close()

            # Session data
            cursor = await self.db_conn.execute("SELECT COUNT(*) as total FROM player_sessions WHERE uid = ?", (uid,))
            total_sessions = (await cursor.fetchone())["total"]
            await cursor.close()

            # 1) build the description…
            description = (
                f"**UID:** {uid}\n"
                f"**Current Name:** {player_info['current_name']}\n"
                f"**Last Seen:** {last_seen}\n"
                f"**Total Playtime:** {self.format_playtime(player_info['total_playtime'])}\n"
                f"**Total Sessions:** {total_sessions}\n\n"
            )
            if name_changes:
                description += "**Past Name Changes:**\n" + "\n".join(
                    f"- {c['old_name']} → {c['new_name']} (at {c['change_time']})"
                    for c in name_changes
                )
            else:
                description += "No past name changes logged."

            # 2) instantiate your view _before_ fetching
            view = self.SessionHistoryView(uid, self)

            # 3) fetch page 1 of sessions and append to the description
            sessions = await view.fetch_sessions()
            if sessions:
                lines = []
                for idx, sess in enumerate(sessions, start=1):
                    st = datetime.fromisoformat(sess["start_time"]).strftime("%m/%d %H:%M")
                    en = datetime.fromisoformat(sess["end_time"]).strftime("%H:%M")
                    dur = self.format_playtime(sess["duration"])
                    lines.append(f"{idx}. {sess['server']} — {st} to {en} ({dur})")
                description += "\n\n**Recent Sessions:**\n" + "\n".join(lines)
            else:
                description += "\n\n*No sessions found.*"

            # 4) now build and send the embed
            embed = discord.Embed(
                title="Player Information",
                description=description,
                color=0x28ef05
            )
            embed.set_footer(text="Data is a rough estimate and not 100% accurate")

            await ctx.send(embed=embed, view=view, ephemeral=True)
        except Exception as e:
            log(f"Error in player command: {e}", level="error")
            await ctx.send("❌ An error occurred while fetching player data.", ephemeral=True)
            return

    @player.error
    async def player_error(self, ctx: commands.Context, error):
        if isinstance(error, commands.MissingRole):
            await ctx.send("❌ You do not have permissions to run this command.", ephemeral=True)
        else:
            raise error

async def setup(bot: commands.Bot):
    await bot.add_cog(PlayerListCog(bot))