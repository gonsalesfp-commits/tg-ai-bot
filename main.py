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
# CLIENTS
# =========================================
bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()
client = OpenAI(api_key=OPENAI_API_KEY)

# =========================================
# GOOGLE
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
chat_history = {}       # история для chat-режима
sheet_history = {}      # история диалога для sheet-режима
active_spreadsheets = {}
sheet_memory = {}       # контекст таблиц
MAX_HISTORY = 60

def get_mem(chat_id):
    if chat_id not in sheet_memory:
        sheet_memory[chat_id] = {"last_sheet": None, "sheets": {}, "log": []}
    return sheet_memory[chat_id]

def remember_sheet(chat_id, title, blocks):
    m = get_mem(chat_id)
    m["last_sheet"] = title
    m["sheets"][title] = {"blocks": blocks}
    m["log"].append(f"Создал лист '{title}' с блоками: {[b['name'] for b in blocks]}")
    m["log"] = m["log"][-30:]

def remember_action(chat_id, text):
    m = get_mem(chat_id)
    m["log"].append(text)
    m["log"] = m["log"][-30:]

def mem_ctx(chat_id):
    m = get_mem(chat_id)
    if not m["last_sheet"] and not m["log"]:
        return "Нет истории действий в этой сессии."
    lines = []
    if m["last_sheet"]:
        lines.append(f'Последний активный лист: "{m["last_sheet"]}"')
        info = m["sheets"].get(m["last_sheet"], {})
        for b in info.get("blocks", []):
            lines.append(f'  Блок "{b["name"]}" — start: {b["start"]}, колонки: {b["headers"]}')
    if m["log"]:
        lines.append("Последние действия:")
        for e in m["log"][-10:]:
            lines.append(f"  • {e}")
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
# UTILS
# =========================================
def extract_sheet_url(text):
    m = re.findall(r'https://docs\.google\.com/spreadsheets/[^\s]+', text)
    return m[0] if m else None

def extract_json(text):
    text = text.replace("```json", "").replace("```", "").strip()
    try:
        return json.loads(text)
    except Exception:
        pass
    m = re.search(r'\{.*\}', text, re.DOTALL)
    if m:
        try:
            return json.loads(m.group())
        except Exception:
            pass
    return None

def col_to_idx(col):
    col = col.upper()
    r = 0
    for c in col:
        r = r * 26 + ord(c) - ord('A') + 1
    return r - 1

def cell_to_rc(cell):
    m = re.match(r'([A-Za-z]+)(\d+)', cell)
    if not m:
        return 0, 0
    return int(m.group(2)) - 1, col_to_idx(m.group(1))

def format_header(spreadsheet_id, sheet_id, r0, r1, c0, c1, color=(0.18, 0.33, 0.45)):
    rr, gg, bb = color
    sheets_service.spreadsheets().batchUpdate(
        spreadsheetId=spreadsheet_id,
        body={"requests": [
            {"repeatCell": {
                "range": {"sheetId": sheet_id, "startRowIndex": r0, "endRowIndex": r1,
                           "startColumnIndex": c0, "endColumnIndex": c1},
                "cell": {"userEnteredFormat": {
                    "textFormat": {"bold": True, "foregroundColor": {"red": 1, "green": 1, "blue": 1}},
                    "backgroundColor": {"red": rr, "green": gg, "blue": bb},
                    "horizontalAlignment": "CENTER"
                }},
                "fields": "userEnteredFormat(textFormat,backgroundColor,horizontalAlignment)"
            }},
            {"autoResizeDimensions": {
                "dimensions": {"sheetId": sheet_id, "dimension": "COLUMNS",
                               "startIndex": c0, "endIndex": c1}
            }}
        ]}
    ).execute()

