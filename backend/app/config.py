import os
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent.parent
PROJECT_ROOT = BACKEND_DIR.parent

DATA_DIR = PROJECT_ROOT / "data"
DB_PATH = DATA_DIR / "app.db"
DATA_DIR.mkdir(parents=True, exist_ok=True)

# --- External wiki repo -----------------------------------------------------
_repo_env = os.environ.get("WIKI_REPO_PATH")
if not _repo_env:
    raise RuntimeError(
        "WIKI_REPO_PATH is not set. Point it at the wiki repo's absolute path, "
        "e.g. export WIKI_REPO_PATH=/Users/you/codes/my-wiki-repo"
    )
WIKI_REPO_DIR = Path(_repo_env).expanduser().resolve()
if not WIKI_REPO_DIR.is_dir():
    raise RuntimeError(f"WIKI_REPO_PATH does not exist or is not a directory: {WIKI_REPO_DIR}")

# Uploads land under <wiki repo>/raw/<category>/.
RAW_DIR = WIKI_REPO_DIR / "raw"

# Category → allowed extensions (lowercased, including the dot).
CATEGORY_EXTENSIONS: dict[str, set[str]] = {
    "articles":  {".md", ".markdown", ".txt", ".html", ".htm", ".rst", ".org"},
    "clippings": {".md", ".markdown", ".txt", ".html", ".htm"},
    "notes":     {".md", ".markdown", ".txt", ".rst", ".org"},
    "images":    {".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg"},
    "pdfs":      {".pdf"},
}
CATEGORIES = tuple(CATEGORY_EXTENSIONS.keys())
TEXT_CATEGORIES = ("articles", "clippings", "notes")

for cat in CATEGORIES:
    (RAW_DIR / cat).mkdir(parents=True, exist_ok=True)


def category_dir(category: str) -> Path:
    if category not in CATEGORY_EXTENSIONS:
        raise ValueError(f"Unknown category: {category}")
    return RAW_DIR / category


MAX_FILE_SIZE = 30 * 1024 * 1024  # 30MB (images/pdfs can be larger)
