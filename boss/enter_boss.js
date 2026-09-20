"use strict";

const TARGET = __TARGET__;
const winsock = Process.getModuleByName("ws2_32.dll");
const sendAddress = winsock.getExportByName("send");
const sendSocket = new NativeFunction(sendAddress, "int", ["pointer", "pointer", "int", "int"]);
const getpeername = new NativeFunction(winsock.getExportByName("getpeername"), "int", ["pointer", "pointer", "pointer"]);
const getLastError = new NativeFunction(winsock.getExportByName("WSAGetLastError"), "int", []);
let liveSocket = null;

// Cho phép group 11, opcode 141 (enter) và 70 (exit)
const ALLOWED_OPCODES = new Set([141, 70]);

function peer(socket) {
    const address = Memory.alloc(128);
    const size = Memory.alloc(4);
    size.writeU32(128);
    if (getpeername(socket, address, size) !== 0 || address.readU16() !== 2) return null;
    return {
        ip: [4, 5, 6, 7].map(i => address.add(i).readU8()).join("."),
        port: (address.add(2).readU8() << 8) | address.add(3).readU8()
    };
}

function isTarget(socket) {
    const remote = peer(socket);
    return remote !== null && remote.ip === TARGET.ip && remote.port === TARGET.port;
}

Interceptor.attach(sendAddress, {
    onEnter(args) {
        if (isTarget(args[0])) liveSocket = args[0];
    }
});

rpc.exports = {
    ready() {
        return liveSocket !== null && isTarget(liveSocket);
    },
    enter(hex) {
        if (liveSocket === null || !isTarget(liveSocket)) {
            throw new Error("Chưa thấy socket game");
        }
        if (!/^[0-9a-f]+$/i.test(hex) || hex.length % 2 !== 0) {
            throw new Error("Hex không hợp lệ");
        }
        const bytes = [];
        for (let i = 0; i < hex.length; i += 2) bytes.push(parseInt(hex.slice(i, i + 2), 16));
        if (bytes.length < 12) throw new Error("Gói phải >= 12 byte");

        const view = new DataView(new Uint8Array(bytes).buffer);
        const payloadLen = view.getUint32(0, false);
        const group = view.getUint32(4, false);
        const opcode = view.getUint32(8, false);

        if (bytes.length !== 12 + payloadLen) {
            throw new Error(`Header báo payload ${payloadLen}, thực tế ${bytes.length - 12}`);
        }
        if (group !== 11 || !ALLOWED_OPCODES.has(opcode)) {
            throw new Error(`(group=${group}, opcode=${opcode}) không hợp lệ. Cho phép group 11, opcode 141/70`);
        }

        const buffer = Memory.alloc(bytes.length);
        buffer.writeByteArray(bytes);
        const sent = sendSocket(liveSocket, buffer, bytes.length, 0);
        if (sent !== bytes.length) {
            throw new Error(`send trả về ${sent}/${bytes.length}, WSA error ${getLastError()}`);
        }
        return sent;
    }
};