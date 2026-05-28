import discord
import re
import json
import aiosqlite
import os
import asyncio
import aiohttp
from discord.ext import commands
from discord import app_commands
from typing import Optional
from datetime import datetime, timezone
from dotenv import load_dotenv
from aiohttp import web

# CARGA DE ARCHIVOS
load_dotenv()
"""
with open('config.json', 'r') as f:
    config = json.load(f)
"""
#"""
with open('config_test.json', 'r') as f:
    config = json.load(f)
#"""

# VARIABLES GLOBALES
ENABLE_IMAGE_SYSTEM = False
CHANNELS = config['channels']
LEADERBOARD_CHANNEL = config['leaderboard_channel_id']

GUILD_ID_TEST = 761276922059292713

CHUNK_LINES = 30

db = None

DEFAULT_CONFIG_POINTS = {

    #Allies
    "allies_1": 1,
    "allies_2": 2,
    "allies_3": 3,
    "allies_4": 4,
    "allies_5": 5,

    #Enemies
    "enemies_1": 1,
    "enemies_2": 2,
    "enemies_3": 3,
    "enemies_4": 4,
    "enemies_5": 5,

    #Others
    "mode_attack": 10,
    "mode_defense": 10,
    "result_win": 10,
    "result_lose": 10,
    "type_Prisma": 10,
    "type_AvA": 10,
    "type_Perco": 10
}

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
    await bot.wait_until_ready()

    guild = discord.Object(id=GUILD_ID_TEST)

    bot.tree.copy_global_to(guild=guild)
    bot.tree.sync(guild=guild)

    print("Slash commands synced")
    print([cmd.name for cmd in bot.tree.get_commands()])

    synced = await bot.tree.sync(guild=guild)
    print("SYNCED: ",[cmd.name for cmd in synced])

    await start_web_server()

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
    await init_config()
    print("Configuración inicializada")

# CLASES
class ConfirmView(discord.ui.View):
    def __init__(self, author, timeout=60):
        super().__init__(timeout=timeout)
        self.author = author
        self.value = None

    # BOTONES
    @discord.ui.button(label="Confirm", style=discord.ButtonStyle.green)
    async def confirm(self, interaction: discord.Interaction, button: discord.ui.Button):
        if self.value is not None:
            return

        if interaction.user != self.author:
            await interaction.response.send_message("You cannot use this button.", ephemeral=True)
            return

        self.value = True

        for item in self.children:
            item.disabled = True

        await interaction.message.edit(view=self)
        self.stop()

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.red)
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button):
        if self.value is not None:    
            return

        if interaction.user != self.author:
            await interaction.response.send_message("You cannot use this button.", ephemeral=True)
            return

        self.value = False

        for item in self.children:
            item.disabled = True

        await interaction.message.edit(view=self)
        self.stop()

# COMANDOS
@bot.hybrid_command(name="hello", description="Say hello to the bot")
@app_commands.default_permissions(administrator=True)
async def hello(ctx):
    await ctx.reply('Hello, I am legendary bot!')

@bot.hybrid_command(name="leaderboard", description="Reload leaderboard")
@app_commands.default_permissions(administrator=True)
@commands.has_permissions(administrator=True)
async def leaderboard(ctx):
    await ctx.defer()
    await update_leaderboard(ctx.guild)
    await ctx.send("Leaderboard reloaded")
    print("Leaderboard reloaded")

@bot.hybrid_command(name="points", description="See your points or another user's points")
@app_commands.default_permissions(administrator=True)
async def points(ctx, member: discord.Member = None):
    await ctx.defer()

    if member is None:
        member = ctx.author

    user_id = str(member.id)
    points = await get_user_points(user_id)

    await ctx.send(f"{member.mention} have {points} points.")

@bot.hybrid_command(name="addpoints", description="Add or remove points to a user")
@app_commands.default_permissions(administrator=True)
@commands.has_permissions(administrator=True)
async def addpoints(ctx, member: discord.Member, amount: int):
    await ctx.defer()

    user_id = str(member.id)

    await update_user_points(user_id, amount)

    new_total = await get_user_points(user_id)

    await save_last_action("/addpoints", {"users": [str(member.id)], "points": amount})

    await send_log_points_edit(ctx.guild, ctx.author, member, amount, new_total)

    await update_leaderboard(ctx.guild)

    if amount > 0:
        await ctx.send(f"Added {amount} points to {member.mention}")
    else:
        await ctx.send(f"Removed {abs(amount)} points from {member.mention}")

