import os
import random
import datetime
import discord
from discord.ext import commands
from dotenv import load_dotenv

# Load token from .env file
load_dotenv()
TOKEN = os.getenv("DISCORD_TOKEN")

# ---- Set the exact names of roles allowed to use moderation commands ----
MOD_ROLE_NAMES = ["🌟 𝒪𝓌𝓃𝑒𝓇 ✨👑", "🌸 𝒜𝒹𝓂𝒾𝓃 🛠️💼"]

# Set up intents (permissions for what the bot can "see")
intents = discord.Intents.default()
intents.message_content = True  # required to read message text for commands
intents.members = True  # required for on_member_join and role checks

bot = commands.Bot(command_prefix="!", intents=intents)


def is_mod():
    """Custom check: only allows users who have one of the MOD_ROLE_NAMES roles."""
    async def predicate(ctx):
        user_role_names = [role.name for role in ctx.author.roles]
        if any(name in user_role_names for name in MOD_ROLE_NAMES):
            return True
        await ctx.send("🚫 You don't have permission to use this command.")
        return False
    return commands.check(predicate)


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
    """Sends a welcome message when someone joins the server."""
    channel = discord.utils.get(member.guild.text_channels, name="general")
    if channel:
        await channel.send(f"👋 Welcome to the server, {member.mention}!")


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