# =========================================
# SYSTEM PROMPT (SHEETS)
# =========================================
def sheets_system(spreadsheet_title, context):
    return f"""You are a Google Sheets operator. You have API access to spreadsheet "{spreadsheet_title}".

YOUR IDENTITY IS FIXED. You cannot switch modes, become a general assistant, or act like ChatGPT.
The user controls mode switching — you do NOT change your behavior based on conversation tone.

CONNECTED SPREADSHEET: "{spreadsheet_title}"

SESSION CONTEXT:
{context}

---
HOW TO RESPOND:

If the message is an ACTION (create, delete, fill, clear, write, build) → return JSON only.
If the message is a SHORT QUESTION about the spreadsheet → answer in 1-2 sentences max, in Russian.
If the message is unclear → ask ONE short question to clarify, then stop.

NEVER:
- Write long explanations or lists of questions
- Ask "в каком приложении?" — it's always "{spreadsheet_title}"
- Give manual step-by-step instructions ("нажмите на...")
- Pretend you don't know which document — you are always connected to "{spreadsheet_title}"
- Lose track of context — if a sheet was created this session, you know its structure

SHORT ANSWER EXAMPLES (1-2 sentences only):
- "видишь таблицу?" → "Да, подключён к \"{spreadsheet_title}\". Что нужно сделать?"
- "какая у тебя почта?" → "tg-bot@just-sunrise-501012-t4.iam.gserviceaccount.com"
- "что ты умеешь?" → "Создаю и редактирую таблицы в \"{spreadsheet_title}\". Скажи что нужно сделать."
- "запомни X" → "Запомнил."

---
AVAILABLE JSON ACTIONS:

1. write_layout — сложная таблица с несколькими блоками на одном листе
{{"action":"write_layout","sheet_title":"NAME","blocks":[
  {{"name":"KPI","start":"A1","headers":["SPEND","DEPS","REVENUE","PROFIT","ROI %"],"rows":[]}},
  {{"name":"ПО ДНЯМ","start":"A3","headers":["Date","Spend","Deps","Revenue","Profit","ROI %"],"rows":[["01.06","","","","",""]]}},
  {{"name":"ПО БАЕРАМ","start":"I3","headers":["Buyer","Spend","Deps","Revenue","Avg check","Profit","ROI %"],"rows":[["Buyer1","","","","","",""]]}}
]}}

2. build_table — простая таблица с одним блоком
{{"action":"build_table","sheet_title":"NAME","headers":["Col1","Col2"],"rows":[["val1","val2"]]}}

3. fill_data — записать строки данных в существующий лист
{{"action":"fill_data","sheet_name":"NAME","start_cell":"A2","rows":[["value1","value2"]]}}

4. write_cell — обновить одну ячейку
{{"action":"write_cell","sheet_name":"NAME","cell":"A1","value":"text"}}

5. clear_range — очистить диапазон ячеек
{{"action":"clear_range","sheet_name":"NAME","range":"A21:Z25"}}

6. create_sheet — создать пустую вкладку
{{"action":"create_sheet","title":"NAME"}}

7. delete_sheets — удалить вкладки
{{"action":"delete_sheets","titles":["Sheet1"]}}

8. list_sheets — показать все вкладки
{{"action":"list_sheets"}}

---
fill_data / write_cell: используй SESSION CONTEXT для определения sheet_name и позиции ячеек.
Default sheet_name = последний активный лист из контекста.
"""

