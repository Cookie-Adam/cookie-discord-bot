import os
import re
import io
import random
import asyncio
import datetime
import hashlib
import sqlite3
import discord
import aiohttp
from discord.ext import commands
from dotenv import load_dotenv
from PIL import Image, ImageDraw
import yt_dlp
import imageio_ffmpeg

# Load token from .env file
load_dotenv()
TOKEN = os.getenv("DISCORD_TOKEN")
YOUTUBE_COOKIES = os.getenv("YOUTUBE_COOKIES")

COOKIES_PATH = "cookies.txt"
if YOUTUBE_COOKIES:
    with open(COOKIES_PATH, "w", encoding="utf-8") as f:
        f.write(YOUTUBE_COOKIES)

FFMPEG_EXECUTABLE = imageio_ffmpeg.get_ffmpeg_exe()

# ---- Set the exact names of roles allowed to use moderation commands ----
MOD_ROLE_NAMES = ["🌟 𝒪𝓌𝓃𝑒𝓇 ✨👑", "🌸 𝒜𝒹𝓂𝒾𝓃 🛠️💼"]

# Set up intents (permissions for what the bot can "see")
intents = discord.Intents.default()
intents.message_content = True
intents.members = True

bot = commands.Bot(command_prefix="!", intents=intents)
http_session: aiohttp.ClientSession = None  # created on startup

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
cursor.execute("""
    CREATE TABLE IF NOT EXISTS reaction_roles (
        message_id INTEGER,
        emoji TEXT,
        role_id INTEGER,
        guild_id INTEGER,
        PRIMARY KEY (message_id, emoji)
    )
""")
db.commit()

xp_cooldowns = {}
XP_COOLDOWN_SECONDS = 60
XP_MIN = 15
XP_MAX = 25

# ---------------- Color scheme ----------------
COLOR_PRIMARY = discord.Color.from_rgb(255, 105, 180)
COLOR_SUCCESS = discord.Color.from_rgb(87, 242, 135)
COLOR_WARNING = discord.Color.from_rgb(255, 200, 60)
COLOR_DANGER = discord.Color.from_rgb(237, 66, 69)
COLOR_MUSIC = discord.Color.from_rgb(88, 101, 242)
COLOR_FUN = discord.Color.from_rgb(255, 71, 141)


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


def make_progress_bar(current: int, total: int, length: int = 12) -> str:
    filled = int(length * current / total) if total > 0 else 0
    filled = max(0, min(filled, length))
    return "■" * filled + "□" * (length - filled)


def simple_embed(description: str, color=COLOR_PRIMARY, title: str = None):
    embed = discord.Embed(description=description, color=color)
    if title:
        embed.title = title
    return embed


def is_mod():
    async def predicate(ctx):
        user_role_names = [role.name for role in ctx.author.roles]
        if any(name in user_role_names for name in MOD_ROLE_NAMES):
            return True
        await ctx.send(embed=simple_embed("🚫 You don't have permission to use this command.", COLOR_DANGER))
        return False
    return commands.check(predicate)


# ---------------- Reaction role helpers ----------------

def add_reaction_role(message_id: int, emoji: str, role_id: int, guild_id: int):
    cursor.execute(
        "INSERT OR REPLACE INTO reaction_roles (message_id, emoji, role_id, guild_id) VALUES (?, ?, ?, ?)",
        (message_id, emoji, role_id, guild_id)
    )
    db.commit()


def remove_reaction_role(message_id: int, emoji: str):
    cursor.execute("DELETE FROM reaction_roles WHERE message_id = ? AND emoji = ?", (message_id, emoji))
    db.commit()


def get_reaction_role(message_id: int, emoji: str):
    cursor.execute("SELECT role_id FROM reaction_roles WHERE message_id = ? AND emoji = ?", (message_id, emoji))
    row = cursor.fetchone()
    return row[0] if row else None


def get_reaction_roles_for_message(message_id: int):
    cursor.execute("SELECT emoji, role_id FROM reaction_roles WHERE message_id = ?", (message_id,))
    return cursor.fetchall()


# ---------------- Music setup ----------------

YTDL_OPTS = {
    "format": "bestaudio/best",
    "noplaylist": True,
    "quiet": True,
    "default_search": "ytsearch",
    "source_address": "0.0.0.0",
}

if YOUTUBE_COOKIES:
    YTDL_OPTS["cookiefile"] = COOKIES_PATH
