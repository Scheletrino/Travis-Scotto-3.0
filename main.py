import os
import json
import shutil
from datetime import datetime, timedelta
import asyncio
import discord
from discord.ext import commands, tasks
from discord import app_commands
from dotenv import load_dotenv
from keep_alive import keep_alive
from PIL import Image, ImageDraw, ImageFont
import io

# =========================
# Configurazione di base
# =========================
load_dotenv()
TOKEN = os.environ.get("DISCORD_TOKEN")

intents = discord.Intents.default()
intents.message_content = True
intents.members = True
intents.voice_states = True

bot = commands.Bot(command_prefix="!", intents=intents)
tree = bot.tree

# =========================
# File di configurazione
# =========================
XP_FILE = "xp_data.json"
CONFIG_FILE = "config.json"

# =========================
# Canali XP e Classifica
# =========================
CHANNEL_LIVELLI = 1451649159911309312       # canale dove usare /profilo /xp /rank
CHANNEL_CLASSIFICA = 1451648890200654115    # canale classifica automatica

# =========================
# Ruoli premio per livelli
# =========================
ROLE_REWARDS = {
    5: 1449438837767016518,
    10: 1449438991576207613,
    20: 1449439072824070174,
    30: 1449439207461490719,
    40: 1449439309764493342,
    50: 1449439380883374204,
    60: 1449440163431452732,
    70: 1449439450043256922,
    80: 1449440292897030357,
    90: 1449440354217627698,
    100: 1449440438455894199,
    150: 1449440494038810766,
    200: 1449440532752498749,
    300: 1449440715351523510
}

# =========================
# Helpers
# =========================
def load_xp():
    try:
        with open(XP_FILE, "r") as f:
            return json.load(f)
    except FileNotFoundError:
        return {}

def save_xp(data):
    with open(XP_FILE, "w") as f:
        json.dump(data, f, indent=4)

def load_config():
    try:
        with open(CONFIG_FILE, "r") as f:
            return json.load(f)
    except FileNotFoundError:
        return {}

def save_config(data):
    with open(CONFIG_FILE, "w") as f:
        json.dump(data, f, indent=4)

def autorizzato(interaction: discord.Interaction) -> bool:
    config = load_config()
    server_id = str(interaction.guild.id)
    roles = config.get(server_id, {}).get("admin_roles", [])
    return interaction.user.guild_permissions.administrator or any(
        role.name in roles for role in interaction.user.roles
    )

# =========================
# Sistema Livellaggio
# =========================
def xp_per_livello(lvl: int) -> int:
    return 5 * (lvl ** 2) + 50 * lvl + 100

def calcola_livello(xp_tot):
    livello = 0
    while xp_tot >= (100 * (livello + 1) ** 2):
        livello += 1
    progresso = xp_tot - (100 * livello ** 2)
    xp_next = (100 * (livello + 1) ** 2) - (100 * livello ** 2)
    return livello, progresso, xp_next

async def assegna_ruolo(member, livello):
    soglie = sorted(ROLE_REWARDS.keys())
    ruolo_target = None

    for soglia in soglie:
        if livello >= soglia:
            ruolo_target = ROLE_REWARDS[soglia]

    if ruolo_target:
        new_role = member.guild.get_role(ruolo_target)

        # Rimuovi ruoli vecchi
        for rid in ROLE_REWARDS.values():
            old_role = member.guild.get_role(rid)
            if old_role in member.roles and old_role != new_role:
                await member.remove_roles(old_role)

        # Assegna ruolo nuovo
        if new_role not in member.roles:
            await member.add_roles(new_role)
# =========================
# Controllo canale per comandi XP
# =========================
async def check_channel(interaction: discord.Interaction):
    if interaction.channel_id != CHANNEL_LIVELLI:
        await interaction.response.send_message(
            f"⛔ Puoi usare questo comando solo nel canale <#{CHANNEL_LIVELLI}>",
            ephemeral=True
        )
        return False
    return True

# =========================
# Eventi del bot
# =========================
@bot.event
async def on_ready():
    print(f"🟢 Bot avviato come {bot.user}")
    await tree.sync()
    xp_vocale_loop.start()
    backup_giornaliero_loop.start()
    aggiorna_classifica.start()

