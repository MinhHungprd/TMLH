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
import time

import frida
import psutil


ROOT = Path(__file__).resolve().parent
GAME_EXE = ROOT / "Game/ThienMenhLacHong_Launcher/Data/ThienMenhLacHong.exe"
REMOTE = {"ip": "14.225.213.205", "port": 1002}

ENTER_GROUP = 11
ENTER_OPCODE = 141          # 0x8d
EXIT_GROUP = 11
EXIT_OPCODE = 70            # 0x46

BOSSES = {
    "trom_cho":     0x6075,
    "ngao_op":      0x6076,
    "dai_tho_san":  0x6077,
}


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


# ---------- Process ----------
def find_game() -> psutil.Process:
    matches = [p for p in psutil.process_iter(["name", "exe"])
               if (p.info["name"] or "").lower() == GAME_EXE.name.lower()
               and p.info["exe"]
               and Path(p.info["exe"]).resolve() == GAME_EXE.resolve()]
    if len(matches) != 1:
        raise RuntimeError(f"Cần đúng 1 process game, tìm thấy {len(matches)}")
    return matches[0]


def has_game_socket(process: psutil.Process) -> bool:
    return any(c.raddr and c.status == psutil.CONN_ESTABLISHED
               and c.raddr.ip == REMOTE["ip"]
               and c.raddr.port == REMOTE["port"]
               for c in process.net_connections(kind="tcp"))


# ---------- Gửi ----------
def send_packet(packet: str, wait: float) -> int:
    process = find_game()
    if not has_game_socket(process):
        raise RuntimeError("Game chưa kết nối tới server port 1002")
    source = Path(__file__).with_suffix(".js").read_text(encoding="utf-8").replace(
        "__TARGET__", json.dumps(REMOTE), 1
    )
    session = frida.attach(process.pid)
    try:
        script = session.create_script(source)
        script.load()
        deadline = time.monotonic() + wait
        while time.monotonic() < deadline and not script.exports_sync.ready():
            time.sleep(0.1)
        if not script.exports_sync.ready():
            raise RuntimeError("Không thấy traffic game port 1002")
        return script.exports_sync.enter(packet)
    finally:
        session.detach()


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


def cmd_enter(target: str, wait: float) -> int:
    name, bid = resolve_boss(target)
    hexstr = enter_boss_hex(bid)
    print(f"[ENTER] {name} (0x{bid:04x}) hex={hexstr}")
    sent = send_packet(hexstr, wait)
    print(f"  -> đã gửi {sent} byte")
    return 0


def cmd_exit(wait: float) -> int:
    hexstr = exit_boss_hex()
    print(f"[EXIT] hex={hexstr}")
    sent = send_packet(hexstr, wait)
    print(f"  -> đã gửi {sent} byte")
    return 0


def cmd_both(target: str, wait: float, delay: float) -> int:
    name, bid = resolve_boss(target)
    print(f"[1/2] ENTER {name} (0x{bid:04x})")
    cmd_enter(target, wait)
    print(f"  đợi {delay}s...")
    time.sleep(delay)
    print(f"[2/2] EXIT")
    cmd_exit(wait)
    print("Xong. Kiểm tra game.")
    return 0


def cmd_cycle(wait: float, delay: float) -> int:
    for i, (name, bid) in enumerate(BOSSES.items(), 1):
        print(f"\n=== [{i}/{len(BOSSES)}] {name} (0x{bid:04x}) ===")
        cmd_both(name, wait, delay)
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
    args = parser.parse_args()

    if not 1 <= args.wait <= 60:
        parser.error("--wait phải trong 1..60")
    if not 0 <= args.delay <= 60:
        parser.error("--delay phải trong 0..60")

    try:
        if args.list:
            return cmd_list()

        if args.send_hex:
            plen, g, o, _ = parse_packet(args.send_hex)
            print(f"Gửi hex tùy ý: len={plen} group={g} opcode={o}")
            sent = send_packet(args.send_hex, args.wait)
            print(f"Đã gửi {sent} byte.")
            return 0

        if args.cycle:
            return cmd_cycle(args.wait, args.delay)

        if args.both:
            return cmd_both(args.both, args.wait, args.delay)

        if args.enter:
            return cmd_enter(args.enter, args.wait)

        if args.exit:
            return cmd_exit(args.wait)

        print("Dry-run. Dùng --list, --enter, --exit, --both, --cycle, --send-hex.")
        return cmd_list()

    except (ValueError, OSError, RuntimeError, psutil.Error,
            frida.InvalidOperationError, frida.ProcessNotFoundError,
            frida.TransportError, frida.core.RPCException) as exc:
        print(f"Lỗi: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())