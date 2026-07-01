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
from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build
 
# =========================================
# TOKENS
# =========================================
BOT_TOKEN = os.getenv("BOT_TOKEN")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
 
# =========================================
# TELEGRAM / OPENAI
# =========================================
bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()
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
 
default_spreadsheet = gs_client.open("TEST BOT")
 
# =========================================
# ПАМЯТЬ
# =========================================
user_modes = {}
chat_history = {}
active_spreadsheets = {}
MAX_HISTORY = 80
 
# Память таблиц: на каждого пользователя храним контекст
# sheet_memory[chat_id] = {
#   "last_sheet": "MEDIA BUYERS DASHBOARD",
#   "sheets": {
#     "MEDIA BUYERS DASHBOARD": {
#       "blocks": [
#         {"name": "KPI", "start": "A1", "headers": ["SPEND","DEPS",...]},
#         {"name": "ПО ДНЯМ", "start": "A3", "headers": ["Date","Spend",...]},
#         {"name": "ПО БАЕРАМ", "start": "I3", "headers": ["Buyer","Spend",...]}
#       ]
#     }
#   },
#   "log": ["Создал лист ...", "Добавил данные ..."]
# }
sheet_memory = {}
 
def get_memory(chat_id: int) -> dict:
    if chat_id not in sheet_memory:
        sheet_memory[chat_id] = {"last_sheet": None, "sheets": {}, "log": []}
    return sheet_memory[chat_id]
 
def remember_sheet(chat_id: int, sheet_title: str, blocks: list):
    mem = get_memory(chat_id)
    mem["last_sheet"] = sheet_title
    mem["sheets"][sheet_title] = {"blocks": blocks}
    mem["log"].append(f"Создал лист '{sheet_title}' с блоками: {[b['name'] for b in blocks]}")
    mem["log"] = mem["log"][-30:]
 
def remember_action(chat_id: int, text: str):
    mem = get_memory(chat_id)
    mem["log"].append(text)
    mem["log"] = mem["log"][-30:]
 
def memory_context(chat_id: int) -> str:
    mem = get_memory(chat_id)
    if not mem["last_sheet"] and not mem["log"]:
        return "Нет истории действий."
    lines = []
    if mem["last_sheet"]:
        lines.append(f'Последний активный лист: "{mem["last_sheet"]}"')
        sheet_info = mem["sheets"].get(mem["last_sheet"], {})
        blocks = sheet_info.get("blocks", [])
        for b in blocks:
            lines.append(f'  Блок "{b["name"]}" начинается с {b["start"]}, колонки: {b["headers"]}')
    if mem["log"]:
        lines.append("История действий:")
        for entry in mem["log"][-10:]:
            lines.append(f"  - {entry}")
    return "\n".join(lines)
 
# =========================================
# KEYBOARD
# =========================================
main_keyboard = ReplyKeyboardMarkup(
    keyboard=[[KeyboardButton(text="💬 Chat"), KeyboardButton(text="📊 Sheets")]],
    resize_keyboard=True,
    input_field_placeholder="Choose mode"
)
 
# =========================================
# HELPERS
# =========================================
def extract_google_sheet_url(text):
    urls = re.findall(r'https://docs\.google\.com/spreadsheets/[^\s]+', text)
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
 
