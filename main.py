import discord
import json
import aiosqlite
import os
import asyncio
from discord.ext import commands
from datetime import datetime, timezone
from dotenv import load_dotenv

# CARGA DE ARCHIVOS
load_dotenv()
#"""
with open('config.json', 'r') as f:
    config = json.load(f)
#"""
"""
with open('config_test.json', 'r') as f:
    config = json.load(f)
"""

# VARIABLES GLOBALES
CHANNELS = config['channels']
LEADERBOARD_CHANNEL = config['leaderboard_channel_id']
CHUNK_LINES = 30

db = None

# CONFIGURACION DEL BOT
processed_messages = set()

intents = discord.Intents.default()
intents.message_content = True
intents.reactions = True
intents.members = True

bot = commands.Bot(command_prefix='!', intents=intents, help_command=None)

# EVENTOS DE INICIO
@bot.event
async def on_ready():
    print(f'Bot conectado como {bot.user.name}')

@bot.event
async def setup_hook():
    global db
    print("Conectando a la base de datos...")

    db = await aiosqlite.connect("bot.db")
    db.row_factory = aiosqlite.Row
    print("Base de datos conectada")

    await create_tables()
    print("Tablas creadas")

# COMANDOS
@bot.command()
async def hello(ctx):
    await ctx.send('Hello, I am legendary bot!')

@bot.command()
@commands.has_permissions(administrator=True)
async def reloadlb(ctx):
    await update_leaderboard(ctx.guild)
    await ctx.send("Leaderboard reloaded")

@bot.command()
async def points(ctx, member: discord.Member = None):
    if member is None:
        member = ctx.author

    user_id = str(member.id)
    points = await get_user_points(user_id)

    await ctx.send(f"{member.mention} have {points} points.")

@bot.command()
@commands.has_permissions(administrator=True)
async def addpoints(ctx, member: discord.Member, amount: int):
    user_id = str(member.id)

    await update_user_points(user_id, amount)

    new_total = await get_user_points(user_id)

    await send_log_points_edit(ctx.guild, ctx.author, member, amount, new_total)

    await update_leaderboard(ctx.guild)

    if amount > 0:
        await ctx.send(f"Added {amount} points to {member.mention}")
    else:
        await ctx.send(f"Removed {abs(amount)} points from {member.mention}")

@bot.command()
@commands.has_permissions(administrator=True)
async def resetpoints(ctx):
    await ctx.send("⚠️ Are you sure? This will remove user from leaderboard (yes/no)")

    def check(m):
        return (m.author == ctx.author and 
                m.channel == ctx.channel and
                m.content.lower() in ["yes", "no"])

    try:
        response = await bot.wait_for("message",timeout=30.0, check=check)
    except asyncio.TimeoutError:
        await ctx.send("⏰ Confirmation timed out. Action cancelled.")
        return

    if response.content.lower() == "no":
        await ctx.send("Points not reseted")
        return

    await create_backups()

    old_leaderboard = await generate_leaderboard()
    user_count = await get_users_count()
    total_points = await get_total_points()
    
    await db.execute("DELETE FROM users")
    await db.execute("DELETE FROM approvals")
    await db.execute("DELETE FROM processed_messages")
    await db.commit()

    await update_leaderboard(ctx.guild)

    await ctx.send("Points reseted")

    await send_log_reset(ctx.guild, ctx.author, user_count, total_points, old_leaderboard)

@bot.command()
@commands.has_permissions(administrator=True)
async def removeuser(ctx, member: discord.Member):
    user_id = str(member.id)
    points = await get_user_points(user_id)

    if points == 0:
        await ctx.send(f"User {member.mention} not found in leaderboard")
        return

    await ctx.send("⚠️ Are you sure? This will remove user from leaderboard (yes/no)")

    def check(m):
        return (m.author == ctx.author and 
                m.channel == ctx.channel and
                m.content.lower() in ["yes", "no"])

    try:
        response = await bot.wait_for("message",timeout=30.0, check=check)
    except asyncio.TimeoutError:
        await ctx.send("⏰ Confirmation timed out. Action cancelled.")
        return

    if response.content.lower() == "no":
        await ctx.send("User not removed from leaderboard")
        return

    total_points = points

    await db.execute("DELETE FROM users WHERE user_id = ?", (user_id,))
    await db.commit()

    await ctx.send(f"User {member.mention} removed from leaderboard")

    await update_leaderboard(ctx.guild)

    await send_log_remove_user(ctx.guild, ctx.author, member, total_points)

