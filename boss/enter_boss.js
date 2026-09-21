"use strict";

const TARGETS = __TARGETS__;
const LOGIN_PORT = 8001;

const winsock = Process.getModuleByName("ws2_32.dll");
const sendAddress = winsock.getExportByName("send");
const sendSocket = new NativeFunction(
    sendAddress,
    "int",
    ["pointer", "pointer", "int", "int"]
);
const getpeername = new NativeFunction(
    winsock.getExportByName("getpeername"),
    "int",
    ["pointer", "pointer", "pointer"]
);
const getLastError = new NativeFunction(
    winsock.getExportByName("WSAGetLastError"),
    "int",
    []
);

let liveSocket = null;
let liveRemote = null;

// Cho phép group 11, opcode 141 (enter) và 70 (exit)
const ALLOWED_OPCODES = new Set([141, 70]);

function peer(socket) {
    const address = Memory.alloc(128);
    const size = Memory.alloc(4);
    size.writeU32(128);

    if (
        getpeername(socket, address, size) !== 0
        || address.readU16() !== 2
    ) {
        return null;
    }

    return {
        ip: [4, 5, 6, 7]
            .map(i => address.add(i).readU8())
            .join("."),
        port:
            (address.add(2).readU8() << 8)
            | address.add(3).readU8()
    };
}

function isCandidate(remote) {
    if (remote === null || remote.port === LOGIN_PORT) {
        return false;
    }

    return TARGETS.some(
        target =>
            remote.ip === target.ip
            && remote.port === target.port
    );
}

function readU32BE(buffer, offset) {
    return (
        (
            buffer.add(offset).readU8() << 24
        )
        | (
            buffer.add(offset + 1).readU8() << 16
        )
        | (
            buffer.add(offset + 2).readU8() << 8
        )
        | buffer.add(offset + 3).readU8()
    ) >>> 0;
}

function looksLikeGamePacket(buffer, length) {
    if (length < 12) {
        return false;
    }

    try {
        const payloadLength = readU32BE(buffer, 0);

        // TMLH packets observed by the bot use:
        // [payload_len 4B][group 4B][opcode 4B][payload...]
        return length === 12 + payloadLength;
    } catch (_error) {
        return false;
    }
}

function isLiveSocket(socket) {
    if (liveSocket === null) {
        return false;
    }

    const remote = peer(socket);

    return (
        socket.equals(liveSocket)
        && isCandidate(remote)
    );
}

Interceptor.attach(sendAddress, {
    onEnter(args) {
        const socket = args[0];
        const buffer = args[1];
        const length = args[2].toInt32();
        const remote = peer(socket);

        if (
            isCandidate(remote)
            && looksLikeGamePacket(
                buffer,
                length
            )
        ) {
            liveSocket = socket;
            liveRemote = remote;
        }
    }
});

rpc.exports = {
    ready() {
        return (
            liveSocket !== null
            && isLiveSocket(liveSocket)
        );
    },

    remote() {
        return liveRemote;
    },

    enter(hex) {
        if (
            liveSocket === null
            || !isLiveSocket(liveSocket)
        ) {
            throw new Error(
                "Chưa thấy socket gameplay"
            );
        }

        if (
            !/^[0-9a-f]+$/i.test(hex)
            || hex.length % 2 !== 0
        ) {
            throw new Error(
                "Hex không hợp lệ"
            );
        }

        const bytes = [];

        for (
            let i = 0;
            i < hex.length;
            i += 2
        ) {
            bytes.push(
                parseInt(
                    hex.slice(i, i + 2),
                    16
                )
            );
        }

        if (bytes.length < 12) {
            throw new Error(
                "Gói phải >= 12 byte"
            );
        }

        const view = new DataView(
            new Uint8Array(bytes).buffer
        );
        const payloadLen = view.getUint32(
            0,
            false
        );
        const group = view.getUint32(
            4,
            false
        );
        const opcode = view.getUint32(
            8,
            false
        );

        if (
            bytes.length
            !== 12 + payloadLen
        ) {
            throw new Error(
                `Header báo payload ${payloadLen}, thực tế ${bytes.length - 12}`
            );
        }

        if (
            group !== 11
            || !ALLOWED_OPCODES.has(opcode)
        ) {
            throw new Error(
                `(group=${group}, opcode=${opcode}) không hợp lệ. Cho phép group 11, opcode 141/70`
            );
        }

        const buffer = Memory.alloc(
            bytes.length
        );
        buffer.writeByteArray(bytes);

        const sent = sendSocket(
            liveSocket,
            buffer,
            bytes.length,
            0
        );

        if (
            sent
            !== bytes.length
        ) {
            throw new Error(
                `send trả về ${sent}/${bytes.length}, WSA error ${getLastError()}`
            );
        }

        return sent;
    }
};
