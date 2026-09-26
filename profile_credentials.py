"""Encrypted per-profile credentials used only by automatic login."""

from __future__ import annotations

from dataclasses import asdict, dataclass
import json
import os
from pathlib import Path

import win32crypt


LOGIN_CREDENTIALS_FILENAME = ".tmlh_login_credentials.bin"

SERVER_LABELS = {
    "van_lang": "Văn Lang",
    "au_lac": "Âu Lạc",
    "server_3": "Vạn Xuân",
}

SERVER_NUMBERS = {
    "1": "van_lang",
    "2": "au_lac",
    "3": "server_3",
}

SERVER_KEYS = {
    label: key
    for key, label in SERVER_LABELS.items()
}


@dataclass(frozen=True)
class LoginCredentials:
    username: str
    password: str
    server: str = "van_lang"

    def validate(self) -> "LoginCredentials":
        username = self.username.strip()

        if not username:
            raise ValueError(
                "Tài khoản không được để trống"
            )

        if not self.password:
            raise ValueError(
                "Mật khẩu không được để trống"
            )

        if self.server not in SERVER_LABELS:
            raise ValueError(
                f"Server không hợp lệ: {self.server!r}"
            )

        return LoginCredentials(
            username=username,
            password=self.password,
            server=self.server,
        )


def _path(
    game_path: str | Path,
) -> Path:
    return (
        Path(game_path)
        / LOGIN_CREDENTIALS_FILENAME
    )


def save_login_credentials(
    game_path: str | Path,
    credentials: LoginCredentials,
) -> Path:
    credentials = credentials.validate()

    payload = json.dumps(
        {
            "version": 1,
            **asdict(credentials),
        },
        ensure_ascii=False,
    ).encode("utf-8")

    protected = win32crypt.CryptProtectData(
        payload,
        "TMLH auto login credentials",
        None,
        None,
        None,
        0,
    )

    path = _path(game_path)
    path.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    temporary = path.with_suffix(
        path.suffix + ".tmp"
    )
    temporary.write_bytes(
        protected
    )
    os.replace(
        temporary,
        path,
    )

    return path


def load_login_credentials(
    game_path: str | Path,
) -> LoginCredentials | None:
    path = _path(game_path)

    if not path.is_file():
        return None

    try:
        raw = win32crypt.CryptUnprotectData(
            path.read_bytes(),
            None,
            None,
            None,
            0,
        )[1]

        payload = json.loads(
            raw.decode("utf-8")
        )
    except Exception as exc:
        raise RuntimeError(
            "Không đọc được tài khoản/mật khẩu đã mã hóa của profile"
        ) from exc

    credentials = LoginCredentials(
        username=str(
            payload.get(
                "username",
                "",
            )
        ),
        password=str(
            payload.get(
                "password",
                "",
            )
        ),
        server=str(
            payload.get(
                "server",
                "van_lang",
            )
        ),
    )

    return credentials.validate()
