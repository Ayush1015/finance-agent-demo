import os
import tempfile

import pytest
from fastapi.testclient import TestClient

from app.db import make_engine
from app.main import create_app
from app.models import Base


@pytest.fixture()
def db_url():
    url = os.environ.get("TEST_DATABASE_URL")
    if url:
        yield url
    else:
        fd, path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        yield f"sqlite+pysqlite:///{path}"
        os.unlink(path)


@pytest.fixture()
def reset_schema(db_url):
    """Every test starts from the same empty schema."""
    engine = make_engine(db_url)
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    engine.dispose()


@pytest.fixture()
def client(db_url, reset_schema):
    app = create_app(db_url)
    with TestClient(app) as c:  # fires startup -> seeds synthetic invoices
        yield c
