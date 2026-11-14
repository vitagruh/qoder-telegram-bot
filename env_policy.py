"""Минимальное окружение для дочернего процесса Qoder Agent SDK.

Агент и выполняемые им команды не должны видеть секреты бота и прочие
переменные родительского процесса. Пробрасываем только то, что нужно
для работы `qodercli`, сети и git.
"""

from __future__ import annotations

import os
from collections.abc import Mapping

PASSTHROUGH = (
    "PATH",
    "HOME",
    "USER",
    "LOGNAME",
    "SHELL",
    "LANG",
    "LC_ALL",
    "LC_CTYPE",
    "TZ",
    "TMPDIR",
    "TEMP",
    "TMP",
    "TERM",
    "COLORTERM",
    "XDG_CONFIG_HOME",
    "XDG_CACHE_HOME",
    "XDG_DATA_HOME",
    "SSL_CERT_FILE",
    "SSL_CERT_DIR",
    "CURL_CA_BUNDLE",
    "HTTP_PROXY",
    "HTTPS_PROXY",
    "NO_PROXY",
    "http_proxy",
    "https_proxy",
    "no_proxy",
)

# Секреты, которые нужны самому qodercli, но не должны теряться при
# санитизации. TELEGRAM_BOT_TOKEN сюда сознательно не входит.
SECRETS_FOR_AGENT = ("QODER_PERSONAL_ACCESS_TOKEN",)


def build_child_env(
    config_dir: str | None = None,
    base: Mapping[str, str] | None = None,
) -> dict[str, str]:
    source = os.environ if base is None else base
    env = {name: source[name] for name in PASSTHROUGH if name in source}
    env.setdefault("PATH", os.defpath)

    for name in SECRETS_FOR_AGENT:
        value = source.get(name)
        if value:
            env[name] = value

    if config_dir:
        env["QODER_CONFIG_DIR"] = config_dir
    elif source.get("QODER_CONFIG_DIR"):
        env["QODER_CONFIG_DIR"] = source["QODER_CONFIG_DIR"]

    # Не читаем пользовательский/системный git config: там могут быть
    # fsmonitor, textconv, filter-хуки и alternateRefsCommand, исполняющие
    # произвольные команды при чтении репозитория.
    env["GIT_CONFIG_GLOBAL"] = os.devnull
    env["GIT_CONFIG_SYSTEM"] = os.devnull
    env["GIT_TERMINAL_PROMPT"] = "0"
    env["PYTHONUNBUFFERED"] = "1"
    return env