@bot.hybrid_command(name="resetpoints", description="Reset all points")
@app_commands.default_permissions(administrator=True)
@commands.has_permissions(administrator=True)
async def resetpoints(ctx):
    await ctx.defer()

    embed = discord.Embed()
    embed.title = "⚠️ Reset points"
    embed.description = "Are you sure? This will remove ALL points from leaderboard"
    embed.color = discord.Color.red()
    embed.set_footer(text="you have 60 seconds to cancel")

    confirmed, message = await confirm_action(ctx, embed)

    if confirmed is None:
        await update_embed(message, embed, "⏰ Confirmation timed out.", discord.Color.greyple())
        return

    if confirmed is False:
        await update_embed(message, embed, "❌ Operation canceled.", discord.Color.red())
        return
    
    if confirmed is True:
        await update_embed(message, embed, "✅ Operation confirmed", discord.Color.green())

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

@bot.hybrid_command(name="removeuser", description="Remove user from leaderboard")
@app_commands.default_permissions(administrator=True)
@commands.has_permissions(administrator=True)
async def removeuser(ctx, member: discord.Member):
    await ctx.defer()

    user_id = str(member.id)
    points = await get_user_points(user_id)

    if points == 0:
        await ctx.send(f"User {member.mention} not found in leaderboard")
        return

    embed = discord.Embed()
    embed.title = "⚠️ Remove user"
    embed.description = f"Are you sure? This will remove all data for {member.mention}"
    embed.color = discord.Color.red()
    embed.set_footer(text="you have 60 seconds to cancel")

    confirmed, message = await confirm_action(ctx, embed)

    if confirmed is None:
        await update_embed(message, embed, "⏰ Confirmation timed out.", discord.Color.greyple())
        return

    if confirmed is False:
        await update_embed(message, embed, "❌ Operation canceled.", discord.Color.red())
        return
    
    if confirmed is True:
        await update_embed(message, embed, "✅ Operation confirmed", discord.Color.green())

    await save_last_action("/removeuser", {"users": [str(member.id)], "points": points})

    await db.execute("DELETE FROM users WHERE user_id = ?", (user_id,))
    await db.commit()

    await ctx.reply(f"User {member.mention} removed from leaderboard")

    await update_leaderboard(ctx.guild)

    await send_log_remove_user(ctx.guild, ctx.author, member, points)

@bot.hybrid_command(name="modifyallpoints", description="Substract points from all users")
@app_commands.default_permissions(administrator=True)
@commands.has_permissions(administrator=True)
async def modifyallpoints(ctx, amount: int):
    await ctx.defer()

    embed = discord.Embed()
    embed.title = "⚠️ Global points adjustment"
    embed.description = "This will modify points from ALL users"
    embed.add_field(name="Amount", value=amount, inline=False)
    embed.color = discord.Color.orange()
    embed.set_footer(text="you have 60 seconds to cancel")

    confirmed, message = await confirm_action(ctx, embed)

    if confirmed is None:
        await update_embed(message, embed, "⏰ Confirmation timed out.", discord.Color.greyple())
        return

    if confirmed is False:
        await update_embed(message, embed, "❌ Operation canceled.", discord.Color.red())
        return
    
    if confirmed is True:
        await update_embed(message, embed, "✅ Operation confirmed", discord.Color.green())
    
    await ctx.send(f"Substracted {amount} points from all users")

    await create_backups()

    old_leaderboard = await generate_leaderboard()
    user_count = await get_users_count()
    total_points = await get_total_points()

    await subtract_points_all(amount)

    await update_leaderboard(ctx.guild)

    await send_log_modify_all_points(ctx.guild, ctx.author, amount, user_count, total_points, old_leaderboard)

