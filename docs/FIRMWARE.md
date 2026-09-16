# Firmware: the escape core on an M5Stack Cardputer ADV

[`firmware/cardputer`](../firmware/cardputer) runs the committed escape core,
[`core-hand-abc`](../circuits/loom-escape/core-hand-abc.json) (173 NAND + 6 LATCH,
1,235 bytes), on an M5Stack Cardputer ADV (ESP32-S3). The netlist is not translated
into C or re-synthesised: its TapeOut bytes are embedded unchanged, decoded at boot
and evaluated cell by cell at every 5 ms tick, as in the web demo and the EVM
contracts.

## Online simulator

Without the device, the
[handheld simulator](https://brucelanlan.github.io/c3s-reflex-circuits/sim/)
([`docs/sim/index.html`](sim/index.html)) runs this firmware in the browser.
`scripts/build_sim.py` compiles `lib/c3s_core` and `firmware/wasm/sim.c` to
`docs/sim/c3s_core.wasm` and records the compiler, the flags and the SHA-256 of the
module and of every source in `docs/sim/build.json`. The netlist decoder, the
evaluator, the encoding, the looming geometry and the boot self-test are the
firmware's C code; the screen, the keys and the tick loop are JavaScript that follows
`src/main.cpp`, and `atan` and `tan` come from the browser's `Math`.

`tests/test_firmware.py` runs the committed module in Node through
`firmware/wasm/sim_cli.mjs` with the same assertions as the native build below, and
checks that `build.json` matches the module and the current sources. The module is not
rebuilt by `scripts/verify.sh`: another clang version may emit different bytes for the
same sources.

## Build and flash

Requires [PlatformIO Core](https://docs.platformio.org/page/core/installation.html).

```sh
pip install platformio
pio run -d firmware/cardputer                 # build
pio run -d firmware/cardputer -t upload       # build and flash over USB-C
pio device monitor -b 115200                  # one log line per tick
```

If the upload cannot connect, hold G0 while plugging in the USB cable to enter the
download mode, then upload again.

Pinned: `espressif32` 6.9.0 (Arduino-ESP32 2.0.17), M5Cardputer 1.1.1, M5Unified
0.2.22, M5GFX 0.2.29. A build on 2026-09-15 used 48,700 bytes of RAM (14.9 %) and
532,769 bytes of flash (15.9 %). The prefix maps in `build_flags` keep the build
machine's directories out of the image.

Without PlatformIO, write the image attached to the
[v0.2.0 release](https://github.com/BruceLanLan/c3s-reflex-circuits/releases/tag/v0.2.0)
(bootloader, partition table, boot selector and application merged at offset 0):

```sh
pip install esptool
python -m esptool --chip esp32s3 write_flash 0x0 c3s-escape-core-cardputer-adv-v0.2.0.bin
```

## What the device checks at boot

| Check | Passes when |
| --- | --- |
| SHA-256 of both netlists | the embedded core and policy bytes hash to the manifests' `netlist_sha256` |
| Reference episodes | for the web demo's three episodes, every tick's sensory bits, motor command and latch state equal the ticks `scripts/build_demo.py` computed in Python from the same samples |
| Core against policy at rest | on all 65,536 sensory patterns, standing and not standing, with every latch clear (131,072 rows), the core's motor command and next state follow from the policy's two pathway bits: GF → short-mode takeoff and a full refractory timer, parallel only → start raising the wings, neither or not standing → hold |
| Live geometry (informational) | the device's own looming geometry reproduces the embedded samples to 1e-6 and lands in the same bins |

The result is shown on screen, kept as a PASS/FAIL badge in the title bar and
printed on the serial port. A netlist that cannot be decoded cannot be run. The
self-test is a check of the build on the device; the full-domain equivalence is
established on the host.

## What the host test establishes

`tests/test_firmware.py` compiles `firmware/cardputer/lib/c3s_core` with the host C
compiler (the test is skipped without one) and checks:

- the generated data files are exactly what `scripts/build_firmware.py` writes from
  the demo's data block;
- the core's step relation on all 8,388,608 (input, state) rows, and the policy's
  truth table on all 65,536 inputs, equal `c3s.exhaust.step_table` on the manifests'
  bytes;
- the on-boot self-test passes, with hashes equal to the manifests';
- the firmware's own bit-sliced digest of the whole step relation (`digest core` and
  `digest policy`, a SHA-256 chain over the output and next-state bit planes of every
  row, in blocks of 256 rows) equals the chain in the EVM fixtures, which the
  Solidity evaluator is checked against — so one 32-byte value ties the device's C,
  the browser's WebAssembly, Python and the EVM together;
- the sensory encoding equals `loom.encode_features` at, and one ulp either side of,
  every bin edge and the edges of the binocular overlap;
- an episode run tick by tick as the firmware runs it (geometry, encoding, policy and
  core) matches the Python engine for all 77 train and holdout stimuli.

The same C files are compiled for the device, so these checks cover the evaluator,
encoding and geometry code that runs on it. They do not cover the device compiler or
its maths library; the on-boot self-test does that for the embedded episodes.

## Using it

The screen is 240 × 135:

- **Left:** the fly seen from above, with a disc approaching from the chosen azimuth.
  The disc is drawn at a fixed distance, so its radius shows the current angular
  size. An eye lights when its sensory bits are non-zero, the wings rise with the
  raise counter, and after a takeoff the fly leaves the disc; in short mode, without
  raising its wings. The line underneath names the motor command.
- **Right:** the four 4-bit sensory fields (most significant bit first, with the bin
  number), the standing bit, the policy's GF and parallel lamps, the six latches
  (raise counter and refractory timer, as values during the tick) and the motor
  command.
- **Bottom:** l/v, azimuth, tick, angular size and the slow-motion factor.

| Key | Action |
| --- | --- |
| Enter, Space, G0 | launch a loom with the current l/v and azimuth |
| `;` `.` | l/v up / down (10–140 ms) |
| `,` `/` | azimuth left / right in 10° steps (−90° to 90°) |
| `1`–`4` | slow motion 1×, 4×, 10× (default), 40× |
| `p`, `n` | pause; one tick while paused |
| `a` | auto demo (on at boot): random l/v and azimuth every few seconds |
| `m` | sound on takeoff |
| `t`, `h` | self-test report, key help |
| `d` | digest the whole 8,388,608-row step relation on the device and show it with the time it took |

Each serial line is one tick, for example:

```
tick  73 size  46.905 speed    453.77 x 10079 gf 0 par 1 latch 04 motor 3 long-mode takeoff
```

`x` is the core's 17 input bits in hexadecimal (standing is bit 16), `latch` the
latch values during the tick (bits 0–2 raise counter, bits 3–5 refractory timer, least
significant first).

## Layout

```
firmware/cardputer/platformio.ini
firmware/cardputer/src/main.cpp            display, keyboard, speaker and tick scheduling
firmware/cardputer/lib/c3s_core/c3s_core.* portable C99: netlist decoder and evaluator,
                                           SHA-256, looming geometry, encoding, episode
                                           runner, self-test
firmware/cardputer/lib/c3s_core/c3s_data.* generated by scripts/build_firmware.py
firmware/host/c3s_host.c                   host driver for tests/test_firmware.py
firmware/wasm/sim.c                        WebAssembly exports for the online simulator
firmware/wasm/include/                     math.h and string.h for the freestanding build
firmware/wasm/sim_cli.mjs                  Node driver for tests/test_firmware.py
docs/sim/                                  the simulator page, c3s_core.wasm and build.json
```

After a rebuild of the circuits, run `python scripts/build_demo.py` and then
`python scripts/build_firmware.py`; `scripts/verify.sh` checks that both reproduce
byte for byte. Only `main.cpp` and `platformio.ini` are specific to this device.

## Limitations

- The drawing is driven by the circuit's inputs and outputs; the fly is always
  standing, and the stimulus is the teacher's constant-velocity looming disc, not the
  web demo's voxel world.
- Ticks are scheduled from `millis()`. The circuit's behaviour depends only on the tick
  sequence, not on wall-clock time; at 1× the screen redraws every few ticks.
- Live geometry is computed with the device's double-precision maths library. A value
  one ulp either side of a bin edge can land in the neighbouring bin, so a live episode
  can differ from Python by a tick near an edge; the tick-exact comparison on the
  device uses the embedded samples.
- A device that ran other firmware before may log
  `esp_core_dump_flash: Incorrect size of core dump image` once at boot. It refers to
  data left in the core-dump partition and does not affect the firmware.
- First run on hardware, 2026-09-15, Cardputer ADV (ESP32-S3 revision v0.2): self-test
  PASS in 4,546 ms, both hashes ok, reference episodes 3/3, core against policy at
  rest 131,072/131,072, live geometry 3/3.
- Nothing here adds a claim about the fly. See [LIMITATIONS.md](LIMITATIONS.md).
