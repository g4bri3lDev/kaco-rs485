"""Bus bring-up and diagnostics.

Three modes, matching the three questions you actually have when putting a new
adapter on a live bus:

    listen   Is anything on this bus at all, and are A/B the right way round?
             Transmits nothing, so it is safe to run while another master is
             still connected.

    scan     Which addresses are occupied, and what is at them? Probes the
             whole bus unless told otherwise.

    sweep    Everything one address can tell you: all probe commands, with
             hexdumps.

    poll     Does it keep working? Continuous cycles with the real pacing.
             Scans first if no addresses are given.

`--url` accepts anything serialx understands:

    /dev/ttyUSB0
    esphome://<host>:6053/?port_name=RS-485
    socket://<host>:<port>
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import datetime as dt
import json
import sys
from pathlib import Path

from . import framing
from .client import CYCLE_COMMANDS, KacoRs485Client
from .discovery import ALL_ADDRESSES, scan
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
from .status import status_text
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
                f"{m.inverter_type} [{status_text(m.status)}] "
                f"dc={m.dc_power_w}W ac={m.ac_power_w}W {m.ac_voltage_v}V "
                f"{m.temperature_c}degC daily={m.daily_yield_wh}Wh  {chk}"
            )
        if command == "3":
            t = parse_cmd3(raw)
            # D4's unit is series-dependent — kWh on xi units, Wh on the
            # blueplanet — and nothing in the frame says which. Print it
            # unlabelled rather than asserting a unit we cannot know here.
            return (
                f"peak={t.daily_peak_w}W daily={t.daily_yield_wh}Wh "
                f"total={t.total_yield_raw} (kWh on xi, Wh on blueplanet) "
                f"uptime={t.total_uptime}"
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
            "Bytes arrived but nothing framed cleanly. In order of likelihood:\n"
            "  1. Wrong baud rate. KACO units use 9600 8N1 unless reconfigured.\n"
            "  2. Two masters transmitting at once. A datalogger or a second\n"
            "     polling device on the same bus will corrupt both sides'\n"
            "     traffic; disconnect it before blaming the wiring.\n"
            "  3. Electrical noise — check shield grounding and cable runs."
        )
    return (
        "No bytes at all. In order of likelihood:\n"
        "  1. A/B swapped. The single most common RS485 fault, and harmless\n"
        "     to test: swap the two lines and retry.\n"
        "  2. Another master owns the bus and the inverters are answering it\n"
        "     instead. Disconnect any datalogger or second polling device.\n"
        "  3. Not actually on the bus — check the terminal block and that the\n"
        "     adapter shares a ground reference with the inverters.\n"
        "  4. Missing termination. Try 120 ohm across A/B; many small adapters\n"
        "     have no termination resistor fitted.\n"
        "  5. The inverters are asleep. xi units stop answering entirely when\n"
        "     the sun is down, so a night-time silence proves nothing."
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
    records: list[dict] = []
    responded: set[int] = set()
    saw_bytes = False

    for address in args.addresses or ALL_ADDRESSES:
        for command, label in PROBE_COMMANDS:
            try:
                reply = await bus.request(address, command)
            except BusError as err:
                print(f"  addr={address:02d} cmd={command!r}  ERROR {err}")
                return 1

            saw_bytes = saw_bytes or bool(reply.raw)
            _report(reply, address, command, label, log, verbose=args.verbose)
            records.append(_record(reply, address, command))
            if reply.responded:
                responded.add(address)

    print(f"\n[+] {len(responded)} address(es) responded: {sorted(responded)}")
    if not responded:
        print("\n" + diagnose_silence(seen_any_bytes=saw_bytes))

    _report_timings(records)

    if log:
        json_path = Path(log.name).with_suffix(".json")
        json_path.write_text(json.dumps(records, indent=1))
        print(f"[+] Raw log:  {log.name}")
        print(f"[+] Records:  {json_path}")
        log.close()
    return 0 if responded else 1


def _record(reply: Reply, address: int, command: str) -> dict:
    """A capture entry, including the raw bytes and the arrival timing.

    `rx_hex` is deliberately included so a session can be replayed through the
    parsers offline, and `arrivals` so the timeout constants can be checked
    against measurement rather than taken on trust.
    """
    return {
        "address": address,
        "command": command,
        "responded": reply.responded,
        "bytes": len(reply.raw),
        "elapsed_ms": round(reply.elapsed_ms, 1),
        "first_byte_ms": (
            round(reply.first_byte_ms, 1) if reply.first_byte_ms is not None else None
        ),
        "max_gap_ms": (round(reply.max_gap_ms, 1) if reply.max_gap_ms is not None else None),
        "arrivals": [[round(at, 1), n] for at, n in reply.arrivals],
        "rx_hex": reply.raw.hex(),
        "decoded": describe(command, reply.raw) if reply.responded else None,
    }


def _report_timings(records: list[dict]) -> None:
    """Compare what actually happened against the configured timeouts.

    The constants in `framing` came from a small number of captures on one
    installation. This is how you find out whether they hold on yours.
    """
    starts = [r["first_byte_ms"] for r in records if r["first_byte_ms"] is not None]
    gaps = [r["max_gap_ms"] for r in records if r["max_gap_ms"] is not None]
    if not starts:
        return

    print("\n[+] Measured timing vs. configured limits:")
    print(
        f"    reply start:  min {min(starts):6.0f}  max {max(starts):6.0f} ms"
        f"   (start_timeout_s = {framing.REPLY_START_TIMEOUT_S * 1000:.0f} ms)"
    )
    if gaps:
        print(
            f"    mid-frame gap: min {min(gaps):6.0f}  max {max(gaps):6.0f} ms"
            f"   (gap_s = {framing.REPLY_GAP_S * 1000:.0f} ms)"
        )
    else:
        print("    mid-frame gap: never observed (every reply arrived in one chunk)")

    headroom = framing.REPLY_START_TIMEOUT_S * 1000 - max(starts)
    if headroom < 500:
        print(
            f"\n[!] Only {headroom:.0f} ms of headroom on the start timeout. "
            "Raise start_timeout_s."
        )
    if gaps and max(gaps) > framing.REPLY_GAP_S * 1000 * 0.7:
        print("\n[!] Mid-frame gaps are close to gap_s — frames risk being cut short.")


async def cmd_scan(bus: AsyncBus, args: argparse.Namespace) -> int:
    """Ask every address who is there."""
    targets = args.addresses or list(ALL_ADDRESSES)
    print(f"[+] Scanning addresses {targets[0]}-{targets[-1]}.")
    print(f"[+] Silent addresses cost ~2.5 s each, so allow up to {len(targets) * 3}s.\n")

    def progress(done: int, total: int) -> None:
        print(f"\r    {done}/{total}", end="", flush=True)

    result = await scan(bus, targets, on_progress=progress)
    print("\r" + " " * 20 + "\r", end="")

    for device in result.supported:
        kind = device.inverter_type or "type unknown (short frame)"
        print(f"  addr={device.address:02d}  {kind}")
    for device in result.unsupported:
        print(
            f"  addr={device.address:02d}  CRC16 Generic Protocol — not an xi unit.\n"
            "              This is a blueplanet or TL/TR device; read it with "
            "kaco-modbus over Modbus TCP instead."
        )

    if not result.found:
        print("[+] Nothing answered.\n")
        print(diagnose_silence(seen_any_bytes=result.saw_any_bytes))
        print("\n    Note: xi units stop answering entirely at night. If the sun is")
        print("    down, an empty scan tells you nothing about your wiring.")
        return 1

    found = [d.address for d in result.supported]
    print(f"\n[+] {len(found)} readable inverter(s): {found}")
    return 0


async def cmd_poll(bus: AsyncBus, args: argparse.Namespace) -> int:
    addresses = args.addresses
    if not addresses:
        print("[+] No --addresses given; scanning the bus first.\n")
        result = await scan(bus, ALL_ADDRESSES)
        addresses = [d.address for d in result.supported]
        if not addresses:
            print("[!] Nothing to poll — no xi units answered.")
            return 1

    client = KacoRs485Client(bus, addresses)
    print(f"[+] Polling {addresses}, commands {list(CYCLE_COMMANDS)}. Ctrl-C to stop.\n")

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
                f"{status_text(m.status)}{avail}"
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
        if args.mode == "scan":
            return await cmd_scan(bus, args)
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

    modes = (
        ("scan", "find out which addresses are occupied"),
        ("sweep", "run every probe command against given addresses"),
        ("poll", "poll continuously"),
    )
    for name, help_ in modes:
        p = sub.add_parser(name, help=help_)
        p.add_argument(
            "--addresses",
            nargs="+",
            type=int,
            default=None,
            help="RS485 addresses; omit to scan the whole bus (1-32)",
        )
        if name == "poll":
            p.add_argument("--interval", type=float, default=10.0)

    args = parser.parse_args(argv)

    with contextlib.suppress(KeyboardInterrupt):
        return asyncio.run(_run(args))
    return 0


if __name__ == "__main__":
    sys.exit(main())
