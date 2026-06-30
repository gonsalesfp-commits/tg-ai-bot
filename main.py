from aiogram import Bot, Dispatcher, types
from aiogram.filters import CommandStart, Command
from aiogram.types import ReplyKeyboardMarkup, KeyboardButton
from openai import OpenAI

import asyncio
import os
import base64
import json
import gspread

from google.oauth2.service_account import Credentials

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

spreadsheet = gs_client.open("TEST BOT")

main_sheet = spreadsheet.sheet1

# =========================================
# MEMORY
# =========================================

user_modes = {}

chat_history = {}

active_spreadsheets = {}

MAX_HISTORY = 60

# =========================================
# KEYBOARD
# =========================================

main_keyboard = ReplyKeyboardMarkup(
    keyboard=[
        [
            KeyboardButton(text="💬 Chat Mode"),
            KeyboardButton(text="📊 Sheet Mode")
        ]
    ],
    resize_keyboard=True
)

# =========================================
# START
# =========================================

@dp.message(CommandStart())
async def start(message: types.Message):

    chat_id = message.chat.id

    user_modes[chat_id] = "chat"

    active_spreadsheets[chat_id] = spreadsheet

    await message.answer(
        "AI Operator Online",
        reply_markup=main_keyboard
    )

# =========================================
# MODE COMMANDS
# =========================================

@dp.message(Command("chat"))
async def set_chat_mode(message: types.Message):

    user_modes[message.chat.id] = "chat"

    await message.answer(
        "💬 Chat mode enabled",
        reply_markup=main_keyboard
    )

@dp.message(Command("sheet"))
async def set_sheet_mode(message: types.Message):

    user_modes[message.chat.id] = "sheet"

    await message.answer(
        "📊 Spreadsheet mode enabled",
        reply_markup=main_keyboard
    )

# =========================================
# BUTTONS
# =========================================

@dp.message(lambda message: message.text == "💬 Chat Mode")
async def button_chat(message: types.Message):

    user_modes[message.chat.id] = "chat"

    await message.answer(
        "💬 Chat mode enabled",
        reply_markup=main_keyboard
    )

@dp.message(lambda message: message.text == "📊 Sheet Mode")
async def button_sheet(message: types.Message):

    user_modes[message.chat.id] = "sheet"

    await message.answer(
        "📊 Spreadsheet mode enabled",
        reply_markup=main_keyboard
    )

# =========================================
# PHOTO HANDLER
# =========================================

