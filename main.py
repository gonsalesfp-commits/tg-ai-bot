from aiogram import Bot, Dispatcher, types
from aiogram.filters import CommandStart
from openai import OpenAI
import asyncio
import os
import base64
import json
import gspread
from google.oauth2.service_account import Credentials

# TOKENS

BOT_TOKEN = os.getenv("BOT_TOKEN")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")

# TELEGRAM

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

# OPENAI

client = OpenAI(api_key=OPENAI_API_KEY)

# GOOGLE SHEETS

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


# START

@dp.message(CommandStart())
async def start(message: types.Message):

    await message.answer("AI Spreadsheet Operator Online")


# PHOTO HANDLER

@dp.message(lambda m: m.photo)
async def handle_photo(message: types.Message):

    try:

        # GET PHOTO

        photo = message.photo[-1]

        file = await bot.get_file(photo.file_id)

        downloaded = await bot.download_file(file.file_path)

        image_bytes = downloaded.read()

        base64_image = base64.b64encode(image_bytes).decode("utf-8")

        # GPT VISION

        response = client.chat.completions.create(
            model="gpt-4.1-mini",
            messages=[
                {
                    "role": "system",
                    "content": """
You are an AI spreadsheet operator.

Analyze screenshots with reports/statistics.

Your task:

1. Extract report data
2. Build spreadsheet structure
3. Return ONLY valid JSON

FORMAT:

{
  "action": "create_report",
  "sheet_title": "Daily Report",
  "headers": [
    "Buyer",
    "Spend",
    "Deps",
    "Revenue",
    "Profit",
    "ROI"
  ],
  "rows": [
    ["Buyer1", "100", "2", "150", "50", "50%"]
  ]
}
"""
                },
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "text",
                            "text": "Analyze this screenshot and create report table JSON."
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

# CLEAN JSON

answer = answer.replace("```json", "")

answer = answer.replace("```", "")

answer = answer.strip()

# PARSE JSON

data = json.loads(answer)

        # EXECUTOR

        if data["action"] == "create_report":

            # CREATE NEW SHEET

            report_sheet = spreadsheet.add_worksheet(
                title=data["sheet_title"],
                rows="300",
                cols="20"
            )

            # ADD HEADERS

            report_sheet.append_row(data["headers"])

            # ADD ROWS

            for row in data["rows"]:

                report_sheet.append_row(row)

            await message.answer(
                f'Report "{data["sheet_title"]}" created successfully'
            )

            return

        await message.answer(str(data))

    except Exception as e:

        await message.answer(f"PHOTO ERROR: {e}")


# TEXT HANDLER

@dp.message()
async def chat(message: types.Message):

    try:

        text = message.text

        # GPT PLANNER

        response = client.chat.completions.create(
            model="gpt-4.1-mini",
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

        # PARSE JSON

        try:

            action = json.loads(answer)

        except Exception:

            await message.answer(answer)
            return

        # CREATE SHEET

        if action["action"] == "create_sheet":

            title = action["title"]

            spreadsheet.add_worksheet(
                title=title,
                rows="100",
                cols="20"
            )

            await message.answer(
                f"Created sheet: {title}"
            )

            return

        # WRITE CELL

        if action["action"] == "write_cell":

            cell = action["cell"]

            value = action["value"]

            main_sheet.update(cell, [[value]])

            await message.answer(
                f"Wrote {value} to {cell}"
            )

            return

        await message.answer(str(action))

    except Exception as e:

        await message.answer(f"TEXT ERROR: {e}")


# MAIN

async def main():

    await dp.start_polling(bot)


if __name__ == "__main__":

    asyncio.run(main())
