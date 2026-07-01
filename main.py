from aiogram import Bot, Dispatcher, types
from aiogram.filters import CommandStart, Command
from aiogram.types import ReplyKeyboardMarkup, KeyboardButton
from openai import OpenAI
import asyncio
import os
import base64
import json
import re
import gspread
from gspread.utils import rowcol_to_a1
from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build
 
# =========================================
# TOKENS
# =========================================
BOT_TOKEN = os.getenv("BOT_TOKEN")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
 
# =========================================
# TELEGRAM
# =========================================
bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()
 
# =========================================
# OPENAI
# =========================================
client = OpenAI(api_key=OPENAI_API_KEY)
 
# =========================================
# GOOGLE SHEETS
# =========================================
scopes = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive"
]
creds = Credentials.from_service_account_file(
    "/etc/secrets/just-sunrise-501012-t4-829cee1f1963.json",
    scopes=scopes
)
gs_client = gspread.authorize(creds)
sheets_service = build("sheets", "v4", credentials=creds)
 
# DEFAULT SPREADSHEET
default_spreadsheet = gs_client.open("TEST BOT")
main_sheet = default_spreadsheet.sheet1
 
# =========================================
# MEMORY
# =========================================
user_modes = {}
chat_history = {}
active_spreadsheets = {}
MAX_HISTORY = 80
 
# =========================================
# KEYBOARD
# =========================================
main_keyboard = ReplyKeyboardMarkup(
    keyboard=[
        [
            KeyboardButton(text="💬 Chat"),
            KeyboardButton(text="📊 Sheets")
        ]
    ],
    resize_keyboard=True,
    input_field_placeholder="Choose mode"
)
 
# =========================================
# HELPERS
# =========================================
def extract_google_sheet_url(text):
    urls = re.findall(
        r'https:\/\/docs\.google\.com\/spreadsheets\/[^\s]+',
        text
    )
    return urls[0] if urls else None
 
 
def extract_json(text):
    text = text.replace("```json", "").replace("```", "").strip()
    try:
        return json.loads(text)
    except Exception:
        pass
    match = re.search(r'\{.*\}', text, re.DOTALL)
    if match:
        try:
            return json.loads(match.group())
        except Exception:
            pass
    return None
 
 
def bold_header(spreadsheet_id, sheet_id, num_cols):
    """Делает первую строку жирной и с фоном через Sheets API v4."""
    requests = [
        {
            "repeatCell": {
                "range": {
                    "sheetId": sheet_id,
                    "startRowIndex": 0,
                    "endRowIndex": 1,
                    "startColumnIndex": 0,
                    "endColumnIndex": num_cols
                },
                "cell": {
                    "userEnteredFormat": {
                        "textFormat": {"bold": True},
                        "backgroundColor": {
                            "red": 0.27,
                            "green": 0.51,
                            "blue": 0.71
                        },
                        "horizontalAlignment": "CENTER"
                    }
                },
                "fields": "userEnteredFormat(textFormat,backgroundColor,horizontalAlignment)"
            }
        },
        {
            "autoResizeDimensions": {
                "dimensions": {
                    "sheetId": sheet_id,
                    "dimension": "COLUMNS",
                    "startIndex": 0,
                    "endIndex": num_cols
                }
            }
        }
    ]
    sheets_service.spreadsheets().batchUpdate(
        spreadsheetId=spreadsheet_id,
        body={"requests": requests}
    ).execute()
 
 
def write_full_table(spreadsheet, sheet_title, headers, rows):
    """
    Создаёт новый лист, записывает всю таблицу за 1 вызов,
    форматирует заголовок.
    """
    # Создаём лист
    ws = spreadsheet.add_worksheet(
        title=sheet_title,
        rows=str(max(len(rows) + 10, 100)),
        cols=str(max(len(headers) + 2, 20))
    )
    # Пишем всё за 1 batch: заголовки + строки
    all_data = [headers] + rows
    ws.update("A1", all_data)
 
    # Форматируем шапку
    sheet_id = ws.id
    bold_header(spreadsheet.id, sheet_id, len(headers))
 
    return ws
 
 
