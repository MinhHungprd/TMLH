import threading
from unittest.mock import Mock, patch

import boss.enter_boss as api


def test_enter_boss_attaches_to_supplied_pid():
    with patch.object(api, "send_packet", return_value=14) as send:
        assert api.enter_boss(1234, "trom_cho") == 14
    send.assert_called_once()
    assert send.call_args.args[0] == 1234


def test_exit_boss_attaches_to_supplied_pid():
    with patch.object(api, "send_packet", return_value=12) as send:
        assert api.exit_boss(4321) == 12
    assert send.call_args.args[0] == 4321


def test_send_packet_attaches_only_to_exact_pid():
    session = Mock()

    script = session.create_script.return_value
    script.exports_sync.ready.return_value = True
    script.exports_sync.enter.return_value = 14

    process = Mock(pid=42)

    remote = {
        "ip": "14.225.213.205",
        "port": 8001,
    }

    with (
        patch.object(
            api.psutil,
            "Process",
            return_value=process,
        ),
        patch.object(
            api,
            "discover_game_remotes",
            return_value=[remote],
        ),
        patch.object(
            api.frida,
            "attach",
            return_value=session,
        ) as attach,
        patch.object(
            api.psutil,
            "process_iter",
            side_effect=AssertionError("global scan"),
        ),
    ):
        result = api.send_packet(
            42,
            api.enter_boss_hex(api.BOSSES["trom_cho"]),
            1,
        )

    assert result == 14

    attach.assert_called_once_with(42)
    session.detach.assert_called_once()


def test_send_packet_allows_different_pids_to_progress_in_parallel():
    both_entered = threading.Event()
    release = threading.Event()
    entered = []
    entered_lock = threading.Lock()
    results = []

    def fake_send_impl(pid, packet, wait, stop_event):
        with entered_lock:
            entered.append(pid)
            if len(entered) == 2:
                both_entered.set()

        release.wait(1.0)
        return pid

    with patch.object(
        api,
        "_send_packet_impl",
        side_effect=fake_send_impl,
    ):
        first = threading.Thread(
            target=lambda: results.append(
                api.send_packet(101, "aa", 1)
            )
        )
        second = threading.Thread(
            target=lambda: results.append(
                api.send_packet(202, "bb", 1)
            )
        )

        first.start()
        second.start()

        assert both_entered.wait(1.0)
        release.set()

        first.join(1.0)
        second.join(1.0)

    assert not first.is_alive()
    assert not second.is_alive()
    assert sorted(results) == [101, 202]


def test_cli_requires_pid_before_any_boss_command():
    for command in (("--enter", "trom_cho"), ("--exit",), ("--both", "trom_cho"), ("--cycle",)):
        with patch("sys.argv", ["enter_boss.py", *command]), patch.object(api, "send_packet") as send:
            try:
                api.main()
            except SystemExit as exc:
                assert exc.code == 2
            else:
                raise AssertionError(f"PID was not required for {command}")
            send.assert_not_called()
def make_connection(ip, port, status=None):
    connection = Mock()

    connection.raddr.ip = ip
    connection.raddr.port = port
    connection.status = status or api.psutil.CONN_ESTABLISHED

    return connection


def test_discover_game_remote_port_1001():
    process = Mock(pid=101)

    process.net_connections.return_value = [
        make_connection(
            "14.225.213.205",
            1001,
        ),
    ]

    assert api.discover_game_remote(process) == {
        "ip": "14.225.213.205",
        "port": 1001,
    }


def test_discover_game_remote_port_8001():
    process = Mock(pid=102)

    process.net_connections.return_value = [
        make_connection(
            "14.225.213.205",
            8001,
        ),
    ]

    assert api.discover_game_remote(process) == {
        "ip": "14.225.213.205",
        "port": 8001,
    }


def test_discover_game_remote_port_1002():
    process = Mock(pid=103)

    process.net_connections.return_value = [
        make_connection(
            "14.225.213.205",
            1002,
        ),
    ]

    assert api.discover_game_remote(process) == {
        "ip": "14.225.213.205",
        "port": 1002,
    }


def test_discover_game_remote_prefers_non_login_socket():
    process = Mock(pid=104)

    process.net_connections.return_value = [
        make_connection(
            "171.244.128.12",
            443,
        ),
        make_connection(
            "14.225.213.205",
            8001,
        ),
    ]

    # Dynamic routing intentionally does not hard-code a server IP. When a
    # dedicated non-login socket exists, it is preferred over :8001.
    assert api.discover_game_remote(process) == {
        "ip": "171.244.128.12",
        "port": 443,
    }


def test_discover_game_remote_missing():
    process = Mock(pid=105)

    process.net_connections.return_value = []

    try:
        api.discover_game_remote(process)
    except RuntimeError as exc:
        assert "105" in str(exc)
        assert "ESTABLISHED" in str(exc)
    else:
        raise AssertionError(
            "Expected RuntimeError"
        )


def test_discover_game_remotes_drop_login_when_gameplay_candidate_exists():
    process = Mock(pid=106)

    process.net_connections.return_value = [
        make_connection(
            "14.225.213.205",
            1001,
        ),
        make_connection(
            "14.225.213.205",
            8001,
        ),
    ]

    assert api.discover_game_remotes(
        process
    ) == [
        {
            "ip": "14.225.213.205",
            "port": 1001,
        },
    ]


def test_two_profiles_can_resolve_different_ports():
    hung = Mock(pid=26420)
    narly = Mock(pid=30000)

    hung.net_connections.return_value = [
        make_connection(
            "14.225.213.205",
            1001,
        ),
    ]

    narly.net_connections.return_value = [
        make_connection(
            "171.244.128.12",
            443,
        ),
        make_connection(
            "14.225.213.205",
            8001,
        ),
    ]

    assert api.discover_game_remote(hung)["port"] == 1001
    assert api.discover_game_remote(narly)["port"] == 443