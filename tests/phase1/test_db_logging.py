import pytest

from app.core.errors import DatabaseError
from app.db.sqlite import SQLiteRepository


@pytest.fixture
def repo(tmp_path):
    # Using a tmp file rather than :memory: because the repository opens a
    # new aiosqlite connection per call — a private :memory: DB would not
    # share state across execute/fetch calls.
    db_path = tmp_path / "test.db"
    return SQLiteRepository(str(db_path))


@pytest.mark.asyncio
async def test_insert_and_fetch_one(repo):
    await repo.execute(
        "CREATE TABLE items (id INTEGER PRIMARY KEY, name TEXT NOT NULL)"
    )
    await repo.execute("INSERT INTO items (name) VALUES (?)", ("alpha",))
    row = await repo.fetch_one("SELECT id, name FROM items WHERE name = ?", ("alpha",))
    assert row is not None
    assert row["name"] == "alpha"
    assert row["id"] == 1


@pytest.mark.asyncio
async def test_fetch_one_missing_returns_none(repo):
    await repo.execute("CREATE TABLE items (id INTEGER PRIMARY KEY, name TEXT)")
    row = await repo.fetch_one("SELECT * FROM items WHERE id = ?", (999,))
    assert row is None


@pytest.mark.asyncio
async def test_bad_query_raises_database_error(repo):
    with pytest.raises(DatabaseError):
        await repo.execute("SELECT * FROM table_that_does_not_exist")
