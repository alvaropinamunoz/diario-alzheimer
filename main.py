import os
import json
import logging
from datetime import datetime, timedelta
import anthropic
import gspread
from google.oauth2.service_account import Credentials
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application, MessageHandler, CommandHandler,
    filters, ContextTypes, CallbackQueryHandler
)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

TELEGRAM_TOKEN = os.environ["TELEGRAM_TOKEN"]
ANTHROPIC_API_KEY = os.environ["ANTHROPIC_API_KEY"]
GOOGLE_CREDENTIALS_JSON = os.environ["GOOGLE_CREDENTIALS_JSON"]
SPREADSHEET_ID = os.environ["SPREADSHEET_ID"]

STATE_WAITING_DESCRIPCION = "waiting_descripcion"
STATE_WAITING_LUGAR = "waiting_lugar"
STATE_WAITING_PERSONAS = "waiting_personas"
STATE_WAITING_GRAVEDAD = "waiting_gravedad"
STATE_WAITING_ESTADO = "waiting_estado"
STATE_WAITING_CONSCIENTE = "waiting_consciente"

BUTTON_STATES = {STATE_WAITING_GRAVEDAD, STATE_WAITING_ESTADO, STATE_WAITING_CONSCIENTE}

COL_FECHA = 0
COL_HORA = 1
COL_USUARIO = 2
COL_DESCRIPCION = 3
COL_LUGAR = 4
COL_PERSONAS = 5
COL_GRAVEDAD = 6
COL_NOTAS = 7
COL_TIPO_OLVIDO = 8
COL_ESTADO = 9
COL_CONSCIENTE = 10

CALLBACK_MAP = {
    "gravedad_leve":      ("gravedad",         "Leve"),
    "gravedad_moderada":  ("gravedad",         "Moderada"),
    "gravedad_severa":    ("gravedad",         "Severa"),
    "estado_tranquilo":   ("estado_paciente",  "Tranquilo/a"),
    "estado_agitado":     ("estado_paciente",  "Agitado/a"),
    "estado_triste":      ("estado_paciente",  "Triste/Deprimido/a"),
    "estado_confuso":     ("estado_paciente",  "Confuso/a"),
    "estado_normal":      ("estado_paciente",  "Normal/Bien"),
    "consciente_si":      ("consciente_olvido","Sí"),
    "consciente_no":      ("consciente_olvido","No"),
}


def get_sheet():
    creds_dict = json.loads(GOOGLE_CREDENTIALS_JSON)
    scopes = [
        "https://spreadsheets.google.com/feeds",
        "https://www.googleapis.com/auth/drive"
    ]
    creds = Credentials.from_service_account_info(creds_dict, scopes=scopes)
    client = gspread.authorize(creds)
    return client.open_by_key(SPREADSHEET_ID).sheet1


def extract_data(text: str) -> dict:
    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)

    prompt = f"""Eres un asistente que ayuda a registrar episodios de pérdida de memoria de un paciente con Alzheimer.

Un familiar ha escrito el siguiente mensaje describiendo un episodio. Extrae la información y devuelve ÚNICAMENTE un objeto JSON con estos campos:
- descripcion: resumen breve y claro del episodio de pérdida de memoria. Si el mensaje no describe ningún episodio concreto o es demasiado vago para entender qué ocurrió, devuelve null
- lugar: dónde ocurrió. Si no se menciona explícitamente, devuelve null
- personas_presentes: quién estaba presente además del paciente. Si no se menciona, devuelve null
- gravedad: clasifica como "Leve", "Moderada" o "Severa" solo si el texto da información suficiente. Si no puedes determinarlo con confianza, devuelve null
- notas_extra: cualquier detalle adicional relevante. Si no hay, devuelve cadena vacía ""
- tipo_olvido: clasifica SIEMPRE como "Memoria reciente", "Memoria remota", "Reconocimiento de personas", "Orientación espacial" u "Orientación temporal". Usa tu mejor criterio aunque no esté explícito en el texto
- estado_paciente: estado anímico o físico del paciente en ese momento. Si no se menciona, devuelve null
- consciente_olvido: si el paciente fue consciente de su propio olvido, devuelve "Sí" o "No". Si no se puede saber, devuelve null

Devuelve null para cualquier campo que no puedas determinar. Nunca devuelvas "No indicado" ni cadenas vacías para campos opcionales.
Responde SOLO con el JSON. Sin texto adicional, sin bloques de código, sin explicaciones.

Mensaje: {text}"""

    response = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=500,
        messages=[{"role": "user", "content": prompt}]
    )

    return json.loads(response.content[0].text)


