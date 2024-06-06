from telegram import Bot
import requests
import os
import asyncio
from dotenv import load_dotenv
load_dotenv()
BOT_TOKEN = os.getenv('TELEGRAM_BOT_TOKEN')
CHAT_IDS=[6071212724,]
print(CHAT_IDS)
async def send_telegram_message(message):
    bot = Bot(token=BOT_TOKEN)
    for chat_id in CHAT_IDS:
        print("CHAT id: ",chat_id)
        await bot.send_message(chat_id=chat_id, text=message)

def get_chat_id():
    token=BOT_TOKEN
    print(token)
    url = f"https://api.telegram.org/bot{token}/getUpdates"
    response = requests.get(url)
    data = response.json()
    print(data)

if __name__ == "__main__":
    # Replace 'YOUR_BOT_API_TOKEN' with your bot's API token
    # Replace 'YOUR_CHAT_ID' with the chat ID you want to send the message to
    chat_id = 'YOUR_CHAT_ID'
    message = 'Hello world from your bot!'
    #get_chat_id()
    list1=["messi","Ronandlo"]
    #send_telegram_message(message)
    asyncio.run(send_telegram_message( list1))
    #send_telegram_message(chat_id, message)
