"use strict";

const TARGET_IP = "14.225.213.205";
const TARGET_PORTS = new Set([
    1001,
    1002,
    8001
]);

const ws2 = Process.getModuleByName("ws2_32.dll");

const sendAddress = ws2.getExportByName("send");
const recvAddress = ws2.getExportByName("recv");

const getpeername = new NativeFunction(
    ws2.getExportByName("getpeername"),
    "int",
    ["pointer", "pointer", "pointer"]
);

function peer(socket) {
    const addr = Memory.alloc(128);
    const len = Memory.alloc(4);

    len.writeU32(128);

    if (getpeername(socket, addr, len) !== 0)
        return null;

    // AF_INET
    if (addr.readU16() !== 2)
        return null;

    const port =
        (addr.add(2).readU8() << 8) |
        addr.add(3).readU8();

    const ip = [
        addr.add(4).readU8(),
        addr.add(5).readU8(),
        addr.add(6).readU8(),
        addr.add(7).readU8()
    ].join(".");

    return {
        ip,
        port
    };
}

function isCandidate(remote) {
    return (
        remote !== null &&
        remote.ip === TARGET_IP &&
        TARGET_PORTS.has(remote.port)
    );
}

function hex(ptr, length) {
    const size = Math.min(length, 48);

    if (size <= 0)
        return "";

    try {
        const data = new Uint8Array(
            ptr.readByteArray(size)
        );

        return Array.from(data)
            .map(x => x.toString(16).padStart(2, "0"))
            .join("");
    } catch (_) {
        return "<read-error>";
    }
}


// ---------------- SEND ----------------

Interceptor.attach(sendAddress, {
    onEnter(args) {
        const socket = args[0];
        const buffer = args[1];
        const length = args[2].toInt32();

        const remote = peer(socket);

        if (!isCandidate(remote))
            return;

        console.log(
            `[SEND] ${remote.ip}:${remote.port} ` +
            `len=${length} ` +
            `hex=${hex(buffer, length)}`
        );
    }
});


// ---------------- RECV ----------------

Interceptor.attach(recvAddress, {
    onEnter(args) {
        this.socket = args[0];
        this.buffer = args[1];
        this.remote = peer(args[0]);
    },

    onLeave(retval) {
        const length = retval.toInt32();

        if (
            length <= 0 ||
            !isCandidate(this.remote)
        ) {
            return;
        }

        console.log(
            `[RECV] ${this.remote.ip}:${this.remote.port} ` +
            `len=${length} ` +
            `hex=${hex(this.buffer, length)}`
        );
    }
});

console.log(
    "Tracing game sockets: 1001 / 1002 / 8001"
);