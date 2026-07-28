#!/usr/bin/env python3
"""Interactive UTF-8 terminal client for the GBK-encoded BYR BBS."""

from __future__ import annotations

import argparse
import codecs
import os
import selectors
import socket
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


def run(host: str, port: int) -> None:
    sock = socket.create_connection((host, port), timeout=10)
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
    args = parser.parse_args()

    try:
        run(args.host, args.port)
    except (OSError, RuntimeError) as exc:
        print(f"连接失败：{exc}", file=sys.stderr)
        raise SystemExit(1) from exc


if __name__ == "__main__":
    main()
