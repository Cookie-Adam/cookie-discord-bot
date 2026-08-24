import os
import re
import random
import asyncio
import datetime
import sqlite3
import discord
from discord.ext import commands
from dotenv import load_dotenv
import yt_dlp

# Load token from .env file
load_dotenv()
TOKEN = os.getenv("DISCORD_TOKEN")

# ---- Set the exact names of roles allowed to use moderation commands ----
MOD_ROLE_NAMES = ["🌟 𝒪𝓌𝓃𝑒𝓇 ✨👑", "🌸 𝒜𝒹𝓂𝒾𝓃 🛠️💼"]

# Set up intents (permissions for what the bot can "see")
intents = discord.Intents.default()
intents.message_content = True
intents.members = True

bot = commands.Bot(command_prefix="!", intents=intents)

# ---------------- Database setup (SQLite) ----------------
db = sqlite3.connect("leveling.db")
cursor = db.cursor()
cursor.execute("""
    CREATE TABLE IF NOT EXISTS levels (
        user_id INTEGER PRIMARY KEY,
        guild_id INTEGER,
        xp INTEGER DEFAULT 0,
        level INTEGER DEFAULT 1
    )
""")
db.commit()

xp_cooldowns = {}
XP_COOLDOWN_SECONDS = 60
XP_MIN = 15
XP_MAX = 25


def get_user(user_id: int, guild_id: int):
    cursor.execute("SELECT xp, level FROM levels WHERE user_id = ? AND guild_id = ?", (user_id, guild_id))
    row = cursor.fetchone()
    if row is None:
        cursor.execute("INSERT INTO levels (user_id, guild_id, xp, level) VALUES (?, ?, 0, 1)", (user_id, guild_id))
        db.commit()
        return 0, 1
    return row


def xp_needed_for_level(level: int) -> int:
    return 5 * (level ** 2) + 50 * level + 100


def add_xp(user_id: int, guild_id: int, amount: int):
    xp, level = get_user(user_id, guild_id)
    xp += amount
    leveled_up = False
    while xp >= xp_needed_for_level(level):
        xp -= xp_needed_for_level(level)
        level += 1
        leveled_up = True
    cursor.execute(
        "UPDATE levels SET xp = ?, level = ? WHERE user_id = ? AND guild_id = ?",
        (xp, level, user_id, guild_id)
    )
    db.commit()
    return level, leveled_up


def is_mod():
    async def predicate(ctx):
        user_role_names = [role.name for role in ctx.author.roles]
        if any(name in user_role_names for name in MOD_ROLE_NAMES):
            return True
        await ctx.send("🚫 You don't have permission to use this command.")
        return False
    return commands.check(predicate)


# ---------------- Music setup ----------------

YTDL_OPTS = {
    "format": "bestaudio/best",
    "noplaylist": True,
    "quiet": True,
    "default_search": "ytsearch",
    "source_address": "0.0.0.0",
}

FFMPEG_OPTS = {
    "before_options": "-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5",
    "options": "-vn",
}

ytdl = yt_dlp.YoutubeDL(YTDL_OPTS)

# guild_id -> list of dicts {"title": ..., "url": ...}
song_queues = {}

SPOTIFY_URL_PATTERN = re.compile(r"open\.spotify\.com/(track|album|playlist)/([A-Za-z0-9]+)")


def resolve_query(query: str) -> str:
    """
    If given a Spotify link, we can't stream from Spotify directly (their API
    doesn't allow bots to play audio). Instead, treat it as a search term by
    stripping it down to a plain search query for YouTube.
    """
    if SPOTIFY_URL_PATTERN.search(query):
        return "spotify track (search fallback) " + query  # yt-dlp will still just search YouTube
    return query


async def get_song_info(query: str):
    """Runs yt-dlp in a background thread so it doesn't block the bot's event loop."""
    loop = asyncio.get_event_loop()
    query = resolve_query(query)

    def extract():
        info = ytdl.extract_info(query, download=False)
        if "entries" in info:  # search results come back as a list
            info = info["entries"][0]
        return info

    info = await loop.run_in_executor(None, extract)
    return {"title": info.get("title", "Unknown title"), "url": info["url"], "webpage_url": info.get("webpage_url", "")}


async def play_next(ctx):
    guild_id = ctx.guild.id
    queue = song_queues.get(guild_id, [])

    if not queue:
        return

    song = queue.pop(0)
    voice_client = ctx.guild.voice_client

    source = discord.FFmpegPCMAudio(song["url"], **FFMPEG_OPTS)

    def after_playing(error):
        if error:
            print(f"Player error: {error}")
        fut = asyncio.run_coroutine_threadsafe(play_next(ctx), bot.loop)
        try:
            fut.result()
        except Exception as e:
            print(f"Error advancing queue: {e}")

    voice_client.play(source, after=after_playing)
    asyncio.create_task(ctx.send(f"🎶 Now playing: **{song['title']}**"))