def format_header(spreadsheet_id, sheet_id, start_row, end_row, start_col, end_col, color):
    """Форматирует диапазон: жирный текст + цвет фона + автоширина."""
    r, g, b = color
    requests = [
        {
            "repeatCell": {
                "range": {
                    "sheetId": sheet_id,
                    "startRowIndex": start_row,
                    "endRowIndex": end_row,
                    "startColumnIndex": start_col,
                    "endColumnIndex": end_col
                },
                "cell": {
                    "userEnteredFormat": {
                        "textFormat": {"bold": True},
                        "backgroundColor": {"red": r, "green": g, "blue": b},
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
                    "startIndex": start_col,
                    "endIndex": end_col
                }
            }
        }
    ]
    sheets_service.spreadsheets().batchUpdate(
        spreadsheetId=spreadsheet_id,
        body={"requests": requests}
    ).execute()
 
def col_letter_to_index(col: str) -> int:
    """A -> 0, B -> 1, AA -> 26 ..."""
    col = col.upper()
    result = 0
    for c in col:
        result = result * 26 + (ord(c) - ord('A') + 1)
    return result - 1
 
def cell_to_rowcol(cell: str):
    """'A1' -> (0, 0), 'I3' -> (2, 8)"""
    match = re.match(r'([A-Za-z]+)(\d+)', cell)
    if not match:
        return 0, 0
    col_str, row_str = match.group(1), match.group(2)
    return int(row_str) - 1, col_letter_to_index(col_str)
 
# =========================================
# EXECUTE ACTION
# =========================================
async def execute_action(action: dict, spreadsheet, message: types.Message, chat_id: int):
    act = action.get("action")
 
    # ---- write_layout (сложная таблица с блоками) ----
    if act == "write_layout":
        sheet_title = action.get("sheet_title", "Layout")
        blocks = action.get("blocks", [])
 
        # Считаем нужный размер листа
        max_row, max_col = 50, 20
        for b in blocks:
            r, c = cell_to_rowcol(b.get("start", "A1"))
            rows_in_block = len(b.get("rows", [])) + 2
            cols_in_block = len(b.get("headers", []))
            max_row = max(max_row, r + rows_in_block + 5)
            max_col = max(max_col, c + cols_in_block + 2)
 
        ws = spreadsheet.add_worksheet(
            title=sheet_title,
            rows=str(max_row),
            cols=str(max_col)
        )
        sheet_id = ws.id
 
        recorded_blocks = []
        for b in blocks:
            block_name = b.get("name", "")
            start_cell = b.get("start", "A1")
            headers = b.get("headers", [])
            rows = b.get("rows", [])
 
            start_row, start_col = cell_to_rowcol(start_cell)
 
            # Пишем название блока если есть
            if block_name:
                title_cell = f"{chr(65 + start_col)}{start_row + 1}"
                ws.update(title_cell, [[block_name]])
                start_row += 1
 
            # Пишем заголовки + данные
            data_to_write = [headers] + rows
            data_start = f"{chr(65 + start_col)}{start_row + 1}"
            ws.update(data_start, data_to_write)
 
            # Форматируем заголовок блока
            colors = {
                "KPI": (0.18, 0.33, 0.18),
                "ПО ДНЯМ": (0.18, 0.33, 0.45),
                "ПО БАЕРАМ": (0.18, 0.33, 0.45),
            }
            color = colors.get(block_name, (0.27, 0.51, 0.71))
            format_header(
                spreadsheet.id, sheet_id,
                start_row, start_row + 1,
                start_col, start_col + len(headers),
                color
            )
 
            recorded_blocks.append({
                "name": block_name,
                "start": start_cell,
                "headers": headers,
                "data_start_row": start_row + 1  # строка после заголовка
            })
 
        remember_sheet(chat_id, sheet_title, recorded_blocks)
        url = f"https://docs.google.com/spreadsheets/d/{spreadsheet.id}/edit#gid={sheet_id}"
        await message.answer(
            f'✅ Таблица "{sheet_title}" создана\n'
            f'Блоков: {len(blocks)}\n🔗 {url}'
        )
        return
 
    # ---- build_table (простая таблица) ----
    if act == "build_table":
        title = action.get("sheet_title", "Table")
        headers = action.get("headers", [])
        rows = action.get("rows", [])
 
        ws = spreadsheet.add_worksheet(
            title=title,
            rows=str(max(len(rows) + 20, 100)),
            cols=str(max(len(headers) + 2, 20))
        )
        ws.update("A1", [headers] + rows)
        format_header(spreadsheet.id, ws.id, 0, 1, 0, len(headers), (0.27, 0.51, 0.71))
 
        remember_sheet(chat_id, title, [{"name": "main", "start": "A1", "headers": headers, "data_start_row": 1}])
        url = f"https://docs.google.com/spreadsheets/d/{spreadsheet.id}/edit#gid={ws.id}"
        await message.answer(
            f'✅ Таблица "{title}" создана\n'
            f'Колонки: {len(headers)}, строк: {len(rows)}\n🔗 {url}'
        )
        return
 
    # ---- fill_data (добавить данные) ----
    if act == "fill_data":
        mem = get_memory(chat_id)
        sheet_name = action.get("sheet_name") or mem.get("last_sheet") or "Sheet1"
        start_cell = action.get("start_cell", "A2")
        rows = action.get("rows", [])
 
        try:
            ws = spreadsheet.worksheet(sheet_name)
        except Exception:
            # Попробуем найти по частичному совпадению
            all_sheets = [s.title for s in spreadsheet.worksheets()]
            match = next((s for s in all_sheets if sheet_name.lower() in s.lower()), None)
            ws = spreadsheet.worksheet(match) if match else spreadsheet.sheet1
            sheet_name = ws.title
 
        ws.update(start_cell, rows)
        remember_action(chat_id, f'Добавил данные в "{sheet_name}" с {start_cell}: {rows}')
 
        await message.answer(
            f'✅ Добавлено {len(rows)} строк в "{sheet_name}" с ячейки {start_cell}'
        )
        return
 
    # ---- write_cell ----
    if act == "write_cell":
        mem = get_memory(chat_id)
        sheet_name = action.get("sheet_name") or mem.get("last_sheet") or "Sheet1"
        cell = action.get("cell", "A1")
        value = action.get("value", "")
 
        try:
            ws = spreadsheet.worksheet(sheet_name)
        except Exception:
            ws = spreadsheet.sheet1
            sheet_name = ws.title
 
        ws.update(cell, [[value]])
        remember_action(chat_id, f'Записал "{value}" в {sheet_name}!{cell}')
        await message.answer(f'✅ Ячейка {sheet_name}!{cell} → "{value}"')
        return
 
    # ---- create_sheet ----
    if act == "create_sheet":
        title = action.get("title", "New Sheet")
        spreadsheet.add_worksheet(title=title, rows="200", cols="30")
        remember_action(chat_id, f'Создал пустой лист "{title}"')
        await message.answer(f'✅ Лист "{title}" создан')
        return
 
    await message.answer(f"⚠️ Неизвестное действие: {act}\n\n{json.dumps(action, ensure_ascii=False)}")
 
 
# =========================================
# SYSTEM PROMPT ДЛЯ SHEETS
# =========================================
def sheets_system(spreadsheet_title: str, mem_ctx: str) -> str:
    return f"""
You are a Google Sheets AI operator with REAL API access.
Current spreadsheet: {spreadsheet_title}
 
CONTEXT (what you already did in this session):
{mem_ctx}
 
YOUR JOB: analyse the user request or image and return a single JSON object.
 
RULES:
- Return ONLY valid JSON. No text before or after. No markdown.
- Use the context above to understand which sheet and columns the user is referring to.
- If the user says "add to buyer 1" or "fill spend" — use the last_sheet and its column structure from context.
- Match column names from context when placing data.
 
AVAILABLE ACTIONS:
 
1. write_layout — for complex tables with multiple blocks on one sheet (use when reference has sections like KPI + BY DAYS + BY BUYERS)
{{
  "action": "write_layout",
  "sheet_title": "MEDIA BUYERS DASHBOARD",
  "blocks": [
    {{
      "name": "KPI",
      "start": "A1",
      "headers": ["SPEND", "DEPS", "AVG CHECK", "CPA GROSS", "REVENUE", "PROFIT", "ROI %"],
      "rows": []
    }},
    {{
      "name": "ПО ДНЯМ",
      "start": "A3",
      "headers": ["Date", "Spend", "Deps", "CPA Gross", "Revenue", "Profit", "ROI %"],
      "rows": [["01.06", "", "", "", "", "", ""]]
    }},
    {{
      "name": "ПО БАЕРАМ",
      "start": "I3",
      "headers": ["Buyer", "Spend", "Deps", "CPA Gross", "Revenue", "Avg check", "CPA Cost", "Profit", "ROI %"],
      "rows": [["Buyer1", "", "", "", "", "", "", "", ""]]
    }}
  ]
}}
 
2. build_table — for simple single-block tables
{{
  "action": "build_table",
  "sheet_title": "Sales",
  "headers": ["Date", "Buyer", "Amount"],
  "rows": [["01.06", "John", "100"]]
}}
 
3. fill_data — add or update data in an existing sheet
{{
  "action": "fill_data",
  "sheet_name": "MEDIA BUYERS DASHBOARD",
  "start_cell": "B5",
  "rows": [["100", "5", "20", "500", "50", "10%"]]
}}
 
4. write_cell — update a single cell
{{
  "action": "write_cell",
  "sheet_name": "MEDIA BUYERS DASHBOARD",
  "cell": "B5",
  "value": "100"
}}
 
5. create_sheet — create a blank sheet tab
{{
  "action": "create_sheet",
  "title": "Raw Data"
}}
 
IMPORTANT for fill_data:
- Use context to determine the correct sheet_name and start_cell.
- If user says "add 100 spend to buyer1" and context shows ПО БАЕРАМ block starts at I3 with headers [Buyer, Spend, ...], then Buyer1 is row I4, Spend is column J — so start_cell = "J4".
- Always use the last active sheet from context unless user specifies another.
"""
 
# =========================================
# GPT CALLS
# =========================================
async def gpt_sheet_text(text: str, spreadsheet, chat_id: int) -> dict | None:
    response = client.chat.completions.create(
        model="gpt-4.1",
        temperature=0,
        messages=[
            {"role": "system", "content": sheets_system(spreadsheet.title, memory_context(chat_id))},
            {"role": "user", "content": text}
        ]
    )
    return extract_json(response.choices[0].message.content)
 
async def gpt_sheet_photo(base64_image: str, caption: str, spreadsheet, chat_id: int) -> dict | None:
    response = client.chat.completions.create(
        model="gpt-4.1",
        temperature=0,
        messages=[
            {"role": "system", "content": sheets_system(spreadsheet.title, memory_context(chat_id))},
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": caption or "Build a table based on this reference. Reproduce the full structure with all blocks, sections, and columns exactly as shown. Use write_layout if there are multiple sections."},
                    {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{base64_image}", "detail": "high"}}
                ]
            }
        ]
    )
    return extract_json(response.choices[0].message.content)
 
