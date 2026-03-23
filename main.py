import discord
import json
import os
from discord.ext import commands
from datetime import datetime, timezone


# CARGA DE ARCHIVOS
with open('config.json', 'r') as f:
    config = json.load(f)

CHANNELS = config['channels']
LEADERBOARD_CHANNEL = config['leaderboard_channel_id']

with open("data.json", "r") as f:
    data = json.load(f)

# CONFIGURACION DEL BOT
processed_messages = set()

intents = discord.Intents.default()
intents.message_content = True
intents.reactions = True
intents.members = True

bot = commands.Bot(command_prefix='!', intents=intents, help_command=None)

# EVENTO DE INICIO
@bot.event
async def on_ready():
    print(f'Bot conectado como {bot.user.name}')

# COMANDOS
@bot.command()
async def hello(ctx):
    await ctx.send('Hello, I am legendary bot!')

@bot.command()
async def points(ctx, member: discord.Member = None):
    if member is None:
        member = ctx.author

    user_id = str(member.id)
    points = data["users"].get(user_id, 0)

    await ctx.send(f"{member.mention} have {points} points.")

@bot.command()
@commands.has_permissions(administrator=True)
async def addpoints(ctx, member: discord.Member, amount: int):
    user_id = str(member.id)

    if user_id not in data["users"]:
        data["users"][user_id] = 0

    data["users"][user_id] += amount

    new_total = data["users"][user_id]

    save_data()

    await send_log_points_edit(ctx.guild, ctx.author, member, amount, new_total)

    await update_leaderboard(ctx.guild)

    if amount > 0:
        await ctx.send(f"Added {amount} points to {member.mention}")
    else:
        await ctx.send(f"Removed {abs(amount)} points from {member.mention}")

@bot.command()
@commands.has_permissions(administrator=True)
async def resetpoints(ctx):
    await ctx.send("⚠️ Are you sure? This will reset all points")

    def check(m):
        return m.author == ctx.author and m.content.lower() in ["yes", "no"]

    response = await bot.wait_for("message", check=check)

    if response.content.lower() == "no":
        await ctx.send("Points not reseted")
        return

    old_leaderboard = generate_leaderboard()
    user_count = len(data["users"])
    total_points = sum(data["users"].values())

    data["users"] = {}
    data["processed_messages"] = []

    save_data()

    await update_leaderboard(ctx.guild)

    await ctx.send("Points reseted")

    await send_log_reset(ctx.guild, ctx.author, user_count, total_points, old_leaderboard)

@bot.command()
async def help(ctx):
    is_admin = ctx.author.guild_permissions.administrator

    embed = discord.Embed(title="📜 Bot help", description="Basic commands and usage", color=discord.Color.blue())
    embed.add_field(name="📸 How it works", value=("• Upload an image in a valid channel\n"
                                                   "• Mention up to 5 users\n"
                                                   "• Admin approves with ✅\n"
                                                   "• Points are assigned automatically"), inline=False)
    embed.add_field(name="👤 Commands", value="!points @user → Check user points", inline=False)

    if is_admin:
        embed.add_field(name="👤 Admin commands", value="!addpoints @user amount → Add/Remove points\n"
                                                        "!resetpoints → Reset leaderboard", inline=False)

    await ctx.send(embed=embed)

@bot.command()
@commands.has_permissions(administrator=True)
async def setup(ctx):
    embed = discord.Embed(title="📸 Image point system", description="Welcome! Here's how the system works:", color=discord.Color.blue())
    embed.add_field(name="📌 How to use", value=("• Upload an image in a valid channel\n"
                                                   "• Mention up to 5 users\n"
                                                   "• Admin approves with ✅\n"
                                                   "• Points are assigned automatically\n"
                                                   "• Cheek out the leaderboard!"), inline=False)
    embed.add_field(name="ℹ️ Help", value="Use !help for more info", inline=False)

    message = await ctx.send(embed=embed)

    await message.pin()

    await ctx.message.delete()

# FUNCIONES
def get_channel_points(channel_id):
    for data in CHANNELS.values():
        if data['id'] == channel_id:
            return data['points']
    return 0

def save_data():
    with open("data.json", "w") as f:
        json.dump(data, f, indent=4)

def generate_leaderboard():
    users = data["users"]

    sorted_users = sorted(users.items(), key=lambda x: x[1], reverse=True)

    text = "🏆 **Leaderboard**\n\n"

    for i, (user_id, points) in enumerate(sorted_users[:10], start=1):
        text += f"{i}. <@{user_id}> - {points} points\n"

    return text

async def update_leaderboard(guild):
    channel = guild.get_channel(LEADERBOARD_CHANNEL)

    if channel is None:
        return

    leaderboard_text = generate_leaderboard()

    if data["leaderboard_message_id"] is None:
        message = await channel.send(leaderboard_text)
        data["leaderboard_message_id"] = message.id
    
        save_data()
    
    else:
        try:
            message = await channel.fetch_message(data["leaderboard_message_id"])
            await message.edit(content=leaderboard_text)

        except: 
            message = await channel.send(leaderboard_text)
    
    data["leaderboard_message_id"] = message.id

    save_data()

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

    MAX_LENGTH = 1900

    if leaderboard_snapshot:
        total_chunks = (len(leaderboard_snapshot) // MAX_LENGTH) + 1
        current_chunk = 1

        for i in range(0, len(leaderboard_snapshot), MAX_LENGTH):
            chunk = leaderboard_snapshot[i:i + MAX_LENGTH]

            if current_chunk == 1:
                await log_channel.send(f"📊 Previous leaderboard (Part {current_chunk}/{total_chunks}):**\n\n" + chunk)

            else:
                await log_channel.send(f"📊 Part {current_chunk}/{total_chunks}):**\n\n" + chunk)

            current_chunk += 1

# EVENTO DE VALIDACION
@bot.event
async def on_message(message):

    if message.author.bot:
        return
    
    await bot.process_commands(message)

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
    if reaction.message.author.id == bot.user.id:
        return
    
    message = reaction.message
    emoji = reaction.emoji

    if not user.guild_permissions.administrator:
        return

    points = get_channel_points(message.channel.id)

    if points is None:
        return
    
    #COMPROBAR DUPLICADOS
    if message.id in data["processed_messages"]:
        return

    #APROBAR
    if str(emoji) == "✅":   
       
        await message.remove_reaction("⌛", bot.user)

        data["processed_messages"].append(message.id)

        for member in message.mentions[:5]:
            user_id = str(member.id)
                
            if user_id not in data["users"]:
                data["users"][user_id] = 0
            
            data["users"][user_id] += points

        save_data()
        
        for member in message.mentions[:5]:
            print(f"APPROVED: {member.name} has {points} points")

        await message.reply("Approved successfully!")

        await send_log_approval(message.guild, message, user, points)

        #ACTUALIZAR LEADERBOARD
        await update_leaderboard(message.guild)

        return

    #RECHAZAR
    if str(emoji) == "❌":
        
        await message.remove_reaction("⌛", bot.user)

        data["processed_messages"].append(message.id)

        await message.reply("Rejected")

        await send_log_rejection(message.guild, message, user)

        #ACTUALIZAR LEADERBOARD
        await update_leaderboard(message.guild)

        return

# INICIAR BOT
bot.run(os.getenv("TOKEN"))