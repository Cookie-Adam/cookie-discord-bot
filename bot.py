import os
import re
import io
import math
import random
import asyncio
import datetime
import hashlib
import sqlite3
import discord
import aiohttp
from discord.ext import commands
from dotenv import load_dotenv
from PIL import Image, ImageDraw, ImageFont, ImageOps
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

MOD_ROLE_NAMES = ["🌟 𝒪𝓌𝓃𝑒𝓇 ✨👑", "🌸 𝒜𝒹𝓂𝒾𝓃 🛠️💼"]

intents = discord.Intents.default()
intents.message_content = True
intents.members = True

bot = commands.Bot(command_prefix="!", intents=intents)
http_session: aiohttp.ClientSession = None

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
cursor.execute("""
    CREATE TABLE IF NOT EXISTS hug_counts (
        user_id INTEGER,
        guild_id INTEGER,
        count INTEGER DEFAULT 0,
        PRIMARY KEY (user_id, guild_id)
    )
""")
cursor.execute("""
    CREATE TABLE IF NOT EXISTS kiss_counts (
        user1_id INTEGER,
        user2_id INTEGER,
        guild_id INTEGER,
        count INTEGER DEFAULT 0,
        PRIMARY KEY (user1_id, user2_id, guild_id)
    )
""")
db.commit()

xp_cooldowns = {}
XP_COOLDOWN_SECONDS = 60
XP_MIN = 15
XP_MAX = 25

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


def is_mod_member(member: discord.Member) -> bool:
    return any(role.name in MOD_ROLE_NAMES for role in member.roles)


def increment_hug_count(user_id: int, guild_id: int) -> int:
    cursor.execute("SELECT count FROM hug_counts WHERE user_id = ? AND guild_id = ?", (user_id, guild_id))
    row = cursor.fetchone()
    new_count = (row[0] if row else 0) + 1
    cursor.execute(
        "INSERT OR REPLACE INTO hug_counts (user_id, guild_id, count) VALUES (?, ?, ?)",
        (user_id, guild_id, new_count)
    )
    db.commit()
    return new_count


def get_hug_count(user_id: int, guild_id: int) -> int:
    cursor.execute("SELECT count FROM hug_counts WHERE user_id = ? AND guild_id = ?", (user_id, guild_id))
    row = cursor.fetchone()
    return row[0] if row else 0


def increment_kiss_count(id_a: int, id_b: int, guild_id: int) -> int:
    u1, u2 = min(id_a, id_b), max(id_a, id_b)
    cursor.execute("SELECT count FROM kiss_counts WHERE user1_id = ? AND user2_id = ? AND guild_id = ?", (u1, u2, guild_id))
    row = cursor.fetchone()
    new_count = (row[0] if row else 0) + 1
    cursor.execute(
        "INSERT OR REPLACE INTO kiss_counts (user1_id, user2_id, guild_id, count) VALUES (?, ?, ?, ?)",
        (u1, u2, guild_id, new_count)
    )
    db.commit()
    return new_count


def get_kiss_count(id_a: int, id_b: int, guild_id: int) -> int:
    u1, u2 = min(id_a, id_b), max(id_a, id_b)
    cursor.execute("SELECT count FROM kiss_counts WHERE user1_id = ? AND user2_id = ? AND guild_id = ?", (u1, u2, guild_id))
    row = cursor.fetchone()
    return row[0] if row else 0