@bot.hybrid_command(name="undo", description="Undo last action")
@app_commands.default_permissions(administrator=True)
@commands.has_permissions(administrator=True)
async def undo(ctx):
    await ctx.defer()

    last = await get_last_action()

    users = []
    points = 0

    if last is not None:
        action_type = last["type"]

        users =  last["data"]["users"]
        points = last["data"]["points"]

        if action_type == "/removeuser":
            undo_type = "remove_user"
            preview = (f"This will undo the last action: {last['type']}\n"
                    f"• User affected: <@{users[0]}>\n"
                    f"• Points returned: {points}")
        else:
            undo_type = "points_change"
            preview = (f"This will undo the last action: {last['type']}\n"
                    f"• Users affected: {len(users)}\n"
                    f"• Points per user: {points}")
    else:
        undo_type = "last_backup"
        preview = "Restore full leaderboard from last backup (global reset state)"

    embed = discord.Embed()
    embed.title = "⚠️ Undo last action"
    embed.description = preview
    embed.color = discord.Color.red()
    embed.set_footer(text="you have 60 seconds to cancel")

    confirmed, message = await confirm_action(ctx, embed)

    if confirmed is None:
        await update_embed(message, embed, "⏰ Confirmation timed out.", discord.Color.greyple())
        return

    if confirmed is False:
        await update_embed(message, embed, "❌ Operation canceled.", discord.Color.red())
        return
    
    if confirmed is True:
        await update_embed(message, embed, "⌛ Reverting last action", discord.Color.orange())
    
    if undo_type == "points_change":

        for user_id in users:
            await update_user_points(user_id, -points)

        await clear_last_action()
        await update_leaderboard(ctx.guild)

        await update_embed(message, embed, "✅ Last action points undone", discord.Color.green())

        await send_log_undo(ctx.guild, ctx.message, ctx.author, undo_type, {"users": users, "points": points})
        return
    
    if undo_type == "remove_user":
        user_id = users[0]

        await update_user_points(user_id, points)

        await clear_last_action()
        await update_leaderboard(ctx.guild)

        await update_embed(message, embed, "✅ Removed user restored", discord.Color.green())

        await send_log_undo(ctx.guild, ctx.message, ctx.author, undo_type, {"users": users, "points": points})
        return
    
    if undo_type == "last_backup":
        success = await restore_backup()

        if not success:
            await update_embed(message, embed, "❌ No backups found.", discord.Color.red())
            return

        await update_leaderboard(ctx.guild)

        await update_embed(message, embed, "✅ Leaderboard restored from backup", discord.Color.green())

        await send_log_undo(ctx.guild, ctx.message, ctx.author, undo_type, None)

@bot.hybrid_command(name="help", description="Bot help")
@app_commands.default_permissions(administrator=True)
async def help(ctx):
    is_admin = ctx.author.guild_permissions.administrator

    embed = discord.Embed(title="📜 Bot help", description="Leaderboard and battle points system", color=discord.Color.blue())

    """
    embed.add_field(name="📸 How it works", value=("• Upload an image in a valid channel and\n"
                                                   "mention up to 5 users in the same message\n"
                                                   "• Admin approves with ✅ or rejects with ❌\n"
                                                   "• Points are assigned automatically in a scoreboard-channel"), inline=False)
    embed.add_field(name="👤 Commands", value="/points @user → Check user points", inline=False)
    """

    if is_admin:

        """
        embed.add_field(name="ℹ️ Important", value=("• If you are an admin, only you see this message\n"
                                                    "• In the logs channel you can see all actions of the bot\n"
                                                    "• PLEASE check that the users are mentioned correctly.\n"
                                                    "If any user is mentioned incorrectly, you must use\n" 
                                                    "the command !addpoints @user to assign the points\n"
                                                    "that were not assigned by the bot.\n"
                                                    "• Admin approves with ✅ or rejects with ❌\n"
                                                    "but if you use ✅ to approve and need revert this action\n"
                                                    "use ❌ in the same message to revert"), inline=False)
        """
        admin_commands = [
            "/points @user → Check user points",
            "/leaderboard → Reload leaderboard",
            "/addpoints @user amount → Add/Remove points",
            "/removeuser @user → Remove user from leaderboard",
            "/battlepoints → Calculate the points of a battle",
            "/resetpoints → Reset leaderboard",
            "/modifyallpoints amount → Substract points from all users",
            "/undo → Undo last action"
        ]

        safety_notes =  [
            "⚠️ Dangerous commands require confirmation",
            "💾 Leaderboard resets create automatic backup",
            "🔖 All admin actions are logged automatically",
            "🔄 '/undo' can revert the lastest admin action"
        ]

        important_notes = [
            "• Mention users correctly before approval",
            "• Incorrect mentions may prevent points assignment",
            "• Only administrators can use bot commands",
            "• Logs channel stores bot audit history"
        ]

        embed.add_field(name="🛡️ Admin commands", value="\n".join(admin_commands), inline=False)
        embed.add_field(name="🔄 Recovery & safety", value="\n".join(safety_notes), inline=False)
        embed.add_field(name="ℹ️ Important notes", value="\n".join(important_notes), inline=False)

    await ctx.send(embed=embed)

    if ctx.message:
        await ctx.message.delete()