@bot.event
async def on_message(message: discord.Message):
    # Ignora bot e DM
    if message.author.bot or message.guild is None:
        return

    # Ignora messaggi troppo corti (anti-spam)
    if len(message.content) < 3:
        return

    guild_id = str(message.guild.id)
    user_id = str(message.author.id)

    data = load_xp()
    g = data.setdefault(guild_id, {})
    u = g.setdefault(user_id, {"text_xp": 0, "voice_xp": 0})

    # Accredito XP testo
    u["text_xp"] += 10

    # Calcolo livello totale
    xp_tot = u["text_xp"] + u["voice_xp"]
    livello, _, _ = calcola_livello(xp_tot)

    # Assegna eventuale ruolo
    await assegna_ruolo(message.author, livello)

    # Salva dati
    save_xp(data)

    # Mantieni la gestione dei comandi
    await bot.process_commands(message)

@bot.event
async def on_guild_join(guild: discord.Guild):
    if guild.system_channel:
        await guild.system_channel.send(
            embed=discord.Embed(
                title="👋 Grazie per aver aggiunto il bot!",
                description="Usa `/setup` per configurare il canale backup e i ruoli autorizzati.",
                color=discord.Color.blue()
            )
        )

# =========================
# XP vocale automatico (ogni 5 minuti)
# =========================
@tasks.loop(minutes=5)
async def xp_vocale_loop():
    for guild in bot.guilds:
        data = load_xp()
        server_id = str(guild.id)

        for channel in guild.voice_channels:
            for member in channel.members:
                if member.bot:
                    continue
                if member.voice.self_deaf or member.voice.deaf:
                    continue
                if guild.afk_channel and channel.id == guild.afk_channel.id:
                    continue

                user_id = str(member.id)
                data.setdefault(server_id, {}).setdefault(user_id, {"text_xp": 0, "voice_xp": 0})

                # Accredito XP voce (10 ogni 5 min)
                data[server_id][user_id]["voice_xp"] += 10

                # Calcolo livello e assegno ruolo
                xp_tot = data[server_id][user_id]["text_xp"] + data[server_id][user_id]["voice_xp"]
                livello, _, _ = calcola_livello(xp_tot)
                await assegna_ruolo(member, livello)

        save_xp(data)

# =========================
# Backup giornaliero automatico
# =========================
@tasks.loop(hours=24)
async def backup_giornaliero_loop():
    await bot.wait_until_ready()
    try:
        for guild in bot.guilds:
            config = load_config()
            server_id = str(guild.id)
            channel_id = config.get(server_id, {}).get("backup_channel")
            if not channel_id:
                continue

            channel = guild.get_channel(channel_id)
            if not channel:
                continue

            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            filename = f"backup_xp_{timestamp}.json"

            if os.path.exists(XP_FILE):
                shutil.copy(XP_FILE, filename)
            else:
                with open(filename, "w") as f:
                    json.dump({}, f)

            await channel.send(
                content=f"📦 Backup XP giornaliero ({timestamp})",
                file=discord.File(filename)
            )

            try:
                os.remove(filename)
            except:
                pass

    except Exception as e:
        print(f"Errore nel backup giornaliero: {e}")
