# cogs/player_list.py

import discord
from discord.ext import tasks, commands
import aiosqlite
import requests, json, asyncio, aiohttp, re, pytz
from datetime import datetime, timedelta
import io
from config_testing import *
from cogs.helpers import log, set_stored_embed, get_stored_embed

def format_playtime(seconds: int) -> str:
    """Convert seconds to 'Xh Ym'."""
    hours, rem = divmod(int(seconds), 3600)
    mins, _ = divmod(rem, 60)
    return f"{hours}h {mins}m"

class SessionsView(discord.ui.View):
    def __init__(self, cog: 'PlayerListCog', uid: str):
        super().__init__(timeout=None)
        self.cog = cog
        self.uid = uid

    @discord.ui.button(label="View Sessions", style=discord.ButtonStyle.primary, custom_id="view_sessions")
    async def view_sessions(self, interaction: discord.Interaction, button: discord.ui.Button):
        # Fetch session history
        cur = await self.cog.db_conn.execute(
            "SELECT server, start_time, end_time, duration "
            "FROM sessions WHERE uid = ? ORDER BY start_time DESC",
            (self.uid,)
        )
        rows = await cur.fetchall()
        if not rows:
            return await interaction.response.send_message("No sessions recorded.", ephemeral=True)

        embed = discord.Embed(title="📝 Session History", color=0x28ef05)
        for server, st, et, dur in rows:
            embed.add_field(
                name=f"{format_playtime(dur)} Session on {server}",
                value=f"Joined: {st}\nLeft: {et}",
                inline=False
            )

        # Navigation buttons (stub callbacks)
        nav = discord.ui.View()
        nav.add_item(discord.ui.Button(label="Home", style=discord.ButtonStyle.secondary, custom_id="home"))
        nav.add_item(discord.ui.Button(label="Switch Site", style=discord.ButtonStyle.secondary, custom_id="switch_site"))

        await interaction.response.send_message(embed=embed, view=nav, ephemeral=True)