else:
    YTDL_OPTS["extractor_args"] = {
        "youtube": {
            "player_client": ["android", "ios"],
        }
    }

FFMPEG_OPTS = {
    "before_options": "-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5",
    "options": "-vn",
}

ytdl = yt_dlp.YoutubeDL(YTDL_OPTS)
song_queues = {}
SPOTIFY_URL_PATTERN = re.compile(r"open\.spotify\.com/(track|album|playlist)/([A-Za-z0-9]+)")


def resolve_query(query: str) -> str:
    if SPOTIFY_URL_PATTERN.search(query):
        return "spotify track (search fallback) " + query
    return query


async def get_song_info(query: str):
    loop = asyncio.get_event_loop()
    query = resolve_query(query)

    def extract():
        info = ytdl.extract_info(query, download=False)
        if "entries" in info:
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
    source = discord.FFmpegPCMAudio(song["url"], executable=FFMPEG_EXECUTABLE, **FFMPEG_OPTS)

    def after_playing(error):
        if error:
            print(f"Player error: {error}")
        fut = asyncio.run_coroutine_threadsafe(play_next(ctx), bot.loop)
        try:
            fut.result()
        except Exception as e:
            print(f"Error advancing queue: {e}")

    voice_client.play(source, after=after_playing)
    embed = discord.Embed(title="🎶 Now Playing", description=f"**{song['title']}**", color=COLOR_MUSIC)
    if song.get("webpage_url"):
        embed.url = song["webpage_url"]
    asyncio.create_task(ctx.send(embed=embed))


# ==================== FUN / PERSONALITY HELPERS ====================

async def fetch_image_bytes(url: str) -> bytes:
    async with http_session.get(url) as resp:
        resp.raise_for_status()
        return await resp.read()


def generate_petpet_gif(avatar_bytes: bytes) -> io.BytesIO:
    """
    Builds a petpet-style GIF entirely in-process with Pillow (no external API
    dependency). The avatar squishes vertically across frames while a simple
    drawn hand descends onto it, mimicking the classic 'petpet' meme.
    """
    size = 120
    avatar = Image.open(io.BytesIO(avatar_bytes)).convert("RGBA").resize((size, size))

    # Squish factors per frame (0 = start, mid = most squished, end = back to normal)
    squish_factors = [1.0, 0.85, 0.7, 0.85, 1.0]
    frames = []

    for factor in squish_factors:
        frame = Image.new("RGBA", (size, size), (0, 0, 0, 0))
        new_height = int(size * factor)
        squished_avatar = avatar.resize((size, new_height))
        y_offset = size - new_height
        frame.paste(squished_avatar, (0, y_offset), squished_avatar)

        # Draw a simple stylized "hand" (rounded shape) descending onto the avatar
        draw = ImageDraw.Draw(frame)
        hand_y = int(y_offset * 0.6)
        hand_height = 26
        draw.ellipse(
            [size * 0.15, hand_y, size * 0.85, hand_y + hand_height],
            fill=(255, 224, 189, 255),
            outline=(120, 90, 60, 255),
            width=3
        )
        # little "fingers"
        for fx in (0.25, 0.45, 0.65):
            draw.ellipse(
                [size * fx, hand_y - 8, size * fx + 14, hand_y + 12],
                fill=(255, 224, 189, 255),
                outline=(120, 90, 60, 255),
                width=2
            )
        frames.append(frame.convert("RGB"))

    buffer = io.BytesIO()
    frames[0].save(
        buffer, format="GIF", save_all=True, append_images=frames[1:],
        duration=90, loop=0
    )
    buffer.seek(0)
    return buffer