@bot.event
async def on_ready():
    print(f"✅ Logged in as {bot.user} (ID: {bot.user.id})")
    try:
        synced = await bot.tree.sync()
        print(f"🔄 Synced {len(synced)} slash command(s)")
    except Exception as e:
        print(f"⚠️ Slash command sync failed: {e}")
    print("------")


@bot.event
async def on_member_join(member):
    channel = discord.utils.get(member.guild.text_channels, name="general")
    if channel:
        await channel.send(f"👋 Welcome to the server, {member.mention}!")


@bot.event
async def on_message(message):
    if message.author.bot or message.guild is None:
        await bot.process_commands(message)
        return

    now = datetime.datetime.utcnow().timestamp()
    key = (message.author.id, message.guild.id)
    last_time = xp_cooldowns.get(key, 0)

    if now - last_time >= XP_COOLDOWN_SECONDS:
        xp_cooldowns[key] = now
        amount = random.randint(XP_MIN, XP_MAX)
        new_level, leveled_up = add_xp(message.author.id, message.guild.id, amount)
        if leveled_up:
            await message.channel.send(f"🎉 {message.author.mention} leveled up to **Level {new_level}**!")

    await bot.process_commands(message)


# ---------------- Basic commands ----------------

@bot.hybrid_command(description="Check if the bot is alive")
async def hello(ctx):
    await ctx.send(f"Hey {ctx.author.mention}! I'm alive 🎉")


@bot.hybrid_command(description="Check the bot's latency")
async def ping(ctx):
    latency_ms = round(bot.latency * 1000)
    await ctx.send(f"🏓 Pong! {latency_ms}ms")


@bot.hybrid_command(description="Roll a dice")
async def roll(ctx, sides: int = 6):
    result = random.randint(1, sides)
    await ctx.send(f"🎲 You rolled a {result} (out of {sides})")


@bot.hybrid_command(description="Show a user's avatar")
async def avatar(ctx, member: discord.Member = None):
    member = member or ctx.author
    await ctx.send(member.display_avatar.url)


# ---------------- Leveling commands ----------------

@bot.hybrid_command(description="Check your (or someone else's) level and XP")
async def rank(ctx, member: discord.Member = None):
    member = member or ctx.author
    xp, level = get_user(member.id, ctx.guild.id)
    needed = xp_needed_for_level(level)
    await ctx.send(f"📊 {member.mention} is **Level {level}** — {xp}/{needed} XP to next level")


@bot.hybrid_command(description="Show the server's XP leaderboard")
async def leaderboard(ctx):
    cursor.execute(
        "SELECT user_id, xp, level FROM levels WHERE guild_id = ? ORDER BY level DESC, xp DESC LIMIT 10",
        (ctx.guild.id,)
    )
    rows = cursor.fetchall()
    if not rows:
        await ctx.send("No one has earned any XP yet — start chatting!")
        return
    lines = []
    for i, (user_id, xp, level) in enumerate(rows, start=1):
        member = ctx.guild.get_member(user_id)
        name = member.display_name if member else f"Unknown User ({user_id})"
        lines.append(f"**{i}.** {name} — Level {level} ({xp} XP)")
    embed = discord.Embed(
        title=f"🏆 {ctx.guild.name} Leaderboard",
        description="\n".join(lines),
        color=discord.Color.gold()
    )
    await ctx.send(embed=embed)


# ---------------- Music commands ----------------

@bot.hybrid_command(description="Play a song from a YouTube link, Spotify link, or search term")
async def play(ctx, *, query: str):
    if ctx.author.voice is None:
        await ctx.send("⚠️ You need to be in a voice channel first.")
        return

    voice_channel = ctx.author.voice.channel
    voice_client = ctx.guild.voice_client

    if voice_client is None:
        voice_client = await voice_channel.connect()
    elif voice_client.channel != voice_channel:
        await voice_client.move_to(voice_channel)

    await ctx.send(f"🔎 Searching for: `{query}`...")

    try:
        song = await get_song_info(query)
    except Exception as e:
        await ctx.send(f"⚠️ Couldn't find or play that: {e}")
        return

    guild_id = ctx.guild.id
    song_queues.setdefault(guild_id, []).append(song)

    if not voice_client.is_playing() and not voice_client.is_paused():
        await play_next(ctx)
    else:
        await ctx.send(f"➕ Added to queue: **{song['title']}**")


@bot.hybrid_command(description="Skip the current song")
async def skip(ctx):
    voice_client = ctx.guild.voice_client
    if voice_client is None or not voice_client.is_playing():
        await ctx.send("⚠️ Nothing is playing right now.")
        return
    voice_client.stop()  # triggers the "after" callback, which plays the next song
    await ctx.send("⏭️ Skipped.")


