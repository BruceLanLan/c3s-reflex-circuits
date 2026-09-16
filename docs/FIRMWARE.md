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

Beside the device the page draws the cells themselves, in two views. **Connections**
is the measured graph: all 311 LC4 and LPLC2 cells that synapse onto the two giant
fibers, each line as thick as its synapse count, in a layout that carries no spatial
meaning. **Real shapes** loads `docs/sim/skeletons.bin` (323 kB, built by
`scripts/build_sim_skeletons.py` from the release's published SWC skeletons) and
draws 26 of those cells where the release says they are. Colour follows the model's
own mapping and nothing finer — the speed field lights that eye's LC4 population,
the size field its LPLC2 population, and the giant fibers light with the GF pathway —
because the circuit sees four 4-bit fields, not individual cells.

The page carries a second evaluator, off by default. Switching to **On BNB Smart
Chain** sends one read-only `eth_call` per row whose state override installs the
compiled `NandMachine` runtime at a throwaway address, so a public node executes the
same netlist: nothing is deployed, and no wallet, private key or gas is involved.
`scripts/build_sim_onchain.py` publishes what that call needs — the runtime bytecode,
the `evaluate` selector and the core's netlist bytes — beside the page in
`docs/sim/onchain.json`. Four real rows of a looming episode are checked (a quiet
tick, the first raise, the fourth raise, and the long-mode takeoff that loads the
refractory timer), each reported as agreeing with the WebAssembly module or
differing. It is opt-in because choosing it sends the netlist bytes, which are
already public, and the visitor's IP address to the chosen endpoint;
`scripts/verify_onchain.py` does the same from the command line.

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
532,769 bytes of flash (15.9 %); with the Wi-Fi link of W11 (below) a build on
2026-09-16 used 159,420 bytes of RAM (48.7 %) and 1,118,993 bytes of flash (33.5 %) —
the radio stack is most of that, and it is linked in whether or not a network is
stored. The prefix maps in `build_flags` keep the build machine's directories out of
the image.

Without PlatformIO, write the image attached to the
[v0.2.0 release](https://github.com/BruceLanLan/c3s-reflex-circuits/releases/tag/v0.2.0)
(bootloader, partition table, boot selector and application merged at offset 0):

```sh
pip install esptool
python -m esptool --chip esp32s3 write_flash 0x0 c3s-escape-core-cardputer-adv-v0.2.0.bin
```

Writing an image is safe; **reading one back off a device that has joined a Wi-Fi
network is not** — see "A provisioned device's flash is a secret" below.

## A provisioned device's flash is a secret

Until W11 this device held nothing: no credential, no key, no state that outlived a
reset. That changed the moment it could reach the console over Wi-Fi. A device that
has been through the **Network** page keeps, in its NVS partition:

- the name of the Wi-Fi network and **its password**;
- the address of the boundary console;
- a **device token**, which is enough to write a person's `confirm` for a call that
  console is waiting on.

So, plainly:

- **Never publish a flash dump of a provisioned device**, and never attach one to an
  issue, a release or a message. It is not an image of this firmware; it is a copy of
  a household's Wi-Fi password and a key to a person's approvals. (There is precedent
  here: the very first backup taken from this device was flagged as possibly holding
  the Wi-Fi credentials of the stock firmware that came on it.)
- **A released image is built from an unprovisioned tree.** Credentials never enter a
  build in the first place — `scripts/check_credentials.py` runs before every
  `pio run` (`extra_scripts` in `platformio.ini`) and fails the build on a Wi-Fi name,
  a password or a token in a source file or a build flag — so `firmware.bin` from a
  clean checkout is safe to publish, and what must never be published is a dump read
  back *off a device* (`esptool read_flash`).
- **Forgetting really forgets.** `WiFi.persistent(false)` keeps the radio driver from
  writing a second copy of the credentials into its own NVS namespace, so *Forget
  network* on the Network page removes the only copy; *Unpair* removes the token. A
  device you hand on, sell or send for repair should have both.
- **Nothing is printed.** The password and the token are never written to the serial
  log, never sent anywhere but the router and the console, and never shown on screen
  (the password is masked as it is typed).

## Over Wi-Fi

USB is the default and the stronger arrangement: over a cable the relay runs inside the
console's own process, there is no token to steal and no address to reach. Wi-Fi is a
convenience — the device can sit on a desk across the room, or in a pocket — and it is
built so that the one property that matters cannot be lost: **nothing on the network can
press this key.**

- The device **never listens**. There is no server on it, no subscription, nothing
  inbound that makes it write. It connects out to the console, polls `GET
  /api/device/frame` once a second for the same frame the cable carries, and posts to
  `/api/tool` only when a finger presses a key.
- The device **says who it is**: a device token, paired once, kept in NVS. The console
  accepts it on `/api/tool` alone, for `confirm`, `confirm_b` or `blocked`, for an agent
  that is in `pending[]` at that moment, with the two-digit matching code (I-2) of the
  call on the screen — and for nothing else. Not `/api/policy`, not `/api/stop-all`, not
  an agent nobody is waiting on. It is exactly as powerful as the cable, and no more.
- Lifting a block and writing `heartbeat` are **refused over the network**, on purpose. A
  block is lifted on the console or over USB; the heartbeat over Wi-Fi is the device's
  *presence*, which the console works out from the polls themselves. A dead man's switch
  a network write could hold open would not be a dead man's switch.
- **No Wi-Fi never means "the person is there."** When the radio drops, the screen says
  so and the polls stop; with a `heartbeat_ticks` halt installed, every agent stops
  within a few ticks. The way back is a person's confirm, as it is when the cable is
  pulled.
- When **both** links are live the cable wins, and the Agent page's header says which
  link the frame on the screen came in on (`host ok USB` / `host ok Wi-Fi`).

### Pairing, by matching four digits

The same idea as the approval codes: approving requires reading the same screen the
request is on.

```sh
# on the computer, where the console runs
c3s pair-device                       # (W1's CLI; today: python -m cardputer_relay open)
# on the device: Network > 4 pair by code. It shows four digits.
c3s pair-device --confirm 0263        # (today: python -m cardputer_relay confirm 0263)
```

The console generates the token inside an open window, hands it to the one device that
claims that window, and derives four digits **from the token itself** — so digits that
match mean both sides hold the same token. The console does not print them: the device
shows them and the console asks for them, because a code both sides display can be
confirmed without looking at the device. Until a person confirms, the token is accepted
for nothing at all; a wrong code cancels the pairing rather than asking again; a second
device that tries to take an open window is refused, and that refusal is the alarm.

Paired devices live in `~/.c3s-circuit-agent/devices.json` (mode 600), which holds each
token's SHA-256 and never a token. `python -m cardputer_relay status` lists them;
`python -m cardputer_relay forget <device-id>` drops one.

### The Network page

| Key | Action |
| --- | --- |
| `1` | scan, and pick a network from the list |
| `2` | type a network's name by hand (a hidden network) |
| `3` | the console's address, `<ip>:<port>` — the one it prints at startup |
| `4` | pair with the console: shows the four digits |
| `5` | forget the network (and its password) |
| `6` | unpair (forget the device token) |
| DEL | back; on an empty entry line, out of the entry |

The console has to be reachable from the device, which means starting it bound to every
interface (`CONSOLE_HOST=0.0.0.0`); on loopback only the cable works. The console answers
only to its own names, and its own LAN addresses count as its own names when it is bound
that way.

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
  the browser's WebAssembly, Python and the EVM together. Measured on the Cardputer
  ADV this firmware was developed on: all 8,388,608 rows in **10,218 ms**, digest
  `477aee38…fba1e1ea`, equal to the fixtures' chain; the same work takes about 1.1 s
  on the development Mac;
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
| `d` | digest the whole 8,388,608-row step relation on the device and show it with the time it took; sending `d` over the serial port does the same and prints the result |
| `8` | the Network page: reach the console over Wi-Fi instead of the cable (above) |
| `w` | the circuit as itself: one dot per cell, lines to the two signals each NAND reads, laid out left to right by logic depth and turning. Each dot's brightness is that gate's value on the tick being shown, recomputed from the netlist every frame — the geometry is the circuit's, not a picture of a fly |

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
firmware/cardputer/src/c3s_net.{h,cpp}     Wi-Fi, NVS, the outbound HTTP client, pairing
firmware/cardputer/pio_check_credentials.py the pre-build credential check (below)
firmware/cardputer/lib/c3s_core/c3s_core.* portable C99: netlist decoder and evaluator,
                                           SHA-256, looming geometry, encoding, episode
                                           runner, self-test
firmware/cardputer/lib/c3s_core/c3s_data.* generated by scripts/build_firmware.py
scripts/check_credentials.py               refuses a build that carries a network,
                                           a password or a token
firmware/host/c3s_host.c                   host driver for tests/test_firmware.py
firmware/wasm/sim.c                        WebAssembly exports for the online simulator
firmware/wasm/include/                     math.h and string.h for the freestanding build
firmware/wasm/sim_cli.mjs                  Node driver for tests/test_firmware.py
docs/sim/                                  the simulator page, c3s_core.wasm and build.json
```

After a rebuild of the circuits, run `python scripts/build_demo.py` and then
`python scripts/build_firmware.py`; `scripts/verify.sh` checks that both reproduce
byte for byte. Only `main.cpp`, `c3s_net.*` and `platformio.ini` are specific to this device.

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
- Over Wi-Fi the device is no longer credential-free: it holds the network's password
  and a device token, and a person's `confirm` reaches the console over HTTP on the
  local network instead of down a cable. The token is limited to what the cable could
  do, and the console records which device wrote each bit, but the honest summary is
  that Wi-Fi trades a cable's unreachability for reach. USB remains the default.
- The Wi-Fi link is plain HTTP on the local network, exactly as the console's own page
  is: the frame the device polls (what is waiting for a person, and why) and its writes
  are readable by anything that can already read the console's own traffic. The console
  answers only to its own names, and the token is a header, not a URL — but this is a
  home LAN's protection, not a transport's.
- Nothing here adds a claim about the fly. See [LIMITATIONS.md](LIMITATIONS.md).
