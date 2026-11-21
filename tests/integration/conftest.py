from __future__ import annotations

import os

import psycopg
import pytest
from psycopg.conninfo import conninfo_to_dict


@pytest.fixture
def database():
    url = os.environ.get("TEST_DATABASE_URL")
    if not url:
        pytest.skip("TEST_DATABASE_URL is required for PostgreSQL integration tests")
    if not conninfo_to_dict(url)["dbname"].startswith("horizon_test_"):
        pytest.fail("integration tests require an isolated horizon_test_* database")
    with psycopg.connect(url) as db:
        yield db
