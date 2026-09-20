"""Per-profile login storage for Thiên Mệnh Lạc Hồng."""

from __future__ import annotations

import base64
import json
from pathlib import Path
import winreg

import win32crypt


REGISTRY_PATH = r"Software\Dreamsoft\ThienMenhLacHong"
AUTH_FILENAME = ".tmlh_profile_auth.bin"

# Các key login thực tế đã xác định từ Registry.
AUTH_PREFIXES = (
    "UsernameThienMenh_",
    "PasswordThienMenh_",
    "username_pass_",
    "token_login_",
    "AutoLogin_",
    "ID_SERVER_INT_",
)


def _is_auth_value(name: str) -> bool:
    return name.startswith(AUTH_PREFIXES)


def _auth_path(game_path: str | Path) -> Path:
    return Path(game_path) / AUTH_FILENAME


def _encode_value(value):
    if isinstance(value, (bytes, bytearray)):
        return {
            "kind": "bytes",
            "value": base64.b64encode(bytes(value)).decode("ascii"),
        }

    return {
        "kind": "json",
        "value": value,
    }


def _decode_value(data):
    if data["kind"] == "bytes":
        return base64.b64decode(data["value"])

    return data["value"]


def _read_current_auth_values() -> list[dict]:
    try:
        key = winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            REGISTRY_PATH,
            0,
            winreg.KEY_READ,
        )
    except FileNotFoundError as exc:
        raise RuntimeError(
            "Không tìm thấy Registry đăng nhập của game"
        ) from exc

    values = []

    try:
        index = 0

        while True:
            try:
                name, value, value_type = winreg.EnumValue(
                    key,
                    index,
                )
            except OSError:
                break

            index += 1

            if not _is_auth_value(name):
                continue

            values.append(
                {
                    "name": name,
                    "type": value_type,
                    "data": _encode_value(value),
                }
            )
    finally:
        winreg.CloseKey(key)

    has_username = any(
        item["name"].startswith("UsernameThienMenh_")
        for item in values
    )

    if not has_username:
        raise RuntimeError(
            "Chưa thấy UsernameThienMenh trong Registry. "
            "Hãy đăng nhập account trước rồi bấm 'Đã đăng nhập'."
        )

    return values


def save_profile_auth(game_path: str | Path) -> Path:
    values = _read_current_auth_values()

    payload = json.dumps(
        {
            "version": 1,
            "values": values,
        },
        ensure_ascii=False,
    ).encode("utf-8")

    protected = win32crypt.CryptProtectData(
        payload,
        "TMLH profile auth",
        None,
        None,
        None,
        0,
    )

    path = _auth_path(game_path)
    path.write_bytes(protected)

    return path


def has_profile_auth(game_path: str | Path) -> bool:
    return _auth_path(game_path).is_file()


def restore_profile_auth(game_path: str | Path) -> None:
    """
    Restore auth của đúng profile vào Registry chung
    ngay trước khi launch game.
    """
    path = _auth_path(game_path)

    if not path.is_file():
        raise RuntimeError(
            "Profile chưa có dữ liệu đăng nhập riêng. "
            "Hãy mở profile bằng Continue Login, đăng nhập đúng account "
            "rồi bấm 'Đã đăng nhập'."
        )

    try:
        raw = win32crypt.CryptUnprotectData(
            path.read_bytes(),
            None,
            None,
            None,
            0,
        )[1]
    except Exception as exc:
        raise RuntimeError(
            f"Không giải mã được auth của profile: {path}"
        ) from exc

    payload = json.loads(raw.decode("utf-8"))

    values = payload.get("values", [])

    if not values:
        raise RuntimeError(
            f"Auth profile rỗng: {path}"
        )

    key = winreg.CreateKeyEx(
        winreg.HKEY_CURRENT_USER,
        REGISTRY_PATH,
        0,
        winreg.KEY_READ | winreg.KEY_WRITE,
    )

    try:
        # Đọc toàn bộ tên hiện tại trước.
        existing_names = []
        index = 0

        while True:
            try:
                name, _value, _type = winreg.EnumValue(
                    key,
                    index,
                )
            except OSError:
                break

            existing_names.append(name)
            index += 1

        # Xóa auth của account/profile trước đó.
        for name in existing_names:
            if not _is_auth_value(name):
                continue

            try:
                winreg.DeleteValue(key, name)
            except FileNotFoundError:
                pass

        # Restore auth của profile chuẩn bị launch.
        for item in values:
            winreg.SetValueEx(
                key,
                item["name"],
                0,
                int(item["type"]),
                _decode_value(item["data"]),
            )

    finally:
        winreg.CloseKey(key)