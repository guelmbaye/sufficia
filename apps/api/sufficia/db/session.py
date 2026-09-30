"""Database engine / session factory. SQLite by default, PostgreSQL through DATABASE_URL."""

from __future__ import annotations

from contextlib import contextmanager
from typing import Iterator

from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from .models import Base


def make_engine(url: str) -> Engine:
    if url.startswith("sqlite"):
        kwargs: dict = {"connect_args": {"check_same_thread": False, "timeout": 30}}
        if url in ("sqlite://", "sqlite:///:memory:"):
            kwargs["poolclass"] = StaticPool  # one shared in-memory database (tests)
        engine = create_engine(url, future=True, **kwargs)

        @event.listens_for(engine, "connect")
        def _pragmas(dbapi_conn, _record):  # pragma: no cover - driver specific
            cur = dbapi_conn.cursor()
            cur.execute("PRAGMA foreign_keys=ON")
            if url not in ("sqlite://", "sqlite:///:memory:"):
                cur.execute("PRAGMA journal_mode=WAL")
            cur.close()

        return engine
    if url.startswith("postgres://"):
        url = "postgresql+psycopg://" + url[len("postgres://") :]
    elif url.startswith("postgresql://"):
        url = "postgresql+psycopg://" + url[len("postgresql://") :]
    return create_engine(url, future=True, pool_pre_ping=True)


class Database:
    def __init__(self, url: str, *, create: bool = True):
        self.url = url
        self.engine = make_engine(url)
        self._factory = sessionmaker(bind=self.engine, expire_on_commit=False, future=True)
        if create:
            Base.metadata.create_all(self.engine)

    @contextmanager
    def session(self) -> Iterator[Session]:
        s = self._factory()
        try:
            yield s
            s.commit()
        except BaseException:
            s.rollback()
            raise
        finally:
            s.close()

    def dispose(self) -> None:
        self.engine.dispose()