@bot.hybrid_command(name="battlepoints", description="Calculate the points of a battle")
@app_commands.default_permissions(administrator=True)
@app_commands.choices(
    mode=[app_commands.Choice(name="Attack", value="attack"), 
          app_commands.Choice(name="Defense", value="defense")], 
    results=[app_commands.Choice(name="Win", value="win"), 
             app_commands.Choice(name="Lose", value="lose")],
    focus=[app_commands.Choice(name="Focus", value="focus"), 
           app_commands.Choice(name="No Focus", value="nofocus")],
    battle_type=[app_commands.Choice(name="Prisma", value="Prisma"), 
                 app_commands.Choice(name="AvA", value="AvA"), 
                 app_commands.Choice(name="Perco", value="Perco")])
@commands.has_permissions(administrator=True)
async def battlepoints(ctx, allies: int, enemies: int, mode: app_commands.Choice[str], results: app_commands.Choice[str], battle_type: app_commands.Choice[str], focus: app_commands.Choice[str], multiplier: str,
                        member1: discord.Member, member2: Optional[discord.Member] = None, member3: Optional[discord.Member] = None, member4: Optional[discord.Member] = None, member5: Optional[discord.Member] = None):

    members = [m for m in [member1, member2, member3, member4, member5] if m]
    mode = mode.value
    results = results.value
    battle_type = battle_type.value
    is_focus = (focus.value == "focus")

    try:
        multiplier = float(multiplier)
    except ValueError:
        return await ctx.send("❌ Invalid multiplier.")

    if allies < 0 or enemies < 0:
        return await ctx.send("❌ Invalid number of allies or enemies.")
    
    if allies > 5 or enemies > 5:
        return await ctx.send("❌ Invalid number of allies or enemies.")

    if mode not in ["attack", "defense"]:
        return await ctx.send("❌ Invalid mode.")

    if results not in ["win", "lose"]:
        return await ctx.send("❌ Invalid results.")

    if battle_type not in ["Prisma", "AvA", "Perco"]:
        return await ctx.send("❌ Invalid battle type.")

    if multiplier <= 0:
        return await ctx.send("❌ Invalid multiplier.")

    if not members:
        return await ctx.send("❌ You must mention at least one user.")

    base_points = await calculate_points(allies, enemies, mode, results, battle_type, is_focus)
    base_points = int(base_points) if base_points.is_integer() else base_points
    total_points = float(base_points * multiplier)
    total_points = int(total_points) if total_points.is_integer() else total_points

    embed = discord.Embed(title="⚠️ Confirm points", description="Are you sure you want to assign these points?", color=discord.Color.orange())

    embed.add_field(name="🎯 Focus", value=str(is_focus), inline=True)

    embed.add_field(name="👤 Allies", value=f"{allies}", inline=True)
    embed.add_field(name="👤 Enemies", value=f"{enemies}", inline=True)

    embed.add_field(name="🛡️ Mode", value=mode.capitalize(), inline=True)
    embed.add_field(name="⚔️ Result", value=results.capitalize(), inline=True)

    embed.add_field(name="🏹 Battle type", value=battle_type, inline=True)
    embed.add_field(name="🎉 Multiplier", value=f"{multiplier}", inline=True)

    embed.add_field(name="👤 Users", value=f"{', '.join([member.mention for member in members])}", inline=False)
    embed.add_field(name="🎉 Base points", value=f"{base_points}", inline=False)

    embed.add_field(name="💰 Total points", value=f"**{total_points}**", inline=False)

    embed.set_footer(text="You have 60 seconds to confirm")

    confirmed, message = await confirm_action(ctx, embed)

    if confirmed is None:
        await update_embed(message, embed, "⏰ Confirmation timed out.", discord.Color.greyple())
        return

    if confirmed is False:
        await update_embed(message, embed, "❌ Operation canceled.", discord.Color.red())
        return
    
    if confirmed is True:
        await update_embed(message, embed, "✅ Operation confirmed", discord.Color.green())

    for member in members:
        user_id = str(member.id)
        await update_user_points(user_id, total_points)

    await save_last_action("/battlepoints", {"users": [str(member.id) for member in members], "points": total_points})

    await ctx.reply(f"✅ assigned {total_points} points to {', '.join([member.mention for member in members])}")

    await log_battle_points(ctx.guild, ctx.message, ctx.author, members, base_points, multiplier, total_points, allies, enemies, mode, results, battle_type, is_focus)

    await update_leaderboard(ctx.guild)

