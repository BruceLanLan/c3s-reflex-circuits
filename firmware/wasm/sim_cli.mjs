// Command-line driver for the WebAssembly build (docs/sim/c3s_core.wasm). It mirrors
// firmware/host/c3s_host.c, so tests/test_firmware.py checks both builds with the same
// assertions.
//
//   node firmware/wasm/sim_cli.mjs selftest | table core|policy FILE | run LV AZ | encode
import { readFileSync, writeFileSync } from "node:fs";

const bytes = readFileSync(new URL("../../docs/sim/c3s_core.wasm", import.meta.url));
const { instance } = await WebAssembly.instantiate(bytes, { env: { atan: Math.atan, tan: Math.tan } });
const w = instance.exports;

function cstr(ptr) {
  const mem = new Uint8Array(w.memory.buffer);
  let end = ptr;
  while (mem[end]) end++;
  return new TextDecoder().decode(mem.subarray(ptr, end));
}

const pass = w.sim_boot();
const report = (i) => w.sim_report(i);
const [cmd, ...args] = process.argv.slice(2);

if (cmd === "selftest") {
  console.log(`decode ${report(1)} ${report(2)}`);
  console.log(`core_sha256 ${cstr(w.sim_sha256(0))}\npolicy_sha256 ${cstr(w.sim_sha256(1))}`);
  console.log(`hashes_ok ${report(3)} ${report(4)}`);
  console.log(`episodes ${report(6)}/${report(5)}`);
  console.log(`geometry ${report(7)}/${report(5)}`);
  console.log(`rest ${report(9)}/${report(8)}`);
  console.log(`pass ${pass}`);
  process.exit(pass ? 0 : 1);
}
if (report(1) !== 0 || report(2) !== 0) {
  console.error(`decode failed: core ${report(1)}, policy ${report(2)}`);
  process.exit(2);
}
if (cmd === "table" && args.length === 2) {
  const which = args[0] === "core" ? 0 : 1;
  const nIn = w.sim_prog(which, 0);
  const rows = 2 ** (nIn + w.sim_prog(which, 1));
  const mask = 2 ** nIn - 1;
  const out = new Uint8Array(rows * 2);
  for (let row = 0; row < rows; row++) {
    const v = w.sim_step(which, row & mask, Math.floor(row / 2 ** nIn));
    out[2 * row] = v & 255;
    out[2 * row + 1] = (v >> 8) & 255;
  }
  writeFileSync(args[1], out);
} else if (cmd === "run" && args.length === 2) {
  w.sim_start(Number(args[0]), Number(args[1]));
  const lines = [];
  while (w.sim_tick()) lines.push(Array.from({ length: 8 }, (_, i) => String(w.sim_last(i))).join(" "));
  if (lines.length) console.log(lines.join("\n"));
} else if (cmd === "encode") {
  const nums = readFileSync(0, "utf8").split(/\s+/).filter(Boolean).map(Number);
  const lines = [];
  for (let i = 0; i + 2 < nums.length; i += 3) lines.push(w.sim_encode(nums[i], nums[i + 1], nums[i + 2]));
  console.log(lines.join("\n"));
} else {
  console.error("usage: sim_cli.mjs selftest | table core|policy FILE | run LV AZ | encode");
  process.exit(2);
}
