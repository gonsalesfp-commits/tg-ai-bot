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

    user_modes[message.chat.id] = "chat"

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
# BUTTON HANDLERS
# =========================================

@dp.message(lambda message: message.text == "💬 Chat Mode")
async def button_chat_mode(message: types.Message):

    user_modes[message.chat.id] = "chat"

    await message.answer(
        "💬 Chat mode enabled",
        reply_markup=main_keyboard
    )


@dp.message(lambda message: message.text == "📊 Sheet Mode")
async def button_sheet_mode(message: types.Message):

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

        # =====================================
        # DOWNLOAD IMAGE
        # =====================================

        photo = message.photo[-1]

        file = await bot.get_file(photo.file_id)

        downloaded = await bot.download_file(file.file_path)

        image_bytes = downloaded.read()

        base64_image = base64.b64encode(image_bytes).decode("utf-8")

        # =====================================
        # CHAT MODE WITH IMAGE
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
- User is building THIS Telegram bot with you
- If user says:
  "this bot"
  "here"
  "our project"
  "buttons"
  "sheets"
  they mean THIS Telegram bot project

- Behave like ChatGPT
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
                ],
                temperature=0.7
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
        # SHEET MODE WITH IMAGE
        # =====================================

        if mode == "sheet":

            response = client.chat.completions.create(
                model="gpt-4.1",
                messages=[
                    {
                        "role": "system",
                        "content": """
You are an AI spreadsheet operator.

Analyze screenshots and create Google Sheets structure.

Return ONLY valid JSON.

AVAILABLE ACTIONS:

1. create_report

FORMAT:

{
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
}
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

            # =====================================
            # CREATE REPORT
            # =====================================

            if data["action"] == "create_report":

                report_sheet = spreadsheet.add_worksheet(
                    title=data["sheet_title"] + "_" + str(message.message_id),
                    rows="300",
                    cols="30"
                )

                # HEADERS

                report_sheet.append_row(data["headers"])

                # ROWS

                for row in data["rows"]:

                    report_sheet.append_row(row)

                await message.answer(
                    f'Report "{data["sheet_title"]}" created successfully'
                )

                return

            await message.answer(str(data))

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

        # IGNORE COMMANDS

        if text.startswith("/"):
            return

        # IGNORE BUTTONS

        if text in [
            "💬 Chat Mode",
            "📊 Sheet Mode"
        ]:
            return

        chat_id = message.chat.id

        mode = user_modes.get(chat_id, "chat")

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
- Never lose conversation context
- User is building THIS Telegram bot with you

- Understand references:
  "this bot"
  "here"
  "our table"
  "make modes"
  "add buttons"

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
                messages=[
                    {
                        "role": "system",
                        "content": """
You are an AI spreadsheet assistant.

Return ONLY valid JSON.

AVAILABLE ACTIONS:

1. create_sheet

FORMAT:
{
  "action": "create_sheet",
  "title": "Buyers"
}

2. write_cell

FORMAT:
{
  "action": "write_cell",
  "cell": "A1",
  "value": "hello"
}
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

                await message.answer(answer)

                return

            # =====================================
            # CREATE SHEET
            # =====================================

            if action["action"] == "create_sheet":

                title = action["title"]

                spreadsheet.add_worksheet(
                    title=title + "_" + str(message.message_id),
                    rows="100",
                    cols="20"
                )

                await message.answer(
                    f"Created sheet: {title}"
                )

                return

            # =====================================
            # WRITE CELL
            # =====================================

            if action["action"] == "write_cell":

                cell = action["cell"]

                value = action["value"]

                main_sheet.update(cell, [[value]])

                await message.answer(
                    f"Wrote {value} to {cell}"
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