def get_next_question(data: dict):
    """Devuelve (state, pregunta, keyboard) para el siguiente campo pendiente, o (None, None, None) si está completo."""

    if not data.get("descripcion"):
        return STATE_WAITING_DESCRIPCION, "No he entendido bien qué ocurrió. ¿Puedes describir el episodio con más detalle?", None

    if not data.get("lugar"):
        return STATE_WAITING_LUGAR, "¿Dónde ocurrió el episodio?", None

    if not data.get("personas_presentes"):
        return STATE_WAITING_PERSONAS, "¿Quién estaba presente además del paciente?\n(Si estaba solo, escribe «Solo»)", None

    if data.get("gravedad") is None:
        keyboard = InlineKeyboardMarkup([[
            InlineKeyboardButton("🟡 Leve", callback_data="gravedad_leve"),
            InlineKeyboardButton("🟠 Moderada", callback_data="gravedad_moderada"),
            InlineKeyboardButton("🔴 Severa", callback_data="gravedad_severa"),
        ]])
        return STATE_WAITING_GRAVEDAD, "¿Cómo de grave fue el episodio?", keyboard

    if data.get("estado_paciente") is None:
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("😌 Tranquilo/a", callback_data="estado_tranquilo"),
             InlineKeyboardButton("😰 Agitado/a", callback_data="estado_agitado")],
            [InlineKeyboardButton("😢 Triste/Deprimido/a", callback_data="estado_triste"),
             InlineKeyboardButton("😵 Confuso/a", callback_data="estado_confuso")],
            [InlineKeyboardButton("😊 Normal/Bien", callback_data="estado_normal")],
        ])
        return STATE_WAITING_ESTADO, "¿Cómo estaba el paciente en ese momento?", keyboard

    if data.get("consciente_olvido") is None:
        keyboard = InlineKeyboardMarkup([[
            InlineKeyboardButton("Sí", callback_data="consciente_si"),
            InlineKeyboardButton("No", callback_data="consciente_no"),
        ]])
        return STATE_WAITING_CONSCIENTE, "¿El paciente fue consciente de su propio olvido?", keyboard

    return None, None, None


async def ask_next_question(responder, context: ContextTypes.DEFAULT_TYPE):
    """Envía la siguiente pregunta pendiente o guarda y confirma si ya está todo completo."""
    data = context.user_data["pending_data"]
    state, question, keyboard = get_next_question(data)

    if state is None:
        user_name = context.user_data["user_name"]
        fecha = context.user_data["fecha"]
        hora = context.user_data["hora"]
        await save_to_sheet(data, user_name, fecha, hora)
        text = build_confirmation(data, user_name, fecha, hora)
        clear_pending(context)
        await responder(text, parse_mode="Markdown")
        return

    context.user_data["state"] = state
    kwargs = {"reply_markup": keyboard} if keyboard else {}
    await responder(question, **kwargs)


def build_confirmation(data: dict, user_name: str, fecha: str, hora: str) -> str:
    gravedad = data.get("gravedad") or "No indicado"
    emoji_gravedad = {"Leve": "🟡", "Moderada": "🟠", "Severa": "🔴"}.get(gravedad, "⚪")

    confirmacion = (
        f"✅ *Registrado correctamente*\n\n"
        f"📅 {fecha} a las {hora}\n"
        f"👤 {user_name}\n"
        f"📝 {data.get('descripcion', '')}\n"
        f"📍 Lugar: {data.get('lugar') or 'No indicado'}\n"
        f"👥 Presentes: {data.get('personas_presentes') or 'No indicado'}\n"
        f"{emoji_gravedad} Gravedad: {gravedad}\n"
        f"🧠 Tipo de olvido: {data.get('tipo_olvido') or 'No indicado'}\n"
        f"😌 Estado del paciente: {data.get('estado_paciente') or 'No indicado'}\n"
        f"🔍 Consciente del olvido: {data.get('consciente_olvido') or 'No indicado'}"
    )

    if data.get("notas_extra"):
        confirmacion += f"\n📌 Notas: {data['notas_extra']}"

    return confirmacion


async def save_to_sheet(data: dict, user_name: str, fecha: str, hora: str):
    sheet = get_sheet()
    sheet.append_row([
        fecha,
        hora,
        user_name,
        data.get("descripcion", ""),
        data.get("lugar") or "No indicado",
        data.get("personas_presentes") or "No indicado",
        data.get("gravedad") or "No indicado",
        data.get("notas_extra", ""),
        data.get("tipo_olvido") or "No indicado",
        data.get("estado_paciente") or "No indicado",
        data.get("consciente_olvido") or "No indicado",
    ])