def draw_heart(draw: ImageDraw.ImageDraw, cx: int, cy: int, size: int, color):
    """Draws a simple filled heart centered at (cx, cy)."""
    r = size // 2
    draw.ellipse([cx - r, cy - r, cx, cy + r // 2], fill=color)
    draw.ellipse([cx, cy - r, cx + r, cy + r // 2], fill=color)
    draw.polygon([(cx - r, cy + r // 4), (cx + r, cy + r // 4), (cx, cy + r + r)], fill=color)


def generate_ship_image(avatar1_bytes: bytes, avatar2_bytes: bytes) -> io.BytesIO:
    """Combines two avatars side by side with a heart between them."""
    size = 200
    padding = 20
    heart_zone = 80
    canvas_w = size * 2 + heart_zone
    canvas_h = size

    canvas = Image.new("RGBA", (canvas_w, canvas_h), (0, 0, 0, 0))

    avatar1 = Image.open(io.BytesIO(avatar1_bytes)).convert("RGBA").resize((size, size))
    avatar2 = Image.open(io.BytesIO(avatar2_bytes)).convert("RGBA").resize((size, size))

    canvas.paste(avatar1, (0, 0), avatar1)
    canvas.paste(avatar2, (size + heart_zone, 0), avatar2)

    draw = ImageDraw.Draw(canvas)
    draw_heart(draw, size + heart_zone // 2, canvas_h // 2, 50, (255, 71, 141, 255))

    buffer = io.BytesIO()
    canvas.convert("RGB").save(buffer, format="PNG")
    buffer.seek(0)
    return buffer


def ship_percentage(id1: int, id2: int) -> int:
    """Deterministic 'random' percentage so the same pair always gets the same result."""
    combined = f"{min(id1, id2)}-{max(id1, id2)}"
    digest = hashlib.sha256(combined.encode()).hexdigest()
    return int(digest, 16) % 101


SHIP_TIERS = [
    (0, 15, "💀 Certified disaster. Please seek professional help."),
    (16, 30, "😬 Rocky at best. It might work on paper, not in real life."),
    (31, 50, "🤷 Coinflip energy. Could go either way."),
    (51, 70, "💛 There's something here. Cautiously optimistic."),
    (71, 85, "💖 Genuinely cute. This could actually happen."),
    (86, 95, "🔥 Soulmates. The universe is trying to tell you something."),
    (96, 100, "💍 Married in another timeline. Might already be happening."),
]

ROAST_LINES = [
    "you have the personality of a loading screen.",
    "you're the reason the mute button exists.",
    "even your shadow looks tired of you.",
    "you bring everyone so much joy... when you leave the call.",
    "you're proof that evolution can go in reverse.",
    "I've seen more effort in a school group project you didn't do.",
    "your aim is so bad, the enemy team feels safe.",
    "you're like a Wi-Fi signal — weak and constantly dropping.",
]

COMPLIMENT_LINES = [
    "genuinely one of the good ones, don't ever change.",
    "your energy could power a small city.",
    "you make this server better just by being in it.",
    "certified vibe curator, no notes.",
    "if kindness had a face, it'd probably look like yours.",
    "you're the plot twist nobody saw coming — in a good way.",
]

FIGHT_MOVES = [
    "throws a devastating haymaker",
    "lands a critical hit out of nowhere",
    "trips over their own feet but somehow recovers",
    "unleashes a flurry of attacks",
    "calls in a surprise ally for backup",
    "uses forbidden technique: rage quit threats",
]


async def fetch_reaction_gif(reaction: str) -> str:
    """Uses the free, no-key otakugifs API for anime hug/kiss reaction gifs."""
    url = f"https://api.otakugifs.xyz/gif?reaction={reaction}"
    async with http_session.get(url) as resp:
        data = await resp.json()
        return data["url"]


# ==================== EVENTS ====================

@bot.event
async def on_ready():
    global http_session
    if http_session is None:
        http_session = aiohttp.ClientSession()
    print(f"✅ Logged in as {bot.user} (ID: {bot.user.id})")
    print(f"🎬 Using FFmpeg at: {FFMPEG_EXECUTABLE}")
    print(f"🍪 YouTube cookies loaded: {bool(YOUTUBE_COOKIES)}")
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
        embed = discord.Embed(
            title="👋 Welcome!",
            description=f"Welcome to the server, {member.mention}! We're glad you're here.",
            color=COLOR_SUCCESS
        )
        embed.set_thumbnail(url=member.display_avatar.url)
        embed.set_footer(text=f"Member #{member.guild.member_count}")
        await channel.send(embed=embed)


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
            embed = discord.Embed(
                description=f"🎉 {message.author.mention} leveled up to **Level {new_level}**!",
                color=COLOR_PRIMARY
            )
            embed.set_thumbnail(url=message.author.display_avatar.url)
            await message.channel.send(embed=embed)

    await bot.process_commands(message)


@bot.event
async def on_raw_reaction_add(payload: discord.RawReactionActionEvent):
    if payload.member is None or payload.member.bot:
        return
    emoji_str = str(payload.emoji)
    role_id = get_reaction_role(payload.message_id, emoji_str)
    if role_id is None:
        return
    guild = bot.get_guild(payload.guild_id)
    role = guild.get_role(role_id)
    if role is None:
        return
    try:
        await payload.member.add_roles(role, reason="Reaction role")
    except discord.Forbidden:
        pass


@bot.event
async def on_raw_reaction_remove(payload: discord.RawReactionActionEvent):
    emoji_str = str(payload.emoji)
    role_id = get_reaction_role(payload.message_id, emoji_str)
    if role_id is None:
        return
    guild = bot.get_guild(payload.guild_id)
    if guild is None:
        return
    member = guild.get_member(payload.user_id)
    if member is None or member.bot:
        return
    role = guild.get_role(role_id)
    if role is None:
        return
    try:
        await member.remove_roles(role, reason="Reaction role removed")
    except discord.Forbidden:
        pass


# ==================== BASIC COMMANDS ====================

@bot.hybrid_command(description="Check if the bot is alive")
async def hello(ctx):
    await ctx.send(embed=simple_embed(f"Hey {ctx.author.mention}! I'm alive 🎉", COLOR_PRIMARY))


@bot.hybrid_command(description="Check the bot's latency")
async def ping(ctx):
    latency_ms = round(bot.latency * 1000)
    await ctx.send(embed=discord.Embed(title="🏓 Pong!", description=f"Latency: **{latency_ms}ms**", color=COLOR_PRIMARY))


@bot.hybrid_command(description="Roll a dice")
async def roll(ctx, sides: int = 6):
    result = random.randint(1, sides)
    embed = discord.Embed(
        title="🎲 Dice Roll",
        description=f"{ctx.author.mention} rolled a **{result}** (out of {sides})",
        color=COLOR_PRIMARY
    )
    await ctx.send(embed=embed)


@bot.hybrid_command(description="Show a user's avatar, with a bit of attitude")
async def avatar(ctx, member: discord.Member = None):
    member = member or ctx.author
    is_compliment = random.random() < 0.55
    line = random.choice(COMPLIMENT_LINES if is_compliment else ROAST_LINES)

    embed = discord.Embed(
        title=f"{member.display_name}'s Avatar",
        description=f"{member.mention} — {line}",
        color=member.color if member.color.value != 0 else COLOR_PRIMARY
    )
    embed.set_image(url=member.display_avatar.url)

    view = discord.ui.View()
    view.add_item(discord.ui.Button(label="⬇️ Download", url=member.display_avatar.url, style=discord.ButtonStyle.link))
    await ctx.send(embed=embed, view=view)


# ==================== LEVELING COMMANDS ====================

@bot.hybrid_command(description="Check your (or someone else's) level and XP")
async def rank(ctx, member: discord.Member = None):
    member = member or ctx.author
    xp, level = get_user(member.id, ctx.guild.id)
    needed = xp_needed_for_level(level)
    bar = make_progress_bar(xp, needed)
    percent = int((xp / needed) * 100) if needed > 0 else 0

    embed = discord.Embed(
        title=f"📊 {member.display_name}'s Rank",
        color=member.color if member.color.value != 0 else COLOR_PRIMARY
    )
    embed.set_thumbnail(url=member.display_avatar.url)
    embed.add_field(name="Level", value=f"**{level}**", inline=True)
    embed.add_field(name="XP", value=f"**{xp} / {needed}**", inline=True)
    embed.add_field(name="Progress", value=f"{bar}  {percent}%", inline=False)
    await ctx.send(embed=embed)


@bot.hybrid_command(description="Show the server's XP leaderboard")
async def leaderboard(ctx):
    cursor.execute(
        "SELECT user_id, xp, level FROM levels WHERE guild_id = ? ORDER BY level DESC, xp DESC LIMIT 10",
        (ctx.guild.id,)
    )
    rows = cursor.fetchall()
    if not rows:
        await ctx.send(embed=simple_embed("No one has earned any XP yet — start chatting!", COLOR_WARNING))
        return

    medals = ["🥇", "🥈", "🥉"]
    lines = []
    top_member = None

    for i, (user_id, xp, level) in enumerate(rows, start=1):
        member = ctx.guild.get_member(user_id)
        name = member.display_name if member else f"Unknown User ({user_id})"
        if i == 1:
            top_member = member
        prefix = medals[i - 1] if i <= 3 else f"`#{i}`"
        lines.append(f"{prefix}  **{name}** — Level {level} ({xp} XP)")

    embed = discord.Embed(title=f"🏆 {ctx.guild.name} Leaderboard", description="\n".join(lines), color=COLOR_PRIMARY)
    if top_member:
        embed.set_thumbnail(url=top_member.display_avatar.url)
    embed.set_footer(text=f"Top {len(rows)} member{'s' if len(rows) != 1 else ''} by level")
    await ctx.send(embed=embed)


# ==================== FUN / LOVE COMMANDS ====================

@bot.hybrid_command(description="Ship two members and see their compatibility")
async def ship(ctx, member1: discord.Member, member2: discord.Member = None):
    member2 = member2 or ctx.author
    percent = ship_percentage(member1.id, member2.id)
    flavor = next(text for lo, hi, text in SHIP_TIERS if lo <= percent <= hi)

    try:
        avatar1_bytes = await fetch_image_bytes(str(member1.display_avatar.url))
        avatar2_bytes = await fetch_image_bytes(str(member2.display_avatar.url))
        loop = asyncio.get_event_loop()
        image_buffer = await loop.run_in_executor(None, generate_ship_image, avatar1_bytes, avatar2_bytes)
        file = discord.File(image_buffer, filename="ship.png")

        embed = discord.Embed(
            title=f"💘 {member1.display_name} × {member2.display_name}",
            description=f"**{percent}%** compatible\n{flavor}",
            color=COLOR_FUN
        )
        embed.set_image(url="attachment://ship.png")
        await ctx.send(embed=embed, file=file)
    except Exception as e:
        print(f"Ship image error: {e}")
        embed = discord.Embed(
            title=f"💘 {member1.display_name} × {member2.display_name}",
            description=f"**{percent}%** compatible\n{flavor}",
            color=COLOR_FUN
        )
        await ctx.send(embed=embed)


@bot.hybrid_command(description="Pet someone's avatar")
async def pet(ctx, member: discord.Member = None):
    member = member or ctx.author
    try:
        avatar_bytes = await fetch_image_bytes(str(member.display_avatar.url))
        loop = asyncio.get_event_loop()
        gif_buffer = await loop.run_in_executor(None, generate_petpet_gif, avatar_bytes)
        file = discord.File(gif_buffer, filename="pet.gif")

        embed = discord.Embed(
            description=f"{ctx.author.mention} is petting {member.mention} 🖐️",
            color=COLOR_FUN
        )
        embed.set_image(url="attachment://pet.gif")
        await ctx.send(embed=embed, file=file)
    except Exception as e:
        print(f"Pet gif error: {e}")
        await ctx.send(embed=simple_embed(f"{ctx.author.mention} is petting {member.mention} 🖐️", COLOR_FUN))


@bot.hybrid_command(description="Hug someone")
async def hug(ctx, member: discord.Member):
    embed = discord.Embed(
        description=f"{ctx.author.mention} hugs {member.mention} 🤗",
        color=COLOR_FUN
    )
    try:
        gif_url = await fetch_reaction_gif("hug")
        embed.set_image(url=gif_url)
    except Exception as e:
        print(f"Hug gif error: {e}")
    await ctx.send(embed=embed)


@bot.hybrid_command(description="Kiss someone")
async def kiss(ctx, member: discord.Member):
    embed = discord.Embed(
        description=f"{ctx.author.mention} kisses {member.mention} 💋",
        color=COLOR_FUN
    )
    try:
        gif_url = await fetch_reaction_gif("kiss")
        embed.set_image(url=gif_url)
    except Exception as e:
        print(f"Kiss gif error: {e}")
    await ctx.send(embed=embed)


# ==================== WILDCARD COMMANDS ====================

@bot.hybrid_command(description="Roast someone (all in good fun)")
async def roast(ctx, member: discord.Member):
    line = random.choice(ROAST_LINES)
    embed = discord.Embed(
        description=f"{member.mention}, {line}",
        color=COLOR_DANGER
    )
    embed.set_thumbnail(url=member.display_avatar.url)
    await ctx.send(embed=embed)


@bot.hybrid_command(description="Simulate an epic fight between two members")
async def fight(ctx, member1: discord.Member, member2: discord.Member):
    winner = random.choice([member1, member2])
    loser = member2 if winner == member1 else member1
    move1 = random.choice(FIGHT_MOVES)
    move2 = random.choice(FIGHT_MOVES)

    embed = discord.Embed(
        title="⚔️ FIGHT!",
        description=(
            f"{member1.mention} {move1}!\n"
            f"{member2.mention} {move2}!\n\n"
            f"🏆 **{winner.display_name}** wins! {loser.display_name} has been defeated."
        ),
        color=COLOR_DANGER
    )
    embed.set_thumbnail(url=winner.display_avatar.url)
    await ctx.send(embed=embed)


@bot.hybrid_command(description="Rate how much one member simps for another")
async def simprate(ctx, member1: discord.Member, member2: discord.Member = None):
    member2 = member2 or ctx.author
    percent = ship_percentage(member1.id + 1, member2.id + 1)  # offset so it differs from /ship

    if percent < 20:
        flavor = "Doesn't even know they exist. Zero simp detected."
    elif percent < 50:
        flavor = "A little smitten, nothing serious yet."
    elif percent < 80:
        flavor = "Certified simp behavior. It's giving obsessed."
    else:
        flavor = "Would drop everything, cancel plans, and change their whole personality. Full simp."

    embed = discord.Embed(
        title=f"💗 Simp Meter: {member1.display_name} → {member2.display_name}",
        description=f"**{percent}%**\n{flavor}",
        color=COLOR_FUN
    )
    embed.set_thumbnail(url=member1.display_avatar.url)
    await ctx.send(embed=embed)


# ==================== MUSIC COMMANDS ====================

@bot.hybrid_command(description="Play a song from a YouTube link, Spotify link, or search term")
async def play(ctx, *, query: str):
    if ctx.author.voice is None:
        await ctx.send(embed=simple_embed("⚠️ You need to be in a voice channel first.", COLOR_WARNING))
        return

    voice_channel = ctx.author.voice.channel
    voice_client = ctx.guild.voice_client

    if voice_client is None:
        voice_client = await voice_channel.connect()
    elif voice_client.channel != voice_channel:
        await voice_client.move_to(voice_channel)

    await ctx.send(embed=simple_embed(f"🔎 Searching for: `{query}`...", COLOR_MUSIC))

    try:
        song = await get_song_info(query)
    except Exception as e:
        await ctx.send(embed=simple_embed(f"⚠️ Couldn't find or play that: {e}", COLOR_DANGER))
        return

    guild_id = ctx.guild.id
    song_queues.setdefault(guild_id, []).append(song)

    if not voice_client.is_playing() and not voice_client.is_paused():
        await play_next(ctx)
    else:
        embed = discord.Embed(title="➕ Added to Queue", description=f"**{song['title']}**", color=COLOR_MUSIC)
        await ctx.send(embed=embed)


@bot.hybrid_command(description="Skip the current song")
async def skip(ctx):
    voice_client = ctx.guild.voice_client
    if voice_client is None or not voice_client.is_playing():
        await ctx.send(embed=simple_embed("⚠️ Nothing is playing right now.", COLOR_WARNING))
        return
    voice_client.stop()
    await ctx.send(embed=simple_embed("⏭️ Skipped.", COLOR_MUSIC))


@bot.hybrid_command(description="Pause the current song")
async def pause(ctx):
    voice_client = ctx.guild.voice_client
    if voice_client and voice_client.is_playing():
        voice_client.pause()
        await ctx.send(embed=simple_embed("⏸️ Paused.", COLOR_MUSIC))
    else:
        await ctx.send(embed=simple_embed("⚠️ Nothing is playing.", COLOR_WARNING))


@bot.hybrid_command(description="Resume the paused song")
async def resume(ctx):
    voice_client = ctx.guild.voice_client
    if voice_client and voice_client.is_paused():
        voice_client.resume()
        await ctx.send(embed=simple_embed("▶️ Resumed.", COLOR_MUSIC))
    else:
        await ctx.send(embed=simple_embed("⚠️ Nothing is paused.", COLOR_WARNING))


@bot.hybrid_command(description="Stop playback and clear the queue")
async def stop(ctx):
    voice_client = ctx.guild.voice_client
    song_queues[ctx.guild.id] = []
    if voice_client:
        voice_client.stop()
    await ctx.send(embed=simple_embed("⏹️ Stopped and cleared the queue.", COLOR_MUSIC))


@bot.hybrid_command(description="Show the current song queue")
async def queue(ctx):
    songs = song_queues.get(ctx.guild.id, [])
    if not songs:
        await ctx.send(embed=simple_embed("The queue is empty.", COLOR_WARNING))
        return
    lines = [f"{i+1}. {s['title']}" for i, s in enumerate(songs)]
    embed = discord.Embed(title="📜 Song Queue", description="\n".join(lines), color=COLOR_MUSIC)
    await ctx.send(embed=embed)


@bot.hybrid_command(description="Make the bot leave the voice channel")
async def leave(ctx):
    voice_client = ctx.guild.voice_client
    if voice_client:
        song_queues[ctx.guild.id] = []
        await voice_client.disconnect()
        await ctx.send(embed=simple_embed("👋 Left the voice channel.", COLOR_MUSIC))
    else:
        await ctx.send(embed=simple_embed("⚠️ I'm not in a voice channel.", COLOR_WARNING))


# ==================== REACTION ROLE COMMANDS ====================

@bot.hybrid_command(description="Set up a reaction role: react to a message with an emoji to get a role")
@is_mod()
async def reactionrole(ctx, message_id: str, emoji: str, role: discord.Role):
    try:
        message_id_int = int(message_id)
    except ValueError:
        await ctx.send(embed=simple_embed("⚠️ That doesn't look like a valid message ID.", COLOR_WARNING))
        return
    try:
        target_message = await ctx.channel.fetch_message(message_id_int)
    except discord.NotFound:
        await ctx.send(embed=simple_embed("⚠️ Couldn't find a message with that ID in this channel.", COLOR_WARNING))
        return
    try:
        await target_message.add_reaction(emoji)
    except discord.HTTPException:
        await ctx.send(embed=simple_embed("⚠️ Couldn't react with that emoji — make sure it's a valid emoji the bot can use.", COLOR_WARNING))
        return
    add_reaction_role(message_id_int, emoji, role.id, ctx.guild.id)
    embed = discord.Embed(
        title="✅ Reaction Role Set",
        description=f"Reacting with {emoji} on that message now grants **{role.name}**.",
        color=COLOR_SUCCESS
    )
    await ctx.send(embed=embed)


@bot.hybrid_command(description="Remove a reaction role from a message")
@is_mod()
async def removereactionrole(ctx, message_id: str, emoji: str):
    try:
        message_id_int = int(message_id)
    except ValueError:
        await ctx.send(embed=simple_embed("⚠️ That doesn't look like a valid message ID.", COLOR_WARNING))
        return
    remove_reaction_role(message_id_int, emoji)
    await ctx.send(embed=simple_embed(f"✅ Removed the reaction role for {emoji} on that message.", COLOR_SUCCESS))


@bot.hybrid_command(description="List all reaction roles set up on a message")
async def listreactionroles(ctx, message_id: str):
    try:
        message_id_int = int(message_id)
    except ValueError:
        await ctx.send(embed=simple_embed("⚠️ That doesn't look like a valid message ID.", COLOR_WARNING))
        return
    rows = get_reaction_roles_for_message(message_id_int)
    if not rows:
        await ctx.send(embed=simple_embed("No reaction roles set up on that message.", COLOR_WARNING))
        return
    lines = []
    for emoji, role_id in rows:
        role = ctx.guild.get_role(role_id)
        role_name = role.name if role else f"Unknown Role ({role_id})"
        lines.append(f"{emoji} → {role_name}")
    embed = discord.Embed(title="🎭 Reaction Roles", description="\n".join(lines), color=COLOR_PRIMARY)
    await ctx.send(embed=embed)


# ==================== MODERATION COMMANDS ====================

@bot.hybrid_command(description="Kick a member from the server")
@is_mod()
async def kick(ctx, member: discord.Member, *, reason: str = "No reason provided"):
    await member.kick(reason=reason)
    embed = discord.Embed(title="👢 Member Kicked", color=COLOR_DANGER)
    embed.set_thumbnail(url=member.display_avatar.url)
    embed.add_field(name="Member", value=member.mention, inline=True)
    embed.add_field(name="Reason", value=reason, inline=True)
    await ctx.send(embed=embed)


@bot.hybrid_command(description="Ban a member from the server")
@is_mod()
async def ban(ctx, member: discord.Member, *, reason: str = "No reason provided"):
    await member.ban(reason=reason)
    embed = discord.Embed(title="🔨 Member Banned", color=COLOR_DANGER)
    embed.set_thumbnail(url=member.display_avatar.url)
    embed.add_field(name="Member", value=member.mention, inline=True)
    embed.add_field(name="Reason", value=reason, inline=True)
    await ctx.send(embed=embed)


@bot.hybrid_command(description="Unban a member by username or ID")
@is_mod()
async def unban(ctx, *, user_input: str):
    banned_users = [entry async for entry in ctx.guild.bans()]
    if user_input.isdigit():
        target = discord.utils.find(lambda e: str(e.user.id) == user_input, banned_users)
    else:
        target = discord.utils.find(lambda e: str(e.user) == user_input, banned_users)
    if target is None:
        await ctx.send(embed=simple_embed("⚠️ Couldn't find that user in the ban list.", COLOR_WARNING))
        return
    await ctx.guild.unban(target.user)
    await ctx.send(embed=discord.Embed(title="✅ Member Unbanned", description=str(target.user), color=COLOR_SUCCESS))


@bot.hybrid_command(description="Timeout (mute) a member for N minutes")
@is_mod()
async def mute(ctx, member: discord.Member, minutes: int, *, reason: str = "No reason provided"):
    duration = datetime.timedelta(minutes=minutes)
    await member.timeout(duration, reason=reason)
    embed = discord.Embed(title="🔇 Member Muted", color=COLOR_DANGER)
    embed.set_thumbnail(url=member.display_avatar.url)
    embed.add_field(name="Member", value=member.mention, inline=True)
    embed.add_field(name="Duration", value=f"{minutes} minute(s)", inline=True)
    embed.add_field(name="Reason", value=reason, inline=False)
    await ctx.send(embed=embed)


@bot.hybrid_command(description="Remove a timeout from a member")
@is_mod()
async def unmute(ctx, member: discord.Member):
    await member.timeout(None)
    embed = discord.Embed(title="🔊 Member Unmuted", description=member.mention, color=COLOR_SUCCESS)
    embed.set_thumbnail(url=member.display_avatar.url)
    await ctx.send(embed=embed)


@bot.hybrid_command(description="Delete the last N messages in this channel")
@is_mod()
async def clear(ctx, amount: int = 5):
    deleted = await ctx.channel.purge(limit=amount + 1)
    msg = await ctx.send(embed=simple_embed(f"🧹 Deleted {len(deleted) - 1} message(s).", COLOR_SUCCESS))
    await msg.delete(delay=3)


@bot.hybrid_command(description="Send a warning DM to a member")
@is_mod()
async def warn(ctx, member: discord.Member, *, reason: str = "No reason provided"):
    dm_embed = discord.Embed(title="⚠️ You have been warned", description=f"In **{ctx.guild.name}**", color=COLOR_WARNING)
    dm_embed.add_field(name="Reason", value=reason)
    try:
        await member.send(embed=dm_embed)
        await ctx.send(embed=simple_embed(f"✅ Warned {member.mention}.", COLOR_SUCCESS))
    except discord.Forbidden:
        embed = discord.Embed(
            title="⚠️ Warning Logged (DM Failed)",
            description=f"Could not DM {member.mention} — they may have DMs disabled.",
            color=COLOR_WARNING
        )
        embed.add_field(name="Reason", value=reason)
        await ctx.send(embed=embed)


# ==================== ERROR HANDLING ====================

@bot.event
async def on_command_error(ctx, error):
    if isinstance(error, commands.CheckFailure):
        return
    if isinstance(error, commands.MissingRequiredArgument):
        await ctx.send(embed=simple_embed(f"⚠️ Missing argument: `{error.param.name}`. Check command usage.", COLOR_WARNING))
    elif isinstance(error, commands.MemberNotFound):
        await ctx.send(embed=simple_embed("⚠️ Couldn't find that member.", COLOR_WARNING))
    elif isinstance(error, commands.RoleNotFound):
        await ctx.send(embed=simple_embed("⚠️ Couldn't find that role.", COLOR_WARNING))
    elif isinstance(error, commands.BadArgument):
        await ctx.send(embed=simple_embed("⚠️ Invalid argument provided.", COLOR_WARNING))
    else:
        print(f"Unhandled error: {error}")
        try:
            await ctx.send(embed=simple_embed("⚠️ Something went wrong running that command.", COLOR_DANGER))
        except discord.NotFound:
            pass


# Run the bot
bot.run(TOKEN)