# =========================================
# EXECUTE ACTION
# =========================================
async def execute_action(action, spreadsheet, message, chat_id):
    act = action.get("action")

    # ---- write_layout ----
    if act == "write_layout":
        title = action.get("sheet_title", "Layout")
        blocks = action.get("blocks", [])

        # Проверяем нет ли уже такого листа
        existing = [s.title for s in spreadsheet.worksheets()]
        final_title = title
        if title in existing:
            final_title = title + "_new"

        max_row, max_col = 50, 20
        for b in blocks:
            r, c = cell_to_rc(b.get("start", "A1"))
            max_row = max(max_row, r + len(b.get("rows", [])) + 10)
            max_col = max(max_col, c + len(b.get("headers", [])) + 2)

        ws = spreadsheet.add_worksheet(title=final_title, rows=str(max_row), cols=str(max_col))
        sid = ws.id

        block_colors = [
            (0.13, 0.27, 0.13),
            (0.18, 0.33, 0.50),
            (0.25, 0.18, 0.45),
            (0.45, 0.25, 0.13),
        ]
        recorded = []
        for i, b in enumerate(blocks):
            start = b.get("start", "A1")
            headers = b.get("headers", [])
            rows = b.get("rows", [])
            name = b.get("name", "")
            sr, sc = cell_to_rc(start)

            # Название блока
            if name:
                ws.update(f"{chr(65+sc)}{sr+1}", [[name]])
                sr += 1

            # Данные
            ws.update(f"{chr(65+sc)}{sr+1}", [headers] + rows)

            # Форматирование заголовка
            color = block_colors[i % len(block_colors)]
            format_header(spreadsheet.id, sid, sr, sr+1, sc, sc+len(headers), color)

            recorded.append({"name": name or f"block{i}", "start": start, "headers": headers, "data_row": sr+1})

        remember_sheet(chat_id, final_title, recorded)
        url = f"https://docs.google.com/spreadsheets/d/{spreadsheet.id}/edit#gid={sid}"
        await message.answer(f'✅ Создал "{final_title}" — {len(blocks)} блока(ов)\n🔗 {url}')
        return

    # ---- build_table ----
    if act == "build_table":
        title = action.get("sheet_title", "Table")
        headers = action.get("headers", [])
        rows = action.get("rows", [])
        existing = [s.title for s in spreadsheet.worksheets()]
        if title in existing:
            title = title + "_new"
        ws = spreadsheet.add_worksheet(title=title, rows=str(max(len(rows)+20,100)), cols=str(max(len(headers)+2,20)))
        ws.update("A1", [headers] + rows)
        format_header(spreadsheet.id, ws.id, 0, 1, 0, len(headers))
        remember_sheet(chat_id, title, [{"name":"main","start":"A1","headers":headers,"data_row":1}])
        url = f"https://docs.google.com/spreadsheets/d/{spreadsheet.id}/edit#gid={ws.id}"
        await message.answer(f'✅ Таблица "{title}" готова — {len(headers)} колонок, {len(rows)} строк\n🔗 {url}')
        return

    # ---- fill_data ----
    if act == "fill_data":
        m = get_mem(chat_id)
        sheet_name = action.get("sheet_name") or m.get("last_sheet") or "Sheet1"
        start_cell = action.get("start_cell", "A2")
        rows = action.get("rows", [])
        try:
            ws = spreadsheet.worksheet(sheet_name)
        except Exception:
            all_s = [s.title for s in spreadsheet.worksheets()]
            match = next((s for s in all_s if sheet_name.lower() in s.lower()), None)
            ws = spreadsheet.worksheet(match) if match else spreadsheet.sheet1
            sheet_name = ws.title
        ws.update(start_cell, rows)
        remember_action(chat_id, f'Записал данные в "{sheet_name}"!{start_cell}: {rows}')
        await message.answer(f'✅ Записал {len(rows)} строк(и) в "{sheet_name}" с {start_cell}')
        return

    # ---- write_cell ----
    if act == "write_cell":
        m = get_mem(chat_id)
        sheet_name = action.get("sheet_name") or m.get("last_sheet") or "Sheet1"
        cell = action.get("cell", "A1")
        value = str(action.get("value", ""))
        try:
            ws = spreadsheet.worksheet(sheet_name)
        except Exception:
            ws = spreadsheet.sheet1
            sheet_name = ws.title
        ws.update(cell, [[value]])
        remember_action(chat_id, f'Записал "{value}" в {sheet_name}!{cell}')
        await message.answer(f'✅ {sheet_name}!{cell} → "{value}"')
        return

    # ---- clear_range ----
    if act == "clear_range":
        m = get_mem(chat_id)
        sheet_name = action.get("sheet_name") or m.get("last_sheet") or "Sheet1"
        rng = action.get("range", "A1")
        try:
            ws = spreadsheet.worksheet(sheet_name)
        except Exception:
            all_s = [s.title for s in spreadsheet.worksheets()]
            match = next((s for s in all_s if sheet_name.lower() in s.lower()), None)
            ws = spreadsheet.worksheet(match) if match else spreadsheet.sheet1
            sheet_name = ws.title
        ws.batch_clear([rng])
        remember_action(chat_id, f'Очистил {sheet_name}!{rng}')
        await message.answer(f'✅ Очистил диапазон {sheet_name}!{rng}')
        return

    # ---- create_sheet ----
    if act == "create_sheet":
        title = action.get("title", "New Sheet")
        existing = [s.title for s in spreadsheet.worksheets()]
        if title in existing:
            await message.answer(f'⚠️ Лист "{title}" уже существует')
            return
        spreadsheet.add_worksheet(title=title, rows="200", cols="30")
        remember_action(chat_id, f'Создал пустой лист "{title}"')
        await message.answer(f'✅ Лист "{title}" создан')
        return

    # ---- delete_sheets ----
    if act == "delete_sheets":
        titles_to_delete = action.get("titles", [])
        all_sheets = spreadsheet.worksheets()

        if len(all_sheets) <= 1:
            await message.answer("⚠️ Нельзя удалить все вкладки — в документе должна остаться хотя бы одна.")
            return

        deleted = []
        skipped = []
        for title in titles_to_delete:
            # Ищем лист по точному названию или частичному совпадению
            ws = next((s for s in all_sheets if s.title == title), None)
            if ws is None:
                ws = next((s for s in all_sheets if title.lower() in s.title.lower()), None)
            if ws is None:
                skipped.append(title)
                continue
            # Нельзя удалить если это последний лист
            remaining = [s for s in all_sheets if s.title not in deleted and s.title != ws.title]
            if not remaining:
                skipped.append(f"{title} (последний лист)")
                continue
            spreadsheet.del_worksheet(ws)
            deleted.append(ws.title)
            # Обновляем список после удаления
            all_sheets = spreadsheet.worksheets()

        parts = []
        if deleted:
            parts.append(f'✅ Удалил: {", ".join(deleted)}')
            remember_action(chat_id, f'Удалил листы: {deleted}')
        if skipped:
            parts.append(f'⚠️ Не нашёл или не смог удалить: {", ".join(skipped)}')
        await message.answer("\n".join(parts) if parts else "Ничего не удалено")
        return

    # ---- list_sheets ----
    if act == "list_sheets":
        sheets = spreadsheet.worksheets()
        names = [f"• {s.title}" for s in sheets]
        await message.answer(f'Вкладки в "{spreadsheet.title}":\n' + "\n".join(names))
        return

    await message.answer(f"⚠️ Неизвестное действие: {act}")