@bot.hybrid_command(name="configpoints", description="Modifies the values of /manualpoints command")
@app_commands.default_permissions(administrator=True)
@app_commands.choices(
    key=[app_commands.Choice(name="allies 1", value="allies_1"),
         app_commands.Choice(name="allies 2", value="allies_2"),
         app_commands.Choice(name="allies 3", value="allies_3"),
         app_commands.Choice(name="allies 4", value="allies_4"),
         app_commands.Choice(name="allies 5", value="allies_5"),
         app_commands.Choice(name="enemies 1", value="enemies_1"),
         app_commands.Choice(name="enemies 2", value="enemies_2"),
         app_commands.Choice(name="enemies 3", value="enemies_3"),
         app_commands.Choice(name="enemies 4", value="enemies_4"),
         app_commands.Choice(name="enemies 5", value="enemies_5"),
         app_commands.Choice(name="attack", value="mode_attack"),
         app_commands.Choice(name="defense", value="mode_defense"),
         app_commands.Choice(name="win", value="result_win"),
         app_commands.Choice(name="lose", value="result_lose"),
         app_commands.Choice(name="Prisma", value="type_Prisma"),
         app_commands.Choice(name="AvA", value="type_AvA"),
         app_commands.Choice(name="Perco", value="type_Perco")]
)
@commands.has_permissions(administrator=True)
async def configpoints(ctx, key: app_commands.Choice[str], value: str):
    value = parse_multiplier(value)
    key = key.value

    await set_config(key, value)
    await ctx.reply(f"✅ {key} updated to {value}")

#COMANDOS DE DESARROLLADOR  
@bot.command(name="setup", description="Developer command: Setup the bot")
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

@bot.command(name="usersbackup", description="Developer command: Backup users data")
@commands.has_permissions(administrator=True)
async def usersbackup(ctx):
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

@bot.command()
@commands.has_permissions(administrator=True)
async def clearglobal(ctx):

    bot.tree.clear_commands(guild=None)
    await bot.tree.sync()

    await ctx.send("Global commands cleared.")

# COMANDOS DE ERROR
@removeuser.error
async def removeuser_error(ctx, error):
    
    if isinstance(error, commands.BadArgument):
        await ctx.send("❌ Invalid user, Make sure to mention a valid member of the server.")