class PlayerListCog(commands.Cog):
    """Cog for online player-list embeds, session logging, name-change tracking, and SWAT commands."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.db_conn: aiosqlite.Connection | None = None
        self.active_sessions: dict[str, tuple[str,str]] = {}   # uid -> (start_iso, server)
        self.last_online: dict[str,bool] = {}
        self.discord_cache = {"timestamp": None, "members": {}}
        self._server_unreachable: dict[str,bool] = {}
        # Start our background loops
        self.update_game_status.start()
        self.send_unique_count.start()

    async def cog_load(self):
        # Async DB setup (remove unsupported detect_types)
        self.db_conn = await aiosqlite.connect("player_logs_new.db")
        self.db_conn.row_factory = aiosqlite.Row
        await self._setup_database()

    async def _setup_database(self):
        # Create tables if missing
        await self.db_conn.execute("""
            CREATE TABLE IF NOT EXISTS players_info (
                uid TEXT PRIMARY KEY,
                current_name TEXT,
                crew TEXT,
                rank INTEGER,
                total_playtime REAL
            )
        """)
        await self.db_conn.execute("""
            CREATE TABLE IF NOT EXISTS sessions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                uid TEXT,
                server TEXT,
                start_time TEXT,
                end_time TEXT,
                duration REAL
            )
        """)
        await self.db_conn.execute("CREATE INDEX IF NOT EXISTS idx_sessions_uid ON sessions(uid)")
        await self.db_conn.execute("""
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
        self.send_unique_count.cancel()
        if self.db_conn:
            # schedule close
            asyncio.create_task(self.db_conn.close())

    # —— Helper methods —— #

    async def get_last_session(self, uid: str) -> dict | None:
        cur = await self.db_conn.execute(
            "SELECT server, start_time, end_time, duration "
            "FROM sessions WHERE uid=? ORDER BY start_time DESC LIMIT 1",
            (uid,)
        )
        row = await cur.fetchone()
        if not row:
            return None
        return dict(row)

    async def get_session_count(self, uid: str) -> int:
        cur = await self.db_conn.execute("SELECT COUNT(*) AS cnt FROM sessions WHERE uid=?", (uid,))
        row = await cur.fetchone()
        return row["cnt"] or 0

    async def get_avg_session_duration(self, uid: str) -> float:
        cur = await self.db_conn.execute("SELECT AVG(duration) AS avgd FROM sessions WHERE uid=?", (uid,))
        row = await cur.fetchone()
        return row["avgd"] or 0.0

    async def get_most_played_server(self, uid: str) -> str:
        cur = await self.db_conn.execute(
            "SELECT server, SUM(duration) AS tot FROM sessions WHERE uid=? "
            "GROUP BY server ORDER BY tot DESC LIMIT 1",
            (uid,)
        )
        row = await cur.fetchone()
        return row["server"] if row else "N/A"

    # —— External fetches & cache —— #

    async def fetch_players(self, region: str):
        if USE_LOCAL_JSON:
            try:
                with open(LOCAL_JSON_FILE, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception as e:
                log(f"Error reading local JSON ({region}): {e}", level="error")
                return []
        url = API_URLS.get(region)
        if not url:
            log(f"No API URL for {region}", level="error")
            return []
        try:
            await asyncio.sleep(1)
            timeout = aiohttp.ClientTimeout(total=5)
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.get(url) as resp:
                    resp.raise_for_status()
                    
                    text = await resp.text(encoding="utf-8")
                    return json.loads(text)
        except asyncio.TimeoutError:
            log(f"Timeout fetching players for {region}", level="error")
            return []
        except aiohttp.ClientError as e:
            log(f"Client error for {region}: {e}", level="error")
            return []

    async def getqueuedata(self):
        try:
            r = requests.get("https://api.gtacnr.net/cnr/servers", timeout=3)
            r.raise_for_status()
            data = r.json()
            qi = {e["Id"]: e for e in data}
            # remap US -> NA
            for old, new in [("US1","NA1"),("US2","NA2"),("US3","NA3")]:
                if old in qi:
                    qi[new] = qi.pop(old)
            return qi
        except Exception as e:
            log(f"Error fetching queue data: {e}", level="error")
            return {}

    async def get_fivem_data(self):
        out = {}
        async with aiohttp.ClientSession() as session:
            for region, url in API_URLS_FIVEM.items():
                try:
                    async with session.get(url, ssl=False, timeout=aiohttp.ClientTimeout(total=5)) as resp:
                        resp.raise_for_status()
                        out[region] = await resp.json(content_type=None)
                        await asyncio.sleep(1.5)
                except Exception as e:
                    log(f"Warning fetching FiveM {region}: {e}", level="warning")
                    out[region] = None
        return out

    async def update_discord_cache(self):
        now = datetime.utcnow()
        if self.discord_cache["timestamp"] and now - self.discord_cache["timestamp"] < timedelta(seconds=CACHE_UPDATE_INTERVAL):
            return
        guild = self.bot.get_guild(GUILD_ID)
        if not guild:
            log(f"Not in guild {GUILD_ID}", level="error")
            return
        members = {m.display_name: {"id": m.id, "roles": [r.id for r in m.roles]} for m in guild.members}
        self.discord_cache = {"timestamp": now, "members": members}

    def time_convert(self, time_string: str) -> str:
        m = re.match(r'^(.+) (\d{2}):(\d{2})$', time_string)
        if not m:
            return "*Restarting now*"
        d, hh, mm = m.groups()
        hh, mm = int(hh), int(mm)
        days = ['Saturday','Friday','Thursday','Wednesday','Tuesday','Monday','Sunday']
        total_hours = (days.index(d)*24*60 + (24-hh-1)*60 + (60-mm))//60
        if total_hours <= 0:
            return "*Restarting now*"
        h, r = divmod(total_hours, 60)
        hs = f"{h} hour{'s'*(h!=1)}" if h else ""
        rs = f"{r} minute{'s'*(r!=1)}" if r else ""
        return f"*Next restart in ~{hs+' and '+rs if hs and rs else hs or rs}*"

    def get_rank_from_roles(self, roles: list[int]) -> int | None:
        for rid, rank in ROLE_TO_RANK.items():
            if rid in roles:
                return rank
        return None

    # —— Embed builder —— #

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

    # —— Main loop —— #

    @tasks.loop(seconds=CHECK_INTERVAL)
    async def update_game_status(self):
        await self.bot.wait_until_ready()
        await self.update_discord_cache()
        queue_data = await self.getqueuedata()
        fivem_data = await self.get_fivem_data()
        channel = self.bot.get_channel(STATUS_CHANNEL_ID)
        if not channel:
            log(f"Channel {STATUS_CHANNEL_ID} missing", level="error")
            return

        # who’s online now?
        now_iso = datetime.utcnow().isoformat()
        regions = list(API_URLS.keys())
        current = {}
        for reg in regions:
            plist = await self.fetch_players(reg)
            for p in plist:
                current[p["Uid"]] = reg

        # detect joins
        for uid, srv in current.items():
            if not self.last_online.get(uid, False):
                self.active_sessions[uid] = (now_iso, srv)
            self.last_online[uid] = True

        # detect leaves
        for uid, was in list(self.last_online.items()):
            if was and uid not in current:
                start_iso, srv = self.active_sessions.pop(uid, (None,None))
                if start_iso:
                    dt1 = datetime.fromisoformat(start_iso)
                    dt2 = datetime.fromisoformat(now_iso)
                    dur = (dt2 - dt1).total_seconds()
                    fmt = "%Y-%m-%d %H:%M:%S+00:00"
                    # write session
                    await self.db_conn.execute(
                        "INSERT INTO sessions (uid,server,start_time,end_time,duration) VALUES (?,?,?,?,?)",
                        (uid, srv, dt1.strftime(fmt), dt2.strftime(fmt), dur)
                    )
                    # bump total_playtime
                    await self.db_conn.execute(
                        "INSERT INTO players_info (uid,current_name,crew,rank,total_playtime) "
                        "VALUES (?, '', 0, 0, ?) "
                        "ON CONFLICT(uid) DO UPDATE SET total_playtime = total_playtime + excluded.total_playtime",
                        (uid, dur)
                    )
                    await self.db_conn.commit()
                self.last_online[uid] = False

        # update each region’s embed
        for reg in regions:
            plist = await self.fetch_players(reg)
            # build matching list
            matching = [] if plist else None
            if plist:
                for pl in plist:
                    uname = pl["Username"]["Username"]
                    if any(x["username"]==uname for x in matching):
                        continue
                    # SWAT prefix logic...
                    if uname.startswith("[SWAT]"):
                        cleaned = re.sub(r'^\[SWAT\]\s*','', uname, flags=re.IGNORECASE)
                        found = False
                        for dname, det in self.discord_cache["members"].items():
                            if re.sub(r'\s*\[SWAT\]$','',dname,flags=re.IGNORECASE).lower()==cleaned.lower():
                                found = True
                                is_lead = LEADERSHIP_ID in det["roles"]
                                disp = f"{LEADERSHIP_EMOJI} {uname}" if is_lead else uname
                                typ = "mentor" if MENTOR_ROLE_ID in det["roles"] else "SWAT"
                                matching.append({
                                    "username": disp, "type": typ,
                                    "discord_id": det["id"],
                                    "rank": self.get_rank_from_roles(det["roles"])
                                })
                                break
                        if not found:
                            matching.append({"username":uname,"type":"SWAT","discord_id":None,"rank":None})
                    else:
                        for dname, det in self.discord_cache["members"].items():
                            base = re.sub(r'\s*\[(CADET|TRAINEE|SWAT)\]$','',dname,flags=re.IGNORECASE)
                            if uname.lower()==base.lower():
                                if CADET_ROLE in det["roles"]:
                                    t="cadet"
                                elif TRAINEE_ROLE in det["roles"]:
                                    t="trainee"
                                elif SWAT_ROLE_ID in det["roles"]:
                                    t="SWAT"
                                else:
                                    continue
                                matching.append({
                                    "username":uname,"type":t,
                                    "discord_id":det["id"],
                                    "rank":self.get_rank_from_roles(det["roles"])
                                })
                                break
                matching.sort(key=lambda x: RANK_HIERARCHY.index(x["rank"]) if x["rank"] in RANK_HIERARCHY else len(RANK_HIERARCHY))

            emb = await self.create_embed(reg, matching, queue_data, fivem_data)
            # stored embed
            stored = await get_stored_embed(reg)
            if stored:
                message_id = stored["message_id"]

                # edit
                for i in range(3):
                    try:
                        msg = await channel.fetch_message(int(message_id))
                        await msg.edit(embed=emb)
                        break
                    except discord.HTTPException as e:
                        await asyncio.sleep(2)
            else:
                # send new
                msg = await channel.send(embed=emb)
                set_stored_embed(reg, str(msg.id), str(msg.channel.id))
            await asyncio.sleep(1)

    # —— Unique SWAT count loop —— #

    @tasks.loop(hours=1)
    async def send_unique_count(self):
        await self.bot.wait_until_ready()
        cutoff = (datetime.utcnow() - timedelta(hours=24)).isoformat()
        cur = await self.db_conn.execute("""
            SELECT COUNT(DISTINCT uid) AS cnt
              FROM sessions
             WHERE datetime(start_time) >= datetime(?)
               AND uid IN (
                   SELECT uid FROM players_info
                    WHERE current_name LIKE '[SWAT]%'
               )
        """, (cutoff,))
        row = await cur.fetchone()
        count = row["cnt"] or 0

        # read token
        try:
            tok = open(SWAT_WEBSITE_TOKEN_FILE).read().strip()
        except Exception as e:
            log(f"Token read error: {e}", level="error")
            return

        if SEND_API_DATA:
            url = f"{SWAT_WEBSITE_URL}/api/players/count"
            headers = {
                "X-Api-Token": tok,
                "Content-Type": "application/json",
                "User-Agent": "swat-bot",
            }
            payload = {"playerCount": str(count)}
            try:
                r = requests.post(url, json=payload, headers=headers, timeout=10)
                r.raise_for_status()
                data = r.json()
                if not data.get("success"):
                    log(f"API error: {data}", level="error")
            except Exception as e:
                log(f"Error sending count: {e}", level="error")

    # —— /topplaytime command —— #

    @commands.has_role(LEADERSHIP_ID)
    @commands.hybrid_command(name="topplaytime", description="Top SWAT playtime over days")
    async def topplaytime(self, ctx: commands.Context, days: int):
        await ctx.defer(ephemeral=True)
        cutoff = (datetime.utcnow() - timedelta(days=days)).strftime("%Y-%m-%d %H:%M:%S")
        cur = await self.db_conn.execute(
            "SELECT p.current_name, SUM(s.duration) AS playtime "
            "FROM sessions s JOIN players_info p ON s.uid=p.uid "
            "WHERE s.start_time>=? AND p.current_name LIKE '[SWAT]%' "
            "GROUP BY s.uid ORDER BY playtime DESC",
            (cutoff,)
        )
        rows = await cur.fetchall()
        if not rows:
            return await ctx.send(f"No data in last {days} days.", ephemeral=True)

        lines = [f"Top SWAT playtime in last {days} day(s):", ""]
        for i, r in enumerate(rows, start=1):
            lines.append(f"{i:>2}. {r['current_name']:<25} – {format_playtime(r['playtime'])}")

        report = "\n".join(lines)
        if len(report) > 4000:
            fp = io.StringIO(report)
            return await ctx.send("⚠️ Too long, see file.", file=discord.File(fp, "topplaytime.txt"), ephemeral=True)

        emb = discord.Embed(title="Top Playtime (SWAT)", description=report, color=0x28ef05)
        emb.set_footer(text="Approximate data")
        await ctx.send(embed=emb, ephemeral=True)

    @topplaytime.error
    async def topplaytime_error(self, ctx: commands.Context, error):
        if isinstance(error, commands.MissingRole):
            return await ctx.send("❌ Leadership role required.", ephemeral=True)
        raise error

    # —— /player command —— #

    @commands.has_role(LEADERSHIP_ID)
    @commands.hybrid_command(name="player", description="Show player info & sessions")
    async def player(self, ctx: commands.Context, *, name: str):
        await ctx.defer(ephemeral=True)
        cur = await self.db_conn.execute(
            "SELECT * FROM players_info WHERE lower(current_name)=lower(?)", (name,)
        )
        pi = await cur.fetchone()
        if not pi:
            # lookup by old name
            cur2 = await self.db_conn.execute(
                "SELECT uid FROM name_changes WHERE lower(old_name)=lower(?) OR lower(new_name)=lower(?) LIMIT 1",
                (name,name)
            )
            row = await cur2.fetchone()
            if row:
                cur3 = await self.db_conn.execute(
                    "SELECT * FROM players_info WHERE uid=?", (row["uid"],)
                )
                pi = await cur3.fetchone()
        if not pi:
            return await ctx.send(f"Player `{name}` not found.", ephemeral=True)

        uid = pi["uid"]
        # gather stats
        total = pi["total_playtime"]
        most  = await self.get_most_played_server(uid)
        count = await self.get_session_count(uid)
        avg   = await self.get_avg_session_duration(uid)
        last  = await self.get_last_session(uid)

        # build embed
        emb = discord.Embed(title=f"Player Information - {pi['current_name']}", color=0x28ef05)
        emb.add_field(name="Basic Information", value=(
            f"🆔 User ID: {uid}\n"
            f"👥 Username: {pi['current_name']}\n"
            f"🛡️ Crew: {pi['crew']}\n"
            f"🏆 Rank: #{pi['rank']}"
        ), inline=False)
        emb.add_field(name="Playtime Information", value=(
            f"⏳ Total Playtime: {format_playtime(total)}\n"
            f"🖥️ Most Played Server: {most}\n"
            f"📊 Session Count: {count}\n"
            f"⏳ Avg Session Duration: {format_playtime(avg)}"
        ), inline=False)
        if last:
            emb.add_field(name="Last Session", value=(
                f"Server: {last['server']}\n"
                f"Joined: {last['start_time']}\n"
                f"Left:   {last['end_time']}\n"
                f"Duration: {format_playtime(last['duration'])}"
            ), inline=False)

        # rename history
        chcur = await self.db_conn.execute(
            "SELECT old_name,new_name,change_time FROM name_changes WHERE uid=? ORDER BY change_time DESC",
            (uid,)
        )
        changes = await chcur.fetchall()
        txt = "\n".join(f"- {c['old_name']} → {c['new_name']} ({c['change_time']})" for c in changes) \
              or "No renames recorded."
        emb.add_field(name="Rename History", value=txt, inline=False)
        emb.set_footer(text="---")

        view = SessionsView(self, uid)
        await ctx.send(embed=emb, view=view, ephemeral=True)

    @player.error
    async def player_error(self, ctx: commands.Context, error):
        if isinstance(error, commands.MissingRole):
            return await ctx.send("❌ Leadership role required.", ephemeral=True)
        raise error

async def setup(bot: commands.Bot):
    await bot.add_cog(PlayerListCog(bot))