# =========================================
# GPT SHEET CALL — только парсинг команд
# =========================================
SHEET_HISTORY_LIMIT = 6  # только последние 6 сообщений

# Захардкоженные ответы на типичные вопросы — GPT не отвечает текстом вообще
HARDCODED_ANSWERS = {
    "почт": "tg-bot@just-sunrise-501012-t4.iam.gserviceaccount.com",
    "email": "tg-bot@just-sunrise-501012-t4.iam.gserviceaccount.com",
    "mail": "tg-bot@just-sunrise-501012-t4.iam.gserviceaccount.com",
    "умеешь": "Создаю таблицы, добавляю данные, удаляю листы, очищаю диапазоны. Всё в документе «{title}».",
    "можешь": "Создаю таблицы, добавляю данные, удаляю листы, очищаю диапазоны. Всё в документе «{title}».",
    "видишь": "Подключён к «{title}». Что сделать?",
    "привет": "Привет. Подключён к «{title}». Что нужно сделать?",
    "хай": "Привет. Подключён к «{title}». Что нужно сделать?",
}

def check_hardcoded(text: str, spreadsheet_title: str) -> str | None:
    """Если текст похож на общий вопрос — возвращаем хардкоженный ответ."""
    low = text.lower()
    for keyword, answer in HARDCODED_ANSWERS.items():
        if keyword in low:
            return answer.format(title=spreadsheet_title)
    return None

async def gpt_parse_action(text: str, spreadsheet, chat_id: int) -> dict | None:
    """GPT используется ТОЛЬКО для парсинга команды в JSON. Никаких текстовых ответов."""
    history = sheet_history.get(chat_id, [])[-SHEET_HISTORY_LIMIT:]

    response = client.chat.completions.create(
        model="gpt-4.1",
        temperature=0,
        messages=[
            {"role": "system", "content": sheets_system(spreadsheet.title, mem_ctx(chat_id))}
        ] + history + [
            {"role": "user", "content": text}
        ]
    )
    answer = response.choices[0].message.content

    # Сохраняем в историю
    history.append({"role": "user", "content": text})
    history.append({"role": "assistant", "content": answer})
    sheet_history[chat_id] = history[-SHEET_HISTORY_LIMIT:]

    return extract_json(answer)

async def gpt_parse_action_photo(b64: str, caption: str, spreadsheet, chat_id: int) -> dict | None:
    """GPT для фото — только возвращает JSON действие."""
    history = sheet_history.get(chat_id, [])[-SHEET_HISTORY_LIMIT:]

    response = client.chat.completions.create(
        model="gpt-4.1",
        temperature=0,
        messages=[
            {"role": "system", "content": sheets_system(spreadsheet.title, mem_ctx(chat_id))}
        ] + history + [
            {"role": "user", "content": [
                {"type": "text", "text": caption},
                {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64}", "detail": "high"}}
            ]}
        ]
    )
    answer = response.choices[0].message.content
    history.append({"role": "user", "content": caption})
    history.append({"role": "assistant", "content": answer})
    sheet_history[chat_id] = history[-SHEET_HISTORY_LIMIT:]
    return extract_json(answer)

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
async def cmd_chat(message: types.Message):
    user_modes[message.chat.id] = "chat"
    await message.answer("💬 Chat mode enabled", reply_markup=main_keyboard)

