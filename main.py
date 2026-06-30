from aiogram import Bot, Dispatcher, types
from aiogram.filters import CommandStart
from openai import OpenAI
import asyncio
import os

BOT_TOKEN = os.getenv("BOT_TOKEN")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

client = OpenAI(api_key=OPENAI_API_KEY)

@dp.message(CommandStart())
async def start(message: types.Message):
    await message.answer("AI bot online")

@dp.message()
async def chat(message: types.Message):

    user_text = message.text

    response = client.chat.completions.create(
        model="gpt-4.1-mini",
        messages=[
            {
                "role": "system",
                "content": "You are a useful AI assistant."
            },
            {
                "role": "user",
                "content": user_text
            }
        ]
    )

    answer = response.choices[0].message.content

    await message.answer(answer)

async def main():
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