@bot.command()
@commands.has_permissions(administrator=True)
async def weeklyreset(ctx, amount: int):
    await ctx.send(f"⚠️ Are you sure? This will substract {amount} points from all users (yes/no)")

    def check(m):
        return (m.author == ctx.author and 
                m.channel == ctx.channel and
                m.content.lower() in ["yes", "no"])

    try:
        response = await bot.wait_for("message", timeout=30.0, check=check)
    
    except asyncio.timeoutError:
        await ctx.send("⏰ Confirmation timed out. Action cancelled.")
        return

    if response.content.lower() == "no":
        await ctx.send("Points not substracted")
        return
    
    await ctx.send(f"Substracted {amount} points from all users")

    await create_backups()

    old_leaderboard = await generate_leaderboard()
    user_count = await get_users_count()
    total_points = await get_total_points()

    await subtract_points_all(amount)

    await update_leaderboard(ctx.guild)

    await send_log_weekly_reset(ctx.guild, ctx.author, amount, user_count, total_points, old_leaderboard)

@bot.command()
@commands.has_permissions(administrator=True)
async def undoreset(ctx):
    await ctx.send("⚠️ Restore previus leaderboard? (yes/no)")

    def check(m):
        return (m.author == ctx.author and 
                m.channel == ctx.channel and
                m.content.lower() in ["yes", "no"])

    try:
        response = await bot.wait_for("message",timeout=30.0, check=check)
    except asyncio.TimeoutError:
        await ctx.send("⏰ Confirmation timed out. Action cancelled.")
        return

    if response.content.lower() == "no":
        await ctx.send("Previus leaderboard not restored")

    success = await restore_backup()

    if not success:
        await ctx.send("❌ No backup available")
        return

    await update_leaderboard(ctx.guild)

    await ctx.send("✅ Previus leaderboard restored")

@bot.command()
async def help(ctx):
    is_admin = ctx.author.guild_permissions.administrator

    embed = discord.Embed(title="📜 Bot help", description="Basic commands and usage", color=discord.Color.blue())
    embed.add_field(name="📸 How it works", value=("• Upload an image in a valid channel and\n"
                                                   "mention up to 5 users in the same message\n"
                                                   "• Admin approves with ✅ or rejects with ❌\n"
                                                   "• Points are assigned automatically in a scoreboard-channel"), inline=False)
    embed.add_field(name="👤 Commands", value="!points @user → Check user points", inline=False)

    if is_admin:
        embed.add_field(name="ℹ️ Important", value=("• If you are an admin, only you see this message\n"
                                                    "• In the logs channel you can see all actions of the bot\n"
                                                    "• PLEASE check that the users are mentioned correctly.\n"
                                                    "If any user is mentioned incorrectly, you must use\n" 
                                                    "the command !addpoints @user to assign the points\n"
                                                    "that were not assigned by the bot.\n"
                                                    "• Admin approves with ✅ or rejects with ❌\n"
                                                    "but if you use ✅ to approve and need revert this action\n"
                                                    "use ❌ in the same message to revert"), inline=False)
        embed.add_field(name="👤 Admin commands", value="!addpoints @user amount → Add/Remove points\n"
                                                        "!resetpoints → Reset leaderboard\n"
                                                        "!removeuser @user → Remove user from leaderboard\n"
                                                        "!reloadlb → Reload leaderboard\n"
                                                        "!weeklyreset amount → Substract points from all users\n"
                                                        "!undoreset → Restore previus leaderboard", inline=False)

    await ctx.send(embed=embed)

#COMANDOS DE DESARROLLADOR  
@bot.command()
@commands.has_permissions(administrator=True)
async def setup(ctx):
    embed = discord.Embed(title="📸 Image point system", description="Welcome! Here's how the system works:", color=discord.Color.blue())
    embed.add_field(name="📌 How to use", value=("• Upload an image in a valid channel and\n"
                                                   "mention up to 5 users in the same message\n"
                                                   "PLEASE mention users correctly;\n"
                                                   "an incorrectly mentioned user may not receive points.\n"
                                                   "• Admin approves with ✅ or rejects with ❌\n"
                                                   "• Points are assigned automatically\n"
                                                   "• Cheek out the leaderboard!"), inline=False)
    embed.add_field(name="ℹ️ Help", value="Use !help for more info and check out the commands", inline=False)

    message = await ctx.send(embed=embed)

    await message.pin()

    await ctx.message.delete()