# =========================================
# START / COMMANDS / BUTTONS
# =========================================
@dp.message(CommandStart())
async def start(message: types.Message):
    chat_id = message.chat.id
    user_modes[chat_id] = "chat"
    active_spreadsheets[chat_id] = default_spreadsheet
    await message.answer("Choose mode:", reply_markup=main_keyboard)
 
@dp.message(Command("chat"))
async def set_chat_mode(message: types.Message):
    user_modes[message.chat.id] = "chat"
    await message.answer("💬 Chat mode enabled", reply_markup=main_keyboard)
 
@dp.message(Command("sheet"))
async def set_sheet_mode(message: types.Message):
    user_modes[message.chat.id] = "sheet"
    await message.answer("📊 Sheet mode enabled", reply_markup=main_keyboard)
 
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
        base64_image = base64.b64encode(downloaded.read()).decode("utf-8")
 
        if mode == "chat":
            history = chat_history.get(chat_id, [])
            user_text = message.caption or "Analyze image"
            response = client.chat.completions.create(
                model="gpt-4.1",
                messages=[
                    {"role": "system", "content": "You are a helpful Telegram AI assistant. Speak naturally in Russian."}
                ] + history + [
                    {"role": "user", "content": [
                        {"type": "text", "text": user_text},
                        {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{base64_image}"}}
                    ]}
                ],
                temperature=0.7
            )
            answer = response.choices[0].message.content
            history.append({"role": "user", "content": user_text})
            history.append({"role": "assistant", "content": answer})
            chat_history[chat_id] = history[-MAX_HISTORY:]
            await message.answer(answer)
 
        elif mode == "sheet":
            current_spreadsheet = active_spreadsheets.get(chat_id, default_spreadsheet)
            await message.answer("🔍 Анализирую изображение...")
            action = await gpt_sheet_photo(base64_image, message.caption, current_spreadsheet, chat_id)
            if action is None:
                await message.answer("❌ Не смог распарсить ответ GPT")
                return
            await execute_action(action, current_spreadsheet, message, chat_id)
 
    except Exception as e:
        await message.answer(f"PHOTO ERROR:\n{e}")
 
# =========================================
# TEXT HANDLER
# =========================================
@dp.message()
async def chat(message: types.Message):
    try:
        text = message.text
        if not text or text.startswith("/") or text in ["💬 Chat", "📊 Sheets"]:
            return
 
        chat_id = message.chat.id
        mode = user_modes.get(chat_id, "chat")
 
        if mode == "chat":
            history = chat_history.get(chat_id, [])
            history.append({"role": "user", "content": text})
            response = client.chat.completions.create(
                model="gpt-4.1",
                messages=[
                    {"role": "system", "content": "You are a helpful AI Telegram assistant. Speak naturally in Russian. Remember previous messages."}
                ] + history[-MAX_HISTORY:],
                temperature=0.7
            )
            answer = response.choices[0].message.content
            history.append({"role": "assistant", "content": answer})
            chat_history[chat_id] = history[-MAX_HISTORY:]
            await message.answer(answer)
 
        elif mode == "sheet":
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
            action = await gpt_sheet_text(text, current_spreadsheet, chat_id)
 
            if action is None:
                await message.answer("❌ GPT не вернул JSON. Попробуй переформулировать.")
                return
 
            await execute_action(action, current_spreadsheet, message, chat_id)
 
    except Exception as e:
        await message.answer(f"TEXT ERROR:\n{e}")
 
# =========================================
# MAIN
# =========================================
async def main():
    await dp.start_polling(bot)
 
if __name__ == "__main__":
    asyncio.run(main())
