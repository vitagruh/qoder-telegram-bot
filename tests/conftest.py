from __future__ import annotations

from pathlib import Path
from typing import Callable

import pytest


@pytest.fixture()
def make_symlink() -> Callable[..., None]:
    """Создать симлинк или пропустить тест, если прав нет (Windows)."""

    def _make(link: Path, target: Path, target_is_directory: bool = False) -> None:
        try:
            link.symlink_to(target, target_is_directory=target_is_directory)
        except (OSError, NotImplementedError) as exc:
            pytest.skip(f"симлинки недоступны в этом окружении: {exc}")

    return _make