# =========================================
# SYSTEM PROMPT ДЛЯ SHEETS
# =========================================
SHEETS_SYSTEM = """
You are a Google Sheets AI operator with REAL API access.
Current spreadsheet: {spreadsheet_title}
 
YOUR JOB: analyse the user's request or image and return a single JSON object.
 
RULES:
- Return ONLY valid JSON. No text before or after.
- No markdown. No explanations.
- All string values must be in the same language as the user's request.
 
AVAILABLE ACTIONS:
 
1. build_table — create a new sheet with a full table (use when user sends a reference image or asks to build a table structure)
{{
  "action": "build_table",
  "sheet_title": "Sales Report",
  "headers": ["Date", "Buyer", "Product", "Qty", "Price", "Total", "Status"],
  "rows": [
    ["2024-01-01", "John", "Widget A", "5", "100", "500", "Paid"],
    ["2024-01-02", "Anna", "Widget B", "3", "200", "600", "Pending"]
  ]
}}
— If the user sends a REFERENCE IMAGE with no real data, generate realistic sample rows that match the column structure.
— If the user sends a REPORT with real data, extract ALL rows from it.
— Always include as many rows as you can extract or generate (minimum 3 sample rows).
 
2. fill_data — fill data into an existing sheet (use when user says "fill", "add data", "populate")
{{
  "action": "fill_data",
  "sheet_name": "Sheet1",
  "start_cell": "A2",
  "rows": [
    ["2024-01-01", "John", "Widget A", "5", "100", "500", "Paid"]
  ]
}}
 
3. write_cell — update a single cell
{{
  "action": "write_cell",
  "sheet_name": "Sheet1",
  "cell": "B3",
  "value": "New value"
}}
 
4. create_sheet — create a blank new sheet tab
{{
  "action": "create_sheet",
  "title": "Raw Data"
}}
 
5. build_dashboard — create a summary/dashboard sheet
{{
  "action": "build_dashboard",
  "title": "Dashboard",
  "headers": ["Metric", "Value"],
  "rows": [["Total Revenue", ""], ["Total Orders", ""], ["Top Buyer", ""]]
}}
"""
 
# =========================================
# EXECUTE ACTION
# =========================================
async def execute_action(action: dict, spreadsheet, message: types.Message):
    act = action.get("action")
 
    # ---- build_table ----
    if act == "build_table":
        title = action.get("sheet_title", "Table")
        headers = action.get("headers", [])
        rows = action.get("rows", [])
        ws = write_full_table(spreadsheet, title, headers, rows)
        url = f"https://docs.google.com/spreadsheets/d/{spreadsheet.id}/edit#gid={ws.id}"
        await message.answer(
            f'✅ Таблица "{title}" создана\n'
            f'Колонки: {len(headers)}, строк данных: {len(rows)}\n'
            f'🔗 {url}'
        )
        return
 
    # ---- fill_data ----
    if act == "fill_data":
        sheet_name = action.get("sheet_name", "Sheet1")
        start_cell = action.get("start_cell", "A2")
        rows = action.get("rows", [])
        try:
            ws = spreadsheet.worksheet(sheet_name)
        except Exception:
            ws = spreadsheet.sheet1
        ws.update(start_cell, rows)
        await message.answer(
            f'✅ Добавлено {len(rows)} строк в "{sheet_name}" с ячейки {start_cell}'
        )
        return
 
    # ---- write_cell ----
    if act == "write_cell":
        sheet_name = action.get("sheet_name", "Sheet1")
        cell = action.get("cell", "A1")
        value = action.get("value", "")
        try:
            ws = spreadsheet.worksheet(sheet_name)
        except Exception:
            ws = spreadsheet.sheet1
        ws.update(cell, [[value]])
        await message.answer(f'✅ Ячейка {cell} обновлена')
        return
 
    # ---- create_sheet ----
    if act == "create_sheet":
        title = action.get("title", "New Sheet")
        spreadsheet.add_worksheet(title=title, rows="200", cols="30")
        await message.answer(f'✅ Лист "{title}" создан')
        return
 
    # ---- build_dashboard ----
    if act == "build_dashboard":
        title = action.get("title", "Dashboard")
        headers = action.get("headers", ["Metric", "Value"])
        rows = action.get("rows", [])
        ws = write_full_table(spreadsheet, title, headers, rows)
        url = f"https://docs.google.com/spreadsheets/d/{spreadsheet.id}/edit#gid={ws.id}"
        await message.answer(
            f'✅ Дашборд "{title}" создан\n🔗 {url}'
        )
        return
 
    await message.answer(f"⚠️ Неизвестное действие: {act}\n\n{action}")
 
 
# =========================================
# GPT SHEET CALL (текст)
# =========================================
async def gpt_sheet_text(text: str, spreadsheet) -> dict | None:
    response = client.chat.completions.create(
        model="gpt-4.1",
        temperature=0,
        messages=[
            {
                "role": "system",
                "content": SHEETS_SYSTEM.format(
                    spreadsheet_title=spreadsheet.title
                )
            },
            {"role": "user", "content": text}
        ]
    )
    return extract_json(response.choices[0].message.content)
 
 
# =========================================
# GPT SHEET CALL (фото)
# =========================================
async def gpt_sheet_photo(base64_image: str, caption: str, spreadsheet) -> dict | None:
    response = client.chat.completions.create(
        model="gpt-4.1",
        temperature=0,
        messages=[
            {
                "role": "system",
                "content": SHEETS_SYSTEM.format(
                    spreadsheet_title=spreadsheet.title
                )
            },
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": caption or "Build a table based on this reference image. Extract all structure and data you see."},
                    {
                        "type": "image_url",
                        "image_url": {
                            "url": f"data:image/jpeg;base64,{base64_image}",
                            "detail": "high"
                        }
                    }
                ]
            }
        ]
    )
    return extract_json(response.choices[0].message.content)
 
 
# =========================================
# START
# =========================================
@dp.message(CommandStart())
async def start(message: types.Message):
    chat_id = message.chat.id
    user_modes[chat_id] = "chat"
    active_spreadsheets[chat_id] = default_spreadsheet
    await message.answer("Choose mode:", reply_markup=main_keyboard)
 
 