def clear_pending(context: ContextTypes.DEFAULT_TYPE):
    for key in ("state", "pending_data", "user_name", "fecha", "hora"):
        context.user_data.pop(key, None)


def get_cell(row: list, index: int, default: str = "") -> str:
    return row[index].strip() if len(row) > index else default


def parse_fecha(fecha_str: str):
    try:
        return datetime.strptime(fecha_str.strip(), "%d/%m/%Y")
    except ValueError:
        return None


def calcular_estadisticas(rows: list, titulo: str) -> str:
    if not rows:
        return f"📊 *{titulo}*\n\nNo hay episodios en este período."

    total = len(rows)
    gravedad_counts: dict = {}
    tipo_counts: dict = {}

    for row in rows:
        g = get_cell(row, COL_GRAVEDAD, "No indicado")
        gravedad_counts[g] = gravedad_counts.get(g, 0) + 1
        t = get_cell(row, COL_TIPO_OLVIDO, "No indicado")
        tipo_counts[t] = tipo_counts.get(t, 0) + 1

    tipo_frecuente = max(tipo_counts, key=tipo_counts.get) if tipo_counts else "No disponible"

    resultado = f"📊 *{titulo}*\n\nTotal de episodios: *{total}*\n\n*Gravedad:*\n"
    for g, emoji in [("Leve", "🟡"), ("Moderada", "🟠"), ("Severa", "🔴")]:
        if g in gravedad_counts:
            resultado += f"  {emoji} {g}: {gravedad_counts[g]}\n"

    resultado += f"\n*Tipo más frecuente:* {tipo_frecuente}\n"
    return resultado


async def send_long_message(update: Update, text: str, parse_mode: str = None):
    max_len = 4096
    while text:
        if len(text) <= max_len:
            await update.message.reply_text(text, parse_mode=parse_mode)
            break
        split_at = text.rfind("\n", 0, max_len)
        if split_at == -1:
            split_at = max_len
        await update.message.reply_text(text[:split_at], parse_mode=parse_mode)
        text = text[split_at:].lstrip()


# ──────────────────────────────────────────
# Comandos
# ──────────────────────────────────────────

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    clear_pending(context)
    await update.message.reply_text(
        "👋 Hola, soy el bot del Diario de Memoria.\n\n"
        "Cuéntame con tus propias palabras lo que ha pasado y yo me encargo de registrarlo en la hoja de cálculo.\n\n"
        "Por ejemplo:\n"
        "_«Hoy papá no reconoció a su nieto cuando vino a visitarle. Estábamos en el salón de casa, eran las 6 de la tarde»_",
        parse_mode="Markdown"
    )


async def ayuda(update: Update, context: ContextTypes.DEFAULT_TYPE):
    clear_pending(context)
    await update.message.reply_text(
        "📖 *Comandos disponibles*\n\n"
        "• *Escribe un mensaje* — describe un episodio con tus palabras y el bot lo registra automáticamente\n\n"
        "📋 *Informes*\n"
        "/informe — resumen estructurado listo para el médico\n"
        "/semana — estadísticas de los últimos 7 días\n"
        "/mes — estadísticas del mes actual\n\n"
        "🔍 *Búsqueda y gestión*\n"
        "/buscar <texto> — busca episodios cuya descripción contenga ese texto\n"
        "/borrar — elimina el último registro\n"
        "/borrar 3 — elimina los últimos 3 registros (máximo 5)\n\n"
        "ℹ️ *Otros*\n"
        "/cancelar — cancela el registro que estás haciendo\n"
        "/start — mensaje de bienvenida\n"
        "/ayuda — muestra este menú",
        parse_mode="Markdown"
    )


