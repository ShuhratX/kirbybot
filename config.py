import os
from dotenv import load_dotenv

load_dotenv()

BOT_TOKEN: str = os.getenv("BOT_TOKEN", "")
GROUP_ID: int = int(os.getenv("GROUP_ID", "-1001234567890"))
WEBAPP_URL: str = os.getenv("WEBAPP_URL", "https://username.github.io/repo")

ADMIN_IDS: list[int] = [
    int(x) for x in os.getenv("ADMIN_IDS", "").split(",") if x.strip()
]

PAYMENT_CARD: str = os.getenv("PAYMENT_CARD", "8600 0000 0000 0000")
PAYMENT_OWNER: str = os.getenv("PAYMENT_OWNER", "Ism Familiya")

# SQLite file path
DB_PATH: str = os.getenv("DB_PATH", "orders.db")

# GitHub API — pushes products.json to GitHub Pages branch
# Token needs 'repo' (or 'contents: write') scope
GITHUB_TOKEN: str = os.getenv("GITHUB_TOKEN", "")
GITHUB_REPO: str = os.getenv("GITHUB_REPO", "")  # "username/repo"
GITHUB_BRANCH: str = os.getenv("GITHUB_BRANCH", "gh-pages")
GITHUB_FILE: str = os.getenv("GITHUB_FILE", "products.json")
PICKUP_LAT: float = float(os.getenv("PICKUP_LAT", "41.336943"))
PICKUP_LON: float = float(os.getenv("PICKUP_LON", "69.322792"))