@bot.command()
@commands.has_permissions(administrator=True)
async def backupusers(ctx):
    cursor = await db.execute("SELECT user_id, points FROM users")
    rows = await cursor.fetchall()

    if not rows: 
        await ctx.send("❌ No users found in the leaderboard.")
        return

    with open("users_backup.txt", "w") as f:
        for row in rows:
            f.write(f"{row['user_id']}, {row['points']}\n")

    await ctx.send(file=discord.File("users_backup.txt"))

    await ctx.send("✅ Users backup saved to users_backup.txt")

    os.remove("users_backup.txt")

# COMANDOS DE ERROR
@removeuser.error
async def removeuser_error(ctx, error):
    if isinstance(error, commands.BadArgument):
        await ctx.send("❌ Invalid user, Make sure to mention a valid member of the server.")

# FUNCIONES
def get_channel_points(channel_id):
    for data in CHANNELS.values():
        if data['id'] == channel_id:
            return data['points']
    return None

async def add_approval(message_id: int, user_list: list[str], points: int):
    users_str = ", ".join(user_list)

    await db.execute("INSERT OR REPLACE INTO approvals (message_id, users, points) VALUES (?, ?, ?)", (str(message_id), users_str, points))
    await db.commit()

async def get_approval(message_id: int):
    cursor = await db.execute("SELECT users, points FROM approvals WHERE message_id = ?", (str(message_id),))
    row = await cursor.fetchone()

    if row:
        users_str, points = row["users"], row["points"]
        return {"users": users_str.split(", "), "points": points}
    
    return None

async def delete_approval(message_id: int):
    await db.execute("DELETE FROM approvals WHERE message_id = ?", (str(message_id),))
    await db.commit()

async def is_message_processed(message_id: int):
    cursor = await db.execute("SELECT 1 FROM processed_messages WHERE message_id = ?", (str(message_id),))

    return await cursor.fetchone() is not None

async def mark_message_processed(message_id: int):
    await db.execute("INSERT OR IGNORE INTO processed_messages (message_id) VALUES (?)", (str(message_id),))
    await db.commit()

async def unmark_message_processed(message_id: int):
    await db.execute("DELETE FROM processed_messages WHERE message_id = ?", (str(message_id),))
    await db.commit()

async def get_leaderboard_message_ids():
    cursor = await db.execute("SELECT message_id FROM leaderboard_message_ids ORDER BY idx")
    rows = await cursor.fetchall()

    return [row["message_id"] for row in rows]

async def set_leaderboard_message_ids(message_ids: list[str]):
    await db.execute("DELETE FROM leaderboard_message_ids")

    for msg_id in message_ids:
        await db.execute("INSERT INTO leaderboard_message_ids (message_id) VALUES (?)", (str(msg_id),))

    await db.commit()

async def get_users_count():
    cursor = await db.execute("SELECT COUNT(*) as count FROM users")  
    row = await cursor.fetchone()

    return row["count"]

async def get_total_points():
    cursor = await db.execute("SELECT SUM(points) as total FROM users")  
    row = await cursor.fetchone()

    return row["total"] if row["total"] is not None else 0

async def get_user_points(user_id: str):
    cursor = await db.execute("SELECT points FROM users WHERE user_id = ?", (user_id,))
    row = await cursor.fetchone()

    return row["points"] if row else 0

async def update_user_points(user_id: str, points: int):
    await db.execute("""
                        INSERT INTO users (user_id, points)
                        VALUES (?, ?)
                        ON CONFLICT (user_id) 
                        DO UPDATE SET points = points + ?
                        """, (user_id, points, points))

    await db.execute("DELETE FROM users WHERE points <= 0")

    await db.commit()

async def subtract_points_all(amount: int):
    await db.execute("UPDATE users SET points = points - ?", (abs(amount),))

    await db.execute("DELETE FROM users WHERE points <= 0")

    await db.commit()

async def generate_leaderboard():
    cursor = await db.execute("SELECT user_id, points FROM users ORDER BY points DESC")
    rows = await cursor.fetchall()

    lines = []

    for i, row in enumerate(rows, start=1):
        lines.append(f"{i}. <@{row['user_id']}> - {row['points']} points\n")

    return lines