async def cancelar(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if context.user_data.get("state") or context.user_data.get("pending_data"):
        clear_pending(context)
        await update.message.reply_text("❌ Registro cancelado. Puedes empezar de nuevo cuando quieras.")
    else:
        await update.message.reply_text("No hay ningún registro en curso.")


async def borrar(update: Update, context: ContextTypes.DEFAULT_TYPE):
    clear_pending(context)
    args = context.args
    n = 1

    if args:
        try:
            n = int(args[0])
        except ValueError:
            await update.message.reply_text("Uso: /borrar [número entre 1 y 5]. Ejemplo: /borrar 3")
            return

    if n < 1 or n > 5:
        await update.message.reply_text("Puedes borrar entre 1 y 5 registros a la vez.")
        return

    try:
        sheet = get_sheet()
        all_values = sheet.get_all_values()
        non_empty = [
            (i + 1, row) for i, row in enumerate(all_values)
            if any(cell.strip() for cell in row)
        ]

        if not non_empty:
            await update.message.reply_text("No hay registros que borrar.")
            return

        n = min(n, len(non_empty))
        to_delete = non_empty[-n:]
        first_idx = to_delete[0][0]
        last_idx = to_delete[-1][0]

        sheet.delete_rows(first_idx, last_idx)

        confirmacion = f"🗑️ *{len(to_delete)} registro(s) eliminado(s):*\n\n"
        for _, row in to_delete:
            fecha = get_cell(row, COL_FECHA, "?")
            hora = get_cell(row, COL_HORA, "?")
            descripcion = get_cell(row, COL_DESCRIPCION, "Sin descripción")
            confirmacion += f"• {fecha} {hora} — {descripcion}\n"

        await update.message.reply_text(confirmacion, parse_mode="Markdown")

    except Exception as e:
        logger.error(f"Error en /borrar: {e}")
        await update.message.reply_text("❌ Error al borrar. Inténtalo de nuevo.")


async def informe(update: Update, context: ContextTypes.DEFAULT_TYPE):
    clear_pending(context)
    try:
        sheet = get_sheet()
        all_rows = sheet.get_all_values()
        data_rows = [row for row in all_rows if any(cell.strip() for cell in row)]

        if not data_rows:
            await update.message.reply_text("No hay registros en la hoja de cálculo.")
            return

        processing = await update.message.reply_text("⏳ Generando informe...")

        registros_text = ""
        for row in data_rows:
            registros_text += (
                f"- Fecha: {get_cell(row, COL_FECHA)} {get_cell(row, COL_HORA)} | "
                f"Descripción: {get_cell(row, COL_DESCRIPCION)} | "
                f"Gravedad: {get_cell(row, COL_GRAVEDAD)} | "
                f"Tipo: {get_cell(row, COL_TIPO_OLVIDO)} | "
                f"Estado: {get_cell(row, COL_ESTADO)} | "
                f"Consciente: {get_cell(row, COL_CONSCIENTE)}\n"
            )

        prompt = f"""Eres un asistente médico especializado en Alzheimer. Analiza los siguientes registros de episodios de memoria y genera un informe estructurado para el médico.

El informe debe incluir:
1. Total de episodios registrados y período cubierto
2. Tipos de olvido más frecuentes (con porcentajes)
3. Evolución de la gravedad a lo largo del tiempo
4. Horas del día con más episodios
5. Observaciones relevantes para el médico

Registros:
{registros_text}

Genera el informe en español, de forma clara y concisa."""

        client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
        response = client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=1500,
            messages=[{"role": "user", "content": prompt}]
        )

        await processing.delete()
        await send_long_message(update, f"📋 INFORME PARA EL MÉDICO\n\n{response.content[0].text}")

    except Exception as e:
        logger.error(f"Error en /informe: {e}")
        await update.message.reply_text("❌ Error al generar el informe. Inténtalo de nuevo.")


async def semana(update: Update, context: ContextTypes.DEFAULT_TYPE):
    clear_pending(context)
    try:
        sheet = get_sheet()
        all_rows = sheet.get_all_values()
        hoy = datetime.now().date()
        hace_7_dias = hoy - timedelta(days=7)

        rows_semana = [
            row for row in all_rows
            if (f := parse_fecha(get_cell(row, COL_FECHA))) and hace_7_dias <= f.date() <= hoy
        ]

        await update.message.reply_text(
            calcular_estadisticas(rows_semana, "Últimos 7 días"),
            parse_mode="Markdown"
        )

    except Exception as e:
        logger.error(f"Error en /semana: {e}")
        await update.message.reply_text("❌ Error al calcular estadísticas. Inténtalo de nuevo.")


async def mes(update: Update, context: ContextTypes.DEFAULT_TYPE):
    clear_pending(context)
    try:
        sheet = get_sheet()
        all_rows = sheet.get_all_values()
        hoy = datetime.now()

        rows_mes = [
            row for row in all_rows
            if (f := parse_fecha(get_cell(row, COL_FECHA))) and f.month == hoy.month and f.year == hoy.year
        ]

        nombres_mes = [
            "enero", "febrero", "marzo", "abril", "mayo", "junio",
            "julio", "agosto", "septiembre", "octubre", "noviembre", "diciembre"
        ]
        titulo = f"Mes de {nombres_mes[hoy.month - 1]} {hoy.year}"

        await update.message.reply_text(
            calcular_estadisticas(rows_mes, titulo),
            parse_mode="Markdown"
        )

    except Exception as e:
        logger.error(f"Error en /mes: {e}")
        await update.message.reply_text("❌ Error al calcular estadísticas. Inténtalo de nuevo.")


