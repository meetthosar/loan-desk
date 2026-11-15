from __future__ import annotations

import hashlib
from collections.abc import Iterator
from contextlib import contextmanager
from functools import lru_cache

from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from loandesk.db.models import Base


@lru_cache
def get_engine(url: str) -> Engine:
    kwargs = {}
    if url.startswith("sqlite"):
        kwargs["connect_args"] = {"check_same_thread": False, "timeout": 30}
    return create_engine(url, pool_pre_ping=True, **kwargs)


def init_db(url: str) -> None:
    Base.metadata.create_all(get_engine(url))


def reset_db(url: str) -> None:
    engine = get_engine(url)
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)


class Database:
    def __init__(self, url: str):
        self.url = url
        self._factory = sessionmaker(get_engine(url), expire_on_commit=False)

    @contextmanager
    def session(self) -> Iterator[Session]:
        s = self._factory()
        try:
            yield s
            s.commit()
        except Exception:
            s.rollback()
            raise
        finally:
            s.close()


def hash_phone(phone: str, salt: str) -> str:
    digits = "".join(ch for ch in phone if ch.isdigit())[-10:]
    return hashlib.sha256(f"{salt}:{digits}".encode()).hexdigest()
