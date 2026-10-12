"""Unit/regression suites must never contact real AI or SQL providers."""
from unittest.mock import Mock

import pytest

from app import db
from app.services import ai_service


@pytest.fixture(autouse=True)
def offline_providers(monkeypatch):
    original = ai_service._complete_cerebras

    async def offline_completion(*args, **kwargs):
        client = ai_service._cerebras_client
        create = getattr(getattr(getattr(client, "chat", None), "completions", None), "create", None)
        if create is None or (not isinstance(create, Mock) and getattr(create, "__module__", "").startswith("cerebras.")):
            raise RuntimeError("Real AI provider calls are disabled in unit tests")
        return await original(*args, **kwargs)

    def offline_sql():
        raise db.DatabaseUnavailableError("Real SQL calls are disabled in unit tests")

    monkeypatch.setattr(ai_service, "_complete_cerebras", offline_completion)
    monkeypatch.setattr(db, "create_db_connection", offline_sql)