# =========================
# Comando /profilo (card grafica)
# =========================
@tree.command(name="profilo", description="Mostra il tuo profilo XP")
async def profilo(interaction: discord.Interaction):

    # Controllo canale
    if not await check_channel(interaction):
        return

    user = interaction.user
    server_id = str(interaction.guild.id)
    user_id = str(user.id)

    data = load_xp()
    utenti = data.get(server_id, {})
    user_data = utenti.get(user_id, {"text_xp": 0, "voice_xp": 0})

    text_xp = user_data["text_xp"]
    voice_xp = user_data["voice_xp"]
    xp_tot = text_xp + voice_xp

    livello, progresso, xp_next = calcola_livello(xp_tot)

    # Calcolo posizione in classifica
    classifica = sorted(
        [(uid, d["text_xp"] + d["voice_xp"]) for uid, d in utenti.items()],
        key=lambda x: x[1],
        reverse=True
    )
    posizione = next((i + 1 for i, (uid, _) in enumerate(classifica) if uid == user_id), 0)


    # Carica sfondo
    from PIL import ImageOps
    img = Image.open("sfondo.png").convert("RGBA")
    img = ImageOps.fit(img, (934, 282), Image.Resampling.LANCZOS)
    draw = ImageDraw.Draw(img)

    # Fascia nera
    rect_top = 30
    rect_bottom = 252
    rect_left = 40
    rect_right = 894
    radius = 35

    overlay = Image.new("RGBA", img.size, (0, 0, 0, 0))
    od = ImageDraw.Draw(overlay)
    od.rounded_rectangle(
        (rect_left, rect_top, rect_right, rect_bottom),
        radius=radius,
        fill=(0, 0, 0, 165)
    )
    img = Image.alpha_composite(img, overlay)
    draw = ImageDraw.Draw(img)

    # Font
    font_title = ImageFont.truetype("Montserrat-SemiBold.ttf", 45)
    font_text = ImageFont.truetype("Montserrat-Regular.ttf", 22)
    font_label = ImageFont.truetype("Montserrat-Regular.ttf", 34)
    font_number = ImageFont.truetype("Montserrat-SemiBold.ttf", 60)

    # Avatar
    avatar_size = 150
    avatar_bytes = await user.display_avatar.read()
    avatar = Image.open(io.BytesIO(avatar_bytes)).resize((avatar_size, avatar_size)).convert("RGBA")

    mask = Image.new("L", (avatar_size, avatar_size), 0)
    ImageDraw.Draw(mask).ellipse((0, 0, avatar_size, avatar_size), fill=255)
    avatar.putalpha(mask)

    avatar_x = rect_left + 25
    avatar_y = rect_top + (rect_bottom - rect_top - avatar_size) // 2

    # Cornice avatar
    ImageDraw.Draw(img).ellipse(
        (avatar_x - 3, avatar_y - 3, avatar_x + avatar_size + 3, avatar_y + avatar_size + 3),
        outline=(0, 0, 0),
        width=3
    )

    img.paste(avatar, (avatar_x, avatar_y), avatar)

    # Rank + Level
    base_x = rect_right - 480
    base_y = rect_top + 45

    draw.text((base_x, base_y), "RANK", font=font_label, fill=(255, 255, 255))
    draw.text((base_x + 115, base_y - 6), f"#{posizione}", font=font_number, fill=(255, 255, 255))

    draw.text((base_x + 255, base_y), "LEVEL", font=font_label, fill=(0, 170, 255))
    draw.text((base_x + 395, base_y - 6), f"{livello}", font=font_number, fill=(0, 170, 255))

    # Nome
    name_x = avatar_x + avatar_size + 60
    name_y = rect_top + 120
    draw.text((name_x, name_y), user.name, font=font_title, fill=(255, 255, 255))

    # Barra XP
    bar_x = name_x
    bar_y = name_y + 65
    bar_width, bar_height = 560, 22

    draw.rectangle((bar_x, bar_y, bar_x + bar_width, bar_y + bar_height), fill=(60, 60, 60))
    progress_width = int(bar_width * (progresso / xp_next)) if xp_next > 0 else bar_width
    draw.rectangle((bar_x, bar_y, bar_x + progress_width, bar_y + bar_height), fill=(0, 255, 200))

    # Testo XP
    xp_text = f"{xp_tot} XP / {xp_tot + (xp_next - progresso)} XP"
    bbox = draw.textbbox((0, 0), xp_text, font=font_text)
    text_w = bbox[2] - bbox[0]
    xp_text_x = bar_x + bar_width - text_w
    xp_text_y = bar_y - 30

    draw.text((xp_text_x, xp_text_y), xp_text, font=font_text, fill=(255, 255, 255))

    # Output immagine
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    buf.seek(0)

    await interaction.response.send_message(file=discord.File(buf, "profilo.png"))


# =========================
# Comando /classifica migliorato
# =========================
@tree.command(name="classifica", description="Mostra la classifica XP del server")
async def classifica(interaction: discord.Interaction):

    # Limitato al canale livelli
    if not await check_channel(interaction):
        return


    server_id = str(interaction.guild.id)
    data = load_xp()
    utenti = data.get(server_id, {})

    classifica = sorted(
        utenti.items(),
        key=lambda x: x[1]["text_xp"] + x[1]["voice_xp"],
        reverse=True
    )

    embed = discord.Embed(
        title="🏆 Classifica XP",
        description="Top 10 utenti con più esperienza",
        color=discord.Color.gold()
    )

    for i, (uid, xp_data) in enumerate(classifica[:10], start=1):
        membro = interaction.guild.get_member(int(uid))
        if membro:
            totale = xp_data["text_xp"] + xp_data["voice_xp"]
            embed.add_field(
                name=f"**#{i} — {membro.display_name}**",
                value=f"✨ **{totale} XP**",
                inline=False
            )

    await interaction.response.send_message(embed=embed)