async def update_leaderboard(guild):
    channel = guild.get_channel(LEADERBOARD_CHANNEL)

    if channel is None:
        return

    lines = await generate_leaderboard()
    message_ids = await get_leaderboard_message_ids()

    updated_ids = await send_chunks(channel, lines, title="🏆 **Leaderboard**\n\n", messages_ids=message_ids)

    await set_leaderboard_message_ids(updated_ids)

async def send_chunks(channel, lines, title=None, messages_ids=None):
    if messages_ids is None:
        messages_ids = []

    chunks = [lines[i:i + CHUNK_LINES]
              for i in range(0, len(lines), CHUNK_LINES)]
    
    new_message_ids = []
    total_chunks = len(chunks)

    for idx, chunk_lines in enumerate(chunks):
        header = f"{title} (Part {idx + 1}/{total_chunks})\n" if title else ""
        chunk_text = header + "".join(chunk_lines)

        if idx < len(messages_ids):
            try:
                msg = await channel.fetch_message(messages_ids[idx])

                await msg.edit(content=chunk_text)

                await asyncio.sleep(0.5)

                new_message_ids.append(msg.id)

                continue

            except Exception as e:
                print(f"Error editing message: {e}")

                pass
        
        msg = await channel.send(chunk_text)

        await asyncio.sleep(0.5)

        new_message_ids.append(msg.id)

    if len(messages_ids) > len(chunks):
        for extra_id in messages_ids[len(chunks):]:
            try:
                msg = await channel.fetch_message(extra_id)

                await msg.delete()
            except:
                pass

    return new_message_ids

async def clear_old_backups():
    await db.execute("DELETE FROM reset_backups")

    await db.execute("DELETE FROM reset_backups_users")

    await db.commit()

async def create_backups():
    await clear_old_backups()

    cursor = await db.execute("INSERT INTO reset_backups DEFAULT VALUES")
    backup_id = cursor.lastrowid

    cursor = await db.execute("SELECT user_id, points FROM users")
    rows = await cursor.fetchall()

    for row in rows:
        await db.execute("INSERT INTO reset_backups_users (backup_id, user_id, points) VALUES (?, ?, ?)", (backup_id, row["user_id"], row["points"]))

    await db.commit()

async def restore_backup():
    cursor = await db.execute("""
                              SELECT id FROM reset_backups
                              ORDER BY id DESC
                              LIMIT 1
                              """)
    
    row = await cursor.fetchone()

    if row is None:
        return False

    backup_id = row["id"]

    await db.execute("DELETE FROM users")

    cursor = await db.execute("""
                              SELECT user_id, points FROM reset_backups_users
                              WHERE backup_id = ?
                              """, (backup_id,))

    rows = await cursor.fetchall()

    for row in rows:
        await db.execute("INSERT INTO users (user_id, points) VALUES (?, ?)", (row["user_id"], row["points"]))
    
    await db.commit()

    return True

# LOGS
async def send_log_approval(guild, message, admin, points):
    log_channel = guild.get_channel(config['log_channel_id'])

    if log_channel is None:
        return

    users = ", ".join([f"<@{u.id}>" for u in message.mentions[:5]])

    embed = discord.Embed(title="📜 Approval log", color=discord.Color.green(), timestamp=datetime.now(timezone.utc))
    embed.add_field(name="👤 Admin", value=f"{admin.mention}", inline=False)
    embed.add_field(name="📍 Channel", value=f"{message.channel.mention}", inline=False)
    embed.add_field(name="🎉 Points", value=f"{points}", inline=False)
    embed.add_field(name="👤 Users", value=f"{users}", inline=False)
    embed.add_field(name="📩 Message", value=f"https://discord.com/channels/{guild.id}/{message.channel.id}/{message.id}", inline=False)

    await log_channel.send(embed=embed)

async def send_log_reversal(guild, message, admin, users, points):
    log_channel = guild.get_channel(config['log_channel_id'])

    if log_channel is None:
        return

    user_mentions = ", ".join([f"<@{user_id}>" for user_id in users])

    embed = discord.Embed(title="📜 Reversal log", color=discord.Color.dark_orange(), timestamp=datetime.now(timezone.utc))
    embed.add_field(name="👤 Admin", value=f"{admin.mention}", inline=False)
    embed.add_field(name="📍 Channel", value=f"{message.channel.mention}", inline=False)
    embed.add_field(name="👤 Users", value=f"{user_mentions}", inline=False)
    embed.add_field(name="🎉 Points removed", value=f"{points}", inline=False)
    embed.add_field(name="📩 Message", value=f"https://discord.com/channels/{guild.id}/{message.channel.id}/{message.id}", inline=False)

    await log_channel.send(embed=embed)

