import os
from dotenv import load_dotenv

load_dotenv()

ENV = os.getenv("BOT_ENV", "production").lower()
if ENV == "testing":
    from config_testing import *
else:
    from config_prod import *