@dp.message(lambda m: m.photo)
async def handle_photo(message: types.Message):

    try:

        chat_id = message.chat.id

        mode = user_modes.get(chat_id, "chat")

        current_spreadsheet = active_spreadsheets.get(
            chat_id,
            spreadsheet
        )

        photo = message.photo[-1]

        file = await bot.get_file(photo.file_id)

        downloaded = await bot.download_file(file.file_path)

        image_bytes = downloaded.read()

        base64_image = base64.b64encode(image_bytes).decode("utf-8")

        # =====================================
        # CHAT MODE
        # =====================================

        if mode == "chat":

            history = chat_history.get(chat_id, [])

            user_text = (
                message.caption
                if message.caption
                else "Analyze image"
            )

            response = client.chat.completions.create(
                model="gpt-4.1",
                messages=[
                    {
                        "role": "system",
                        "content": """
You are a persistent Telegram AI assistant.

IMPORTANT:

- Remember previous messages
- Continue conversations naturally
- User is building THIS Telegram bot
- Behave like normal ChatGPT
- Speak Russian naturally
"""
                    }
                ] + history + [
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "text",
                                "text": user_text
                            },
                            {
                                "type": "image_url",
                                "image_url": {
                                    "url": f"data:image/jpeg;base64,{base64_image}"
                                }
                            }
                        ]
                    }
                ]
            )

            answer = response.choices[0].message.content

            history.append({
                "role": "user",
                "content": user_text
            })

            history.append({
                "role": "assistant",
                "content": answer
            })

            history = history[-MAX_HISTORY:]

            chat_history[chat_id] = history

            await message.answer(answer)

            return

        # =====================================
        # SHEET MODE
        # =====================================

        if mode == "sheet":

            response = client.chat.completions.create(
                model="gpt-4.1",
                temperature=0,
                messages=[
                    {
                        "role": "system",
                        "content": f"""
You are a Google Sheets AI operator.

IMPORTANT:

You DO have access to Google Sheets.

Current spreadsheet:
{current_spreadsheet.title}

Return ONLY valid JSON.

AVAILABLE ACTIONS:

1. create_report

{{
  "action": "create_report",
  "sheet_title": "Daily Report",
  "headers": [
    "Buyer",
    "Spend",
    "Deps"
  ],
  "rows": [
    ["Buyer1", "100", "2"]
  ]
}}
"""
                    },
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "text",
                                "text": (
                                    message.caption
                                    if message.caption
                                    else "Analyze screenshot"
                                )
                            },
                            {
                                "type": "image_url",
                                "image_url": {
                                    "url": f"data:image/jpeg;base64,{base64_image}"
                                }
                            }
                        ]
                    }
                ]
            )

            answer = response.choices[0].message.content

            answer = answer.replace("```json", "")
            answer = answer.replace("```", "")
            answer = answer.strip()

            data = json.loads(answer)

            if data["action"] == "create_report":

                report_sheet = current_spreadsheet.add_worksheet(
                    title=data["sheet_title"],
                    rows="300",
                    cols="30"
                )

                report_sheet.append_row(data["headers"])

                for row in data["rows"]:

                    report_sheet.append_row(row)

                await message.answer(
                    f'Report "{data["sheet_title"]}" created'
                )

                return

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

        if text in [
            "💬 Chat Mode",
            "📊 Sheet Mode"
        ]:
            return

        chat_id = message.chat.id

        mode = user_modes.get(chat_id, "chat")

        current_spreadsheet = active_spreadsheets.get(
            chat_id,
            spreadsheet
        )

        # =====================================
        # CHAT MODE
        # =====================================

        if mode == "chat":

            history = chat_history.get(chat_id, [])

            history.append({
                "role": "user",
                "content": text
            })

            history = history[-MAX_HISTORY:]

            response = client.chat.completions.create(
                model="gpt-4.1",
                messages=[
                    {
                        "role": "system",
                        "content": """
You are a persistent Telegram AI assistant.

IMPORTANT:

- Remember previous messages
- Continue conversations naturally
- User is building THIS Telegram bot
- Behave like ChatGPT
- Speak naturally in Russian
"""
                    }
                ] + history,
                temperature=0.7
            )

            answer = response.choices[0].message.content

            history.append({
                "role": "assistant",
                "content": answer
            })

            history = history[-MAX_HISTORY:]

            chat_history[chat_id] = history

            await message.answer(answer)

            return

        # =====================================
        # SHEET MODE
        # =====================================

        if mode == "sheet":

            response = client.chat.completions.create(
                model="gpt-4.1",
                temperature=0,
                messages=[
                    {
                        "role": "system",
                        "content": f"""
You are a Google Sheets AI operator.

IMPORTANT:

You DO have access to Google Sheets.

Current spreadsheet:
{current_spreadsheet.title}

Return ONLY valid JSON.

NEVER explain.
NEVER chat.
NEVER answer with text.

AVAILABLE ACTIONS:

1. open_spreadsheet

{{
  "action": "open_spreadsheet",
  "url": "https://docs.google.com/..."
}}

2. create_sheet

{{
  "action": "create_sheet",
  "title": "Buyers"
}}

3. write_cell

{{
  "action": "write_cell",
  "cell": "A1",
  "value": "hello"
}}

4. write_range

{{
  "action": "write_range",
  "start_cell": "A1",
  "values": [
    ["Name", "Spend"],
    ["John", "100"]
  ]
}}

5. build_dashboard

{{
  "action": "build_dashboard",
  "title": "Media Buyers Dashboard"
}}

If user sends Google Sheets URL:
use open_spreadsheet

If user asks:
- create table
- build dashboard
- add tabs
- fill cells

you MUST return JSON command.
"""
                    },
                    {
                        "role": "user",
                        "content": text
                    }
                ]
            )

            answer = response.choices[0].message.content

            answer = answer.replace("```json", "")
            answer = answer.replace("```", "")
            answer = answer.strip()

            try:

                action = json.loads(answer)

            except Exception:

                await message.answer(
                    f"JSON ERROR:\n{answer}"
                )

                return

            # =====================================
            # OPEN SPREADSHEET
            # =====================================

            if action["action"] == "open_spreadsheet":

                url = action["url"]

                opened_spreadsheet = gs_client.open_by_url(url)

                active_spreadsheets[chat_id] = opened_spreadsheet

                await message.answer(
                    f"Connected:\n{opened_spreadsheet.title}"
                )

                return

            # =====================================
            # CREATE SHEET
            # =====================================

            if action["action"] == "create_sheet":

                title = action["title"]

                current_spreadsheet.add_worksheet(
                    title=title,
                    rows="200",
                    cols="30"
                )

                await message.answer(
                    f"Sheet created: {title}"
                )

                return

            # =====================================
            # WRITE CELL
            # =====================================

            if action["action"] == "write_cell":

                current_sheet = current_spreadsheet.sheet1

                current_sheet.update(
                    action["cell"],
                    [[action["value"]]]
                )

                await message.answer(
                    f'Updated {action["cell"]}'
                )

                return

            # =====================================
            # WRITE RANGE
            # =====================================

            if action["action"] == "write_range":

                current_sheet = current_spreadsheet.sheet1

                current_sheet.update(
                    action["start_cell"],
                    action["values"]
                )

                await message.answer(
                    "Range updated"
                )

                return

            # =====================================
            # BUILD DASHBOARD
            # =====================================

            if action["action"] == "build_dashboard":

                dashboard = current_spreadsheet.add_worksheet(
                    title=action["title"],
                    rows="100",
                    cols="30"
                )

                headers = [[
                    "Buyer",
                    "Spend",
                    "Deps",
                    "Revenue",
                    "Profit",
                    "ROI"
                ]]

                dashboard.update("A1:F1", headers)

                await message.answer(
                    f'Dashboard "{action["title"]}" created'
                )

                return

            await message.answer(str(action))

            return

    except Exception as e:

        await message.answer(f"TEXT ERROR:\n{e}")

# =========================================
# MAIN
# =========================================

async def main():

    await dp.start_polling(bot)

if __name__ == "__main__":

    asyncio.run(main())
