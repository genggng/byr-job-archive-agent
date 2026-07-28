#!/usr/bin/env python3
"""Interactive UTF-8 terminal client for the GBK-encoded BYR BBS."""

from __future__ import annotations

import argparse
import codecs
import os
import selectors
import socket
import struct
import sys
import termios
import tty


IAC = 255
DONT = 254
DO = 253
WONT = 252
WILL = 251
SB = 250
SE = 240


class TelnetFilter:
    """Remove Telnet negotiation bytes and reject unsupported options."""

    def __init__(self, sock: socket.socket) -> None:
        self.sock = sock
        self.state = "data"
        self.command = 0

    def feed(self, data: bytes) -> bytes:
        output = bytearray()

        for byte in data:
            if self.state == "data":
                if byte == IAC:
                    self.state = "iac"
                else:
                    output.append(byte)
            elif self.state == "iac":
                if byte == IAC:
                    output.append(IAC)
                    self.state = "data"
                elif byte in (DO, DONT, WILL, WONT):
                    self.command = byte
                    self.state = "option"
                elif byte == SB:
                    self.state = "subnegotiation"
                else:
                    self.state = "data"
            elif self.state == "option":
                if self.command == DO:
                    self.sock.sendall(bytes((IAC, WONT, byte)))
                elif self.command == WILL:
                    self.sock.sendall(bytes((IAC, DONT, byte)))
                self.state = "data"
            elif self.state == "subnegotiation":
                if byte == IAC:
                    self.state = "subnegotiation_iac"
            elif self.state == "subnegotiation_iac":
                self.state = "data" if byte == SE else "subnegotiation"

        return bytes(output)


def connect_socks5(proxy: str, target_host: str, target_port: int) -> socket.socket:
    proxy_host, proxy_port_text = proxy.rsplit(":", 1)
    sock = socket.create_connection((proxy_host, int(proxy_port_text)), timeout=10)
    sock.sendall(b"\x05\x01\x00")
    if sock.recv(2) != b"\x05\x00":
        raise RuntimeError("SOCKS5 proxy rejected unauthenticated connection")

    try:
        packed_host = socket.inet_aton(target_host)
        address = b"\x01" + packed_host
    except OSError:
        encoded_host = target_host.encode("idna")
        address = b"\x03" + bytes((len(encoded_host),)) + encoded_host

    sock.sendall(b"\x05\x01\x00" + address + struct.pack("!H", target_port))
    header = sock.recv(4)
    if len(header) != 4 or header[1] != 0:
        raise RuntimeError(f"SOCKS5 proxy connection failed: {header!r}")

    address_type = header[3]
    if address_type == 1:
        remaining = 4 + 2
    elif address_type == 3:
        length = sock.recv(1)
        remaining = length[0] + 2
    elif address_type == 4:
        remaining = 16 + 2
    else:
        raise RuntimeError("SOCKS5 proxy returned an unknown address type")

    while remaining:
        chunk = sock.recv(remaining)
        if not chunk:
            raise RuntimeError("SOCKS5 proxy closed during handshake")
        remaining -= len(chunk)

    sock.settimeout(None)
    return sock


def connect(host: str, port: int, proxy: str | None) -> socket.socket:
    if proxy:
        return connect_socks5(proxy, host, port)
    return socket.create_connection((host, port), timeout=10)


def run(host: str, port: int, proxy: str | None) -> None:
    sock = connect(host, port, proxy)
    sock.setblocking(False)

    selector = selectors.DefaultSelector()
    selector.register(sock, selectors.EVENT_READ)
    selector.register(sys.stdin, selectors.EVENT_READ)

    telnet_filter = TelnetFilter(sock)
    gbk_decoder = codecs.getincrementaldecoder("gbk")(errors="replace")
    utf8_decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")
    stdin_fd = sys.stdin.fileno()
    old_settings = termios.tcgetattr(stdin_fd)

    try:
        tty.setraw(stdin_fd)
        while True:
            for key, _ in selector.select():
                if key.fileobj is sock:
                    data = sock.recv(65536)
                    if not data:
                        return
                    text = gbk_decoder.decode(telnet_filter.feed(data))
                    if text:
                        sys.stdout.buffer.write(text.encode("utf-8"))
                        sys.stdout.buffer.flush()
                else:
                    data = os.read(stdin_fd, 4096)
                    if not data or b"\x03" in data:
                        return
                    text = utf8_decoder.decode(data)
                    if text:
                        sock.sendall(text.encode("gbk", errors="replace"))
    finally:
        termios.tcsetattr(stdin_fd, termios.TCSADRAIN, old_settings)
        selector.close()
        sock.close()
        sys.stdout.write("\n")
        sys.stdout.flush()


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Connect to BYR BBS while converting GBK to terminal UTF-8."
    )
    parser.add_argument("--host", default="bbs.byr.cn")
    parser.add_argument("--port", type=int, default=23)
    parser.add_argument(
        "--proxy",
        metavar="HOST:PORT",
        help="optional SOCKS5 proxy, for example 127.0.0.1:7890",
    )
    args = parser.parse_args()

    try:
        run(args.host, args.port, args.proxy)
    except (OSError, RuntimeError) as exc:
        print(f"连接失败：{exc}", file=sys.stderr)
        raise SystemExit(1) from exc


if __name__ == "__main__":
    main()
