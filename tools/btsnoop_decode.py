#!/usr/bin/env python3
"""Decode a btsnoop capture into the ATT writes that drive the panel.

Written to avoid a tshark dependency: tshark needs root to install and its
output needs post-processing anyway. This reads btsnoop directly and prints
one line per ATT operation, which is the only layer we care about.

Handles both capture sources:

  * Android `btsnoop_hci.log` (datalink 1001/1002, H4-framed)
  * Linux `btmon -w` (datalink 2001, Linux monitor framing)

CLEAN-ROOM NOTE: this decodes a public, documented file format and the
Bluetooth Core Specification's own L2CAP/ATT framing. It is not derived from
any GPL iDotMatrix implementation.

Usage:
    python3 tools/btsnoop_decode.py captures/vendor-app.log
    python3 tools/btsnoop_decode.py captures/vendor-app.log --min-gap 2.0
"""

from __future__ import annotations

import argparse
import struct
import sys
from dataclasses import dataclass
from pathlib import Path

# Microseconds between 0000-01-01 (btsnoop epoch) and 1970-01-01 (unix epoch).
BTSNOOP_EPOCH_DELTA = 0x00DCDDB30F2F8000

DATALINK_H4 = (1001, 1002, 1003)
DATALINK_MONITOR = 2001

L2CAP_CID_ATT = 0x0004

ATT_OPCODES = {
    0x0A: "read_req",
    0x0B: "read_rsp",
    0x12: "write_req",
    0x13: "write_rsp",
    0x1B: "notify",
    0x1D: "indicate",
    0x52: "write_cmd",
}

# Linux monitor opcodes we care about (btmon framing).
MONITOR_ACL_TX = 0x04
MONITOR_ACL_RX = 0x05


@dataclass
class AttOp:
    ts: float          # seconds, unix epoch
    outbound: bool
    opcode: int
    handle: int
    value: bytes

    @property
    def name(self) -> str:
        return ATT_OPCODES.get(self.opcode, f"att_0x{self.opcode:02x}")


def _read_records(path: Path):
    """Yield (timestamp_seconds, flags, payload) for each btsnoop record."""
    data = path.read_bytes()
    if not data.startswith(b"btsnoop\x00"):
        raise ValueError(f"{path} is not a btsnoop capture")

    version, datalink = struct.unpack_from(">II", data, 8)
    if version != 1:
        raise ValueError(f"unsupported btsnoop version {version}")

    offset = 16
    while offset + 24 <= len(data):
        orig_len, incl_len, flags, _drops, ts = struct.unpack_from(">IIIIq", data, offset)
        del orig_len
        offset += 24
        payload = data[offset:offset + incl_len]
        if len(payload) < incl_len:
            break                      # truncated final record
        offset += incl_len
        yield (ts - BTSNOOP_EPOCH_DELTA) / 1_000_000.0, flags, payload, datalink


def _acl_from_record(flags: int, payload: bytes, datalink: int):
    """Return (outbound, acl_bytes) or None if this record is not ACL data."""
    if datalink == DATALINK_MONITOR:
        opcode = flags & 0xFFFF
        if opcode == MONITOR_ACL_TX:
            return True, payload
        if opcode == MONITOR_ACL_RX:
            return False, payload
        return None

    # H4: first byte is the packet type indicator.
    if not payload or payload[0] != 0x02:      # 0x02 = ACL
        return None
    # btsnoop flag bit 0: 0 = host -> controller (a write we sent)
    return (flags & 0x01) == 0, payload[1:]


def iter_att_ops(path: Path):
    """Yield AttOp for every ATT operation in the capture.

    Reassembles L2CAP fragments, which matters: a 64x64 image upload is far
    larger than one ACL packet, so the interesting payloads always arrive
    fragmented.
    """
    pending: dict[int, tuple[bool, int, bytearray]] = {}

    for ts, flags, payload, datalink in _read_records(path):
        acl = _acl_from_record(flags, payload, datalink)
        if acl is None:
            continue
        outbound, body = acl
        if len(body) < 4:
            continue

        handle_flags, _acl_len = struct.unpack_from("<HH", body, 0)
        handle = handle_flags & 0x0FFF
        pb = (handle_flags >> 12) & 0x03
        fragment = body[4:]

        if pb == 0x01 and handle in pending:            # continuation
            out, cid, buf = pending[handle]
            buf.extend(fragment)
        else:                                            # start of a new PDU
            if len(fragment) < 4:
                continue
            l2_len, cid = struct.unpack_from("<HH", fragment, 0)
            del l2_len
            out, buf = outbound, bytearray(fragment[4:])
            pending[handle] = (out, cid, buf)

        out, cid, buf = pending[handle]
        if cid != L2CAP_CID_ATT:
            continue

        # Emit once the PDU looks complete enough to parse. ATT writes are
        # opcode + 2-byte handle + value; we emit on every growth step and
        # deduplicate by keeping only the longest form per PDU below.
        if len(buf) >= 3:
            pending[handle] = (out, cid, buf)
            yield AttOp(ts, out, buf[0], struct.unpack_from("<H", buf, 1)[0], bytes(buf[3:]))


def collapse(ops):
    """Keep only the final, longest version of each reassembled PDU.

    iter_att_ops emits a growing PDU repeatedly as fragments arrive; the last
    emission for a given (timestamp-ish, handle) run is the complete one.
    """
    out: list[AttOp] = []
    for op in ops:
        if (out and out[-1].handle == op.handle and out[-1].opcode == op.opcode
                and len(op.value) > len(out[-1].value)
                and op.value.startswith(out[-1].value)):
            out[-1] = op
        else:
            out.append(op)
    return out


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("capture", type=Path)
    p.add_argument("--min-gap", type=float, default=1.5,
                   help="seconds of silence that marks a new action block "
                        "(matches the pauses in the capture checklist)")
    p.add_argument("--max-bytes", type=int, default=64,
                   help="truncate printed payloads to this many bytes")
    p.add_argument("--writes-only", action="store_true",
                   help="show only writes to the device, hiding notifications")
    args = p.parse_args(argv)

    try:
        ops = collapse(iter_att_ops(args.capture))
    except (OSError, ValueError, struct.error) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    if args.writes_only:
        ops = [o for o in ops if o.opcode in (0x12, 0x52)]

    if not ops:
        print("no ATT operations found -- was the snoop log enabled before "
              "the app connected?", file=sys.stderr)
        return 1

    base = ops[0].ts
    prev = base
    block = 1
    print(f"--- block 1 {'-' * 52}")
    for op in ops:
        gap = op.ts - prev
        if gap >= args.min_gap:
            block += 1
            print(f"\n--- block {block}  (+{gap:.1f}s silence) "
                  f"{'-' * 40}")
        prev = op.ts
        arrow = "-->" if op.outbound else "<--"
        payload = op.value[:args.max_bytes]
        tail = "..." if len(op.value) > args.max_bytes else ""
        print(f"{op.ts - base:8.3f}  {arrow} {op.name:<10} "
              f"h=0x{op.handle:04x}  len={len(op.value):<5} "
              f"{payload.hex(' ')}{tail}")

    sys.stdout.flush()      # keep the summary after the listing, not before it
    print(f"\n{len(ops)} ATT operations in {block} action blocks",
          file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
