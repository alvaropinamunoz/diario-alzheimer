import os
import json
import logging
from datetime import datetime
import anthropic
import gspread
from google.oauth2.service_account import Credentials
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application, MessageHandler, CommandHandler,
    filters, ContextTypes, ConversationHandler, CallbackQueryHandler
)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

TELEGRAM_TOKEN = os.environ["TELEGRAM_TOKEN"]
ANTHROPIC_API_KEY = os.environ["ANTHROPIC_API_KEY"]
GOOGLE_CREDENTIALS_JSON = os.environ["GOOGLE_CREDENTIALS_JSON"]
SPREADSHEET_ID = os.environ["SPREADSHEET_ID"]

WAITING_ESTADO, WAITING_CONSCIENTE = range(2)


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
- descripcion: resumen breve y claro del episodio
- lugar: dónde ocurrió (si no se menciona, pon "No indicado")
- personas_presentes: quién estaba presente además del paciente (si no se menciona, pon "No indicado")
- gravedad: clasifica como "Leve", "Moderada" o "Severa" según tu criterio
- notas_extra: cualquier detalle adicional relevante (si no hay, pon cadena vacía)
- tipo_olvido: clasifica como "Memoria reciente", "Memoria remota", "Reconocimiento de personas", "Orientación espacial" u "Orientación temporal"
- estado_paciente: estado anímico/físico del paciente (si no se menciona, pon null)
- consciente_olvido: si el paciente fue consciente de su propio olvido, valores "Sí", "No" o null si no se menciona

Responde SOLO con el JSON. Sin texto adicional, sin bloques de código, sin explicaciones.

Mensaje: {text}"""

    response = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=500,
        messages=[{"role": "user", "content": prompt}]
    )

    return json.loads(response.content[0].text)


def build_confirmation(data: dict, user_name: str, fecha: str, hora: str) -> str:
    gravedad = data.get("gravedad", "No indicado")
    emoji_gravedad = {"Leve": "🟡", "Moderada": "🟠", "Severa": "🔴"}.get(gravedad, "⚪")

    confirmacion = (
        f"✅ *Registrado correctamente*\n\n"
        f"📅 {fecha} a las {hora}\n"
        f"👤 {user_name}\n"
        f"📝 {data.get('descripcion', '')}\n"
        f"📍 Lugar: {data.get('lugar', 'No indicado')}\n"
        f"👥 Presentes: {data.get('personas_presentes', 'No indicado')}\n"
        f"{emoji_gravedad} Gravedad: {gravedad}\n"
        f"🧠 Tipo de olvido: {data.get('tipo_olvido', 'No indicado')}\n"
        f"😌 Estado del paciente: {data.get('estado_paciente', 'No indicado')}\n"
        f"🔍 Consciente del olvido: {data.get('consciente_olvido', 'No indicado')}"
    )

    if data.get("notas_extra"):
        confirmacion += f"\n📌 Notas: {data.get('notas_extra')}"

    return confirmacion


async def save_to_sheet(data: dict, user_name: str, fecha: str, hora: str):
    sheet = get_sheet()
    sheet.append_row([
        fecha,
        hora,
        user_name,
        data.get("descripcion", ""),
        data.get("lugar", "No indicado"),
        data.get("personas_presentes", "No indicado"),
        data.get("gravedad", "No indicado"),
        data.get("notas_extra", ""),
        data.get("tipo_olvido", "No indicado"),
        data.get("estado_paciente", "No indicado"),
        data.get("consciente_olvido", "No indicado"),
    ])


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "👋 Hola, soy el bot del Diario de Memoria.\n\n"
        "Cuéntame con tus propias palabras lo que ha pasado y yo me encargo de registrarlo en la hoja de cálculo.\n\n"
        "Por ejemplo:\n"
        "_«Hoy papá no reconoció a su nieto cuando vino a visitarle. Estábamos en el salón de casa, eran las 6 de la tarde»_",
        parse_mode="Markdown"
    )


async def ayuda(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "📖 *Cómo usar el bot*\n\n"
        "Escribe con naturalidad describiendo lo que ha pasado. Intenta incluir:\n"
        "• Qué olvidó o qué episodio ocurrió\n"
        "• Dónde estabais\n"
        "• Quién estaba presente\n"
        "• Si te pareció un olvido leve, moderado o grave\n\n"
        "El bot extraerá toda esa información automáticamente y la guardará en la hoja compartida.",
        parse_mode="Markdown"
    )


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message = update.message
    if not message or not message.text:
        return ConversationHandler.END

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

        if data.get("estado_paciente") is None:
            await message.reply_text(
                "¿Cómo estaba el paciente en ese momento? Describe su estado anímico o físico."
            )
            return WAITING_ESTADO

        if data.get("consciente_olvido") is None:
            keyboard = [[
                InlineKeyboardButton("Sí", callback_data="consciente_si"),
                InlineKeyboardButton("No", callback_data="consciente_no"),
            ]]
            await message.reply_text(
                "¿El paciente fue consciente de su propio olvido?",
                reply_markup=InlineKeyboardMarkup(keyboard)
            )
            return WAITING_CONSCIENTE

        await save_to_sheet(data, user_name, fecha, hora)
        await message.reply_text(
            build_confirmation(data, user_name, fecha, hora),
            parse_mode="Markdown"
        )
        return ConversationHandler.END

    except Exception as e:
        logger.error(f"Error procesando mensaje: {e}")
        await processing.delete()
        await message.reply_text(
            "❌ Hubo un error al registrar el episodio. Por favor inténtalo de nuevo."
        )
        return ConversationHandler.END


async def handle_estado(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["pending_data"]["estado_paciente"] = update.message.text.strip()
    data = context.user_data["pending_data"]

    if data.get("consciente_olvido") is None:
        keyboard = [[
            InlineKeyboardButton("Sí", callback_data="consciente_si"),
            InlineKeyboardButton("No", callback_data="consciente_no"),
        ]]
        await update.message.reply_text(
            "¿El paciente fue consciente de su propio olvido?",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
        return WAITING_CONSCIENTE

    user_name = context.user_data["user_name"]
    fecha = context.user_data["fecha"]
    hora = context.user_data["hora"]
    await save_to_sheet(data, user_name, fecha, hora)
    await update.message.reply_text(
        build_confirmation(data, user_name, fecha, hora),
        parse_mode="Markdown"
    )
    return ConversationHandler.END


async def handle_consciente(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    context.user_data["pending_data"]["consciente_olvido"] = (
        "Sí" if query.data == "consciente_si" else "No"
    )

    data = context.user_data["pending_data"]
    user_name = context.user_data["user_name"]
    fecha = context.user_data["fecha"]
    hora = context.user_data["hora"]

    await save_to_sheet(data, user_name, fecha, hora)
    await query.edit_message_text(
        build_confirmation(data, user_name, fecha, hora),
        parse_mode="Markdown"
    )
    return ConversationHandler.END


def main():
    app = Application.builder().token(TELEGRAM_TOKEN).build()

    conv_handler = ConversationHandler(
        entry_points=[MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message)],
        states={
            WAITING_ESTADO: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_estado)],
            WAITING_CONSCIENTE: [CallbackQueryHandler(handle_consciente, pattern="^consciente_")],
        },
        fallbacks=[
            CommandHandler("start", start),
            CommandHandler("ayuda", ayuda),
        ],
    )

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("ayuda", ayuda))
    app.add_handler(conv_handler)
    logger.info("Bot arrancado")
    app.run_polling()


if __name__ == "__main__":
    main()