# =========================
# Classifica automatica ogni minuto
# =========================
@tasks.loop(minutes=1)
async def aggiorna_classifica():
    channel = bot.get_channel(CHANNEL_CLASSIFICA)
    if channel is None:
        return

    data = load_xp()
    if not data:
        return

    server_id = list(data.keys())[0]
    utenti = data[server_id]

    classifica = sorted(
        utenti.items(),
        key=lambda x: x[1]["text_xp"] + x[1]["voice_xp"],
        reverse=True
    )

    embed = discord.Embed(
        title="🏆 Classifica XP (Aggiornata ogni minuto)",
        color=discord.Color.gold()
    )

    guild = channel.guild

    for i, (uid, xp_data) in enumerate(classifica[:10], start=1):
        membro = guild.get_member(int(uid))
        totale = xp_data["text_xp"] + xp_data["voice_xp"]

        if membro:
            nome = membro.display_name
        else:
            nome = f"Utente sconosciuto ({uid})"

        embed.add_field(
            name=f"**#{i} — {nome}**",
            value=f"✨ **{totale} XP**",
            inline=False
        )

    # Cancella il messaggio precedente
    await channel.purge(limit=1)

    # Invia la nuova classifica
    await channel.send(embed=embed)

# =========================
# Comandi XP: dettagli e gestione
# =========================
@tree.command(name="xp", description="Mostra l'XP totale di un utente")
@app_commands.describe(membro="Utente da controllare (se vuoto, te stesso)")
async def xp(interaction: discord.Interaction, membro: discord.Member | None = None):

    # Limitato al canale livelli
    if not await check_channel(interaction):
        return

    if membro is None:
        membro = interaction.user

    server_id = str(interaction.guild.id)
    data = load_xp()
    user_data = data.get(server_id, {}).get(str(membro.id), {})
    text_xp = user_data.get("text_xp", 0)
    voice_xp = user_data.get("voice_xp", 0)
    total = text_xp + voice_xp

    await interaction.response.send_message(
        f"{membro.mention} — Totale: **{total} XP** | 💬 {text_xp} | 🔊 {voice_xp}"
    )


@tree.command(name="xpvoce", description="Mostra il tuo XP vocale")
async def xpvoce(interaction: discord.Interaction):

    # Limitato al canale livelli
    if not await check_channel(interaction):
        return

    server_id = str(interaction.guild.id)
    data = load_xp()
    user_data = data.get(server_id, {}).get(str(interaction.user.id), {})
    voice_xp = user_data.get("voice_xp", 0)
    await interaction.response.send_message(f"🔊 Il tuo XP vocale è: **{voice_xp}**")


@tree.command(name="xptesto", description="Mostra il tuo XP testuale")
async def xptesto(interaction: discord.Interaction):

    # Limitato al canale livelli
    if not await check_channel(interaction):
        return

    server_id = str(interaction.guild.id)
    data = load_xp()
    user_data = data.get(server_id, {}).get(str(interaction.user.id), {})
    text_xp = user_data.get("text_xp", 0)
    await interaction.response.send_message(f"💬 Il tuo XP testuale è: **{text_xp}**")


# =========================
# Gestione XP (solo admin)
# =========================
@tree.command(name="aggiungixp", description="Aggiunge XP a un utente (solo admin)")
@app_commands.describe(membro="Utente", tipo="testo o voce", quantità="XP da aggiungere")
async def aggiungixp(interaction: discord.Interaction, membro: discord.Member, tipo: str, quantità: int):
    if not autorizzato(interaction):
        await interaction.response.send_message("⛔ Non hai il permesso per usare questo comando.", ephemeral=True)
        return

    tipo = tipo.lower()
    if tipo not in ["testo", "voce"]:
        await interaction.response.send_message("⚠️ Tipo XP non valido. Usa 'testo' o 'voce'.", ephemeral=True)
        return

    server_id = str(interaction.guild.id)
    data = load_xp()
    data.setdefault(server_id, {}).setdefault(str(membro.id), {"text_xp": 0, "voice_xp": 0})

    # Accredito XP
    if tipo == "testo":
        data[server_id][str(membro.id)]["text_xp"] += quantità
    else:
        data[server_id][str(membro.id)]["voice_xp"] += quantità

    # Calcolo livello e assegno ruoli
    xp_tot = data[server_id][str(membro.id)]["text_xp"] + data[server_id][str(membro.id)]["voice_xp"]
    livello, _, _ = calcola_livello(xp_tot)
    await assegna_ruolo(membro, livello)

    save_xp(data)

    await interaction.response.send_message(
        f"✅ Aggiunti {quantità} XP {tipo} a {membro.mention}. Ora è livello {livello}.",
        ephemeral=True
    )