# FUNCIONES
async def handle_update_points(request):
    # 1. CORS: Responder siempre a las peticiones 'OPTIONS' (el "preflight" del navegador)
    if request.method == 'OPTIONS':
        headers = {
            'Access-Control-Allow-Origin': '*',
            'Access-Control-Allow-Methods': 'POST, OPTIONS',
            'Access-Control-Allow-Headers': 'Content-Type, Authorization',
        }
        return web.Response(headers=headers, status=200)

    # 2. Validación de Token
    token = request.headers.get('Authorization')
    if token != os.getenv("WEB_API_TOKEN"):
        return web.Response(text="No autorizado", status=401, headers={'Access-Control-Allow-Origin': '*'})
    
    # 3. Procesamiento de datos
    try:
        data = await request.json()
        for key, value in data.items():
            await set_config(key, value)
            
        # IMPORTANTE: Incluir Access-Control-Allow-Origin en la respuesta de éxito
        return web.Response(
            text="Configuración actualizada con éxito", 
            status=200, 
            headers={'Access-Control-Allow-Origin': '*'}
        )
    except Exception as e:
        print(f"Error procesando JSON: {e}")
        return web.Response(
            text="Error en el formato", 
            status=400, 
            headers={'Access-Control-Allow-Origin': '*'}
        )

async def start_web_server():
    app = web.Application()
    app.router.add_post('/api/guardar-tablas', handle_update_points)
    app.router.add_options('/api/guardar-tablas', handle_update_points)

    runner = web.AppRunner(app)
    await runner.setup()

    port = int(os.environ.get("PORT", 8080))
    site = web.TCPSite(runner, '0.0.0.0', port)
    await site.start()
    print(f"Web server started on port: {port}")

def parse_multiplier(value: str) -> float:
    value = value.strip()
    value = value.replace(",", ".")

    try:
        return float(value)
    except ValueError:
        raise ValueError("Invalid multiplier format.")

async def calculate_points(allies, enemies, mode, results, battle_type, focus):
    
    """
    points = 0

    points += await get_config(f"allies_{allies}") or 0
    points += await get_config(f"enemies_{enemies}") or 0
    points += await get_config(f"mode_{mode}") or 0
    points += await get_config(f"result_{results}") or 0
    points += await get_config(f"type_{battle_type}") or 0
    """

    # 1. Normalización (igual a lo que discutimos)
    mode_map = {"attack": "ataque", "defense": "defensa"}
    res_map = {"win": "victoria", "lose": "derrota"}
    m = mode_map.get(mode, mode)
    r = res_map.get(results, results)
    f = "focus" if focus else "nofocus"
    
    # El nombre de la condicion completa como lo guardaste en el JSON
    condicion = f"{m}_{r}_{f}" 
    
    # 2. Obtener puntos base (Tabla de aliados/enemigos)
    # Ajuste: Si en tu index 'e1' es 0 enemigos, entonces el índice es 'enemies + 1'
    key_base = f"base_{condicion}_a{allies}_e{enemies + 1}"
    base_pts = float(await get_config(key_base) or 0)
    
    # 3. Obtener puntos extra (Prisma, Perco o AvA)
    # Tu index guarda esto como: extra_prism_ataque_victoria_focus
    key_extra = f"extra_{battle_type.lower()}_{condicion}"
    extra_pts = float(await get_config(key_extra) or 0)
    
    # 4. Cálculo final
    total = base_pts + extra_pts
    
    print(f"Base: {base_pts}, Extra: {extra_pts}, Total: {total}")
    return total

def validate_message(message):
    if not (message.attachments or message.embeds):
        return False, "❌ Message must contain an image"
    
    if len(message.mentions) == 0:
        return False, "❌ Message must contain at least 1 user"
    
    if len(message.mentions) > 5:
        return False, "❌ Max 5 mentions allowed"
    
    return True, None

async def safe_reply(message, content):
    try:
        await message.reply(content)
    except:
        await message.channel.send(content)

async def send_response(ctx, *, embed=None, view=None, content=None, ephemeral=False):
    if ctx.interaction:

        if ctx.interaction.response.is_done():
            return await ctx.interaction.followup.send(content=content, embed=embed, view=view, ephemeral=ephemeral)

        await ctx.interaction.response.send_message(content=content, embed=embed, view=view, ephemeral=ephemeral)

        return await ctx.interaction.original_response()

    return await ctx.send(content=content, embed=embed, view=view)

async def confirm_action(ctx, embed):
    view = ConfirmView(ctx.author)

    message = await send_response(ctx, embed=embed, view=view)
    
    await view.wait()

    return view.value, message

