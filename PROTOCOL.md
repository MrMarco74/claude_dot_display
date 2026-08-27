# iDotMatrix 64x64 — BLE Protocol

Derived clean-room from traffic observed on the wire against a physical
64x64 unit. **No GPL source was read.** See "Provenance" at the end.

> **Status: the core command set is verified against hardware.** Every frame
> below was first captured from the vendor's own Android application, then
> re-sent by this project's own driver and confirmed by photographing the
> physical panel. Entries still marked `replayed: no` are readings of the
> wire, not proven commands -- treat them accordingly.

## Transport

| Property | Value |
| --- | --- |
| Service | `000000fa-0000-1000-8000-00805f9b34fb` |
| Write characteristic | `0000fa02-0000-1000-8000-00805f9b34fb` (declaration `0x0005`, value `0x0006`) |
| Notify characteristic | `0000fa03-0000-1000-8000-00805f9b34fb` (declaration `0x0008`, value `0x0009`) |
| Negotiated MTU | 517, so writes up to 514 bytes |
| Max write size observed | 509 bytes |

The captures record ATT *handles*; the UUIDs above were read from the panel
directly. The handle in a capture is the characteristic's **value** handle,
one above its declaration handle -- which is why `0x0006` in the traffic
corresponds to the characteristic declared at `0x0005`.

**BlueZ reports the 23-byte default MTU until it is explicitly acquired.**
Taking that at face value caps writes at 20 bytes and makes a full frame
roughly six times slower than necessary.

Large payloads are split across many writes; the protocol's own framing —
not the ATT layer — defines message boundaries.

## Frame envelope

Every message begins with its own total length as a little-endian `u16`:

```
<u16 total_length> <body...>
```

For short commands the whole message fits one write. For bulk transfers the
message is chunked; see "Bulk transfer".

## Short commands

### Brightness

```
05 00  04 80  <level>
└──┬─┘ └──┬─┘ └──┬──┘
   5    cmd     0x00..0x64
```

Observed values `0x4b` (75) and `0x0f` (15) while dragging the app's slider.
The slider emits a value per movement step, so a capture of one adjustment
contains dozens of these.

- observed: `captures/vendor-app-2026-08-27.btsnoop`, blocks 2 and 3
- replayed: **yes**, 2026-08-27 — panel visibly dimmed and brightened

### Power

```
05 00  07 01  <00 = off | 01 = on>
```

- observed: same capture, blocks 4 (off) and 5 (on)
- replayed: **yes**, 2026-08-27 — panel went fully dark, then returned

### Draw a single pixel

```
0a 00  05 01  00  <R> <G> <B>  <X> <Y>
└──┬─┘ └──┬─┘     └────┬────┘  └──┬──┘
  10    cmd          colour     0..63
```

Confirmed by content: freehand strokes in the app produced runs of these with
`ff 00 00` (red) and `00 00 ff` (blue) exactly matching the colour selected.

- observed: same capture, blocks 7, 9, 11
- replayed: **yes**, 2026-08-27 — 32 pixels drawn as a clean diagonal

### Set display mode

```
05 00  04 01  01
```

Sent immediately before every bulk image transfer. `04 01 00` appears at the
end of a session.

- observed: same capture, blocks 12-16 (leading write), block 17
- replayed: **yes**, 2026-08-27 — precedes every successful image upload

## Bulk transfer — images and animations

This is the fast path, and the reason this project exists in its current
form. A full 64x64 frame transfers in **0.77 s** from this driver (measured,
2026-08-27), against ~0.9 s from the vendor application and roughly 6 s for
the per-pixel approach used by the existing GPL libraries.

### Chunk header (9 bytes)

```
<u16 chunk_length>  <u16 kind>  <u8 flag>  <u32 total_length>
```

| Field | Meaning |
| --- | --- |
| `chunk_length` | Bytes in this chunk **including** these 9 header bytes |
| `kind` | `0x0000` = still image, `0x0001` = animation |
| `flag` | `0x00` on the first chunk, `0x02` on every following chunk |
| `total_length` | Total payload across all chunks |

The `kind` field was only resolvable because a still image and an animation
were both captured: it is the single field that differs between them while
everything else stays identical.

### Still image payload

Raw **RGB888**, **row-major**, origin **top-left**:

```
total_length = 64 * 64 * 3 = 12288
chunks       = 3 x 4105 bytes  (9 header + 4096 data)
wire total   = 5 (mode) + 3 * 4105 = 12320 bytes
duration     = 0.77 s measured from this driver
```

