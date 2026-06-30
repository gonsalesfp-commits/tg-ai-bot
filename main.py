from aiogram import Bot, Dispatcher, types
from aiogram.filters import CommandStart
from openai import OpenAI
import asyncio
import os
import base64
import gspread
from google.oauth2.service_account import Credentials

BOT_TOKEN = os.getenv("BOT_TOKEN")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

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

sheet = gs_client.open("TEST BOT").sheet1

@dp.message(CommandStart())
async def start(message: types.Message):
    await message.answer("AI + Sheets bot online")

@dp.message()
async def chat(message: types.Message):

    text = message.text

    # GPT planner

    response = client.chat.completions.create(
        model="gpt-4.1-mini",
        messages=[
            {
                "role": "system",
                "content": """
You are an AI spreadsheet assistant.

You must return ONLY JSON.

Available actions:

1. create_sheet
Example:
{
  "action": "create_sheet",
  "title": "Buyers"
}

2. write_cell
Example:
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

    import json

    try:

        action = json.loads(answer)

    except Exception as e:

        await message.answer(f"JSON ERROR: {e}")
        return

    # EXECUTOR

    if action["action"] == "create_sheet":

        title = action["title"]

        sheet.spreadsheet.add_worksheet(
            title=title,
            rows="100",
            cols="20"
        )

        await message.answer(f"Created sheet: {title}")

        return

    if action["action"] == "write_cell":

        cell = action["cell"]
        value = action["value"]

        sheet.update(cell, [[value]])

        await message.answer(f"Wrote {value} to {cell}")

        return

    await message.answer(str(action))
    @dp.message(lambda message: message.photo)
async def handle_photo(message: types.Message):

    # biggest photo
    photo = message.photo[-1]

    file_info = await bot.get_file(photo.file_id)

    file_path = file_info.file_path

    downloaded_file = await bot.download_file(file_path)

    image_bytes = downloaded_file.read()

    base64_image = base64.b64encode(image_bytes).decode("utf-8")

    response = client.chat.completions.create(
        model="gpt-4.1-mini",
        messages=[
            {
                "role": "user",
                "content": [
                    {
                        "type": "text",
                        "text": """
Read this screenshot.

Extract all useful report/statistics data.

Return ONLY JSON.
"""
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

    await message.answer(answer)
async def main():
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
