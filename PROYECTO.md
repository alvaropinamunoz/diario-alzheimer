# Diario de Alzheimer — Bot de Telegram

## Qué es esto

Bot de Telegram para que familiares de un paciente con Alzheimer registren episodios de pérdida de memoria de forma sencilla. El familiar describe lo que pasó en lenguaje natural, y el bot usa Claude (IA de Anthropic) para extraer la información relevante y guardarla automáticamente en una hoja de Google Sheets.

## Repositorio

GitHub: `https://github.com/alvaropinamunoz/diario-alzheimer`  
Rama principal: `main`

---

## Archivos del proyecto

| Archivo | Qué es |
|---|---|
| `main.py` | Todo el código del bot |
| `requirements.txt` | Dependencias Python |
| `Procfile` | Comando de arranque para despliegue (Heroku u otros) |
| `.python-version` | Versión de Python: 3.11 |

---

## Cómo funciona

1. El familiar escribe un mensaje describiendo un episodio (ej: *"Hoy papá no reconoció a su nieto, estábamos en el salón"*).
2. El bot llama a Claude para extraer los datos en formato JSON.
3. Si Claude no pudo determinar el estado del paciente o si fue consciente del olvido, el bot **pregunta al familiar** antes de guardar.
4. Cuando tiene todos los datos, los guarda como una fila nueva en Google Sheets.

### Campos que se guardan en Sheets (en orden de columna)

| Columna | Contenido |
|---|---|
| fecha | dd/mm/yyyy |
| hora | HH:MM |
| usuario | Nombre del familiar que escribió |
| descripcion | Resumen del episodio |
| lugar | Dónde ocurrió |
| personas_presentes | Quién estaba |
| gravedad | Leve / Moderada / Severa |
| notas_extra | Detalles adicionales |
| tipo_olvido | Memoria reciente / Memoria remota / Reconocimiento de personas / Orientación espacial / Orientación temporal |
| estado_paciente | Estado anímico o físico del paciente |
| consciente_olvido | Sí / No (si el paciente fue consciente de su propio olvido) |

---

## Comandos del bot

| Comando | Función |
|---|---|
| *(mensaje de texto)* | Registra un episodio nuevo |
| `/informe` | Claude genera un resumen estructurado para llevar al médico |
| `/semana` | Estadísticas de los últimos 7 días |
| `/mes` | Estadísticas del mes actual |
| `/buscar <texto>` | Busca registros cuya descripción contenga ese texto |
| `/borrar` | Elimina el último registro |
| `/borrar 3` | Elimina los últimos N registros (máximo 5) |
| `/ayuda` | Lista todos los comandos |
| `/start` | Mensaje de bienvenida |

---

## Variables de entorno necesarias

El bot necesita estas 4 variables para funcionar (configuradas en el servidor/plataforma de despliegue):

| Variable | Qué es |
|---|---|
| `TELEGRAM_TOKEN` | Token del bot de Telegram (se obtiene desde @BotFather) |
| `ANTHROPIC_API_KEY` | API key de Anthropic para usar Claude |
| `GOOGLE_CREDENTIALS_JSON` | JSON completo de credenciales de una cuenta de servicio de Google |
| `SPREADSHEET_ID` | ID de la hoja de Google Sheets donde se guardan los datos |

---

## Dependencias principales

- `python-telegram-bot==21.3` — framework del bot
- `anthropic` — SDK de Claude (Anthropic)
- `gspread` + `google-auth` — lectura y escritura en Google Sheets

---

## Estado actual (abril 2026)

- El código está completo y funcional.
- **Pendiente:** desplegar en un servidor para que el bot esté siempre activo. El `Procfile` está preparado para Heroku, Railway u otras plataformas similares.
- La hoja de Google Sheets hay que crearla manualmente y configurar las credenciales de la cuenta de servicio.

---

## Flujo de conversación del bot

Cuando el familiar envía un mensaje, el bot puede entrar en un flujo de preguntas si faltan datos:

```
Familiar: "Hoy no recordó que había desayunado"
    ↓
Bot analiza con Claude
    ↓
¿Falta estado_paciente? → Bot pregunta: "¿Cómo estaba el paciente?"
    ↓
¿Falta consciente_olvido? → Bot pregunta con botones [Sí] [No]
    ↓
Bot guarda en Sheets y confirma
```

Cualquier comando (`/borrar`, `/informe`, etc.) cancela el flujo pendiente y ejecuta el comando directamente.
