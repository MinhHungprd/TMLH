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
    session.create_script.return_value.exports_sync.ready.return_value = True
    session.create_script.return_value.exports_sync.enter.return_value = 14
    process = Mock(pid=42)
    with patch.object(api.psutil, "Process", return_value=process), patch.object(api, "has_game_socket", return_value=True), patch.object(api.frida, "attach", return_value=session) as attach, patch.object(api.psutil, "process_iter", side_effect=AssertionError("global scan")):
        assert api.send_packet(42, api.enter_boss_hex(api.BOSSES["trom_cho"]), 1) == 14
    attach.assert_called_once_with(42)
    session.detach.assert_called_once()


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
