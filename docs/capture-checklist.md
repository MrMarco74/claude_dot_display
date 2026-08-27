# BLE Capture Checklist

A one-pager to follow with the phone in hand. Goal: produce a capture that is
cheap to decode, not merely a capture.

**Clean-room rule:** everything here observes *behaviour*. Do not open GPL
source at any point. See section 9 of the architecture design.

---

## Before you start

Install on marcohp:

```bash
sudo apt install android-tools-adb tshark
```

Already present: `btmon`, `bluetoothctl`, and a working Bluetooth adapter
(check with `bluetoothctl list`).

Create somewhere to put the results:

```bash
mkdir -p captures
```

---

## Path A — vendor app on the Galaxy A7 (preferred)

Every Galaxy A7 generation has Bluetooth 4.0+ with LE, so the model is not a
blocker. Only the Android version matters, and only for retrieving the log.

### Phone setup

1. Settings, About phone, tap **Build number** seven times.
2. Developer options, enable **USB debugging**.
3. Developer options, enable **Bluetooth HCI snoop log**.
4. Toggle Bluetooth off and on. Some builds need a reboot before the log
   actually starts. Do not skip this.
5. Install the **iDotMatrix** app from the Play Store and connect it to the
   panel once, to get pairing out of the way.
6. Connect USB, accept the RSA prompt on the phone, then confirm:

```bash
adb devices          # expect: <serial>  device
```

If Developer options misbehaves, Samsung phones expose a service menu at
`*#9900#` (SysDump) that can enable and collect Bluetooth logs.

### Record the version now

```
Android version: ______      (decides retrieval, see below)
```

### The choreographed run

One action at a time. **Pause about five seconds between each**, and write down
the wall-clock time. The pauses are what make the capture decodable: they turn
one continuous byte stream into labelled segments.

| # | Action in the app | Time | What it isolates |
| --- | --- | --- | --- |
| 1 | Connect to the panel | ____ | Handshake, service and characteristic discovery |
| 2 | Brightness to 10% | ____ | Single-parameter opcode |
| 3 | Brightness to 90% | ____ | Same opcode, second value: reveals the encoding |
| 4 | Power off | ____ | Simplest possible frame |
| 5 | Power on | ____ | Its counterpart |
| 6 | Fill screen solid red | ____ | Colour encoding, without image framing |
| 7 | Fill screen solid blue | ____ | Confirms the colour field |
| 8 | **Upload plain black 64x64 image** | ____ | Upload envelope, minimal payload entropy |
| 9 | **Upload black image, one white pixel** | ____ | Diff against 8: pixel ordering and packing |
| 10 | Upload a real photo | ____ | Chunking, pacing, full-size behaviour |
| 11 | Show the clock | ____ | Bonus opcode |

Steps 8 and 9 are the important ones. Two captures differing by exactly one
pixel produce byte streams differing in almost exactly one place, which hands
you row-major versus column-major, bit packing, and endianness by direct
comparison instead of trial and error. Prepare both images beforehand:

```bash
python3 -c "
from PIL import Image
Image.new('RGB', (64, 64), (0, 0, 0)).save('black.png')
i = Image.new('RGB', (64, 64), (0, 0, 0)); i.putpixel((0, 0), (255, 255, 255))
i.save('one-pixel.png')"
```

Put both on the phone before starting, so no step involves fumbling.

### Retrieve the log

Android 7 or older:

```bash
adb pull /sdcard/btsnoop_hci.log captures/vendor-app-YYYY-MM-DD.log
```

Android 8 or 9:

```bash
adb bugreport captures/bugreport.zip
unzip -j captures/bugreport.zip 'FS/data/misc/bluetooth/logs/*' -d captures/
```

### Turn the snoop log off again

It logs every Bluetooth connection the phone makes, so switch it off in
Developer options once you are done.

---

## Path B — host-side capture on marcohp (always available)

Captures what the existing library puts on the wire, treating it as a black
box. Sufficient for a complete driver of everything currently used; it just
cannot show the vendor's fast image upload.

```bash
btmon -w captures/host-YYYY-MM-DD.btsnoop
```

Then, in another shell, drive the same numbered action list through the
existing tooling, keeping the same pauses and the same notes.

---

## Decode

```bash
tshark -r captures/<file> -Y 'btatt.opcode' -T fields \
       -e frame.time_relative -e btatt.handle -e btatt.value
```

Every write to the panel comes out as one timestamped hex line. Line those up
against your action log, and each numbered step becomes a labelled block of
bytes.

Record findings in `PROTOCOL.md`, one entry per opcode:

```
0x05 set_brightness
  observed: <capture file>, step 2 and 3
  encoding: <what the bytes mean>
  verified: replayed against the 64x64 unit, panel changed  [yes/no]
  source:   wire only, no GPL code read
```

An opcode is not finished until `verified` says the physical panel changed.
