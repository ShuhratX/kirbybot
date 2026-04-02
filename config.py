import os
from dotenv import load_dotenv

load_dotenv()

BOT_TOKEN: str = os.getenv("BOT_TOKEN", "")
GROUP_ID: int = int(os.getenv("GROUP_ID", "-1001234567890"))
WEBAPP_URL: str = os.getenv("WEBAPP_URL", "https://kirbybot.vercel.app")
ADMIN_IDS: list[int] = [
    int(x) for x in os.getenv("ADMIN_IDS", "143249567").split(",") if x.strip()
]
PAYMENT_CARD: str = os.getenv("PAYMENT_CARD", "8600 0000 0000 0000")
PAYMENT_OWNER: str = os.getenv("PAYMENT_OWNER", "Ism Familiya")

# PostgreSQL DSN — asyncpg format
# Example: postgresql://user:password@localhost:5432/orderbot
DATABASE_URL: str = os.getenv(
    "DATABASE_URL",
    "postgresql://postgres:postgres@localhost:5432/kirby_db",
)