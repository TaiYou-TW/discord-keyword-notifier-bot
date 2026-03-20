from bot import bot
from config import TOKEN

# register commands and events
import commands
import events

if __name__ == "__main__":
    bot.run(TOKEN)
