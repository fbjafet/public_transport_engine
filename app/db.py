from contextlib import contextmanager
from typing import Iterator

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.config import Config


class Base(DeclarativeBase):
    pass


write_engine = create_engine(Config.DATABASE_URL, pool_pre_ping=True, future=True)
read_engine = create_engine(Config.READ_REPLICA_URL, pool_pre_ping=True, future=True)

WriteSessionLocal = sessionmaker(
    bind=write_engine, autoflush=False, expire_on_commit=False
)
ReadSessionLocal = sessionmaker(
    bind=read_engine, autoflush=False, expire_on_commit=False
)


@contextmanager
def get_write_session() -> Iterator[Session]:
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
def get_read_session() -> Iterator[Session]:
    session = ReadSessionLocal()
    try:
        yield session
    finally:
        session.close()


def init_db() -> None:
    import models.transit  # noqa: F401

    Base.metadata.create_all(bind=write_engine)