@dp.message(Command("sheet"))
async def cmd_sheet(message: types.Message):
    user_modes[message.chat.id] = "sheet"
    await message.answer("📊 Sheet mode enabled", reply_markup=main_keyboard)

@dp.message(lambda m: m.text == "💬 Chat")
async def btn_chat(message: types.Message):
    user_modes[message.chat.id] = "chat"
    await message.answer("💬 Chat mode enabled", reply_markup=main_keyboard)

@dp.message(lambda m: m.text == "📊 Sheets")
async def btn_sheet(message: types.Message):
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
        b64 = base64.b64encode((await bot.download_file(file.file_path)).read()).decode()

        if mode == "chat":
            history = chat_history.get(chat_id, [])
            user_text = message.caption or "Analyze image"
            resp = client.chat.completions.create(
                model="gpt-4.1",
                messages=[{"role":"system","content":"You are a helpful Telegram AI assistant. Speak naturally in Russian."}]
                         + history
                         + [{"role":"user","content":[
                             {"type":"text","text":user_text},
                             {"type":"image_url","image_url":{"url":f"data:image/jpeg;base64,{b64}"}}
                         ]}],
                temperature=0.7
            )
            answer = resp.choices[0].message.content
            history.append({"role":"user","content":user_text})
            history.append({"role":"assistant","content":answer})
            chat_history[chat_id] = history[-MAX_HISTORY:]
            await message.answer(answer)

        elif mode == "sheet":
            spreadsheet = active_spreadsheets.get(chat_id, default_spreadsheet)
            await message.answer("🔍 Анализирую...")
            caption = message.caption or "Build a table based on this reference. Reproduce the full structure with all blocks and columns. Use write_layout if there are multiple sections."
            action = await gpt_parse_action_photo(b64, caption, spreadsheet, chat_id)
            if action:
                await execute_action(action, spreadsheet, message, chat_id)
            else:
                await message.answer("❓ Не понял что нужно сделать с этим изображением. Уточни задачу.")

    except Exception as e:
        await message.answer(f"PHOTO ERROR:\n{e}")

# =========================================
# TEXT HANDLER
# =========================================
@dp.message()
async def handle_text(message: types.Message):
    try:
        text = message.text
        if not text or text.startswith("/") or text in ["💬 Chat", "📊 Sheets"]:
            return

        chat_id = message.chat.id
        mode = user_modes.get(chat_id, "chat")

        # ---- CHAT MODE ----
        if mode == "chat":
            history = chat_history.get(chat_id, [])
            history.append({"role":"user","content":text})
            resp = client.chat.completions.create(
                model="gpt-4.1",
                messages=[{"role":"system","content":"You are a helpful AI Telegram assistant. Speak naturally in Russian. Remember previous messages."}]
                         + history[-MAX_HISTORY:],
                temperature=0.7
            )
            answer = resp.choices[0].message.content
            history.append({"role":"assistant","content":answer})
            chat_history[chat_id] = history[-MAX_HISTORY:]
            await message.answer(answer)
            return

        # ---- SHEET MODE ----
        if mode == "sheet":
            # Подключение по URL
            url = extract_sheet_url(text)
            if url:
                try:
                    opened = gs_client.open_by_url(url)
                    active_spreadsheets[chat_id] = opened
                    sheet_history[chat_id] = []
                    await message.answer(f'✅ Подключился к "{opened.title}"')
                except Exception as e:
                    await message.answer(f"❌ Не удалось открыть:\n{e}")
                return

            spreadsheet = active_spreadsheets.get(chat_id, default_spreadsheet)

            # Сначала проверяем захардкоженные ответы — GPT не трогаем
            hardcoded = check_hardcoded(text, spreadsheet.title)
            if hardcoded:
                await message.answer(hardcoded)
                return

            # GPT только парсит команду → JSON
            action = await gpt_parse_action(text, spreadsheet, chat_id)

            if action and "action" in action:
                await execute_action(action, spreadsheet, message, chat_id)
            else:
                # JSON не получили — значит команда непонятна
                # Не даём GPT отвечать свободно, отвечаем сами
                await message.answer(
                    f"❓ Не понял команду. Я работаю только с таблицей «{spreadsheet.title}».\n"
                    f"Скажи что нужно сделать: создать лист, добавить данные, удалить вкладку и т.д."
                )

    except Exception as e:
        await message.answer(f"TEXT ERROR:\n{e}")

# =========================================
# MAIN
# =========================================
async def main():
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
