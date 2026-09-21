from types import SimpleNamespace
from unittest.mock import patch

import psutil

from boss.enter_boss import (
    discover_game_remote,
    discover_game_remotes,
    has_gameplay_socket,
)


def _conn(
    ip,
    port,
    status=psutil.CONN_ESTABLISHED,
):
    return SimpleNamespace(
        raddr=SimpleNamespace(
            ip=ip,
            port=port,
        ),
        status=status,
    )


class FakeProcess:
    def __init__(
        self,
        pid,
        connections,
    ):
        self.pid = pid
        self._connections = connections

    def net_connections(
        self,
        kind="tcp",
    ):
        assert kind == "tcp"
        return list(
            self._connections
        )


def test_discovery_prefers_known_1002_but_keeps_dynamic_candidates():
    process = FakeProcess(
        42,
        [
            _conn(
                "10.0.0.8",
                8001,
            ),
            _conn(
                "10.0.0.9",
                3456,
            ),
            _conn(
                "10.0.0.10",
                1002,
            ),
        ],
    )

    remotes = discover_game_remotes(
        process
    )

    assert remotes == [
        {
            "ip": "10.0.0.10",
            "port": 1002,
        },
        {
            "ip": "10.0.0.9",
            "port": 3456,
        },
    ]

    assert discover_game_remote(
        process
    ) == remotes[0]


def test_discovery_accepts_non_1002_gameplay_port():
    process = FakeProcess(
        42,
        [
            _conn(
                "10.0.0.8",
                8001,
            ),
            _conn(
                "10.0.0.9",
                3456,
            ),
        ],
    )

    assert discover_game_remotes(
        process
    ) == [
        {
            "ip": "10.0.0.9",
            "port": 3456,
        }
    ]


def test_has_gameplay_socket_accepts_dynamic_non_login_port():
    process = FakeProcess(
        42,
        [
            _conn(
                "10.0.0.8",
                8001,
            ),
            _conn(
                "10.0.0.9",
                3456,
            ),
        ],
    )

    with patch(
        "boss.enter_boss.psutil.Process",
        return_value=process,
    ):
        assert has_gameplay_socket(
            42
        ) is True


def test_has_gameplay_socket_accepts_established_same_port_fallback():
    process = FakeProcess(
        42,
        [
            _conn(
                "10.0.0.8",
                8001,
            ),
        ],
    )

    with patch(
        "boss.enter_boss.psutil.Process",
        return_value=process,
    ):
        assert has_gameplay_socket(
            42
        ) is True



def test_discovery_falls_back_to_8001_when_it_is_the_only_connection():
    process = FakeProcess(
        42,
        [
            _conn(
                "10.0.0.8",
                8001,
            ),
        ],
    )

    assert discover_game_remotes(
        process
    ) == [
        {
            "ip": "10.0.0.8",
            "port": 8001,
        }
    ]
