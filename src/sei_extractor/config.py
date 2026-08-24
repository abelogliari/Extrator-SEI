import os
from dotenv import load_dotenv

load_dotenv()

SEI_URL = os.getenv("SEI_URL")
SEI_USERNAME = os.getenv("SEI_USERNAME")
SEI_PASSWORD = os.getenv("SEI_PASSWORD")
THREADS = int(os.getenv("THREADS", 10))
HEADLESS = os.getenv("HEADLESS", "True").lower() in ("true", "1", "t")