async def buscar(update: Update, context: ContextTypes.DEFAULT_TYPE):
    clear_pending(context)
    if not context.args:
        await update.message.reply_text("Uso: /buscar <texto>. Ejemplo: /buscar nieto")
        return

    query_text = " ".join(context.args).lower()

    try:
        sheet = get_sheet()
        all_rows = sheet.get_all_values()
        resultados = [
            row for row in all_rows
            if query_text in get_cell(row, COL_DESCRIPCION).lower()
        ]

        if not resultados:
            await update.message.reply_text(f"No se encontraron resultados para «{query_text}».")
            return

        mensaje = f"🔍 *{len(resultados)} resultado(s) para «{query_text}»:*\n\n"
        for row in resultados[:10]:
            fecha = get_cell(row, COL_FECHA)
            hora = get_cell(row, COL_HORA)
            descripcion = get_cell(row, COL_DESCRIPCION)
            gravedad = get_cell(row, COL_GRAVEDAD)
            emoji = {"Leve": "🟡", "Moderada": "🟠", "Severa": "🔴"}.get(gravedad, "⚪")
            mensaje += f"📅 {fecha} {hora} {emoji}\n{descripcion}\n\n"

        if len(resultados) > 10:
            mensaje += f"_...y {len(resultados) - 10} resultado(s) más._"

        await send_long_message(update, mensaje, parse_mode="Markdown")

    except Exception as e:
        logger.error(f"Error en /buscar: {e}")
        await update.message.reply_text("❌ Error al buscar. Inténtalo de nuevo.")


# ──────────────────────────────────────────
# Gestión de mensajes y conversación
# ──────────────────────────────────────────

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message = update.message
    if not message or not message.text:
        return

    state = context.user_data.get("state")

    if state == STATE_WAITING_DESCRIPCION:
        context.user_data["pending_data"]["descripcion"] = message.text.strip()
        await ask_next_question(message.reply_text, context)
        return

    if state == STATE_WAITING_LUGAR:
        context.user_data["pending_data"]["lugar"] = message.text.strip()
        await ask_next_question(message.reply_text, context)
        return

    if state == STATE_WAITING_PERSONAS:
        context.user_data["pending_data"]["personas_presentes"] = message.text.strip()
        await ask_next_question(message.reply_text, context)
        return

    if state in BUTTON_STATES:
        await message.reply_text("Por favor, usa los botones para responder.")
        return

    # Episodio nuevo
    text = message.text
    user = message.from_user
    user_name = user.full_name or user.username or "Desconocido"
    processing = await message.reply_text("⏳ Analizando y registrando...")

    try:
        data = extract_data(text)
        now = datetime.now()
        fecha = now.strftime("%d/%m/%Y")
        hora = now.strftime("%H:%M")

        context.user_data["pending_data"] = data
        context.user_data["user_name"] = user_name
        context.user_data["fecha"] = fecha
        context.user_data["hora"] = hora

        await processing.delete()
        await ask_next_question(message.reply_text, context)

    except Exception as e:
        logger.error(f"Error procesando mensaje: {e}")
        await processing.delete()
        clear_pending(context)
        await message.reply_text("❌ Hubo un error al registrar el episodio. Por favor inténtalo de nuevo.")


async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    state = context.user_data.get("state")
    data = context.user_data.get("pending_data")

    if data is None or state not in BUTTON_STATES:
        await query.edit_message_text("Esta respuesta ya no es válida. Envía un nuevo mensaje para registrar un episodio.")
        return

    if query.data not in CALLBACK_MAP:
        await query.edit_message_text("Respuesta no reconocida. Envía un nuevo mensaje.")
        return

    field, value = CALLBACK_MAP[query.data]
    data[field] = value

    await ask_next_question(query.edit_message_text, context)


def main():
    app = Application.builder().token(TELEGRAM_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("ayuda", ayuda))
    app.add_handler(CommandHandler("cancelar", cancelar))
    app.add_handler(CommandHandler("borrar", borrar))
    app.add_handler(CommandHandler("informe", informe))
    app.add_handler(CommandHandler("semana", semana))
    app.add_handler(CommandHandler("mes", mes))
    app.add_handler(CommandHandler("buscar", buscar))
    app.add_handler(CallbackQueryHandler(handle_callback))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    logger.info("Bot arrancado")
    app.run_polling()


if __name__ == "__main__":
    main()
