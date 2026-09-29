from collections.abc import Iterator
from typing import NoReturn

import pytest


@pytest.fixture(autouse=True)
def block_external_dependencies(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    def raise_for_network_access(*_: object, **__: object) -> NoReturn:
        raise RuntimeError("Network access is prohibited in tests.")

    monkeypatch.setattr("socket.create_connection", raise_for_network_access)
    yield
