from aiogram import Bot, Dispatcher, types
from aiogram.filters import CommandStart
import asyncio
import os

TOKEN = os.getenv("BOT_TOKEN")

bot = Bot(token=TOKEN)
dp = Dispatcher()

@dp.message(CommandStart())
async def start(message: types.Message):
    await message.answer("Bot works")

@dp.message()
async def echo(message: types.Message):
    await message.answer(f"You said: {message.text}")

async def main():
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
