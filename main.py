import os
import json
import logging
from datetime import datetime
import anthropic
import gspread
from google.oauth2.service_account import Credentials
from telegram import Update
from telegram.ext import Application, MessageHandler, CommandHandler, filters, ContextTypes

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

TELEGRAM_TOKEN = os.environ["TELEGRAM_TOKEN"]
ANTHROPIC_API_KEY = os.environ["ANTHROPIC_API_KEY"]
GOOGLE_CREDENTIALS_JSON = os.environ["GOOGLE_CREDENTIALS_JSON"]
SPREADSHEET_ID = os.environ["SPREADSHEET_ID"]


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

Responde SOLO con el JSON. Sin texto adicional, sin bloques de código, sin explicaciones.

Mensaje: {text}"""

    response = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=500,
        messages=[{"role": "user", "content": prompt}]
    )

    return json.loads(response.content[0].text)


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
        return

    text = message.text
    user = message.from_user
    user_name = user.full_name or user.username or "Desconocido"

    processing = await message.reply_text("⏳ Analizando y registrando...")

    try:
        data = extract_data(text)

        now = datetime.now()
        fecha = now.strftime("%d/%m/%Y")
        hora = now.strftime("%H:%M")

        sheet = get_sheet()
        sheet.append_row([
            fecha,
            hora,
            user_name,
            data.get("descripcion", text),
            data.get("lugar", "No indicado"),
            data.get("personas_presentes", "No indicado"),
            data.get("gravedad", "No indicado"),
            data.get("notas_extra", "")
        ])

        gravedad = data.get("gravedad", "No indicado")
        emoji_gravedad = {"Leve": "🟡", "Moderada": "🟠", "Severa": "🔴"}.get(gravedad, "⚪")

        confirmacion = (
            f"✅ *Registrado correctamente*\n\n"
            f"📅 {fecha} a las {hora}\n"
            f"👤 {user_name}\n"
            f"📝 {data.get('descripcion', text)}\n"
            f"📍 Lugar: {data.get('lugar', 'No indicado')}\n"
            f"👥 Presentes: {data.get('personas_presentes', 'No indicado')}\n"
            f"{emoji_gravedad} Gravedad: {gravedad}"
        )

        if data.get("notas_extra"):
            confirmacion += f"\n📌 Notas: {data.get('notas_extra')}"

        await processing.delete()
        await message.reply_text(confirmacion, parse_mode="Markdown")

    except Exception as e:
        logger.error(f"Error procesando mensaje: {e}")
        await processing.delete()
        await message.reply_text(
            "❌ Hubo un error al registrar el episodio. Por favor inténtalo de nuevo."
        )


def main():
    app = Application.builder().token(TELEGRAM_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("ayuda", ayuda))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    logger.info("Bot arrancado")
    app.run_polling()


if __name__ == "__main__":
    main()