def ordinal(n: int) -> str:
    if 11 <= n % 100 <= 13:
        suffix = "th"
    else:
        suffix = {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
    return f"{n}{suffix}"


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


async def fetch_image_bytes(url: str) -> bytes:
    async with http_session.get(url) as resp:
        resp.raise_for_status()
        return await resp.read()


def generate_petpet_gif(avatar_bytes: bytes) -> io.BytesIO:
    size = 120
    avatar = Image.open(io.BytesIO(avatar_bytes)).convert("RGBA").resize((size, size))
    squish_factors = [1.0, 0.85, 0.7, 0.85, 1.0]
    frames = []

    for factor in squish_factors:
        frame = Image.new("RGBA", (size, size), (0, 0, 0, 0))
        new_height = int(size * factor)
        squished_avatar = avatar.resize((size, new_height))
        y_offset = size - new_height
        frame.paste(squished_avatar, (0, y_offset), squished_avatar)

        draw = ImageDraw.Draw(frame)
        hand_y = int(y_offset * 0.6)
        hand_height = 26
        draw.ellipse(
            [size * 0.15, hand_y, size * 0.85, hand_y + hand_height],
            fill=(255, 224, 189, 255), outline=(120, 90, 60, 255), width=3
        )
        for fx in (0.25, 0.45, 0.65):
            draw.ellipse(
                [size * fx, hand_y - 8, size * fx + 14, hand_y + 12],
                fill=(255, 224, 189, 255), outline=(120, 90, 60, 255), width=2
            )
        frames.append(frame.convert("RGB"))

    buffer = io.BytesIO()
    frames[0].save(buffer, format="GIF", save_all=True, append_images=frames[1:], duration=90, loop=0)
    buffer.seek(0)
    return buffer


def draw_heart(draw: ImageDraw.ImageDraw, cx: int, cy: int, size: int, color):
    r = size // 2
    draw.ellipse([cx - r, cy - r, cx, cy + r // 2], fill=color)
    draw.ellipse([cx, cy - r, cx + r, cy + r // 2], fill=color)
    draw.polygon([(cx - r, cy + r // 4), (cx + r, cy + r // 4), (cx, cy + r + r)], fill=color)


def generate_ship_image(avatar1_bytes: bytes, avatar2_bytes: bytes) -> io.BytesIO:
    size = 200
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


def generate_l_stamp(avatar_bytes: bytes) -> io.BytesIO:
    size = 200
    avatar = Image.open(io.BytesIO(avatar_bytes)).convert("RGBA").resize((size, size))
    canvas = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    canvas.paste(avatar, (0, 0), avatar)

    overlay = Image.new("RGBA", (size, size), (0, 0, 0, 90))
    canvas = Image.alpha_composite(canvas, overlay)

    draw = ImageDraw.Draw(canvas)
    thickness = 22
    draw.rectangle([40, 30, 40 + thickness, 170], fill=(220, 20, 20, 255))
    draw.rectangle([40, 170 - thickness, 160, 170], fill=(220, 20, 20, 255))

    buffer = io.BytesIO()
    canvas.convert("RGB").save(buffer, format="PNG")
    buffer.seek(0)
    return buffer


# ---------------- Welcome banner ----------------
# Fetches a couple of free Google Fonts once at startup so the welcome image
# has real bold typography instead of the tiny default bitmap font.

FONT_DIR = "fonts"
BANGERS_URL = "https://raw.githubusercontent.com/google/fonts/main/ofl/bangers/Bangers-Regular.ttf"
POPPINS_URL = "https://raw.githubusercontent.com/google/fonts/main/ofl/poppins/Poppins-SemiBold.ttf"
BANGERS_PATH = os.path.join(FONT_DIR, "Bangers-Regular.ttf")
POPPINS_PATH = os.path.join(FONT_DIR, "Poppins-SemiBold.ttf")

WELCOME_BG_TOP = (70, 20, 110)
WELCOME_BG_BOTTOM = (255, 71, 141)


async def ensure_fonts():
    os.makedirs(FONT_DIR, exist_ok=True)
    for url, path in [(BANGERS_URL, BANGERS_PATH), (POPPINS_URL, POPPINS_PATH)]:
        if not os.path.exists(path):
            try:
                data = await fetch_image_bytes(url)
                with open(path, "wb") as f:
                    f.write(data)
            except Exception as e:
                print(f"⚠️ Failed to download font {url}: {e}")


def load_font(path, size):
    try:
        return ImageFont.truetype(path, size)
    except Exception:
        return ImageFont.load_default()


def generate_welcome_image(avatar_bytes: bytes, display_name: str, member_count: int) -> io.BytesIO:
    W, H = 1000, 560

    # Diagonal gradient background
    grad = Image.linear_gradient("L").resize((W, H))
    grad = grad.rotate(25, expand=True).resize((W, H))
    bg = ImageOps.colorize(grad, black=WELCOME_BG_TOP, white=WELCOME_BG_BOTTOM).convert("RGBA")

    # Scattered translucent "confetti" circles for texture
    overlay = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    odraw = ImageDraw.Draw(overlay)
    for cx, cy, r, alpha in [(110, 90, 55, 28), (900, 420, 95, 22), (860, 70, 40, 35),
                              (60, 460, 65, 20), (500, 30, 25, 30), (950, 250, 30, 25)]:
        odraw.ellipse([cx - r, cy - r, cx + r, cy + r], fill=(255, 255, 255, alpha))
    bg = Image.alpha_composite(bg, overlay)

    draw = ImageDraw.Draw(bg)
    bangers_big = load_font(BANGERS_PATH, 76)
    poppins_small = load_font(POPPINS_PATH, 32)
    poppins_name = load_font(POPPINS_PATH, 44)
    poppins_footer = load_font(POPPINS_PATH, 24)

    def centered_text(y, text, font, fill=(255, 255, 255, 255), stroke_width=4, stroke_fill=(0, 0, 0, 160)):
        bbox = draw.textbbox((0, 0), text, font=font, stroke_width=stroke_width)
        w = bbox[2] - bbox[0]
        draw.text(((W - w) / 2, y), text, font=font, fill=fill, stroke_width=stroke_width, stroke_fill=stroke_fill)

    centered_text(30, "WELCOME TO", poppins_small, stroke_width=3)
    centered_text(70, "THE CHOOMS", bangers_big, stroke_width=6)

    # Circular avatar with a ring border
    avatar_size = 200
    avatar = Image.open(io.BytesIO(avatar_bytes)).convert("RGBA").resize((avatar_size, avatar_size))
    mask = Image.new("L", (avatar_size, avatar_size), 0)
    ImageDraw.Draw(mask).ellipse([0, 0, avatar_size, avatar_size], fill=255)

    ax, ay = (W - avatar_size) // 2, 190
    pad = 10
    draw.ellipse([ax - pad, ay - pad, ax + avatar_size + pad, ay + avatar_size + pad], fill=(255, 255, 255, 255))
    draw.ellipse([ax - pad + 5, ay - pad + 5, ax + avatar_size + pad - 5, ay + avatar_size + pad - 5],
                 fill=(255, 71, 141, 255))
    bg.paste(avatar, (ax, ay), mask)

    centered_text(ay + avatar_size + 18, display_name, poppins_name, stroke_width=4)
    centered_text(ay + avatar_size + 78, f"Member #{member_count}", poppins_footer, stroke_width=2)

    buffer = io.BytesIO()
    bg.convert("RGB").save(buffer, format="PNG")
    buffer.seek(0)
    return buffer


def ship_percentage(id1: int, id2: int) -> int:
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
    "you're the human equivalent of a buffering icon.",
    "you have main character energy in a background character's storyline.",
    "your rizz has a 404 error.",
    "you peaked the day you joined this server, and it's been downhill since.",
    "you're not lazy, you're just deeply committed to underachieving.",
    "if disappointment was a person, they'd still be less disappointing than you.",
    "you're the group project partner nobody wanted to text back.",
    "your last brain cell is doing overtime and still failing.",
    "you type like autocorrect gave up on you.",
    "you're the reason instructions come with pictures.",
    "even the NPCs have more personality than you.",
    "your vibe check bounced.",
    "you're proof that participation trophies went too far.",
    "you're the human version of a plot hole.",
    "you bring the same energy as a phone at 1% battery.",
    "you're not built different, you're just built wrong.",
    "you talk a big game for someone who folds under a group chat.",
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

CLAPBACK_LINES = [
    "your last argument just got dragged out back and put down.",
    "that take just got clapped so hard the echo left the chat.",
    "L taken, ratio'd, and the replay's already circulating.",
    "cooked. seasoned. served. next.",
    "that's not a comeback, that's a cry for help.",
    "the disrespect just wrote itself and you handed over the pen.",
    "somewhere a group chat just went silent out of respect.",
]

EXPOSED_CRIMES = [
    "using the last of the hot water and lying about it",
    "muting on voice chat but still typing like everyone can hear the keyboard",
    "liking their own comment to make it look like someone agreed",
    "claiming they '5-0'd the lobby' with zero screen recording evidence",
    "still owing everyone money from three server events ago",
    "changing their Discord name every 6 hours for attention",
    "pretending to read the group chat then asking what happened",
    "ghosting the group project then resurfacing for the credit",
]


async def fetch_reaction_gif(reaction: str) -> str:
    url = f"https://api.otakugifs.xyz/gif?reaction={reaction}"
    async with http_session.get(url) as resp:
        data = await resp.json()
        return data["url"]


MIN_PLAYERS = 4
MAX_PLAYERS = 10
SUBMIT_WINDOW_SECONDS = 60
VOTE_WINDOW_SECONDS = 10
SCENARIO_WRITE_TIMEOUT = 120

MEDIA_URL_PATTERN = re.compile(r"(https?://\S+\.(?:gif|png|jpe?g|webp))(?:\?\S*)?", re.IGNORECASE)


class MemeGame:
    def __init__(self, channel: discord.TextChannel, host: discord.Member):
        self.channel = channel
        self.host = host
        self.players = [host]
        self.state = "lobby"
        self.lobby_message: discord.Message = None
        self.turn_order = []
        self.round_number = 0
        self.current_writer = None
        self.current_scenario = None
        self.submissions = {}
        self.total_scores = {}
        self.total_likes = {}
        self.best_overall = {"likes": -1, "bytes": None, "filename": None, "user_id": None, "scenario": None}
        self.task: asyncio.Task = None


active_games = {}
lobby_watch = {}
active_votes = {}
submitting_players = {}
pending_role_menus = {}  # channel_id -> {"title":..., "description":..., "entries": [(emoji, role_id, label)]}

VOTE_UP = "👍"
VOTE_DOWN = "👎"
JOIN_EMOJI = "🎮"


def build_lobby_embed(game: MemeGame) -> discord.Embed:
    names = "\n".join(f"• {p.mention}" for p in game.players) or "No one yet."
    embed = discord.Embed(
        title="🎮 Meme Game — Lobby Open!",
        description=(
            f"React with {JOIN_EMOJI} to join!\n"
            f"Need **{MIN_PLAYERS}-{MAX_PLAYERS}** players.\n\n"
            f"**Players ({len(game.players)}/{MAX_PLAYERS}):**\n{names}\n\n"
            f"Host ({game.host.mention}) runs `!mgbegin` when ready."
        ),
        color=COLOR_FUN
    )
    return embed


def make_collage(entries) -> io.BytesIO:
    cell = 150
    pad = 12
    caption_h = 34
    columns = min(5, max(1, len(entries)))
    rows = math.ceil(len(entries) / columns)
    width = columns * (cell + pad) + pad
    height = rows * (cell + caption_h + pad) + pad

    canvas = Image.new("RGB", (width, height), (32, 34, 40))
    draw = ImageDraw.Draw(canvas)
    font = ImageFont.load_default()

    for idx, (img_bytes, caption) in enumerate(entries):
        col = idx % columns
        row = idx // columns
        x = pad + col * (cell + pad)
        y = pad + row * (cell + caption_h + pad)
        try:
            thumb = Image.open(io.BytesIO(img_bytes)).convert("RGBA").resize((cell, cell))
            canvas.paste(thumb, (x, y), thumb)
        except Exception:
            draw.rectangle([x, y, x + cell, y + cell], fill=(80, 80, 80))
        draw.text((x, y + cell + 4), caption[:22], fill=(255, 255, 255), font=font)

    buffer = io.BytesIO()
    canvas.save(buffer, format="PNG")
    buffer.seek(0)
    return buffer


async def extract_media_from_message(message: discord.Message):
    if message.attachments:
        att = message.attachments[0]
        if att.content_type and att.content_type.startswith("image"):
            try:
                data = await att.read()
                return data, att.filename
            except Exception:
                pass

    def find_embed_media(embeds):
        for embed in embeds:
            for source in (embed.image, embed.thumbnail):
                if source and source.url:
                    return source.url
            if embed.video and embed.video.url and embed.video.url.lower().endswith((".gif", ".mp4", ".webm")):
                return embed.video.url
        return None

    candidate = find_embed_media(message.embeds)
    if candidate:
        try:
            data = await fetch_image_bytes(candidate)
            filename = candidate.split("/")[-1].split("?")[0] or "submission.gif"
            return data, filename
        except Exception:
            pass

    content = message.content or ""
    match = MEDIA_URL_PATTERN.search(content)
    if match:
        url = match.group(1)
        try:
            data = await fetch_image_bytes(url)
            filename = url.split("/")[-1].split("?")[0] or "submission.gif"
            return data, filename
        except Exception:
            pass

    if content.strip():
        await asyncio.sleep(1.5)
        try:
            fresh = await message.channel.fetch_message(message.id)
        except discord.HTTPException:
            fresh = message
        candidate = find_embed_media(fresh.embeds)
        if candidate:
            try:
                data = await fetch_image_bytes(candidate)
                filename = candidate.split("/")[-1].split("?")[0] or "submission.gif"
                return data, filename
            except Exception:
                pass

    return None


async def run_countdown(message: discord.Message, total_seconds: int, label: str, color, event: asyncio.Event = None, step: int = 5):
    remaining = total_seconds
    ended_early = False

    while remaining > 0:
        if event and event.is_set():
            ended_early = True
            break
        try:
            await message.edit(embed=discord.Embed(description=f"{label} **{remaining}s** remaining...", color=color))
        except discord.HTTPException:
            pass

        tick = min(step, remaining)
        if event:
            try:
                await asyncio.wait_for(event.wait(), timeout=tick)
                ended_early = True
                break
            except asyncio.TimeoutError:
                pass
        else:
            await asyncio.sleep(tick)
        remaining -= tick

    final_text = "✅ Everyone voted!" if ended_early else "⏰ Time's up!"
    try:
        await message.edit(embed=discord.Embed(description=final_text, color=color))
    except discord.HTTPException:
        pass


async def run_memegame(game: MemeGame):
    channel = game.channel
    game.turn_order = random.sample(game.players, len(game.players))
    total_rounds = len(game.turn_order)

    for round_index, writer in enumerate(game.turn_order, start=1):
        game.round_number = round_index
        game.current_writer = writer
        game.state = "writing"
        game.submissions = {}

        await channel.send(embed=discord.Embed(
            title=f"📝 Round {round_index}/{total_rounds}",
            description=f"{writer.mention}, it's your turn to write a scenario! Just type it in this channel (you have {SCENARIO_WRITE_TIMEOUT // 60} minutes).",
            color=COLOR_FUN
        ))

        def check(m):
            return m.author.id == writer.id and m.channel.id == channel.id

        try:
            msg = await bot.wait_for("message", check=check, timeout=SCENARIO_WRITE_TIMEOUT)
            scenario = msg.content.strip() or "(blank scenario)"
        except asyncio.TimeoutError:
            await channel.send(embed=simple_embed(f"⌛ {writer.mention} took too long — round skipped.", COLOR_WARNING))
            continue

        game.current_scenario = scenario
        await channel.send(embed=discord.Embed(title="🎭 Scenario", description=scenario, color=COLOR_FUN))

        game.state = "submitting"
        for p in game.players:
            submitting_players[p.id] = game

        await channel.send(embed=simple_embed(
            "📸 Time to submit! Use `/submit` in the server **or DM me directly** — you can upload a file, "
            "use Discord's GIF button, or paste a direct image/gif link.",
            COLOR_MUSIC
        ))

        # DM every player so they don't have to be watching the channel to catch the scenario
        dm_failed = []
        dm_embed = discord.Embed(
            title=f"🎮 Round {round_index}/{total_rounds} — Your turn to submit!",
            description=(
                f"**Scenario:** {scenario}\n\n"
                f"You have **{SUBMIT_WINDOW_SECONDS} seconds**. Reply right here in this DM with an image/gif "
                f"(upload a file, use Discord's GIF button, or paste a direct image link) — or use `/submit` "
                f"back in the server."
            ),
            color=COLOR_FUN
        )
        for p in game.players:
            try:
                await p.send(embed=dm_embed)
            except discord.Forbidden:
                dm_failed.append(p)
            except discord.HTTPException:
                dm_failed.append(p)

        if dm_failed:
            names = ", ".join(p.mention for p in dm_failed)
            await channel.send(embed=simple_embed(
                f"⚠️ Couldn't DM: {names} — they'll need to use `/submit` in the server instead.",
                COLOR_WARNING
            ))

        countdown_msg = await channel.send(embed=discord.Embed(description=f"📸 Submissions close in **{SUBMIT_WINDOW_SECONDS}s**...", color=COLOR_MUSIC))
        await run_countdown(countdown_msg, SUBMIT_WINDOW_SECONDS, "📸 Submissions close in", COLOR_MUSIC)

        for p in game.players:
            submitting_players.pop(p.id, None)
        game.state = "revealing"

        submitted_ids = list(game.submissions.keys())
        random.shuffle(submitted_ids)

        skipped = [p for p in game.players if p.id not in submitted_ids]
        if skipped:
            names = ", ".join(p.mention for p in skipped)
            await channel.send(embed=simple_embed(f"⏭️ Skipped (no submission): {names}", COLOR_WARNING))

        round_results = []

        for idx, uid in enumerate(submitted_ids, start=1):
            img_bytes, filename = game.submissions[uid]
            safe_filename = filename if "." in filename else f"{filename}.png"
            file = discord.File(io.BytesIO(img_bytes), filename=safe_filename)

            embed = discord.Embed(
                title=f"Submission #{idx}/{len(submitted_ids)}",
                description=f"React {VOTE_UP} or {VOTE_DOWN}!",
                color=COLOR_FUN
            )
            embed.set_image(url=f"attachment://{safe_filename}")
            reveal_msg = await channel.send(embed=embed, file=file)
            await reveal_msg.add_reaction(VOTE_UP)
            await reveal_msg.add_reaction(VOTE_DOWN)

            vote_data = {
                "counts": {VOTE_UP: 0, VOTE_DOWN: 0},
                "voters": set(),
                "user_votes": {},
                "event": asyncio.Event(),
                "game": game,
            }
            active_votes[reveal_msg.id] = vote_data

            countdown_msg = await channel.send(embed=discord.Embed(description=f"⏳ Voting closes in **{VOTE_WINDOW_SECONDS}s**...", color=COLOR_FUN))
            await run_countdown(countdown_msg, VOTE_WINDOW_SECONDS, "⏳ Voting closes in", COLOR_FUN, event=vote_data["event"], step=2)
            vote_data["event"].set()
            active_votes.pop(reveal_msg.id, None)

            likes = vote_data["counts"][VOTE_UP]
            dislikes = vote_data["counts"][VOTE_DOWN]
            net = likes - dislikes

            game.total_scores[uid] = game.total_scores.get(uid, 0) + net
            game.total_likes[uid] = game.total_likes.get(uid, 0) + likes

            if likes > game.best_overall["likes"]:
                game.best_overall = {
                    "likes": likes, "bytes": img_bytes, "filename": safe_filename,
                    "user_id": uid, "scenario": scenario
                }

            round_results.append((uid, likes, dislikes, img_bytes, safe_filename))
            await asyncio.sleep(1)

        round_results.sort(key=lambda r: r[1] - r[2], reverse=True)
        lines = []
        collage_entries = []
        for rank, (uid, likes, dislikes, img_bytes, filename) in enumerate(round_results, start=1):
            member = channel.guild.get_member(uid)
            name = member.display_name if member else f"User {uid}"
            net = likes - dislikes
            lines.append(f"**#{rank} {name}** — {VOTE_UP}{likes} {VOTE_DOWN}{dislikes} (net {net:+d})")
            collage_entries.append((img_bytes, f"{name} ({likes}{VOTE_UP})"))

        embed = discord.Embed(
            title=f"📊 Round {round_index} Leaderboard",
            description="\n".join(lines) if lines else "No submissions this round.",
            color=COLOR_PRIMARY
        )
        if collage_entries:
            loop = asyncio.get_event_loop()
            collage_buf = await loop.run_in_executor(None, make_collage, collage_entries)
            collage_file = discord.File(collage_buf, filename="round_leaderboard.png")
            embed.set_image(url="attachment://round_leaderboard.png")
            await channel.send(embed=embed, file=collage_file)
        else:
            await channel.send(embed=embed)

    game.state = "finished"
    final_sorted = sorted(game.total_scores.items(), key=lambda kv: kv[1], reverse=True)
    medals = ["🥇", "🥈", "🥉"]
    lines = []
    for i, (uid, score) in enumerate(final_sorted, start=1):
        member = channel.guild.get_member(uid)
        name = member.display_name if member else f"User {uid}"
        prefix = medals[i - 1] if i <= 3 else f"`#{i}`"
        lines.append(f"{prefix} **{name}** — {score:+d} total points")

    await channel.send(embed=discord.Embed(
        title="🏁 Final Leaderboard",
        description="\n".join(lines) if lines else "No scores recorded.",
        color=COLOR_PRIMARY
    ))

    if game.best_overall["bytes"] is not None:
        member = channel.guild.get_member(game.best_overall["user_id"])
        name = member.display_name if member else "Unknown"
        file = discord.File(io.BytesIO(game.best_overall["bytes"]), filename=game.best_overall["filename"])
        embed = discord.Embed(
            title="👑 Most Liked Entry of the Game",
            description=f"By **{name}** — {game.best_overall['likes']} {VOTE_UP}\nScenario: *{game.best_overall['scenario']}*",
            color=COLOR_FUN
        )
        embed.set_image(url=f"attachment://{game.best_overall['filename']}")
        await channel.send(embed=embed, file=file)

    active_games.pop(channel.id, None)


@bot.hybrid_command(name="mgstart", description="Start a new Meme Game lobby in this channel")
async def mgstart(ctx):
    if ctx.channel.id in active_games:
        await ctx.send(embed=simple_embed("⚠️ A game is already active in this channel.", COLOR_WARNING))
        return

    game = MemeGame(channel=ctx.channel, host=ctx.author)
    active_games[ctx.channel.id] = game

    embed = build_lobby_embed(game)
    msg = await ctx.send(embed=embed)
    await msg.add_reaction(JOIN_EMOJI)
    game.lobby_message = msg
    lobby_watch[msg.id] = game


@bot.hybrid_command(name="mgbegin", description="Lock the lobby and start the Meme Game")
async def mgbegin(ctx):
    game = active_games.get(ctx.channel.id)
    if game is None or game.state != "lobby":
        await ctx.send(embed=simple_embed("⚠️ There's no open lobby in this channel.", COLOR_WARNING))
        return
    if ctx.author.id != game.host.id and not is_mod_member(ctx.author):
        await ctx.send(embed=simple_embed("🚫 Only the host or a mod can start the game.", COLOR_DANGER))
        return
    if not (MIN_PLAYERS <= len(game.players) <= MAX_PLAYERS):
        await ctx.send(embed=simple_embed(
            f"⚠️ Need between {MIN_PLAYERS} and {MAX_PLAYERS} players — currently have {len(game.players)}.",
            COLOR_WARNING
        ))
        return

    lobby_watch.pop(game.lobby_message.id, None)
    game.state = "starting"
    names = ", ".join(p.mention for p in game.players)
    await ctx.send(embed=discord.Embed(
        title="🎬 Game Starting!",
        description=f"Players: {names}\n\nGet ready — first scenario is coming up!",
        color=COLOR_SUCCESS
    ))
    game.task = bot.loop.create_task(run_memegame(game))


@bot.hybrid_command(name="mgcancel", description="Cancel the active Meme Game in this channel")
async def mgcancel(ctx):
    game = active_games.get(ctx.channel.id)
    if game is None:
        await ctx.send(embed=simple_embed("⚠️ No active game in this channel.", COLOR_WARNING))
        return
    if ctx.author.id != game.host.id and not is_mod_member(ctx.author):
        await ctx.send(embed=simple_embed("🚫 Only the host or a mod can cancel the game.", COLOR_DANGER))
        return

    if game.task:
        game.task.cancel()
    if game.lobby_message:
        lobby_watch.pop(game.lobby_message.id, None)
    for p in game.players:
        submitting_players.pop(p.id, None)
    active_games.pop(ctx.channel.id, None)
    await ctx.send(embed=simple_embed("🛑 Game cancelled.", COLOR_DANGER))


@bot.hybrid_command(name="mgstatus", description="Show the current Meme Game status")
async def mgstatus(ctx):
    game = active_games.get(ctx.channel.id)
    if game is None:
        await ctx.send(embed=simple_embed("No active game in this channel.", COLOR_WARNING))
        return
    names = ", ".join(p.mention for p in game.players)
    embed = discord.Embed(
        title="🎮 Meme Game Status",
        description=f"**State:** {game.state}\n**Round:** {game.round_number}\n**Players:** {names}",
        color=COLOR_PRIMARY
    )
    await ctx.send(embed=embed)


@bot.tree.command(name="submit", description="Privately submit your image or gif for the current meme round")
async def submit_command(interaction: discord.Interaction, attachment: discord.Attachment):
    game = active_games.get(interaction.channel_id)
    if game is None or game.state != "submitting":
        await interaction.response.send_message("There's no active submission window right now.", ephemeral=True)
        return
    if interaction.user.id not in [p.id for p in game.players]:
        await interaction.response.send_message("You're not in this game.", ephemeral=True)
        return
    if interaction.user.id in game.submissions:
        await interaction.response.send_message("You've already submitted this round!", ephemeral=True)
        return
    if not (attachment.content_type and attachment.content_type.startswith("image")):
        await interaction.response.send_message("Please attach an image or gif.", ephemeral=True)
        return

    try:
        data = await attachment.read()
    except Exception:
        await interaction.response.send_message("Couldn't read that file — try again.", ephemeral=True)
        return

    game.submissions[interaction.user.id] = (data, attachment.filename)
    await interaction.response.send_message("✅ Submission received! Wait for the reveal.", ephemeral=True)


@bot.event
async def on_ready():
    global http_session
    if http_session is None:
        http_session = aiohttp.ClientSession()
    print(f"✅ Logged in as {bot.user} (ID: {bot.user.id})")
    print(f"🎬 Using FFmpeg at: {FFMPEG_EXECUTABLE}")
    print(f"🍪 YouTube cookies loaded: {bool(YOUTUBE_COOKIES)}")
    await ensure_fonts()
    try:
        synced = await bot.tree.sync()
        print(f"🔄 Synced {len(synced)} slash command(s)")
    except Exception as e:
        print(f"⚠️ Slash command sync failed: {e}")
    print("------")


@bot.event
async def on_member_join(member):
    channel = discord.utils.get(member.guild.text_channels, name="general")
    if channel is None:
        return
    try:
        avatar_bytes = await fetch_image_bytes(str(member.display_avatar.url))
        loop = asyncio.get_event_loop()
        banner_buf = await loop.run_in_executor(
            None, generate_welcome_image, avatar_bytes, member.display_name, member.guild.member_count
        )
        file = discord.File(banner_buf, filename="welcome.png")
        embed = discord.Embed(color=COLOR_FUN)
        embed.set_image(url="attachment://welcome.png")
        await channel.send(content=member.mention, embed=embed, file=file)
    except Exception as e:
        print(f"Welcome image error: {e}")
        embed = discord.Embed(
            title="👋 Welcome!",
            description=f"Welcome to The Chooms, {member.mention}!",
            color=COLOR_SUCCESS
        )
        embed.set_thumbnail(url=member.display_avatar.url)
        await channel.send(embed=embed)


@bot.event
async def on_message(message):
    if message.author.bot:
        await bot.process_commands(message)
        return

    if message.guild is None:
        game = submitting_players.get(message.author.id)
        if game is not None and game.state == "submitting" and message.author.id not in game.submissions:
            media = await extract_media_from_message(message)
            if media:
                data, filename = media
                game.submissions[message.author.id] = (data, filename)
                try:
                    await message.channel.send(embed=simple_embed(
                        "✅ Submission received! Head back to the server for the reveal.", COLOR_SUCCESS
                    ))
                except discord.HTTPException:
                    pass
            else:
                try:
                    await message.channel.send(embed=simple_embed(
                        "⚠️ I couldn't find an image or gif in that — try uploading a file, using the GIF button, or pasting a direct image link.",
                        COLOR_WARNING
                    ))
                except discord.HTTPException:
                    pass
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
    if payload.user_id == bot.user.id:
        return

    emoji_str = str(payload.emoji)

    role_id = get_reaction_role(payload.message_id, emoji_str)
    if role_id is not None and payload.member is not None and not payload.member.bot:
        guild = bot.get_guild(payload.guild_id)
        role = guild.get_role(role_id) if guild else None
        if role is not None:
            try:
                await payload.member.add_roles(role, reason="Reaction role")
            except discord.Forbidden:
                pass
        return

    if payload.message_id in lobby_watch and emoji_str == JOIN_EMOJI:
        game = lobby_watch[payload.message_id]
        if game.state == "lobby" and payload.member is not None and not payload.member.bot:
            already_in = any(p.id == payload.member.id for p in game.players)
            if not already_in and len(game.players) < MAX_PLAYERS:
                game.players.append(payload.member)
                try:
                    await game.lobby_message.edit(embed=build_lobby_embed(game))
                except discord.HTTPException:
                    pass
            elif not already_in:
                try:
                    await game.lobby_message.remove_reaction(JOIN_EMOJI, payload.member)
                except discord.HTTPException:
                    pass
        return

    if payload.message_id in active_votes and emoji_str in (VOTE_UP, VOTE_DOWN):
        vote_data = active_votes[payload.message_id]
        game = vote_data["game"]
        if payload.user_id not in [p.id for p in game.players]:
            return
        prior = vote_data["user_votes"].get(payload.user_id)
        if prior == emoji_str:
            return
        if prior:
            vote_data["counts"][prior] -= 1
        vote_data["counts"][emoji_str] += 1
        vote_data["user_votes"][payload.user_id] = emoji_str
        vote_data["voters"].add(payload.user_id)
        if len(vote_data["voters"]) >= len(game.players):
            vote_data["event"].set()


@bot.event
async def on_raw_reaction_remove(payload: discord.RawReactionActionEvent):
    emoji_str = str(payload.emoji)

    role_id = get_reaction_role(payload.message_id, emoji_str)
    if role_id is not None:
        guild = bot.get_guild(payload.guild_id)
        if guild is None:
            return
        member = guild.get_member(payload.user_id)
        if member is None or member.bot:
            return
        role = guild.get_role(role_id)
        if role is not None:
            try:
                await member.remove_roles(role, reason="Reaction role removed")
            except discord.Forbidden:
                pass
        return

    if payload.message_id in lobby_watch and emoji_str == JOIN_EMOJI:
        game = lobby_watch[payload.message_id]
        if game.state == "lobby":
            game.players = [p for p in game.players if p.id != payload.user_id]
            try:
                await game.lobby_message.edit(embed=build_lobby_embed(game))
            except discord.HTTPException:
                pass
        return

    if payload.message_id in active_votes and emoji_str in (VOTE_UP, VOTE_DOWN):
        vote_data = active_votes[payload.message_id]
        if vote_data["user_votes"].get(payload.user_id) == emoji_str:
            vote_data["counts"][emoji_str] -= 1
            del vote_data["user_votes"][payload.user_id]
            vote_data["voters"].discard(payload.user_id)


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

        embed = discord.Embed(description=f"{ctx.author.mention} is petting {member.mention} 🖐️", color=COLOR_FUN)
        embed.set_image(url="attachment://pet.gif")
        await ctx.send(embed=embed, file=file)
    except Exception as e:
        print(f"Pet gif error: {e}")
        await ctx.send(embed=simple_embed(f"{ctx.author.mention} is petting {member.mention} 🖐️", COLOR_FUN))


@bot.hybrid_command(description="Hug someone")
async def hug(ctx, member: discord.Member):
    new_count = increment_hug_count(member.id, ctx.guild.id)
    embed = discord.Embed(description=f"{ctx.author.mention} hugs {member.mention} 🤗", color=COLOR_FUN)
    try:
        gif_url = await fetch_reaction_gif("hug")
        embed.set_image(url=gif_url)
    except Exception as e:
        print(f"Hug gif error: {e}")
    embed.set_footer(text=f"This is the {ordinal(new_count)} time {member.display_name} has been hugged!")
    await ctx.send(embed=embed)


@bot.hybrid_command(description="Kiss someone")
async def kiss(ctx, member: discord.Member):
    new_count = increment_kiss_count(ctx.author.id, member.id, ctx.guild.id)
    embed = discord.Embed(description=f"{ctx.author.mention} kisses {member.mention} 💋", color=COLOR_FUN)
    try:
        gif_url = await fetch_reaction_gif("kiss")
        embed.set_image(url=gif_url)
    except Exception as e:
        print(f"Kiss gif error: {e}")
    embed.set_footer(text=f"{ctx.author.display_name} and {member.display_name} have kissed {new_count} time{'s' if new_count != 1 else ''}!")
    await ctx.send(embed=embed)


@bot.hybrid_command(description="Check how many times someone has been hugged")
async def hugstats(ctx, member: discord.Member = None):
    member = member or ctx.author
    count = get_hug_count(member.id, ctx.guild.id)
    embed = discord.Embed(
        description=f"🤗 {member.mention} has been hugged **{count}** time{'s' if count != 1 else ''}.",
        color=COLOR_FUN
    )
    embed.set_thumbnail(url=member.display_avatar.url)
    await ctx.send(embed=embed)


@bot.hybrid_command(description="Check how many times two members have kissed")
async def kissstats(ctx, member1: discord.Member, member2: discord.Member = None):
    member2 = member2 or ctx.author
    count = get_kiss_count(member1.id, member2.id, ctx.guild.id)
    embed = discord.Embed(
        description=f"💋 {member1.mention} and {member2.mention} have kissed **{count}** time{'s' if count != 1 else ''}.",
        color=COLOR_FUN
    )
    await ctx.send(embed=embed)


@bot.hybrid_command(description="Roast someone (all in good fun)")
async def roast(ctx, member: discord.Member):
    line = random.choice(ROAST_LINES)
    embed = discord.Embed(description=f"{member.mention}, {line}", color=COLOR_DANGER)
    embed.set_thumbnail(url=member.display_avatar.url)
    await ctx.send(embed=embed)


@bot.hybrid_command(name="l", description="Stamp a big L on someone")
async def l_command(ctx, member: discord.Member = None):
    member = member or ctx.author
    try:
        avatar_bytes = await fetch_image_bytes(str(member.display_avatar.url))
        loop = asyncio.get_event_loop()
        image_buffer = await loop.run_in_executor(None, generate_l_stamp, avatar_bytes)
        file = discord.File(image_buffer, filename="l.png")

        embed = discord.Embed(description=f"{member.mention} just took the biggest L in server history.", color=COLOR_DANGER)
        embed.set_image(url="attachment://l.png")
        await ctx.send(embed=embed, file=file)
    except Exception as e:
        print(f"L stamp error: {e}")
        await ctx.send(embed=simple_embed(f"{member.mention} just took the biggest L in server history. 🅻", COLOR_DANGER))


@bot.hybrid_command(description="One-sided insult battle — someone's getting clapped")
async def clapback(ctx, member1: discord.Member, member2: discord.Member):
    winner = random.choice([member1, member2])
    loser = member2 if winner == member1 else member1
    line = random.choice(CLAPBACK_LINES)

    embed = discord.Embed(
        title="🔥 CLAPBACK",
        description=f"{winner.mention} just ended {loser.mention}'s whole career:\n\n*\"{line}\"*",
        color=COLOR_DANGER
    )
    embed.set_thumbnail(url=loser.display_avatar.url)
    await ctx.send(embed=embed)


@bot.hybrid_command(description="Dramatically expose someone's 'crimes'")
async def exposed(ctx, member: discord.Member):
    crime = random.choice(EXPOSED_CRIMES)
    embed = discord.Embed(
        title="🚨 EXPOSED 🚨",
        description=f"{member.mention} has been caught **{crime}**.\n\nThe court of public opinion has spoken.",
        color=COLOR_DANGER
    )
    embed.set_thumbnail(url=member.display_avatar.url)
    embed.set_footer(text="This has been a public service announcement.")
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
    percent = ship_percentage(member1.id + 1, member2.id + 1)

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


def build_role_menu_embed(pending: dict, guild: discord.Guild) -> discord.Embed:
    lines = []
    for emoji, role_id, label in pending["entries"]:
        role = guild.get_role(role_id)
        role_name = role.name if role else "Unknown role"
        line = f"{emoji}  —  **{role_name}**"
        if label:
            line += f"\n     ↳ {label}"
        lines.append(line)
    body = pending["description"]
    body += "\n\n" + "\n".join(lines) if lines else "\n\n*No options added yet — use `!rolemenu add`.*"
    embed = discord.Embed(title=pending["title"], description=body, color=COLOR_PRIMARY)
    embed.set_footer(text="React below to get the matching role!")
    return embed


@bot.hybrid_group(name="rolemenu", description="Build a reaction-role menu, step by step")
@is_mod()
async def rolemenu(ctx):
    if ctx.invoked_subcommand is None:
        await ctx.send(embed=simple_embed(
            "Build a role menu in steps:\n"
            "`!rolemenu new [title]` → start\n"
            "`!rolemenu desc <text>` → set the description (optional)\n"
            "`!rolemenu add <emoji> @role [label]` → add an option\n"
            "`!rolemenu removeoption <emoji>` → remove one\n"
            "`!rolemenu preview` → see it before posting\n"
            "`!rolemenu post` → make it live\n"
            "`!rolemenu cancel` → discard",
            COLOR_PRIMARY, title="🎭 Role Menu Builder"
        ))


@rolemenu.command(name="new", description="Start building a new role menu in this channel")
async def rolemenu_new(ctx, *, title: str = "Choose your roles!"):
    pending_role_menus[ctx.channel.id] = {
        "title": title,
        "description": "React to a role below to get it — react again to remove it.",
        "entries": []
    }
    await ctx.send(embed=simple_embed(
        f"🆕 Started a new role menu: **{title}**. Now add options with `!rolemenu add <emoji> @role [label]`.",
        COLOR_SUCCESS
    ))


@rolemenu.command(name="desc", description="Set the role menu's description text")
async def rolemenu_desc(ctx, *, description: str):
    pending = pending_role_menus.get(ctx.channel.id)
    if not pending:
        await ctx.send(embed=simple_embed("⚠️ No pending role menu — start one with `!rolemenu new`.", COLOR_WARNING))
        return
    pending["description"] = description
    await ctx.send(embed=simple_embed("✅ Description updated.", COLOR_SUCCESS))


@rolemenu.command(name="add", description="Add an emoji → role option to the pending menu")
async def rolemenu_add(ctx, emoji: str, role: discord.Role, *, label: str = None):
    pending = pending_role_menus.get(ctx.channel.id)
    if not pending:
        await ctx.send(embed=simple_embed("⚠️ No pending role menu — start one with `!rolemenu new`.", COLOR_WARNING))
        return
    pending["entries"] = [e for e in pending["entries"] if e[0] != emoji]
    pending["entries"].append((emoji, role.id, label))
    extra = f" — {label}" if label else ""
    await ctx.send(embed=simple_embed(f"➕ Added {emoji} → **{role.name}**{extra}", COLOR_SUCCESS))


@rolemenu.command(name="removeoption", description="Remove an emoji option from the pending menu")
async def rolemenu_removeoption(ctx, emoji: str):
    pending = pending_role_menus.get(ctx.channel.id)
    if not pending:
        await ctx.send(embed=simple_embed("⚠️ No pending role menu.", COLOR_WARNING))
        return
    before = len(pending["entries"])
    pending["entries"] = [e for e in pending["entries"] if e[0] != emoji]
    if len(pending["entries"]) == before:
        await ctx.send(embed=simple_embed(f"⚠️ {emoji} wasn't in the menu.", COLOR_WARNING))
    else:
        await ctx.send(embed=simple_embed(f"➖ Removed {emoji}.", COLOR_SUCCESS))


@rolemenu.command(name="preview", description="Preview the pending role menu before posting it")
async def rolemenu_preview(ctx):
    pending = pending_role_menus.get(ctx.channel.id)
    if not pending:
        await ctx.send(embed=simple_embed("⚠️ No pending role menu.", COLOR_WARNING))
        return
    await ctx.send(embed=build_role_menu_embed(pending, ctx.guild))


@rolemenu.command(name="post", description="Post the finished role menu and make it live")
async def rolemenu_post(ctx):
    pending = pending_role_menus.get(ctx.channel.id)
    if not pending or not pending["entries"]:
        await ctx.send(embed=simple_embed("⚠️ Nothing to post — add at least one option with `!rolemenu add`.", COLOR_WARNING))
        return

    embed = build_role_menu_embed(pending, ctx.guild)
    msg = await ctx.send(embed=embed)
    for emoji, role_id, label in pending["entries"]:
        try:
            await msg.add_reaction(emoji)
        except discord.HTTPException:
            continue
        add_reaction_role(msg.id, emoji, role_id, ctx.guild.id)

    pending_role_menus.pop(ctx.channel.id, None)
    await ctx.send(embed=simple_embed("✅ Role menu is live!", COLOR_SUCCESS))


@rolemenu.command(name="cancel", description="Discard the pending role menu")
async def rolemenu_cancel(ctx):
    if pending_role_menus.pop(ctx.channel.id, None):
        await ctx.send(embed=simple_embed("🗑️ Pending role menu discarded.", COLOR_WARNING))
    else:
        await ctx.send(embed=simple_embed("⚠️ No pending role menu to cancel.", COLOR_WARNING))


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


bot.run(TOKEN)
