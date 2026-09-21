"""SOCKS5 routing support for large TMLH profile batches.

Routing policy:
    profiles 1..30   -> direct
    profiles 31..60  -> proxy 1
    profiles 61..90  -> proxy 2
    ...
If there are more proxy groups than configured proxies, the final proxy is
reused. Therefore with one proxy configured, every profile after the first 30
uses that proxy.

Actual transparent per-process routing is delegated to ProxiFyre on Windows.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
import ctypes
import json
import os
from pathlib import Path
import shutil
import socket
import struct
import time
import subprocess
import tempfile

import win32crypt


DIRECT_PROFILE_LIMIT = 30
PROXY_GROUP_SIZE = 30
PROXY_SETTINGS_FILENAME = ".tmlh_proxy_settings.bin"


@dataclass(frozen=True)
class Socks5Proxy:
    host: str
    port: int
    username: str
    password: str

    @property
    def endpoint(self) -> str:
        return f"{self.host}:{self.port}"

    def as_line(self) -> str:
        return (
            f"{self.host}:{self.port}:"
            f"{self.username}:{self.password}"
        )


@dataclass(frozen=True)
class ProxyTestResult:
    endpoint: str
    ok: bool
    latency_ms: float | None
    detail: str


@dataclass(frozen=True)
class ProxyVerification:
    config_applied: bool
    test_mode: bool
    proxy_results: tuple[ProxyTestResult, ...]


@dataclass(frozen=True)
class ProxySettings:
    proxifyre_path: str = ""
    proxies: tuple[Socks5Proxy, ...] = ()
    # Test mode lets a small local setup verify transparent game routing:
    # profile 1 stays Direct, profile 2 uses proxy 1, profile 3 uses proxy 2,
    # and any remaining profiles reuse the last configured proxy.
    test_mode: bool = False


def _recv_exact(
    sock: socket.socket,
    size: int,
) -> bytes:
    chunks = bytearray()

    while len(chunks) < size:
        chunk = sock.recv(
            size - len(chunks)
        )

        if not chunk:
            raise ConnectionError(
                "SOCKS5 đóng kết nối sớm"
            )

        chunks.extend(chunk)

    return bytes(chunks)


def test_socks5_proxy(
    proxy: Socks5Proxy,
    *,
    timeout: float = 5.0,
    target_host: str = "1.1.1.1",
    target_port: int = 443,
) -> ProxyTestResult:
    """
    Validate the configured SOCKS5 endpoint itself.

    This proves TCP reachability, SOCKS5 negotiation/authentication and a
    CONNECT request through the proxy. It does not by itself prove that an
    already-open game socket has been intercepted by ProxiFyre.
    """
    started = time.perf_counter()

    try:
        username = proxy.username.encode(
            "utf-8"
        )
        password = proxy.password.encode(
            "utf-8"
        )

        if len(username) > 255:
            raise ValueError(
                "username SOCKS5 dài quá 255 byte"
            )

        if len(password) > 255:
            raise ValueError(
                "password SOCKS5 dài quá 255 byte"
            )

        with socket.create_connection(
            (
                proxy.host,
                proxy.port,
            ),
            timeout=timeout,
        ) as sock:
            sock.settimeout(timeout)

            # Offer both no-auth and username/password. Providers that require
            # credentials normally choose method 0x02.
            sock.sendall(
                b"\x05\x02\x00\x02"
            )

            version, method = _recv_exact(
                sock,
                2,
            )

            if version != 5:
                raise ConnectionError(
                    "Phản hồi không phải SOCKS5"
                )

            if method == 0xFF:
                raise PermissionError(
                    "SOCKS5 từ chối phương thức xác thực"
                )

            if method == 0x02:
                auth = (
                    b"\x01"
                    + bytes(
                        [len(username)]
                    )
                    + username
                    + bytes(
                        [len(password)]
                    )
                    + password
                )
                sock.sendall(auth)

                auth_version, status = (
                    _recv_exact(
                        sock,
                        2,
                    )
                )

                if (
                    auth_version != 1
                    or status != 0
                ):
                    raise PermissionError(
                        "Sai username/password SOCKS5"
                    )
            elif method != 0x00:
                raise ConnectionError(
                    f"SOCKS5 chọn auth method không hỗ trợ: {method}"
                )

            try:
                packed_host = socket.inet_aton(
                    target_host
                )
                address = (
                    b"\x01"
                    + packed_host
                )
            except OSError:
                host_bytes = target_host.encode(
                    "idna"
                )

                if len(host_bytes) > 255:
                    raise ValueError(
                        "Target host quá dài"
                    )

                address = (
                    b"\x03"
                    + bytes(
                        [len(host_bytes)]
                    )
                    + host_bytes
                )

            request = (
                b"\x05\x01\x00"
                + address
                + struct.pack(
                    "!H",
                    target_port,
                )
            )
            sock.sendall(request)

            version, reply, _reserved, atyp = (
                _recv_exact(
                    sock,
                    4,
                )
            )

            if version != 5:
                raise ConnectionError(
                    "SOCKS5 CONNECT trả sai version"
                )

            if reply != 0:
                messages = {
                    1: "general failure",
                    2: "connection not allowed",
                    3: "network unreachable",
                    4: "host unreachable",
                    5: "connection refused",
                    6: "TTL expired",
                    7: "command not supported",
                    8: "address type not supported",
                }
                raise ConnectionError(
                    "SOCKS5 CONNECT lỗi "
                    f"{reply}: "
                    f"{messages.get(reply, 'unknown')}"
                )

            if atyp == 0x01:
                _recv_exact(
                    sock,
                    4,
                )
            elif atyp == 0x04:
                _recv_exact(
                    sock,
                    16,
                )
            elif atyp == 0x03:
                length = _recv_exact(
                    sock,
                    1,
                )[0]
                _recv_exact(
                    sock,
                    length,
                )
            else:
                raise ConnectionError(
                    "SOCKS5 trả address type không hợp lệ"
                )

            _recv_exact(
                sock,
                2,
            )

        latency_ms = (
            time.perf_counter()
            - started
        ) * 1000.0

        return ProxyTestResult(
            endpoint=proxy.endpoint,
            ok=True,
            latency_ms=latency_ms,
            detail=(
                "SOCKS5 auth + CONNECT OK"
            ),
        )

    except Exception as exc:
        latency_ms = (
            time.perf_counter()
            - started
        ) * 1000.0

        return ProxyTestResult(
            endpoint=proxy.endpoint,
            ok=False,
            latency_ms=latency_ms,
            detail=str(exc),
        )


def parse_proxy_line(value: str) -> Socks5Proxy:
    raw = (value or "").strip()
    parts = raw.split(":", 3)

    if len(parts) != 4:
        raise ValueError(
            "Proxy phải có dạng ip:port:user:pass"
        )

    host, port_text, username, password = (
        part.strip()
        for part in parts
    )

    if not host:
        raise ValueError("Proxy thiếu IP/host")

    try:
        port = int(port_text)
    except ValueError:
        raise ValueError(
            f"Port proxy không hợp lệ: {port_text!r}"
        ) from None

    if not 1 <= port <= 65535:
        raise ValueError(
            f"Port proxy ngoài phạm vi 1..65535: {port}"
        )

    if not username:
        raise ValueError("Proxy thiếu username")

    if not password:
        raise ValueError("Proxy thiếu password")

    return Socks5Proxy(
        host=host,
        port=port,
        username=username,
        password=password,
    )


def parse_proxy_lines(value: str) -> tuple[Socks5Proxy, ...]:
    proxies = []

    for line_number, line in enumerate(
        (value or "").splitlines(),
        start=1,
    ):
        stripped = line.strip()

        if not stripped:
            continue

        try:
            proxies.append(
                parse_proxy_line(stripped)
            )
        except ValueError as exc:
            raise ValueError(
                f"Proxy dòng {line_number}: {exc}"
            ) from exc

    return tuple(proxies)


def proxy_for_profile_index(
    index: int,
    proxy_count: int,
) -> int | None:
    """Return zero-based proxy index or None for direct routing."""
    if index < DIRECT_PROFILE_LIMIT:
        return None

    if proxy_count <= 0:
        return None

    proxy_index = (
        index - DIRECT_PROFILE_LIMIT
    ) // PROXY_GROUP_SIZE

    return min(
        proxy_index,
        proxy_count - 1,
    )


def proxy_for_test_profile_index(
    index: int,
    proxy_count: int,
) -> int | None:
    """
    Small-batch proxy validation mode.

    profile 1 -> Direct
    profile 2 -> proxy 1
    profile 3 -> proxy 2
    ...
    profiles beyond the configured proxy count reuse the final proxy.
    """
    if index <= 0 or proxy_count <= 0:
        return None

    return min(
        index - 1,
        proxy_count - 1,
    )


def assign_profile_proxies(
    profiles,
    proxies,
    *,
    test_mode: bool = False,
) -> dict[str, int | None]:
    proxy_count = len(proxies)
    resolver = (
        proxy_for_test_profile_index
        if test_mode
        else proxy_for_profile_index
    )

    return {
        profile.profile_id: resolver(
            index,
            proxy_count,
        )
        for index, profile in enumerate(profiles)
    }


def game_executable_path(profile) -> Path:
    return (
        Path(profile.game_path)
        / "Data"
        / "ThienMenhLacHong.exe"
    )


def build_proxifyre_config(
    profiles,
    proxies,
    *,
    test_mode: bool = False,
) -> dict:
    """
    Build a ProxiFyre configuration with one route per proxy.

    Normal mode:
        only profiles beyond the first 30 are included.

    Test mode:
        profile 1 stays direct and profile 2+ are routed immediately so a
        machine with only two game tabs can verify that ProxiFyre routing
        actually works.
    """
    proxies = tuple(proxies)
    assignments = assign_profile_proxies(
        profiles,
        proxies,
        test_mode=test_mode,
    )

    grouped_paths: list[list[str]] = [
        []
        for _ in proxies
    ]

    for profile in profiles:
        proxy_index = assignments[
            profile.profile_id
        ]

        if proxy_index is None:
            continue

        executable = game_executable_path(
            profile
        )

        grouped_paths[proxy_index].append(
            str(executable)
        )

    rules = []

    for proxy, app_names in zip(
        proxies,
        grouped_paths,
    ):
        if not app_names:
            continue

        rules.append(
            {
                "appNames": app_names,
                "socks5ProxyEndpoint": proxy.endpoint,
                "username": proxy.username,
                "password": proxy.password,
                "socks5Transport": "TCP",
                "supportedProtocols": ["TCP"],
                "supportedAddressFamilies": ["IPv4"],
            }
        )

    return {
        "logLevel": "Error",
        "bypassLan": True,
        "proxies": rules,
        "excludes": [],
    }


class ProxySettingsStorage:
    """Persist proxy credentials encrypted with Windows DPAPI."""

    def __init__(
        self,
        path: str | Path,
    ):
        self.path = Path(path)

    def load(self) -> ProxySettings:
        if not self.path.is_file():
            return ProxySettings()

        try:
            raw = win32crypt.CryptUnprotectData(
                self.path.read_bytes(),
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
                "Không đọc được cấu hình proxy đã mã hóa"
            ) from exc

        proxies = tuple(
            Socks5Proxy(**item)
            for item in payload.get(
                "proxies",
                [],
            )
        )

        return ProxySettings(
            proxifyre_path=str(
                payload.get(
                    "proxifyre_path",
                    "",
                )
            ),
            proxies=proxies,
            test_mode=bool(
                payload.get(
                    "test_mode",
                    False,
                )
            ),
        )

    def save(
        self,
        settings: ProxySettings,
    ) -> None:
        payload = json.dumps(
            {
                "version": 1,
                "proxifyre_path": (
                    settings.proxifyre_path
                ),
                "proxies": [
                    asdict(proxy)
                    for proxy in settings.proxies
                ],
                "test_mode": bool(
                    settings.test_mode
                ),
            },
            ensure_ascii=False,
        ).encode("utf-8")

        protected = win32crypt.CryptProtectData(
            payload,
            "TMLH proxy settings",
            None,
            None,
            None,
            0,
        )

        self.path.parent.mkdir(
            parents=True,
            exist_ok=True,
        )

        temporary = self.path.with_suffix(
            self.path.suffix + ".tmp"
        )
        temporary.write_bytes(protected)
        os.replace(
            temporary,
            self.path,
        )


def is_admin() -> bool:
    try:
        return bool(
            ctypes.windll.shell32.IsUserAnAdmin()
        )
    except Exception:
        return False


def validate_proxifyre_executable(
    value: str | Path,
) -> Path:
    path = Path(value).expanduser()

    if path.is_dir():
        path = path / "ProxiFyre.exe"

    if not path.is_file():
        raise FileNotFoundError(
            f"Không tìm thấy ProxiFyre.exe: {path}"
        )

    return path.resolve()


def write_runtime_config(
    bot_root: str | Path,
    profiles,
    settings: ProxySettings,
) -> Path:
    config = build_proxifyre_config(
        profiles,
        settings.proxies,
        test_mode=settings.test_mode,
    )

    runtime_dir = (
        Path(bot_root)
        / "proxy_runtime"
    )
    runtime_dir.mkdir(
        parents=True,
        exist_ok=True,
    )

    # Remove abandoned plaintext runtime configs from a cancelled/failed
    # previous Apply attempt before writing a new one.
    for old in runtime_dir.glob(
        "proxifyre_*.json"
    ):
        try:
            old.unlink()
        except OSError:
            pass

    fd, temp_name = tempfile.mkstemp(
        prefix="proxifyre_",
        suffix=".json",
        dir=runtime_dir,
    )
    os.close(fd)

    path = Path(temp_name)
    path.write_text(
        json.dumps(
            config,
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    return path


def _restart_proxifyre_admin(
    executable: Path,
    source_config: Path,
) -> None:
    destination = (
        executable.parent
        / "app-config.json"
    )

    shutil.copy2(
        source_config,
        destination,
    )

    try:
        subprocess.run(
            [str(executable), "stop"],
            cwd=str(executable.parent),
            check=False,
            capture_output=True,
            text=True,
            timeout=15,
        )

        result = subprocess.run(
            [str(executable), "start"],
            cwd=str(executable.parent),
            check=False,
            capture_output=True,
            text=True,
            timeout=20,
        )

        if result.returncode != 0:
            detail = (
                result.stderr
                or result.stdout
                or "unknown error"
            ).strip()
            raise RuntimeError(
                "Không start được ProxiFyre service: "
                f"{detail}"
            )
    finally:
        try:
            source_config.unlink()
        except OSError:
            pass


def _quote_powershell(value: str) -> str:
    return "'" + value.replace(
        "'",
        "''",
    ) + "'"


def _restart_proxifyre_elevated(
    executable: Path,
    source_config: Path,
) -> None:
    destination = (
        executable.parent
        / "app-config.json"
    )

    exe_q = _quote_powershell(
        str(executable)
    )
    source_q = _quote_powershell(
        str(source_config)
    )
    destination_q = _quote_powershell(
        str(destination)
    )

    command = (
        "$ErrorActionPreference='Stop';"
        f"Copy-Item -LiteralPath {source_q} "
        f"-Destination {destination_q} -Force;"
        f"& {exe_q} stop | Out-Null;"
        "Start-Sleep -Milliseconds 500;"
        f"& {exe_q} start;"
        "$code=$LASTEXITCODE;"
        f"Remove-Item -LiteralPath {source_q} "
        "-Force -ErrorAction SilentlyContinue;"
        "if($code -ne 0){exit $code}"
    )

    parameters = (
        "-NoProfile -ExecutionPolicy Bypass "
        "-Command "
        + subprocess.list2cmdline(
            [command]
        )
    )

    result = ctypes.windll.shell32.ShellExecuteW(
        None,
        "runas",
        "powershell.exe",
        parameters,
        str(executable.parent),
        1,
    )

    if result <= 32:
        raise RuntimeError(
            "Không thể yêu cầu quyền Administrator "
            "để áp dụng ProxiFyre"
        )


def apply_proxy_routing(
    bot_root: str | Path,
    profiles,
    settings: ProxySettings,
) -> Path:
    """
    Write ProxiFyre app-config.json and restart its service.

    Returns the ProxiFyre executable path. The operation happens only when
    the user explicitly clicks Apply; normal boss/screenshot loops do no
    proxy-management work.
    """
    executable = (
        validate_proxifyre_executable(
            settings.proxifyre_path
        )
    )

    source_config = write_runtime_config(
        bot_root,
        profiles,
        settings,
    )

    if is_admin():
        _restart_proxifyre_admin(
            executable,
            source_config,
        )
    else:
        _restart_proxifyre_elevated(
            executable,
            source_config,
        )

    return executable



def verify_proxy_setup(
    profiles,
    settings: ProxySettings,
    *,
    config_wait_seconds: float = 8.0,
    proxy_timeout: float = 5.0,
) -> ProxyVerification:
    """
    Check two separate things:
    1) ProxiFyre app-config.json matches the current profile routing policy.
    2) Every configured SOCKS5 endpoint accepts auth + CONNECT.

    The config wait makes this usable immediately after an elevated Apply,
    whose PowerShell/UAC restart runs asynchronously.
    """
    deadline = (
        time.monotonic()
        + max(
            0.0,
            float(config_wait_seconds),
        )
    )

    config_applied = False

    while True:
        config_applied = (
            routing_config_matches(
                profiles,
                settings,
            )
        )

        if config_applied:
            break

        if time.monotonic() >= deadline:
            break

        time.sleep(0.25)

    results = tuple(
        test_socks5_proxy(
            proxy,
            timeout=proxy_timeout,
        )
        for proxy in settings.proxies
    )

    return ProxyVerification(
        config_applied=config_applied,
        test_mode=bool(
            settings.test_mode
        ),
        proxy_results=results,
    )


def routing_config_matches(
    profiles,
    settings: ProxySettings,
) -> bool:
    """
    True when ProxiFyre's active app-config.json exactly matches the routing
    configuration generated from the current profile list and saved proxies.
    """
    if (
        not settings.proxies
        and not settings.proxifyre_path.strip()
    ):
        return True

    try:
        executable = validate_proxifyre_executable(
            settings.proxifyre_path
        )
    except (ValueError, OSError):
        return False

    config_path = (
        executable.parent
        / "app-config.json"
    )

    if not config_path.is_file():
        return False

    try:
        current = json.loads(
            config_path.read_text(
                encoding="utf-8",
            )
        )
    except (
        OSError,
        json.JSONDecodeError,
    ):
        return False

    expected = build_proxifyre_config(
        profiles,
        settings.proxies,
        test_mode=settings.test_mode,
    )

    return current == expected