@tree.command(name="rimuovixp", description="Rimuove XP da un utente (solo admin)")
@app_commands.describe(membro="Utente", tipo="testo o voce", quantità="XP da rimuovere")
async def rimuovixp(interaction: discord.Interaction, membro: discord.Member, tipo: str, quantità: int):
    if not autorizzato(interaction):
        await interaction.response.send_message("⛔ Non hai il permesso per usare questo comando.", ephemeral=True)
        return

    tipo = tipo.lower()
    if tipo not in ["testo", "voce"]:
        await interaction.response.send_message("⚠️ Tipo XP non valido. Usa 'testo' o 'voce'.", ephemeral=True)
        return

    server_id = str(interaction.guild.id)
    data = load_xp()
    data.setdefault(server_id, {}).setdefault(str(membro.id), {"text_xp": 0, "voice_xp": 0})

    if tipo == "testo":
        data[server_id][str(membro.id)]["text_xp"] = max(0, data[server_id][str(membro.id)]["text_xp"] - quantità)
    else:
        data[server_id][str(membro.id)]["voice_xp"] = max(0, data[server_id][str(membro.id)]["voice_xp"] - quantità)

    # ricalcolo livello e ruoli
    xp_tot = data[server_id][str(membro.id)]["text_xp"] + data[server_id][str(membro.id)]["voice_xp"]
    livello, _, _ = calcola_livello(xp_tot)
    await assegna_ruolo(membro, livello)

    save_xp(data)

    await interaction.response.send_message(
        f"✅ Rimossi {quantità} XP {tipo} da {membro.mention}. Ora è livello {livello}.",
        ephemeral=True
    )


@tree.command(name="resetxp", description="Resetta XP di un utente o di tutti (solo admin)")
@app_commands.describe(membro="Utente da resettare (lascia vuoto per tutti)", tutti="Se true, resetta tutti")
async def resetxp(interaction: discord.Interaction, membro: discord.Member | None = None, tutti: bool = False):
    if not autorizzato(interaction):
        await interaction.response.send_message("⛔ Non hai il permesso per usare questo comando.", ephemeral=True)
        return

    server_id = str(interaction.guild.id)
    data = load_xp()

    if tutti:
        data[server_id] = {}
        save_xp(data)
        await interaction.response.send_message("♻️ XP di tutti gli utenti resettato.", ephemeral=True)
        return

    if membro is None:
        await interaction.response.send_message(
            "⚠️ Specifica un utente oppure usa l'opzione 'tutti: true'.",
            ephemeral=True
        )
        return

    data.setdefault(server_id, {}).setdefault(str(membro.id), {"text_xp": 0, "voice_xp": 0})
    data[server_id][str(membro.id)] = {"text_xp": 0, "voice_xp": 0}
    save_xp(data)

    await interaction.response.send_message(f"♻️ XP di {membro.mention} resettato.", ephemeral=True)


@tree.command(name="backupxp", description="Crea e invia un backup XP (solo admin)")
async def backupxp(interaction: discord.Interaction):
    if not autorizzato(interaction):
        await interaction.response.send_message("⛔ Non hai il permesso per usare questo comando.", ephemeral=True)
        return

    config = load_config()
    server_id = str(interaction.guild.id)
    channel_id = config.get(server_id, {}).get("backup_channel")

    if not channel_id:
        await interaction.response.send_message("⚠️ Nessun canale backup configurato. Usa `/setbackup`.", ephemeral=True)
        return

    channel = interaction.guild.get_channel(channel_id)
    if not channel:
        await interaction.response.send_message("⚠️ Canale backup non trovato.", ephemeral=True)
        return

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    filename = f"backup_xp_{timestamp}.json"

    try:
        if os.path.exists(XP_FILE):
            shutil.copy(XP_FILE, filename)
        else:
            with open(filename, "w") as f:
                json.dump({}, f)

        await channel.send(
            content=f"📦 Backup XP manuale ({timestamp})",
            file=discord.File(filename)
        )

        await interaction.response.send_message("✅ Backup inviato al canale configurato.", ephemeral=True)

        try:
            os.remove(filename)
        except:
            pass

    except Exception as e:
        await interaction.response.send_message(f"❌ Errore nel backup: {e}", ephemeral=True)


