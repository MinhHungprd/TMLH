"""Điều khiển vào/ra boss trong game Thiên Mệnh Lạc Hồng.

Cấu trúc gói:
    VÀO BOSS  : [len=2][group=11][opcode=141][boss_id 2B]  (14 byte)
    THOÁT BOSS: [len=0][group=11][opcode=70]              (12 byte)

Boss ID:
    Trộm chó    = 0x6075 (24693)
    Ngáo ộp     = 0x6076 (24694)
    Đại thợ săn = 0x6077 (24695)
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
import threading
import time

import frida
import psutil

try:
    from perf_metrics import perf_timer
except ModuleNotFoundError:
    # Keep the standalone boss/enter_boss.py CLI usable even when the
    # repository root is not on sys.path. The desktop bot imports the real
    # profiler from the project root.
    from contextlib import contextmanager

    @contextmanager
    def perf_timer(_name):
        yield


GAMEPLAY_PORT = 1002
LOGIN_PORT = 8001

ENTER_GROUP = 11
ENTER_OPCODE = 141          # 0x8d
EXIT_GROUP = 11
EXIT_OPCODE = 70            # 0x46

BOSSES = {
    "trom_cho":     0x6075,
    "ngao_op":      0x6076,
    "dai_tho_san":  0x6077,
}

# Only one attach/load/detach operation at a time. Multiple simultaneous
# Frida sessions create short CPU/context-switch spikes even when average
# utilization remains low.
_FRIDA_LOCK = threading.Lock()


# ---------- Tạo gói ----------
def enter_boss_hex(boss_id: int) -> str:
    return f"{2:08x}{ENTER_GROUP:08x}{ENTER_OPCODE:08x}{boss_id:04x}"


def exit_boss_hex() -> str:
    return f"{0:08x}{EXIT_GROUP:08x}{EXIT_OPCODE:08x}"


def parse_packet(hexstr: str):
    b = bytes.fromhex(hexstr)
    if len(b) < 12:
        raise ValueError(f"Gói phải >= 12 byte, nhận {len(b)}")
    plen = int.from_bytes(b[0:4], "big")
    group = int.from_bytes(b[4:8], "big")
    opcode = int.from_bytes(b[8:12], "big")
    return plen, group, opcode, hexstr[24:]


def _established_remotes(
    process: psutil.Process,
) -> list[dict]:
    remotes = []
    seen = set()

    for conn in process.net_connections(
        kind="tcp"
    ):
        if not conn.raddr:
            continue

        if (
            conn.status
            != psutil.CONN_ESTABLISHED
        ):
            continue

        key = (
            conn.raddr.ip,
            conn.raddr.port,
        )

        if key in seen:
            continue

        seen.add(key)
        remotes.append(
            {
                "ip": conn.raddr.ip,
                "port": conn.raddr.port,
            }
        )

    return remotes


def discover_game_remotes(
    process: psutil.Process,
) -> list[dict]:
    """
    Return plausible gameplay sockets for the exact game PID.

    :1002 remains the preferred known gameplay port, but it is no longer a
    hard requirement. Different servers/routes can expose another remote
    port. The known login socket :8001 is excluded; remaining ESTABLISHED
    sockets are passed to the Frida hook, which identifies the real gameplay
    socket from game-protocol-shaped outgoing traffic.
    """
    remotes = _established_remotes(
        process
    )

    gameplay = [
        remote
        for remote in remotes
        if remote["port"] != LOGIN_PORT
    ]

    gameplay.sort(
        key=lambda remote: (
            0
            if remote["port"]
            == GAMEPLAY_PORT
            else 1,
            remote["port"],
            remote["ip"],
        )
    )

    if gameplay:
        return gameplay

    if remotes:
        candidates = ", ".join(
            (
                f"{remote['ip']}:"
                f"{remote['port']}"
            )
            for remote in remotes
        )
        raise RuntimeError(
            (
                f"PID {process.pid} chưa có gameplay socket khả dụng. "
                f"Socket hiện tại: {candidates}"
            )
        )

    raise RuntimeError(
        (
            f"PID {process.pid} "
            "không có TCP connection ESTABLISHED"
        )
    )


def discover_game_remote(
    process: psutil.Process,
) -> dict:
    """
    Backward-compatible single-remote helper.

    Prefer :1002 when present; otherwise return the best dynamic candidate.
    """
    return discover_game_remotes(
        process
    )[0]


def has_gameplay_socket(pid: int) -> bool:
    """
    Readiness check for the exact PID.

    Historically this required remote port :1002. We now accept any
    ESTABLISHED non-login socket because server/route selection can change
    the remote gameplay port. Startup UI still has to be absent continuously
    before AutomationWorker treats this as IN_GAME.
    """
    try:
        process = psutil.Process(pid)
        remotes = _established_remotes(
            process
        )

        return any(
            remote["port"] != LOGIN_PORT
            for remote in remotes
        )

    except psutil.NoSuchProcess:
        return False

    except psutil.AccessDenied as exc:
        raise RuntimeError(
            (
                f"Không có quyền đọc socket "
                f"của game PID {pid}"
            )
        ) from exc

    except psutil.Error:
        return False


# ---------- Gửi ----------
def send_packet(pid: int, packet: str, wait: float, stop_event=None) -> int:
    wait_started = time.perf_counter()

    with _FRIDA_LOCK:
        try:
            from perf_metrics import record_perf_ms

            record_perf_ms(
                "wait_frida_ms",
                (
                    time.perf_counter()
                    - wait_started
                )
                * 1000.0,
            )
        except ModuleNotFoundError:
            pass

        with perf_timer("frida_ms"):
            return _send_packet_impl(
                pid,
                packet,
                wait,
                stop_event,
            )


def _send_packet_impl(pid: int, packet: str, wait: float, stop_event=None) -> int:
    if stop_event is not None and stop_event.is_set():
        raise RuntimeError("Boss command cancelled")

    process = psutil.Process(pid)

    remotes = discover_game_remotes(
        process
    )

    source = (
        Path(__file__)
        .with_suffix(".js")
        .read_text(encoding="utf-8")
        .replace(
            "__TARGETS__",
            json.dumps(remotes),
            1,
        )
    )

    session = frida.attach(process.pid)

    try:
        script = session.create_script(source)
        script.load()

        deadline = time.monotonic() + wait

        while (
            time.monotonic() < deadline
            and not script.exports_sync.ready()
        ):
            if stop_event is not None:
                if stop_event.wait(0.1):
                    raise RuntimeError("Boss command cancelled")
            else:
                time.sleep(0.1)

        if not script.exports_sync.ready():
            candidates = ", ".join(
                (
                    f"{remote['ip']}:"
                    f"{remote['port']}"
                )
                for remote in remotes
            )
            raise RuntimeError(
                (
                    "Không nhận diện được gameplay traffic "
                    f"trên PID {process.pid}. "
                    f"Candidate sockets: {candidates}"
                )
            )

        if stop_event is not None and stop_event.is_set():
            raise RuntimeError("Boss command cancelled")

        return script.exports_sync.enter(packet)

    finally:
        session.detach()


def enter_boss(pid: int, boss_type: str, wait: float = 12, stop_event=None) -> int:
    _, boss_id = resolve_boss(boss_type)
    return send_packet(pid, enter_boss_hex(boss_id), wait, stop_event)


def exit_boss(pid: int, wait: float = 12, stop_event=None) -> int:
    return send_packet(pid, exit_boss_hex(), wait, stop_event)


# ---------- Tiện ích ----------
def resolve_boss(name_or_id: str) -> tuple[str, int]:
    key = name_or_id.lower().strip()
    if key in BOSSES:
        return key, BOSSES[key]
    try:
        bid = int(name_or_id, 0)
        for name, b in BOSSES.items():
            if b == bid:
                return name, bid
        return f"0x{bid:04x}", bid
    except ValueError:
        raise ValueError(
            f"Không rõ boss: '{name_or_id}'. "
            f"Tên hợp lệ: {list(BOSSES.keys())} hoặc ID hex/dec"
        )


# ---------- CLI ----------
def cmd_list() -> int:
    print("Cấu trúc gói boss:")
    print(f"  VÀO BOSS  : [len=2][group=11][opcode=141][boss_id 2B]")
    print(f"              Ví dụ: {enter_boss_hex(BOSSES['trom_cho'])}")
    print(f"  THOÁT BOSS: [len=0][group=11][opcode=70]")
    print(f"              Ví dụ: {exit_boss_hex()}")
    print()
    print("Boss đã biết:")
    for i, (name, bid) in enumerate(BOSSES.items(), 1):
        print(f"  {i}. {name:12s}  0x{bid:04x} ({bid})  →  {enter_boss_hex(bid)}")
    print()
    print("Cách dùng:")
    print("  python enter_boss.py --enter trom_cho")
    print("  python enter_boss.py --enter 0x6077")
    print("  python enter_boss.py --exit")
    print("  python enter_boss.py --both ngao_op --delay 3")
    print("  python enter_boss.py --cycle --delay 3")
    return 0


def cmd_enter(pid: int, target: str, wait: float) -> int:
    name, bid = resolve_boss(target)
    hexstr = enter_boss_hex(bid)
    print(f"[ENTER] {name} (0x{bid:04x}) hex={hexstr}")
    sent = send_packet(pid, hexstr, wait)
    print(f"  -> đã gửi {sent} byte")
    return 0


def cmd_exit(pid: int, wait: float) -> int:
    hexstr = exit_boss_hex()
    print(f"[EXIT] hex={hexstr}")
    sent = send_packet(pid, hexstr, wait)
    print(f"  -> đã gửi {sent} byte")
    return 0


def cmd_both(pid: int, target: str, wait: float, delay: float) -> int:
    name, bid = resolve_boss(target)
    print(f"[1/2] ENTER {name} (0x{bid:04x})")
    cmd_enter(pid, target, wait)
    print(f"  đợi {delay}s...")
    time.sleep(delay)
    print(f"[2/2] EXIT")
    cmd_exit(pid, wait)
    print("Xong. Kiểm tra game.")
    return 0


def cmd_cycle(pid: int, wait: float, delay: float) -> int:
    for i, (name, bid) in enumerate(BOSSES.items(), 1):
        print(f"\n=== [{i}/{len(BOSSES)}] {name} (0x{bid:04x}) ===")
        cmd_both(pid, name, wait, delay)
    print("\nĐã test xong tất cả boss.")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--list", action="store_true")
    parser.add_argument("--enter", metavar="BOSS")
    parser.add_argument("--exit", action="store_true")
    parser.add_argument("--both", metavar="BOSS")
    parser.add_argument("--cycle", action="store_true")
    parser.add_argument("--send-hex", metavar="HEX")
    parser.add_argument("--delay", type=float, default=3.0)
    parser.add_argument("--wait", type=float, default=12)
    parser.add_argument("--pid", type=int)
    args = parser.parse_args()

    if not 1 <= args.wait <= 60:
        parser.error("--wait phải trong 1..60")
    if not 0 <= args.delay <= 60:
        parser.error("--delay phải trong 0..60")

    try:
        if args.list:
            return cmd_list()

        if args.pid is None and any((args.send_hex, args.cycle, args.both, args.enter, args.exit)):
            parser.error("--pid is required for boss commands")

        if args.send_hex:
            plen, g, o, _ = parse_packet(args.send_hex)
            print(f"Gửi hex tùy ý: len={plen} group={g} opcode={o}")
            sent = send_packet(args.pid, args.send_hex, args.wait)
            print(f"Đã gửi {sent} byte.")
            return 0

        if args.cycle:
            return cmd_cycle(args.pid, args.wait, args.delay)

        if args.both:
            return cmd_both(args.pid, args.both, args.wait, args.delay)

        if args.enter:
            return cmd_enter(args.pid, args.enter, args.wait)

        if args.exit:
            return cmd_exit(args.pid, args.wait)

        print("Dry-run. Dùng --list, --enter, --exit, --both, --cycle, --send-hex.")
        return cmd_list()

    except (ValueError, OSError, RuntimeError, psutil.Error,
            frida.InvalidOperationError, frida.ProcessNotFoundError,
            frida.TransportError, frida.core.RPCException) as exc:
        print(f"Lỗi: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
