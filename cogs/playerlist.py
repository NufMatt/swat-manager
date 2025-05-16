# cogs/player_list.py
import discord
from discord.ext import tasks, commands
import requests, json, asyncio, aiohttp, re, pytz, aiosqlite
from aiohttp import ContentTypeError
from datetime import datetime, timedelta
import io
from config import *
from cogs.helpers import log, set_stored_embed, get_stored_embed


class PlayerListCog(commands.Cog):
    """Cog for updating an online player list embed based on external APIs,
    while logging playtime and name changes and adding leadership-only commands."""
    
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        # Cache for Discord members
        self.discord_cache = {"timestamp": None, "members": {}}
        self.queue_cache = {
            "timestamp": None,   # when we last fetched
            "data":      None    # what we got
        }
        # Dictionary to track server unreachable state for each region.
        self._server_unreachable = {}
        # Placeholder for aiosqlite connection
        self.db_conn = None
        # Single shared HTTP session
        self.http: aiohttp.ClientSession = None
        # For playtime increment calculation.
        self.last_update_time = None
        # A lock to enforce 1 s between each external request
        self.rate_limit_lock = asyncio.Lock()
        # Kick off initialization (DB + HTTP + starts loops)
        self.bot.loop.create_task(self.init_database())



    async def init_database(self):
        """Initialize aiosqlite DB connection, HTTP session, setup tables, then start loops."""
        # DB
        self.db_conn = await aiosqlite.connect("player_logs.db")
        self.db_conn.row_factory = aiosqlite.Row
        await self.setup_database()
        # HTTP
        self.http = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=5))
        # Now safe to start background loops
        self.update_game_status.start()
        self.send_unique_count.start()


    async def setup_database(self):
        """Creates the necessary tables if they do not exist."""
        async with self.db_conn.cursor() as cur:
            await cur.execute("""
                CREATE TABLE IF NOT EXISTS players_info (
                    uid TEXT PRIMARY KEY,
                    current_name TEXT,
                    last_login TEXT,
                    total_playtime REAL
                )
            """)
            await cur.execute("""
                CREATE TABLE IF NOT EXISTS playtime_log (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    uid TEXT,
                    log_time TEXT,
                    seconds REAL
                )
            """)
            await cur.execute("""
                CREATE TABLE IF NOT EXISTS name_changes (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    uid TEXT,
                    old_name TEXT,
                    new_name TEXT,
                    change_time TEXT
                )
            """)
        await self.db_conn.commit()

    def cog_unload(self):
        self.update_game_status.cancel()
        # Close DB
        if self.db_conn:
            self.bot.loop.create_task(self.db_conn.close())
        # Close HTTP session
        if self.http and not self.http.closed:
            self.bot.loop.create_task(self.http.close())


    async def fetch_players(self, region: str):
        """
        Fetch the player list for `region`, but serialised behind the same lock
        and decoding as UTF-8 (with errors replaced) to avoid charmap decode errors.
        """
        url = API_URLS.get(region)
        if not url:
            log(f"No API URL defined for region {region}.", level="error")
            return None

        try:
            async with self.rate_limit_lock:
                async with self.http.get(url) as resp:
                    resp.raise_for_status()
                    # read raw bytes, then decode as utf-8 (replace on errors)
                    raw = await resp.read()
                # release lock after read + throttle
                await asyncio.sleep(1)

            # now decode & parse JSON
            text = raw.decode("utf-8", errors="replace")
            return json.loads(text)

        except Exception as e:
            # logs both 429 and other errors
            code = getattr(e, "status", 0)
            log(f"Error fetching players for {region}: {code}, {e}", level="error")
            return None



    async def fetch_queue(self) -> dict:
        """
        Fetch queue info—but serialise it behind self.rate_limit_lock,
        parse JSON from text to avoid mimetype checks, and wait 1s before releasing.
        """
        url = "https://api.gtacnr.net/cnr/servers"
        try:
            async with self.rate_limit_lock:
                async with self.http.get(url) as resp:
                    resp.raise_for_status()
                    # read raw text and parse, ignoring Content-Type header
                    text = await resp.text()
                    data = json.loads(text)
                # enforce at least 1s between external calls
                await asyncio.sleep(1)
        except ContentTypeError:
            # fallback if aiohttp still complains
            text = await resp.text()
            data = json.loads(text)
        except Exception as e:
            log(f"Error fetching queue data: {e}", level="error")
            return {}

        # remap US→NA
        queue_info = {entry["Id"]: entry for entry in data}
        for old, new in [("US1","NA1"),("US2","NA2"),("US3","NA3")]:
            if old in queue_info:
                queue_info[new] = queue_info.pop(old)
        return queue_info

    async def get_cached_queue(self) -> dict:
        """
        Return cached queue info if it's fresh; otherwise fetch & cache it once.
        """
        now = datetime.utcnow()
        # Only refresh if we've never fetched, or if more than CHECK_INTERVAL has passed
        if (
            self.queue_cache["timestamp"] is None
            or now - self.queue_cache["timestamp"] > timedelta(seconds=CHECK_INTERVAL)
        ):
            # Fetch and store
            self.queue_cache["data"] = await self.fetch_queue()
            self.queue_cache["timestamp"] = now
        return self.queue_cache["data"]


    async def fetch_fivem(self, region: str):
        url = API_URLS_FIVEM.get(region)
        if not url:
            return None

        try:
            async with self.http.get(url, ssl=False) as resp:
                resp.raise_for_status()
                text = await resp.text(encoding="utf-8")
                return json.loads(text)
        except Exception as e:
            log(f"Error fetching FiveM data for {region}: {e}", level="warning")
            return None


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

    async def log_player_data(self, uid: str, username: str, observed_time: str, increment: float):
        """
        Logs playtime and name changes for a given player.
        Instead of using the API timestamp, it now uses the observed time.
        The increment is the actual elapsed time (in seconds) since the last update.
        """
        try:
            async with self.db_conn.cursor() as cur:
                await cur.execute("SELECT * FROM players_info WHERE uid = ?", (uid,))
                row = await cur.fetchone()
                if row is None:
                    await cur.execute(
                        """
                        INSERT INTO players_info (uid, current_name, last_login, total_playtime)
                        VALUES (?, ?, ?, ?)
                        """,
                        (uid, username, observed_time, increment)
                    )
                else:
                    if row["current_name"].lower() != username.lower():
                        await cur.execute(
                            """
                            INSERT INTO name_changes (uid, old_name, new_name, change_time)
                            VALUES (?, ?, ?, ?)
                            """,
                            (uid, row["current_name"], username, observed_time)
                        )
                        await cur.execute(
                            """
                            UPDATE players_info SET current_name = ?, last_login = ?
                            WHERE uid = ?
                            """,
                            (username, observed_time, uid)
                        )
                    else:
                        await cur.execute(
                            """
                            UPDATE players_info SET last_login = ?
                            WHERE uid = ?
                            """,
                            (observed_time, uid)
                        )
                    await cur.execute(
                        """
                        UPDATE players_info SET total_playtime = total_playtime + ?
                        WHERE uid = ?
                        """,
                        (increment, uid)
                    )
                await cur.execute(
                    """
                    INSERT INTO playtime_log (uid, log_time, seconds)
                    VALUES (?, ?, ?)
                    """,
                    (uid, observed_time, increment)
                )
            await self.db_conn.commit()
        except Exception as e:
            log(f"Error logging player data for uid {uid}: {e}", level="error")

    @tasks.loop(seconds=CHECK_INTERVAL)
    async def update_game_status(self):
        """
        One‐by‐one updating: refresh discord cache, get queue *once*, then
        for each region fetch players/FiveM, log playtime, build & send/edit embed.
        """
        await self.bot.wait_until_ready()
        now_utc = datetime.utcnow()

        # 1) Refresh Discord member cache
        await self.update_discord_cache()

        # 2) Fetch (or re‐use) the queue data once
        queue_info = await self.get_cached_queue()

        # 3) Get the status channel
        channel = self.bot.get_channel(STATUS_CHANNEL_ID)
        if not channel:
            log(f"Status channel {STATUS_CHANNEL_ID} not found.", level="error")
            return

        # 4) Compute elapsed seconds since last run
        increment = (
            (now_utc - self.last_update_time).total_seconds()
            if self.last_update_time
            else CHECK_INTERVAL
        )
        self.last_update_time = now_utc
        observed_time = now_utc.isoformat()

        # 5) Process each region serially
        for region in API_URLS.keys():
            # a) Fetch the player list and FiveM info
            players   = await self.fetch_players(region)
            fivem_dat = await self.fetch_fivem(region)

            # b) Log playtime for each unique player
            if isinstance(players, list):
                seen = set()
                for pl in players:
                    uid = pl["Uid"]
                    if uid in seen:
                        continue
                    seen.add(uid)
                    await self.log_player_data(
                        uid,
                        pl["Username"]["Username"],
                        observed_time,
                        increment
                    )

            # c) Build matching_players list by cross‐referencing Discord cache
            matching_players = [] if players is not None else None
            if isinstance(players, list):
                for pl in players:
                    username = pl["Username"]["Username"]
                    # avoid duplicates
                    if any(mp["username"] == username for mp in matching_players):
                        continue

                    # SWAT/Mentor block
                    if username.startswith("[SWAT] "):
                        cleaned = re.sub(r'^\[SWAT\]\s*', '', username, flags=re.IGNORECASE)
                        found = False
                        for dn, details in self.discord_cache["members"].items():
                            # strip any trailing [SWAT]
                            compare_dn = re.sub(r'\s*\[SWAT\]$', '', dn, flags=re.IGNORECASE)
                            if cleaned.lower() == compare_dn.lower():
                                found = True
                                is_leader = LEADERSHIP_ID in details["roles"]
                                display = f"{LEADERSHIP_EMOJI} {username}" if is_leader else username
                                mtype = "mentor" if MENTOR_ROLE_ID in details["roles"] else "SWAT"
                                matching_players.append({
                                    "username":   display,
                                    "type":       mtype,
                                    "discord_id": details["id"],
                                    "rank":       self.get_rank_from_roles(details["roles"])
                                })
                                break
                        if not found:
                            matching_players.append({
                                "username":   username,
                                "type":       "SWAT",
                                "discord_id": None,
                                "rank":       None
                            })

                    # Cadet/Trainee block
                    else:
                        for dn, details in self.discord_cache["members"].items():
                            tmp = re.sub(r'\s*\[(?:CADET|TRAINEE|SWAT)\]$', '', dn, flags=re.IGNORECASE)
                            if username.lower() == tmp.lower():
                                if CADET_ROLE in details["roles"]:
                                    ptype = "cadet"
                                elif TRAINEE_ROLE in details["roles"]:
                                    ptype = "trainee"
                                elif SWAT_ROLE_ID in details["roles"]:
                                    ptype = "SWAT"
                                else:
                                    ptype = None
                                matching_players.append({
                                    "username":   username,
                                    "type":       ptype,
                                    "discord_id": details["id"],
                                    "rank":       self.get_rank_from_roles(details["roles"])
                                })
                                break

            # d) Sort by rank hierarchy (lowest index = highest rank)
            if matching_players is not None:
                try:
                    matching_players.sort(
                        key=lambda mp: RANK_HIERARCHY.index(mp["rank"])
                        if mp["rank"] in RANK_HIERARCHY else len(RANK_HIERARCHY)
                    )
                except Exception as e:
                    log(f"Error sorting players for {region}: {e}", level="error")

            # e) Build the embed
            embed = await self.create_embed(
                region,
                matching_players,
                queue_info,
                {region: fivem_dat}
            )

            # f) Update or send the embed message
            await self.update_or_create_embed_for_region(channel, region, embed)

            # g) Rate‐limit: wait 2 seconds before the next region
            await asyncio.sleep(2)


    async def update_or_create_embed_for_region(self, channel, region, embed):
        """
        Edit the existing embed for a region, or send a new one if missing.
        Retries up to 3 times on HTTP 503 and tracks unreachable→reachable transitions.
        """
        # Initialize unreachable flag if needed
        if region not in self._server_unreachable:
            self._server_unreachable[region] = False

        stored = await get_stored_embed(region)
        MAX_RETRIES = 3

        if stored:
            # Try editing the existing message
            for attempt in range(1, MAX_RETRIES + 1):
                try:
                    msg = await channel.fetch_message(int(stored["message_id"]))
                    await msg.edit(embed=embed)
                    # If we previously marked it unreachable, clear that now
                    if self._server_unreachable[region]:
                        log(f"Discord reachable again for region {region}.", level="info")
                        self._server_unreachable[region] = False
                    break
                except discord.HTTPException as e:
                    if e.status == 503:
                        # Service unavailable → mark unreachable & retry
                        if not self._server_unreachable[region]:
                            log(f"503 editing embed for {region}, attempt {attempt}: {e}", level="error")
                            self._server_unreachable[region] = True
                        if attempt == MAX_RETRIES:
                            log(f"Max retries reached editing embed for {region}.", level="error")
                    else:
                        log(f"HTTPException editing embed for {region}: {e}", level="error")
                        break
                except Exception as ex:
                    log(f"Unexpected error editing embed for {region}: {ex}", level="error")
                    break

        else:
            # No stored message → send a new one
            for attempt in range(1, MAX_RETRIES + 1):
                try:
                    sent = await channel.send(embed=embed)
                    await set_stored_embed(region, str(sent.id), str(sent.channel.id))
                    if self._server_unreachable[region]:
                        log(f"Discord reachable again for region {region}.", level="info")
                        self._server_unreachable[region] = False
                    break
                except discord.HTTPException as e:
                    if e.status == 503:
                        if not self._server_unreachable[region]:
                            log(f"503 sending embed for {region}, attempt {attempt}: {e}", level="error")
                            self._server_unreachable[region] = True
                        if attempt < MAX_RETRIES:
                            await asyncio.sleep(5)
                        else:
                            log(f"Max retries reached sending embed for {region}.", level="error")
                    else:
                        log(f"HTTPException sending embed for {region}: {e}", level="error")
                        break
                except Exception as ex:
                    log(f"Unexpected error sending embed for {region}: {ex}", level="error")
                    break

    def format_playtime(self, seconds: int) -> str:
        # your existing formatter
        hours, rem = divmod(seconds, 3600)
        mins, secs = divmod(rem, 60)
        return f"{int(hours)}h {int(mins)}m"


    @tasks.loop(hours=1)
    async def send_unique_count(self):
        await self.bot.wait_until_ready()
        cutoff = (datetime.utcnow() - timedelta(hours=24)).isoformat()
        # Async DB query
        async with self.db_conn.cursor() as cur:
            await cur.execute("""
                SELECT COUNT(DISTINCT l.uid) AS cnt
                  FROM playtime_log l
                  JOIN players_info p ON p.uid = l.uid
                 WHERE datetime(l.log_time) >= datetime(?)
                   AND p.current_name LIKE '[SWAT]%'
            """, (cutoff,))
            row = await cur.fetchone()
        count = row["cnt"] if row else 0

        # 3) read your website API token
        try:
            with open(SWAT_WEBSITE_TOKEN_FILE, "r") as f:
                api_token = f.read().strip()
        except Exception as e:
            log(f"Could not read SWAT_WEBSITE_TOKEN_FILE: {e}", level="error")
            return

        # 4) fire off the POST
        if SEND_API_DATA:
            url = f"{SWAT_WEBSITE_URL}/api/players/count"
            headers = {"X-Api-Token": api_token, "Content-Type": "application/json",
                        "User-Agent": (
                        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                        "AppleWebKit/537.36 (KHTML, like Gecko) " 
                        "Chrome/114.0.0.0 Safari/537.36"),
                        "Accept": "application/json, text/javascript, */*; q=0.01",
                        "Accept-Language": "en-US,en;q=0.9"}
            payload = {"playerCount": str(count)}
            log(f"Sending unique SWAT count={count} to {url}", level="info")

            try:
                resp = requests.post(url, json=payload, headers=headers, timeout=10)
                resp.raise_for_status()
            except requests.exceptions.HTTPError as e:
                body = getattr(e.response, "text", "")
                log(f"Players count API HTTP {e.response.status_code}", level="error")
            except requests.exceptions.RequestException as e:
                log(f"Error sending players count: {e}", level="error")
            else:
                # parse JSON to confirm success
                try:
                    data = resp.json()
                except ValueError:
                    log(f"Invalid JSON from players count API: {resp.text}", level="error")
                else:
                    if data.get("success"):
                        log(f"Successfully sent unique SWAT count={count}", level="info")
                    else:
                        err = data.get("error", "unknown")
                        log(f"Players count API error response: {err}", level="error")


    @commands.has_role(LEADERSHIP_ID)
    @commands.hybrid_command(
        name="topplaytime",
        description="Shows top playtime for SWAT members in the given timeframe (days)."
    )
    async def topplaytime(self, ctx: commands.Context, days: int):
        await ctx.defer(ephemeral=True)
        cutoff = (datetime.utcnow() - timedelta(days=days)).isoformat()
        async with self.db_conn.cursor() as cur:
            await cur.execute("""
                SELECT p.uid, p.current_name, SUM(l.seconds) AS playtime
                  FROM playtime_log l
                  JOIN players_info p ON l.uid = p.uid
                 WHERE datetime(l.log_time) >= datetime(?)
                   AND p.current_name LIKE '[SWAT]%'
                 GROUP BY p.uid
                 ORDER BY playtime DESC
            """, (cutoff,))
            results = await cur.fetchall()

        if not results:
            await ctx.send(f"No playtime data for SWAT members in the last {days} day(s).", ephemeral=True)
            return

        # Build the full report as plain text
        lines = [f"Top SWAT playtime in the last {days} day(s):", ""]
        for idx, row in enumerate(results, 1):
            playtime_formatted = self.format_playtime(row["playtime"])
            lines.append(f"{idx:>2}. {row['current_name']:<25} – {playtime_formatted}")

        report = "\n".join(lines)

        # If it exceeds Discord's embed-desc limit (4096 chars), send as a text file
        if len(report) > 4000:
            fp = io.StringIO(report)
            fp.seek(0)
            await ctx.send(
                "⚠️ Result is too long for an embed, here's a text file instead:",
                file=discord.File(fp, filename="topplaytime.txt"),
                ephemeral=True
            )
            return

        # Otherwise send as a neat embed
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
            await ctx.send(
                "❌ You must have the Leadership role to use `/topplaytime`.",
                ephemeral=True
            )
        else:
            raise error

    @topplaytime.error
    async def topplaytime_error(self, ctx: commands.Context, error):
        if isinstance(error, commands.MissingRole):
            await ctx.send(
                "❌ You do not have permissions to run this command.",
                ephemeral=True
            )
        else:
            # Let other errors bubble up (optional)
            raise error

    @commands.has_any_role(LEADERSHIP_ID, RECRUITER_ID)
    @commands.hybrid_command(
        name="player",
        description="Shows playtime, last seen and past names for the specified player."
    )
    async def player(
        self,
        ctx: commands.Context,
        target: discord.Member = None,
        *,
        name: str = None
    ):
        await ctx.defer(ephemeral=True)

        # determine lookup_name from either a mention or plain text
        if target:
            # take their server display name
            disp = target.display_name
            # SWAT at end? move to front
            if disp.lower().endswith("[swat]"):
                core = disp[: -6].strip()
                lookup_name = f"[SWAT] {core}"
            # Trainee/Cadet at end? strip it
            elif re.search(r"\[(?:trainee|cadet)\]$", disp, re.IGNORECASE):
                lookup_name = re.sub(r"\s*\[(?:trainee|cadet)\]$", "", disp, flags=re.IGNORECASE)
            else:
                lookup_name = disp
        elif name:
            lookup_name = name
        else:
            return await ctx.send(
                "❌ Please either mention a user or provide their name.",
                ephemeral=True
            )

        # Now exactly as before, but using lookup_name for DB queries
        try:
            # 1) Lookup player_info
            async with self.db_conn.cursor() as cur:
                await cur.execute(
                    "SELECT * FROM players_info WHERE lower(current_name)=lower(?)",
                    (lookup_name,)
                )
                player_info = await cur.fetchone()
                if not player_info:
                    await cur.execute(
                        """
                        SELECT uid FROM name_changes
                         WHERE lower(old_name)=lower(?) OR lower(new_name)=lower(?)
                         LIMIT 1
                        """,
                        (lookup_name, lookup_name)
                    )
                    row = await cur.fetchone()
                    if row:
                        await cur.execute(
                            "SELECT * FROM players_info WHERE uid = ?",
                            (row["uid"],)
                        )
                        player_info = await cur.fetchone()

            if not player_info:
                return await ctx.send(
                    f"Player `{lookup_name}` not found in the logs.",
                    ephemeral=True
                )

            uid = player_info["uid"]
            current_name = player_info["current_name"]
            total_playtime = player_info["total_playtime"]

            # 2) Determine last seen
            async with self.db_conn.cursor() as cur:
                await cur.execute(
                    "SELECT MAX(log_time) AS last_seen FROM playtime_log WHERE uid = ?",
                    (uid,)
                )
                last_seen_row = await cur.fetchone()

            last_seen = "Unknown"
            if last_seen_row and last_seen_row["last_seen"]:
                try:
                    last_seen_dt = datetime.fromisoformat(last_seen_row["last_seen"])
                    diff = datetime.utcnow() - last_seen_dt
                    if diff < timedelta(hours=24):
                        secs = diff.total_seconds()
                        last_seen = (
                            f"{int(secs//60)} min ago"
                            if secs < 3600
                            else f"{int(secs//3600)} hour(s) ago"
                        )
                    else:
                        last_seen = last_seen_dt.strftime("%d.%m.%Y - %H:%M")
                except Exception:
                    last_seen = last_seen_row["last_seen"]

            # 3) Fetch name change history
            async with self.db_conn.cursor() as cur:
                await cur.execute(
                    """
                    SELECT old_name, new_name, change_time
                      FROM name_changes
                     WHERE uid = ?
                  ORDER BY change_time DESC
                    """,
                    (uid,)
                )
                name_changes = await cur.fetchall()

            # 4) Build and send embed
            description = (
                f"**UID:** {uid}\n"
                f"**Current Name:** {current_name}\n"
                f"**Last Seen:** {last_seen}\n"
                f"**Total Playtime:** {self.format_playtime(total_playtime)}\n\n"
            )
            if name_changes:
                description += "**Past Name Changes:**\n"
                for change in name_changes:
                    description += (
                        f"- {change['old_name']} → {change['new_name']} "
                        f"(at {change['change_time']})\n"
                    )
            else:
                description += "No past name changes logged."

            embed = discord.Embed(
                title="Player Information",
                description=description,
                color=0x28ef05
            )
            embed.set_footer(text="Data is a rough estimate and not 100% accurate")
            await ctx.send(embed=embed, ephemeral=True)

        except Exception as e:
            log("error", f"Error in /player command: {e}")
            await ctx.send(
                "❌ An error occurred while fetching the player data.",
                ephemeral=True
            )

    @player.error
    async def player_error(self, ctx: commands.Context, error):
        if isinstance(error, commands.MissingRole):
            await ctx.send(
                "❌ You do not have permissions to run this command.",
                ephemeral=True
            )
        else:
            raise error

async def setup(bot: commands.Bot):
    await bot.add_cog(PlayerListCog(bot))