Pixel `i` occupies bytes `3i, 3i+1, 3i+2` and sits at `x = i % 64`,
`y = i // 64`.

**How the ordering was established.** Three images differing from a black
baseline by exactly one white pixel, at three known corners, were uploaded and
the brightest pixel located in each payload:

| Image | Brightest index | Row-major | Column-major |
| --- | --- | --- | --- |
| top-left `(0,0)` | 0 | `(0,0)` | `(0,0)` |
| top-right `(63,0)` | **63** | **`(63,0)`** ✓ | `(0,63)` ✗ |
| bottom-left `(0,63)` | **4032** | **`(0,63)`** ✓ | `(63,0)` ✗ |

Column-major would have swapped the last two. This is a measurement, not an
inference.

Note the app **resamples** what it sends: a single-pixel source arrives spread
over roughly 33 pixels with one full-intensity centre. Our own encoder should
send exact pixels and skip that step.

- observed: `captures/vendor-app-2026-08-27.btsnoop`, blocks 12-16
- replayed: **yes**, 2026-08-27 — solid colour fills the panel; a four-corner
  marker (red top-left, white top-right, green bottom-left, blue bottom-right)
  appeared exactly as encoded, confirming row-major order and a top-left
  origin end to end
- measured: **0.77 s** per full frame from this driver, against ~0.9 s from
  the vendor application

### Animation payload

Same envelope with `kind = 0x0001`.

```
total_length = 38278   (one captured GIF)
chunks       = 9 x 4112 + 1 x 1430
duration     = ~3.1 s
```

**Unresolved:** the chunk lengths do not reconcile with `total_length` the way
the still-image ones do (`38438` on the wire against a declared `38278`, a
160-byte discrepancy that 10 nine-byte headers do not explain). The animation
chunk header is therefore **not** fully decoded. Do not implement animation
from this entry without a further capture.

- observed: `captures/vendor-app-2026-08-27b.btsnoop`, block 20
- replayed: **no**

## Text

The device has **no font**. The application rasterises text on the phone and
transmits one bitmap per character.

```
<u16 total_length> 03 00 00 <u32 body_length> <4 bytes, varies> 00 00 <..>
  ... then repeating per glyph:
  00 00 02 <R G B fg> <R G B bg> <11 bytes bitmap>
```

Each glyph is **8 wide by 11 tall**, one byte per row, **least significant bit
leftmost**. Rendering the captured bytes reproduces legible serif letters —
this is how the structure was identified rather than guessed:

```
...#....     #####...     ..#####.
...#....     .#...#..     .#....#.
...##...     .#...#..     .#....#.
..#.#...     .#...#..     #.......
..#.#...     .####...     #.......
..#..#..     .#...#..     #.......
..####..     .#....#.     #.......
.#...#..     .#....#.     #.......
.#....#.     .#....#.     .#....#.
.#....#.     .#...#..     .#...#..
###..###     #####...     ..###...
    A            B            C
```

The four varying bytes after `body_length` differ per message (`55 35 44 52`
and `3c fd b2 f2` in two captures) and are **not** decoded. A checksum is the
obvious hypothesis; it is not established.

**Consequence for this project:** we do not need this command. We rasterise
text ourselves and send it through the still-image path, which is both simpler
and already the fastest route. This entry exists because it explains a
long-standing bug elsewhere — libraries that assume an on-device text mode
cannot work, because there isn't one.

- observed: `captures/vendor-app-2026-08-27b.btsnoop`, blocks 18 and 19
- replayed: **no**

## Open questions

1. The animation chunk header's 160-byte discrepancy.
2. The four varying bytes in the text header.
3. Whether `flag` distinguishes more than first (`0x00`) and continuation
   (`0x02`).
4. The connection handshake in block 1 has not been analysed.

## Provenance

Captured from a Samsung Galaxy A7 (2018), `SM_A750FN`, Android 10 (SDK 29),
running the vendor iDotMatrix application against the physical 64x64 panel.
Bluetooth HCI snoop logging was enabled via
`settings put global bluetooth_hci_log 1` followed by a Bluetooth stack
restart, and the log retrieved with `adb bugreport`
(`FS/data/log/bt/btsnoop_hci.log` on Samsung, not the AOSP path).

Decoded with `tools/btsnoop_decode.py`, which parses the public btsnoop
format and the Bluetooth Core Specification's own L2CAP/ATT framing.

Actions were driven one at a time with pauses between them, so that the
decoder's silence-based block splitting labels each action.

**No GPL-licensed source was consulted.** Every statement above derives from
bytes observed on the wire or from images we constructed ourselves.
