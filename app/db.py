from contextlib import contextmanager
from typing import Iterator

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.config import Config


class Base(DeclarativeBase):
    pass


if not Config.DATABASE_URL:
    raise RuntimeError(
        "DATABASE_URL is not set. Set it in the environment (see .env.example)."
    )

write_engine = create_engine(Config.DATABASE_URL, pool_pre_ping=True)
# Reuse the same engine (and pool) when there is no separate replica.
if Config.READ_REPLICA_URL == Config.DATABASE_URL:
    read_engine = write_engine
else:
    read_engine = create_engine(Config.READ_REPLICA_URL, pool_pre_ping=True)

WriteSessionLocal = sessionmaker(
    bind=write_engine, autoflush=False, expire_on_commit=False
)
ReadSessionLocal = sessionmaker(
    bind=read_engine, autoflush=False, expire_on_commit=False
)


@contextmanager
def get_write_session() -> Iterator[Session]:
    """Primary database, commits on success, rolls back on error."""
    session = WriteSessionLocal()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


@contextmanager
def get_primary_session() -> Iterator[Session]:
    """Primary database, read-only use (never commits).

    Use for anything that must see the latest committed data: authentication
    lookups, pages shown right after a write, and the read phase of services.
    """
    session = WriteSessionLocal()
    try:
        yield session
    finally:
        session.close()


@contextmanager
def get_read_session() -> Iterator[Session]:
    """Read replica (may lag the primary). Prefer app.consistency.read_session()."""
    session = ReadSessionLocal()
    try:
        yield session
    finally:
        session.close()


def init_db() -> None:
    """Create tables directly from the models. Tests and local dev only;
    production schema changes go through Alembic migrations."""
    from models import register_all

    register_all()
    Base.metadata.create_all(bind=write_engine)
