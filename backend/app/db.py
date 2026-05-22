import aiosqlite
from .config import DB_PATH

SCHEMA = """
CREATE TABLE IF NOT EXISTS wikis (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    category     TEXT NOT NULL,
    filename     TEXT NOT NULL,
    original     TEXT NOT NULL,
    size_bytes   INTEGER NOT NULL,
    uploaded_at  TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE (category, filename)
);

CREATE TABLE IF NOT EXISTS sessions (
    id          TEXT PRIMARY KEY,
    user_id     TEXT NOT NULL DEFAULT 'default',
    title       TEXT,
    created_at  TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS messages (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id  TEXT NOT NULL,
    role        TEXT NOT NULL,  -- user | assistant
    content     TEXT NOT NULL,
    created_at  TEXT NOT NULL DEFAULT (datetime('now')),
    FOREIGN KEY (session_id) REFERENCES sessions(id) ON DELETE CASCADE
);
"""


async def init_db() -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.executescript(SCHEMA)
        cur = await db.execute("PRAGMA table_info(sessions)")
        columns = {row[1] for row in await cur.fetchall()}
        if "user_id" not in columns:
            await db.execute(
                "ALTER TABLE sessions ADD COLUMN user_id TEXT NOT NULL DEFAULT 'default'"
            )
        await db.execute(
            "CREATE INDEX IF NOT EXISTS idx_sessions_user_created "
            "ON sessions (user_id, created_at DESC)"
        )
        await db.commit()


async def get_db():
    db = await aiosqlite.connect(DB_PATH)
    db.row_factory = aiosqlite.Row
    try:
        yield db
    finally:
        await db.close()
