from dotenv import load_dotenv

import os

load_dotenv()
TOKEN = os.getenv("DISCORD_TOKEN")

import discord
from discord.ext import commands

# Configuration
config = {
    'token': TOKEN,
    'prefix': '!',
}

intents = discord.Intents.default()
intents.message_content = True
intents.members = True

# Creating bot
bot = commands.Bot(command_prefix=config['prefix'], intents=intents)

# Event on the start
@bot.event
async def on_ready():
    print(f'Bot {bot.user.name} was successful started!')

# Loading all the cogs
@bot.event
async def setup_hook():
    await bot.load_extension("cogs.moderation")

# Running the bot
bot.run(config['token'])