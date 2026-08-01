"""Bus bring-up and diagnostics.

Three modes, matching the three questions you actually have when putting a new
adapter on a live bus:

    listen   Is anything on this bus at all, and are A/B the right way round?
             Transmits nothing, so it is safe to run while another master is
             still connected.

    sweep    Which addresses answer, and what are they? One pass of the
             identification commands per address, with hexdumps.

    poll     Does it keep working? Continuous cycles with the real pacing.

`--url` accepts anything serialx understands:

    /dev/tty.usbserial-0001
    esphome://atoms3-lite-rs485-f601cc.local:6053/?port_name=RS-485
    socket://host:port
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import datetime as dt
import sys
from pathlib import Path

from .client import CYCLE_COMMANDS, KacoRs485Client
from .framing import trim_leading_junk
from .protocol import (
    ParseError,
    Protocol,
    parse_cmd0,
    parse_cmd3,
    parse_cmd8,
    parse_cmd9,
    verify_checksum,
)
from .transport import AsyncBus, BusError, Reply

PROBE_COMMANDS = [
    ("0", "Measured values"),
    ("3", "Total yield + hours"),
    ("8", "Firmware"),
    ("9", "Inverter type"),
]


def hexdump(data: bytes, width: int = 16) -> str:
    lines = []
    for off in range(0, len(data), width):
        chunk = data[off : off + width]
        hexes = " ".join(f"{b:02x}" for b in chunk).ljust(width * 3 - 1)
        ascii_ = "".join(chr(b) if 32 <= b < 127 else "." for b in chunk)
        lines.append(f"  {off:04x}  {hexes}  |{ascii_}|")
    return "\n".join(lines)


def describe(command: str, raw: bytes) -> str:
    """One-line decode, or the reason it could not be decoded."""
    proto = Protocol.from_reply(raw)
    if proto is Protocol.GENERIC_CRC16:
        return "Generic CRC16 protocol (not an xi unit — blueplanet or TL/TR series)"
    try:
        if command == "0":
            m = parse_cmd0(raw)
            ok, expected, actual = verify_checksum(raw)
            chk = "CHK ok" if ok else f"CHK BAD (want {expected:#04x}, got {actual:#04x})"
            return (
                f"{m.inverter_type} status={m.status} "
                f"dc={m.dc_power_w}W ac={m.ac_power_w}W {m.ac_voltage_v}V "
                f"{m.temperature_c}degC daily={m.daily_yield_wh}Wh  {chk}"
            )
        if command == "3":
            t = parse_cmd3(raw)
            # kWh on xi units, Wh on the blueplanet — see protocol.TotalYield.
            return (
                f"peak={t.daily_peak_w}W daily={t.daily_yield_wh}Wh "
                f"total={t.total_yield_raw}kWh uptime={t.total_uptime}"
            )
        if command == "8":
            return parse_cmd8(raw).raw_text
        if command == "9":
            return parse_cmd9(raw)
    except ParseError as err:
        return f"unparsed: {err}"
    return ""


def diagnose_silence(seen_any_bytes: bool) -> str:
    """The bring-up checklist, printed when nothing answers."""
    if seen_any_bytes:
        return (
            "Bytes arrived but nothing framed cleanly. Most likely: wrong baud rate,\n"
            "or two masters transmitting at once — check the old ESP32-S3 node is\n"
            "powered down before blaming the wiring."
        )
    return (
        "No bytes at all. In order of likelihood:\n"
        "  1. A/B swapped — the single most common RS485 fault. Swap them and retry.\n"
        "  2. The old ESP32-S3 node is still driving the bus.\n"
        "  3. Not actually connected to the bus (check the SolarLog RS485/422 terminal).\n"
        "  4. Missing termination — try 120 ohm across A/B; the ATOMIC base has none.\n"
        "  5. Inverters are asleep (no sun). They stop answering entirely at night."
    )


async def cmd_listen(bus: AsyncBus, args: argparse.Namespace) -> int:
    """Passive monitor: never transmits."""
    print(f"[+] Listening for {args.seconds}s — transmitting nothing.")
    print("[+] Safe to run with another master still on the bus.\n")

    loop = asyncio.get_running_loop()
    deadline = loop.time() + args.seconds
    seen = b""

    while loop.time() < deadline:
        chunk = await bus.read_raw(min(1.0, deadline - loop.time()))
        if not chunk:
            continue
        seen += chunk
        print(hexdump(chunk))

    print(f"\n[+] {len(seen)} bytes in {args.seconds}s")
    if not seen:
        print("\n" + diagnose_silence(seen_any_bytes=False))
    elif not trim_leading_junk(seen):
        print(
            "\n[!] Bytes arrived but none was an LF, so nothing can frame.\n"
            "    Suspect the wrong baud rate, or A/B polarity producing noise."
        )
    return 0


async def cmd_sweep(bus: AsyncBus, args: argparse.Namespace) -> int:
    log = _open_log(args)
    responded: set[int] = set()
    saw_bytes = False

    for address in args.addresses:
        for command, label in PROBE_COMMANDS:
            try:
                reply = await bus.request(address, command)
            except BusError as err:
                print(f"  addr={address:02d} cmd={command!r}  ERROR {err}")
                return 1

            saw_bytes = saw_bytes or bool(reply.raw)
            _report(reply, address, command, label, log, verbose=args.verbose)
            if reply.responded:
                responded.add(address)

    print(f"\n[+] {len(responded)} address(es) responded: {sorted(responded)}")
    if not responded:
        print("\n" + diagnose_silence(seen_any_bytes=saw_bytes))
    if log:
        print(f"[+] Raw log: {log.name}")
        log.close()
    return 0 if responded else 1


async def cmd_poll(bus: AsyncBus, args: argparse.Namespace) -> int:
    client = KacoRs485Client(bus, args.addresses)
    print(f"[+] Polling {args.addresses}, commands {list(CYCLE_COMMANDS)}. Ctrl-C to stop.\n")

    while True:
        await client.poll_cycle()
        stamp = dt.datetime.now(tz=dt.UTC).strftime("%H:%M:%S")
        for address, state in sorted(client.states.items()):
            if state.measured is None:
                status = "asleep" if state.asleep else f"no reply ({state.consecutive_misses})"
                print(f"  {stamp}  addr={address:02d}  {status}")
                continue
            m = state.measured
            avail = "" if state.available else "  [unavailable]"
            print(
                f"  {stamp}  addr={address:02d}  {m.ac_power_w:5d}W  "
                f"{m.ac_voltage_v:6.1f}V  {m.temperature_c:3d}degC  "
                f"status={m.status}{avail}"
            )
        print()
        await asyncio.sleep(args.interval)


def _open_log(args: argparse.Namespace):
    if not args.log_dir:
        return None
    directory = Path(args.log_dir)
    directory.mkdir(parents=True, exist_ok=True)
    stamp = dt.datetime.now(tz=dt.UTC).strftime("%Y%m%d_%H%M%S")
    handle = (directory / f"{stamp}.log").open("w")
    handle.write(f"# kaco-rs485 session {stamp}\n# url={args.url}\n")
    return handle


def _report(
    reply: Reply, address: int, command: str, label: str, log, *, verbose: bool
) -> None:
    if not reply.responded:
        print(f"  addr={address:02d} cmd={command!r} ({label})  no reply")
        if log:
            log.write(f"\n=== addr={address:02d} cmd={command!r} no reply ===\n")
        return

    print(
        f"  addr={address:02d} cmd={command!r} ({label})  "
        f"{len(reply.raw):3d}B {reply.elapsed_ms:6.0f}ms  {describe(command, reply.raw)}"
    )
    if verbose:
        print(hexdump(reply.raw))
    if log:
        log.write(
            f"\n=== addr={address:02d} cmd={command!r} ({label})  "
            f"{reply.elapsed_ms:.0f} ms  {len(reply.raw)} bytes ===\n"
            f"  TX  {reply.request!r}\n  RX  {reply.raw!r}\n{hexdump(reply.raw)}\n"
        )


async def _run(args: argparse.Namespace) -> int:
    bus = AsyncBus(args.url, baudrate=args.baud, key=args.key)
    try:
        await bus.open()
    except BusError as err:
        print(f"[!] {err}", file=sys.stderr)
        return 1

    try:
        if args.mode == "listen":
            return await cmd_listen(bus, args)
        if args.mode == "sweep":
            return await cmd_sweep(bus, args)
        return await cmd_poll(bus, args)
    finally:
        await bus.close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="kaco-rs485", description=__doc__)
    parser.add_argument("--url", required=True, help="serialx URL or device path")
    parser.add_argument("--baud", type=int, default=9600)
    parser.add_argument("--key", default=None, help="ESPHome API encryption key, if set")
    parser.add_argument("-v", "--verbose", action="store_true", help="hexdump every reply")
    parser.add_argument("--log-dir", default=None, help="write a session log here")

    sub = parser.add_subparsers(dest="mode", required=True)

    listen = sub.add_parser("listen", help="passive monitor, transmits nothing")
    listen.add_argument("--seconds", type=int, default=30)

    for name, help_ in (("sweep", "probe addresses once"), ("poll", "poll continuously")):
        p = sub.add_parser(name, help=help_)
        p.add_argument(
            "--addresses",
            nargs="+",
            type=int,
            default=[1, 2, 4],
            help="RS485 addresses (default: the three xi units)",
        )
        if name == "poll":
            p.add_argument("--interval", type=float, default=10.0)

    args = parser.parse_args(argv)

    with contextlib.suppress(KeyboardInterrupt):
        return asyncio.run(_run(args))
    return 0


if __name__ == "__main__":
    sys.exit(main())
