from aiogram import Bot, Dispatcher, types
from aiogram.filters import CommandStart
from openai import OpenAI
import asyncio
import os
import gspread
from google.oauth2.service_account import Credentials

BOT_TOKEN = os.getenv("BOT_TOKEN")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

client = OpenAI(api_key=OPENAI_API_KEY)

# GOOGLE SHEETS

scopes = [
    "https://www.googleapis.com/auth/spreadsheets"
]

creds = Credentials.from_service_account_file(
    "/etc/secrets/credentials.json",
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

    # запись в таблицу
    if text.startswith("/write"):

        value = text.replace("/write ", "")

        sheet.update("A1", [[value]])

        await message.answer(f"Wrote to sheet: {value}")

        return

    # GPT chat

    response = client.chat.completions.create(
        model="gpt-4.1-mini",
        messages=[
            {
                "role": "system",
                "content": "You are a useful AI assistant."
            },
            {
                "role": "user",
                "content": text
            }
        ]
    )

    answer = response.choices[0].message.content

    await message.answer(answer)

async def main():
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