async def send_log_rejection(guild, message, admin):
    log_channel = guild.get_channel(config['log_channel_id'])

    if log_channel is None:
        return

    users = ", ".join([f"<@{u.id}>" for u in message.mentions[:5]]) or "No users"

    embed = discord.Embed(title="📜 Rejection log", color=discord.Color.red(), timestamp=datetime.now(timezone.utc))
    embed.add_field(name="👤 Admin", value=f"{admin.mention}", inline=False)
    embed.add_field(name="📍 Channel", value=f"{message.channel.mention}", inline=False)
    embed.add_field(name="👤 Users", value=f"{users}", inline=False)
    embed.add_field(name="📩 Message", value=f"https://discord.com/channels/{guild.id}/{message.channel.id}/{message.id}", inline=False)

    await log_channel.send(embed=embed)

async def send_log_points_edit(guild, admin, member, amount, new_total):
    log_channel = guild.get_channel(config['log_channel_id'])

    if log_channel is None:
        return

    embed = discord.Embed(title="📜 Edited points log", color=discord.Color.orange(), timestamp=datetime.now(timezone.utc))
    embed.add_field(name="👤 Admin", value=f"{admin.mention}", inline=False)
    embed.add_field(name="👤 user", value=f"{member.mention}", inline=False)
    embed.add_field(name="🎉 Amount", value=f"{amount}", inline=False)
    embed.add_field(name="🎉 New total", value=f"{new_total}", inline=False)

    await log_channel.send(embed=embed)

async def send_log_reset(guild, admin, user_count, total_points, leaderboard_snapshot):
    log_channel = guild.get_channel(config['log_channel_id'])

    if log_channel is None:
        return

    embed = discord.Embed(title="📜 Reset leaderboard", color=discord.Color.dark_red(), timestamp=datetime.now(timezone.utc))
    embed.add_field(name="👤 Admin", value=f"{admin.mention}", inline=False)
    embed.add_field(name="⚠️ Action", value=f"All points reset", inline=False)
    embed.add_field(name="👤 Affected users", value=str(user_count), inline=False)
    embed.add_field(name="💰 Total points", value=str(total_points), inline=False)

    await log_channel.send(embed=embed)

    if leaderboard_snapshot:
        lines = leaderboard_snapshot

        await send_chunks(log_channel, lines, title="📊 **Previous Leaderboard**\n\n")

async def send_log_weekly_reset(guild, admin, amount, user_count, total_points, leaderboard_snapshot):
    log_channel = guild.get_channel(config['log_channel_id'])

    if log_channel is None:
        return

    embed = discord.Embed(title="📜 Weekly reset leaderboard", color=discord.Color.purple(), timestamp=datetime.now(timezone.utc))
    embed.add_field(name="👤 Admin", value=f"{admin.mention}", inline=False)
    embed.add_field(name="⚠️ Action", value=f"Weekly reset", inline=False)
    embed.add_field(name="🎉 Decay per user", value=str(amount), inline=False)
    embed.add_field(name="👤 Users in leaderboard", value=str(user_count), inline=False)
    embed.add_field(name="💰 Total points before", value=str(total_points), inline=False)

    await log_channel.send(embed=embed)

    if leaderboard_snapshot:
        lines = leaderboard_snapshot

        await send_chunks(log_channel, lines, title="📊 **Leaderboard before weekly reset**\n\n")

async def send_log_remove_user(guild, admin, user, total_points):
    log_channel = guild.get_channel(config['log_channel_id'])

    if log_channel is None:
        return

    embed = discord.Embed(title="📜 Remove user log", color=discord.Color.red(), timestamp=datetime.now(timezone.utc))
    embed.add_field(name="👤 Admin", value=f"{admin.mention}", inline=False)
    embed.add_field(name="👤 User", value=f"{user.mention}", inline=False)
    embed.add_field(name="⚠️ Action", value=f"User removed from leaderboard", inline=False)
    embed.add_field(name="💰 Total points", value=str(total_points), inline=False)

    await log_channel.send(embed=embed)