@bot.hybrid_command(description="Pause the current song")
async def pause(ctx):
    voice_client = ctx.guild.voice_client
    if voice_client and voice_client.is_playing():
        voice_client.pause()
        await ctx.send("⏸️ Paused.")
    else:
        await ctx.send("⚠️ Nothing is playing.")


@bot.hybrid_command(description="Resume the paused song")
async def resume(ctx):
    voice_client = ctx.guild.voice_client
    if voice_client and voice_client.is_paused():
        voice_client.resume()
        await ctx.send("▶️ Resumed.")
    else:
        await ctx.send("⚠️ Nothing is paused.")


@bot.hybrid_command(description="Stop playback and clear the queue")
async def stop(ctx):
    voice_client = ctx.guild.voice_client
    song_queues[ctx.guild.id] = []
    if voice_client:
        voice_client.stop()
    await ctx.send("⏹️ Stopped and cleared the queue.")


@bot.hybrid_command(description="Show the current song queue")
async def queue(ctx):
    songs = song_queues.get(ctx.guild.id, [])
    if not songs:
        await ctx.send("The queue is empty.")
        return
    lines = [f"{i+1}. {s['title']}" for i, s in enumerate(songs)]
    await ctx.send("📜 **Queue:**\n" + "\n".join(lines))


@bot.hybrid_command(description="Make the bot leave the voice channel")
async def leave(ctx):
    voice_client = ctx.guild.voice_client
    if voice_client:
        song_queues[ctx.guild.id] = []
        await voice_client.disconnect()
        await ctx.send("👋 Left the voice channel.")
    else:
        await ctx.send("⚠️ I'm not in a voice channel.")


# ---------------- Moderation commands (Owner/Admin roles only) ----------------

@bot.hybrid_command(description="Kick a member from the server")
@is_mod()
async def kick(ctx, member: discord.Member, *, reason: str = "No reason provided"):
    await member.kick(reason=reason)
    await ctx.send(f"👢 {member.mention} was kicked. Reason: {reason}")


@bot.hybrid_command(description="Ban a member from the server")
@is_mod()
async def ban(ctx, member: discord.Member, *, reason: str = "No reason provided"):
    await member.ban(reason=reason)
    await ctx.send(f"🔨 {member.mention} was banned. Reason: {reason}")


@bot.hybrid_command(description="Unban a member by username or ID")
@is_mod()
async def unban(ctx, *, user_input: str):
    banned_users = [entry async for entry in ctx.guild.bans()]
    if user_input.isdigit():
        target = discord.utils.find(lambda e: str(e.user.id) == user_input, banned_users)
    else:
        target = discord.utils.find(lambda e: str(e.user) == user_input, banned_users)
    if target is None:
        await ctx.send("⚠️ Couldn't find that user in the ban list.")
        return
    await ctx.guild.unban(target.user)
    await ctx.send(f"✅ Unbanned {target.user}")


@bot.hybrid_command(description="Timeout (mute) a member for N minutes")
@is_mod()
async def mute(ctx, member: discord.Member, minutes: int, *, reason: str = "No reason provided"):
    duration = datetime.timedelta(minutes=minutes)
    await member.timeout(duration, reason=reason)
    await ctx.send(f"🔇 {member.mention} was muted for {minutes} minute(s). Reason: {reason}")


@bot.hybrid_command(description="Remove a timeout from a member")
@is_mod()
async def unmute(ctx, member: discord.Member):
    await member.timeout(None)
    await ctx.send(f"🔊 {member.mention} was unmuted.")


@bot.hybrid_command(description="Delete the last N messages in this channel")
@is_mod()
async def clear(ctx, amount: int = 5):
    deleted = await ctx.channel.purge(limit=amount + 1)
    msg = await ctx.send(f"🧹 Deleted {len(deleted) - 1} message(s).")
    await msg.delete(delay=3)


@bot.hybrid_command(description="Send a warning DM to a member")
@is_mod()
async def warn(ctx, member: discord.Member, *, reason: str = "No reason provided"):
    try:
        await member.send(f"⚠️ You have been warned in **{ctx.guild.name}**. Reason: {reason}")
        await ctx.send(f"✅ Warned {member.mention}.")
    except discord.Forbidden:
        await ctx.send(f"⚠️ Could not DM {member.mention} (they may have DMs disabled). Warning logged here instead: {reason}")


# ---------------- Error handling ----------------

@bot.event
async def on_command_error(ctx, error):
    if isinstance(error, commands.CheckFailure):
        return
    if isinstance(error, commands.MissingRequiredArgument):
        await ctx.send(f"⚠️ Missing argument: `{error.param.name}`. Check command usage.")
    elif isinstance(error, commands.MemberNotFound):
        await ctx.send("⚠️ Couldn't find that member.")
    elif isinstance(error, commands.BadArgument):
        await ctx.send("⚠️ Invalid argument provided.")
    else:
        print(f"Unhandled error: {error}")
        await ctx.send("⚠️ Something went wrong running that command.")


# Run the bot
bot.run(TOKEN)