@tree.command(name="ripristinaxp", description="Ripristina XP da un file allegato (solo admin)")
@app_commands.describe(file="File JSON di backup (allegalo al comando)")
async def ripristinaxp(interaction: discord.Interaction, file: discord.Attachment):
    if not autorizzato(interaction):
        await interaction.response.send_message("⛔ Non hai il permesso per usare questo comando.", ephemeral=True)
        return

    if not file.filename.lower().endswith(".json"):
        await interaction.response.send_message("⚠️ Carica un file .json valido.", ephemeral=True)
        return

    try:
        content = await file.read()
        data = json.loads(content.decode("utf-8"))

        with open(XP_FILE, "w") as f:
            json.dump(data, f, indent=4)

        await interaction.response.send_message("✅ XP ripristinato dal backup.", ephemeral=True)

    except Exception as e:
        await interaction.response.send_message(f"❌ Errore nel ripristino: {e}", ephemeral=True)


# =========================
# Comandi Setup/Admin
# =========================
@tree.command(name="setup", description="Configura canale backup e ruoli admin")
@app_commands.describe(canale="Canale per i backup", ruoli="Lista di ruoli admin separati da virgola")
async def setup(interaction: discord.Interaction, canale: discord.TextChannel, ruoli: str):
    if not interaction.user.guild_permissions.administrator:
        await interaction.response.send_message("⛔ Solo gli amministratori possono usare questo comando.", ephemeral=True)
        return

    config = load_config()
    server_id = str(interaction.guild.id)
    config.setdefault(server_id, {})

    config[server_id]["backup_channel"] = canale.id
    config[server_id]["admin_roles"] = [r.strip() for r in ruoli.split(",")]

    save_config(config)

    await interaction.response.send_message(
        f"✅ Setup completato.\nCanale backup: {canale.mention}\nRuoli admin: {', '.join(config[server_id]['admin_roles'])}",
        ephemeral=True
    )


@tree.command(name="setbackup", description="Imposta solo il canale backup")
async def setbackup(interaction: discord.Interaction, canale: discord.TextChannel):
    if not interaction.user.guild_permissions.administrator:
        await interaction.response.send_message("⛔ Solo gli amministratori possono usare questo comando.", ephemeral=True)
        return

    config = load_config()
    server_id = str(interaction.guild.id)
    config.setdefault(server_id, {})

    config[server_id]["backup_channel"] = canale.id
    save_config(config)

    await interaction.response.send_message(
        f"✅ Canale backup impostato su {canale.mention}",
        ephemeral=True
    )


@tree.command(name="setadminrole", description="Aggiunge o rimuove un ruolo admin")
@app_commands.describe(ruolo="Ruolo da gestire", azione="add o remove")
async def setadminrole(interaction: discord.Interaction, ruolo: discord.Role, azione: str):
    if not interaction.user.guild_permissions.administrator:
        await interaction.response.send_message("⛔ Solo gli amministratori possono usare questo comando.", ephemeral=True)
        return

    azione = azione.lower()
    if azione not in ["add", "remove"]:
        await interaction.response.send_message("⚠️ Azione non valida. Usa 'add' o 'remove'.", ephemeral=True)
        return

    config = load_config()
    server_id = str(interaction.guild.id)
    config.setdefault(server_id, {}).setdefault("admin_roles", [])

    if azione == "add":
        if ruolo.name not in config[server_id]["admin_roles"]:
            config[server_id]["admin_roles"].append(ruolo.name)
    else:
        if ruolo.name in config[server_id]["admin_roles"]:
            config[server_id]["admin_roles"].remove(ruolo.name)

    save_config(config)

    await interaction.response.send_message(
        f"✅ Ruoli admin aggiornati: {', '.join(config[server_id]['admin_roles'])}",
        ephemeral=True
    )


# =========================
# Avvio bot
# =========================
keep_alive()
bot.run(TOKEN)