# EVENTO DE VALIDACION
@bot.event
async def on_message(message):

    if message.author.bot:
        return
    
    if message.content.startswith("!"):
        await bot.process_commands(message)
        return
    
    points = get_channel_points(message.channel.id)

    if points is None:
        return
    
    has_image = False

    if message.attachments:
        has_image = True
    elif message.embeds:
        has_image = True

    if not has_image:
        return
    
    if len(message.mentions) == 0:
        return
    
    if len(message.mentions) > 5:
        await message.reply("Only 5 people mentioned")
        return

    await message.add_reaction("⌛")

# EVENTO DE APROBACION 
@bot.event
async def on_reaction_add(reaction, user):

    #COMPROBAR SOLO ADMINS
    if user.bot:
        return
    
    message = reaction.message
    emoji = reaction.emoji

    if not user.guild_permissions.administrator:
        return

    points = get_channel_points(message.channel.id)

    if points is None:
        return
    
    #COMPROBAR MARCADO DEL MENSAJE POR EL BOT
    is_pending = any(str(r.emoji) == "⌛" and r.me for r in message.reactions)
    approval = await get_approval(message.id)
    is_approved = approval is not None
    processed = await is_message_processed(message.id)

    if not is_pending and not is_approved:
        return
    
    #COMPROBAR DUPLICADOS
    if processed and not is_approved:
        return
    
    #COMPROBAR REACCION
    has_image = bool(message.attachments or message.embeds)

    if not has_image:
        await message.reply("❌ Message must contain an image")
        return
    
    if len(message.mentions) == 0:
        await message.reply("❌ Message must contain at least 1 user")
        return
    
    if len(message.mentions) > 5:
        await message.reply("❌ Max 5 mentions allowed")
        return
    
    #APROBAR
    if str(emoji) == "✅":   

        if is_approved:
            await message.reply("⚠️ This message is already approved")
            return
       
        await message.remove_reaction("⌛", bot.user)

        await mark_message_processed(message.id)

        #GUARDAR APROVACION
        user_id = [str(member.id) for member in message.mentions[:5]]
        await add_approval(message.id, user_id, points)

        for member in message.mentions[:5]:
            await update_user_points((str(member.id)), points)

            new_points = await get_user_points(str(member.id))
            print(f"DEBUG: {member.name} ahora tiene {new_points} puntos")

        await message.reply("Approved successfully!")

        await send_log_approval(message.guild, message, user, points)

        #ACTUALIZAR LEADERBOARD
        await update_leaderboard(message.guild)

        return

    #RECHAZAR
    if str(emoji) == "❌":

        #CASO 1: APROBADO INICIALMENTE
        if is_approved:

            for user_id in approval["users"]:
                await update_user_points(user_id, -approval["points"])
            
            await delete_approval(message.id)

            if await is_message_processed(message.id):
                await unmark_message_processed(message.id)

            await message.add_reaction("⌛")

            await message.reply("Approval reversed successfully!")

            await send_log_reversal(message.guild, message, user, approval["users"], approval["points"])

            await update_leaderboard(message.guild)

            return

        #CASO 2: NO APROBADO INICIALMENTE
        await message.remove_reaction("⌛", bot.user)

        await mark_message_processed(message.id)

        await message.reply("Rejected")

        await send_log_rejection(message.guild, message, user)

        return

# CREACION DE TABLAS
async def create_tables():
    await db.execute("""
                        CREATE TABLE IF NOT EXISTS users 
                        (user_id TEXT PRIMARY KEY, 
                        points INTEGER)
                        """)

    await db.execute("""
                        CREATE TABLE IF NOT EXISTS approvals 
                        (message_id TEXT PRIMARY KEY, 
                        users TEXT, 
                        points INTEGER)
                        """)

    await db.execute("""
                        CREATE TABLE IF NOT EXISTS processed_messages 
                        (message_id TEXT PRIMARY KEY)
                        """)
    
    await db.execute("""
                        CREATE TABLE IF NOT EXISTS leaderboard_message_ids
                        (idx INTEGER PRIMARY KEY AUTOINCREMENT,
                        message_id TEXT NOT NULL)
                        """)
    
    await db.execute("""
                        CREATE TABLE IF NOT EXISTS reset_backups
                        (id INTEGER PRIMARY KEY, 
                        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)
                        """)

    await db.execute("""
                        CREATE TABLE IF NOT EXISTS reset_backups_users
                        (backup_id INTEGER, 
                        user_id TEXT,
                        points INTEGER)
                        """)

    await db.commit()

# INICIO DEL BOT
bot.run(os.getenv("TOKEN"))