# kaco-rs485

Read KACO Powador **xi-series** inverters (6400xi, 8000xi, and relatives) over
RS485 — from a local adapter, or across the network through an
[ESPHome serial proxy](https://esphome.io/components/serial_proxy/).

Companion to [`kaco-modbus`](https://github.com/g4bri3lDev/kaco-modbus), which
covers the newer blueplanet units over SunSpec Modbus TCP. Different protocol,
different hardware generation, same fleet.

## Install

```bash
uv add kaco-rs485          # library
uv add 'kaco-rs485[cli]'   # + the diagnostic CLI and ESPHome transport
```

## The bus is addressed by URL

There is one transport, and it takes an opaque URL. serialx resolves the
scheme, so nothing in this library knows or cares how the bytes get there:

| URL | Where |
|---|---|
| `/dev/tty.usbserial-0001` | local RS485 adapter |
| `esphome://host:6053/?port_name=RS-485` | ESPHome proxy, standalone |
| `esphome-hass://esphome/<entry_id>?port_name=RS-485` | ESPHome proxy, inside Home Assistant |
| `socket://host:port` | any ser2net-style bridge |

The `esphome-hass` scheme is registered by Home Assistant and resolves the
ESPHome integration's already-authenticated client — which is why the Home
Assistant integration built on this library stores no host and no API key.

## Library

```python
from kaco_rs485 import AsyncBus, KacoRs485Client
from kaco_rs485.discovery import scan

async with AsyncBus("esphome://proxy.local:6053/?port_name=RS-485") as bus:
    found = await scan(bus)
    client = KacoRs485Client(bus, [d.address for d in found.supported])
    states = await client.poll_cycle()
    for address, state in states.items():
        print(address, state.measured.ac_power_w)
```

`KacoRs485Client` owns the pacing. Don't drive `AsyncBus` from two tasks at
once — RS485 is a shared medium and interleaved requests garble each other.

## Bringing up a bus

Four CLI modes, in the order you actually need them:

```bash
# 1. Is anything there, and are A/B the right way round?
#    Transmits nothing — safe while another master is still connected.
kaco-rs485 --url <url> listen --seconds 30

# 2. Which addresses are occupied, and what is at them?
#    Probes all 32 addresses; a silent one costs ~2.5s.
kaco-rs485 --url <url> scan

# 3. Everything one address can tell you.
kaco-rs485 --url <url> sweep --addresses 2 -v

# 4. Does it keep working? Scans first if you omit --addresses.
kaco-rs485 --url <url> poll
```

When nothing answers, the CLI prints a diagnostic checklist rather than just a
timeout. **Only one master may drive an RS485 bus** — power down any other
polling device before blaming the wiring.

## Protocol notes

The parsers were validated against bytes captured from a live installation,
and they deliberately tolerate several places where real hardware diverges
from the KACO specification:

- Command `3` replies on xi units **omit** the `*<adr>3 ` header the spec
  shows. Both shapes are accepted.
- Command `3` total yield is in **kWh** on xi units, **Wh** on the blueplanet.
  `total_yield_raw` is exposed unscaled; the caller decides.
- Command `s` (serial number) is **unsupported** on xi units — they return
  nothing. Use command `9` for identification.
- The command `0` checksum byte may be **any** value 0-255, including CR and
  LF. This is why framing is length-gated instead of scanning for a
  terminator, and why readline-style parsing cannot work here.
- xi units pause **>250 ms mid-frame**, between the checksum byte and the
  trailing type string. Frames legitimately end anywhere from 58 to 65 bytes;
  the type string is decorative.
- Replies start **1-2 seconds** after the request.
- Status codes **4 (feeding in)** and **6 (standby)** are observed in the
  field but absent from the specification's table.

`tests/reference/captures.json` holds real frames from the live bus. The
framing tests replay them split at every possible byte boundary, because
through a network proxy the arrival chunking is arbitrary.