async def save_last_action(action_type, data):
    await db.execute("""
        INSERT INTO last_action (id, type, data)
        VALUES (1, ?, ?)
        ON CONFLICT(id) DO UPDATE SET
            type = excluded.type,
            data = excluded.data
    """, (action_type, json.dumps(data)))

    await db.commit()

async def get_last_action():
    cursor = await db.execute("SELECT type, data FROM last_action WHERE id = 1")
    row = await cursor.fetchone()

    if row is None:
        return None
    
    return {"type": row["type"], "data": json.loads(row["data"]) if row["data"] else {}}

async def clear_last_action():
    await db.execute("DELETE FROM last_action WHERE id = 1")
    await db.commit()

async def update_embed(message, embed, title, color, footer=None):
    embed.title = title
    embed.color = color

    if footer:
        embed.set_footer(text=footer)

    await message.edit(embed=embed, view=None)

def get_channel_points(channel_id):
    for data in CHANNELS.values():
        if data['id'] == channel_id:
            return data['points']
    return None

def extract_number(content):
    match = re.search(r'\d+', content)
    return int(match.group()) if match else None

async def init_config():
    for key, value in DEFAULT_CONFIG_POINTS.items():
        valor_numerico = float(value)
        await db.execute(
            "INSERT OR IGNORE INTO points_config (key, value) VALUES (?, ?)", 
            (key, valor_numerico)
        )
    
    await db.commit()
    print("Base de datos de configuración inicializada correctamente.")

async def get_config(key):
    cursor = await db.execute("SELECT value FROM points_config WHERE key = ?", (key,))
    row = await cursor.fetchone()

    if row:
        return float(row[0])
    
    return 0.0

async def set_config(key, value):
    await db.execute("INSERT INTO points_config (key, value) VALUES (?, ?) ON CONFLICT (key) DO UPDATE SET value = excluded.value", (key, float(value)))
    await db.commit()

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

    cursor = await db.execute("""
                              SELECT user_id, points FROM reset_backups_users
                              WHERE backup_id = ?
                              """, (backup_id,))

    rows = await cursor.fetchall()

    if not rows:
        return False

    await db.execute("DELETE FROM users")

    for row in rows:
        await db.execute("INSERT INTO users (user_id, points) VALUES (?, ?)", (row["user_id"], row["points"]))
    
    await db.execute("DELETE FROM reset_backups WHERE id = ?", (backup_id,))
    await db.execute("DELETE FROM reset_backups_users WHERE backup_id = ?", (backup_id,))

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

async def send_log_undo(guild, message, admin, undo_type, data):
    log_channel = guild.get_channel(config['log_channel_id'])

    if log_channel is None:
        return

    embed = discord.Embed(title="↩️ Undo log", color=discord.Color.blurple(), timestamp=datetime.now(timezone.utc))
    embed.add_field(name="🔧 Undo type", value=undo_type, inline=False)
    embed.add_field(name="👤 Admin", value=f"{admin.mention}", inline=False)

    if undo_type == "points_change":
        users_mentions = ", ".join([f"<@{u}>" for u in data["users"]])

        embed.add_field(name="👥 Users affected", value=users_mentions, inline=False)
        embed.add_field(name="🎯 Points per user", value=str(data["points"]), inline=False)

    elif undo_type == "last_backup":
        embed.add_field(name="📦 Action", value="Full leaderboard restore from backup", inline=False)

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

async def send_log_modify_all_points(guild, admin, amount, user_count, total_points, leaderboard_snapshot):
    log_channel = guild.get_channel(config['log_channel_id'])

    if log_channel is None:
        return

    embed = discord.Embed(title="📜 Modify all leaderboard", color=discord.Color.purple(), timestamp=datetime.now(timezone.utc))
    embed.add_field(name="👤 Admin", value=f"{admin.mention}", inline=False)
    embed.add_field(name="⚠️ Action", value=f"Modify all points on leaderboard", inline=False)
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