# =========================================
# COMMANDS
# =========================================
@dp.message(Command("chat"))
async def set_chat_mode(message: types.Message):
    user_modes[message.chat.id] = "chat"
    await message.answer("💬 Chat mode enabled", reply_markup=main_keyboard)
 
@dp.message(Command("sheet"))
async def set_sheet_mode(message: types.Message):
    user_modes[message.chat.id] = "sheet"
    await message.answer("📊 Sheet mode enabled", reply_markup=main_keyboard)
 
 
# =========================================
# BUTTONS
# =========================================
@dp.message(lambda m: m.text == "💬 Chat")
async def button_chat_mode(message: types.Message):
    user_modes[message.chat.id] = "chat"
    await message.answer("💬 Chat mode enabled", reply_markup=main_keyboard)
 
@dp.message(lambda m: m.text == "📊 Sheets")
async def button_sheet_mode(message: types.Message):
    user_modes[message.chat.id] = "sheet"
    await message.answer("📊 Sheet mode enabled", reply_markup=main_keyboard)
 
 
# =========================================
# PHOTO HANDLER
# =========================================
@dp.message(lambda m: m.photo)
async def handle_photo(message: types.Message):
    try:
        chat_id = message.chat.id
        mode = user_modes.get(chat_id, "chat")
 
        photo = message.photo[-1]
        file = await bot.get_file(photo.file_id)
        downloaded = await bot.download_file(file.file_path)
        image_bytes = downloaded.read()
        base64_image = base64.b64encode(image_bytes).decode("utf-8")
 
        # ---- CHAT MODE ----
        if mode == "chat":
            history = chat_history.get(chat_id, [])
            user_text = message.caption or "Analyze image"
            response = client.chat.completions.create(
                model="gpt-4.1",
                messages=[
                    {
                        "role": "system",
                        "content": "You are a persistent Telegram AI assistant. Speak naturally in Russian."
                    }
                ] + history + [
                    {
                        "role": "user",
                        "content": [
                            {"type": "text", "text": user_text},
                            {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{base64_image}"}}
                        ]
                    }
                ],
                temperature=0.7
            )
            answer = response.choices[0].message.content
            history.append({"role": "user", "content": user_text})
            history.append({"role": "assistant", "content": answer})
            chat_history[chat_id] = history[-MAX_HISTORY:]
            await message.answer(answer)
            return
 
        # ---- SHEET MODE ----
        if mode == "sheet":
            current_spreadsheet = active_spreadsheets.get(chat_id, default_spreadsheet)
            await message.answer("🔍 Анализирую изображение...")
            action = await gpt_sheet_photo(base64_image, message.caption, current_spreadsheet)
            if action is None:
                await message.answer("❌ Не смог распарсить ответ GPT")
                return
            await execute_action(action, current_spreadsheet, message)
 
    except Exception as e:
        await message.answer(f"PHOTO ERROR:\n{e}")
 
 
# =========================================
# TEXT HANDLER
# =========================================
@dp.message()
async def chat(message: types.Message):
    try:
        text = message.text
        if not text:
            return
        if text.startswith("/"):
            return
        if text in ["💬 Chat", "📊 Sheets"]:
            return
 
        chat_id = message.chat.id
        mode = user_modes.get(chat_id, "chat")
 
        # ---- CHAT MODE ----
        if mode == "chat":
            history = chat_history.get(chat_id, [])
            history.append({"role": "user", "content": text})
            history = history[-MAX_HISTORY:]
            response = client.chat.completions.create(
                model="gpt-4.1",
                messages=[
                    {
                        "role": "system",
                        "content": "You are a persistent AI Telegram assistant. Speak naturally in Russian. Remember previous messages."
                    }
                ] + history,
                temperature=0.7
            )
            answer = response.choices[0].message.content
            history.append({"role": "assistant", "content": answer})
            chat_history[chat_id] = history[-MAX_HISTORY:]
            await message.answer(answer)
            return
 
        # ---- SHEET MODE ----
        if mode == "sheet":
            # Подключение по URL
            sheet_url = extract_google_sheet_url(text)
            if sheet_url:
                try:
                    opened = gs_client.open_by_url(sheet_url)
                    active_spreadsheets[chat_id] = opened
                    await message.answer(f'✅ Подключился к: {opened.title}')
                except Exception as e:
                    await message.answer(f"❌ Не удалось открыть таблицу:\n{e}")
                return
 
            current_spreadsheet = active_spreadsheets.get(chat_id, default_spreadsheet)
            action = await gpt_sheet_text(text, current_spreadsheet)
 
            if action is None:
                await message.answer("❌ GPT не вернул JSON. Попробуй переформулировать запрос.")
                return
 
            await execute_action(action, current_spreadsheet, message)
 
    except Exception as e:
        await message.answer(f"TEXT ERROR:\n{e}")
 
 
# =========================================
# MAIN
# =========================================
async def main():
    await dp.start_polling(bot)
 
if __name__ == "__main__":
    asyncio.run(main())
