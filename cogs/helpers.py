import discord
from config_testing import *

def is_in_correct_guild(interaction: discord.Interaction) -> bool:
    return interaction.guild_id == GUILD_ID


# helpers.py

import logging
import inspect
from datetime import datetime

# 1) Generate a log file name based on date/time
LOG_FILENAME = datetime.now().strftime("botlog_%Y-%m-%d_%H-%M-%S.log")

# 2) Configure the logging to write to that file
logging.basicConfig(
    filename=LOG_FILENAME,
    filemode="a",            # append to the file
    level=logging.INFO,      # or DEBUG, etc.
    format="%(asctime)s - %(message)s",  # We'll prepend module info ourselves below
    datefmt="%Y-%m-%d %H:%M:%S"
)

def log(message: str, level: str = "info"):
    # 1) Identify which file (module) called log()
    #    - we look one frame up in the stack
    caller_frame = inspect.stack()[1]
    caller_module = inspect.getmodule(caller_frame[0])
    module_name = caller_module.__name__ if caller_module else "UnknownModule"

    # 2) Build the final text with [module_name]
    full_msg = f"[{module_name}] {message}"

    # 3) Dispatch to the built-in logger with the chosen level
    if level.lower() == "error":
        logging.error(full_msg)
    elif level.lower() == "warning":
        logging.warning(full_msg)
    elif level.lower() == "debug":
        logging.debug(full_msg)
    else:
        logging.info(full_msg)