async def log_battle_points(guild, message, admin, members, base_points, multiplier, total_points, allies, enemies, mode, result, battle_type, focus):
    log_channel = guild.get_channel(config['log_channel_id'])

    if log_channel is None:
        return
    
    users_mentions = ", ".join([f"<@{u.id}>" for u in members])

    embed = discord.Embed(title="📜 Battle points log", color=discord.Color.green(), timestamp=datetime.now(timezone.utc))
    embed.add_field(name="👤 Admin", value=f"{admin.mention}", inline=False)
    embed.add_field(name="👤 Users", value=f"{users_mentions}", inline=False)
    embed.add_field(name="🎉 Base points", value=f"{base_points}", inline=False)
    embed.add_field(name="🎉 Multiplier", value=f"{multiplier}", inline=False)
    embed.add_field(name="💰 Total points", value=f"{total_points}", inline=False)
    embed.add_field(name="🎯 Focus", value=str(focus), inline=False)
    embed.add_field(name="👤 Allies", value=f"{allies}", inline=False)
    embed.add_field(name="👤 Enemies", value=f"{enemies}", inline=False)
    embed.add_field(name="🛡️ Mode", value=f"{mode}", inline=False)
    embed.add_field(name="⚔️ Result", value=f"{result}", inline=False)
    embed.add_field(name="🏹 Battle type", value=f"{battle_type}", inline=False)
    embed.add_field(name="📩 Message", value=f"https://discord.com/channels/{guild.id}/{message.channel.id}/{message.id}", inline=False)

    await log_channel.send(embed=embed)

# EVENTO DE VALIDACION
@bot.event
async def on_message(message):

    if message.author.bot:
        return
    
    if ENABLE_IMAGE_SYSTEM:

        if message.author.id == bot.user.id:
            return

        if message.content.startswith(tuple(bot.command_prefix)):
            await bot.process_commands(message)
            return
        
        points = get_channel_points(message.channel.id)
        nuggets = message.channel.id == config['nuggets_channel_id']

        if points is None and not nuggets:
            return
        
        valid, error = validate_message(message)

        if not valid:
            await safe_reply(message, error)
            return

        if points is not None:
            await message.add_reaction("⌛")

        print(nuggets)

        if nuggets:
            number = extract_number(message.content)
            print(number)

            if number is None:
                await safe_reply(message, "❌ You must include a number")
                return

            print("aprobado para reaccion espera en nuggets")

            await message.add_reaction("⌛")
    
    await bot.process_commands(message)

# EVENTO DE APROBACION 
@bot.event
async def on_reaction_add(reaction, user):

    if not ENABLE_IMAGE_SYSTEM:
        return

    #COMPROBAR SOLO ADMINS
    if user.bot:
        return
    
    if not user.guild_permissions.administrator:
        return
    
    message = reaction.message
    emoji = reaction.emoji

    points = get_channel_points(message.channel.id)
    nuggets = message.channel.id == config['nuggets_channel_id']

    #COMPROBAR CANALES
    if points is None and not nuggets:
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
    valid, error = validate_message(message)

    if not valid:
        await message.reply(error)
        return
    
    #CANAL DE PEPITAS
    if nuggets:
        
        #APROBAR
        if str(emoji) == "✅":   

            if is_approved:
                await message.reply("⚠️ This message is already approved")
                return
        
            await message.remove_reaction("⌛", bot.user)

            await mark_message_processed(message.id)
        
        #EXTRAER NUMERO
        number = extract_number(message.content)
        discount_multiplier = float(0.2)

        if number is None:
            await message.reply("❌ You must include a number")
            return
        
        result = int(number * discount_multiplier)

        target_channel = bot.get_channel(config['nuggets_bank_channel_id'])

        if target_channel:
            await target_channel.send(f"{user.mention} has send {number} nuggets, and {result} nuggets are owed to him")

        await message.reply("Approved successfully!")

        return
    
    #CANAL DE PUNTOS

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

    await db.execute("""
                        CREATE TABLE IF NOT EXISTS points_config
                        (key TEXT PRIMARY KEY, 
                        value REAL)
                        """)
    

    await db.execute("""
                        CREATE TABLE IF NOT EXISTS last_action
                        (id INTEGER PRIMARY KEY CHECK (id = 1), 
                        type TEXT NOT NULL,
                        data TEXT)
                        """)

    await db.commit()

# INICIO DEL BOT
bot.run(os.getenv("TOKEN"))