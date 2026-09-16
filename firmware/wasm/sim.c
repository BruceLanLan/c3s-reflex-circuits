/* WebAssembly glue for the online simulator (docs/sim). It links the firmware's own
 * lib/c3s_core, so the page runs the decoder, evaluator, encoding, looming geometry and
 * boot self-test that the device runs; only the drawing is redone in JavaScript.
 * Every export takes and returns scalars, so the page never reads C struct layouts. */
#include "c3s_core.h"
#include "c3s_data.h"

#define EXPORT(name) __attribute__((export_name(#name)))

static c3s_prog core, policy;
static c3s_report report;
static c3s_run run;
static c3s_tick_info last;
static int passed;

EXPORT(sim_boot)
int sim_boot(void) {
  passed = c3s_self_test(&core, &policy, &report);
  return passed;
}

/* 0 pass, 1 core decode, 2 policy decode, 3 core hash ok, 4 policy hash ok, 5 episodes,
 * 6 episodes ok, 7 geometry ok, 8 rest rows, 9 rest ok, 10 bad episode, 11 bad tick */
EXPORT(sim_report)
int sim_report(int field) {
  switch (field) {
    case 0: return passed;
    case 1: return report.core_decode;
    case 2: return report.policy_decode;
    case 3: return report.core_hash_ok;
    case 4: return report.policy_hash_ok;
    case 5: return report.episodes;
    case 6: return report.episodes_ok;
    case 7: return report.geometry_ok;
    case 8: return (int)report.rest_rows;
    case 9: return (int)report.rest_ok;
    case 10: return report.bad_episode;
    case 11: return report.bad_tick;
    default: return -1;
  }
}

EXPORT(sim_sha256)
const char *sim_sha256(int which) { return which == 0 ? report.core_sha256 : report.policy_sha256; }

/* SHA-256 chain over the whole step relation; "" when the domain is unsupported. */
EXPORT(sim_digest)
const char *sim_digest(int which) {
  static char hex[65];
  uint8_t d[32];
  if (!c3s_domain_digest(which == 0 ? &core : &policy, d)) return "";
  c3s_hex(d, 32, hex);
  return hex;
}

/* 0 core NAND, 1 core LATCH, 2 core bytes, 3 core depth, 4 policy NAND, 5 policy bytes,
 * 6 sensory bits, 7 raise-counter bits, 8 reference episodes, 9 tick ms */
EXPORT(sim_info)
int sim_info(int field) {
  switch (field) {
    case 0: return C3S_CORE_NAND;
    case 1: return C3S_CORE_LATCHES;
    case 2: return C3S_CORE_BYTES;
    case 3: return C3S_CORE_DEPTH;
    case 4: return C3S_POLICY_NAND;
    case 5: return C3S_POLICY_BYTES;
    case 6: return C3S_FEATURES;
    case 7: return C3S_RAISE_BITS;
    case 8: return C3S_EPISODES;
    case 9: return (int)C3S_TICK_MS;
    default: return -1;
  }
}

EXPORT(sim_name)
const char *sim_name(int which) { return which == 0 ? C3S_CORE_NAME : C3S_POLICY_NAME; }

EXPORT(sim_motor_name)
const char *sim_motor_name(int motor) { return c3s_motor_names[motor & 3]; }

/* which: 0 core, 1 policy. 0 n_in, 1 n_state */
EXPORT(sim_prog)
int sim_prog(int which, int field) {
  const c3s_prog *p = which == 0 ? &core : &policy;
  return field == 0 ? p->n_in : p->n_state;
}

/* One tick of a netlist: outputs in the low byte, next state in the next byte. */
EXPORT(sim_step)
int sim_step(int which, int inputs, int state) {
  uint32_t nxt;
  uint32_t out = c3s_step(which == 0 ? &core : &policy, (uint32_t)inputs, (uint32_t)state, &nxt);
  return (int)(out | nxt << 8);
}

/* The circuit's own structure and its live values, so a view can show the gates
 * switching instead of a picture of a fly. Signals are numbered as in the netlist:
 * 0 and 1 are the constants, then the inputs, then one per cell. */
static uint8_t trace[C3S_MAX_SIGNALS];

/* One tick that also records every signal; returns as sim_step. */
EXPORT(sim_trace)
int sim_trace(int which, int inputs, int state) {
  uint32_t nxt;
  uint32_t out = c3s_step_trace(which == 0 ? &core : &policy, (uint32_t)inputs, (uint32_t)state, &nxt, trace);
  return (int)(out | nxt << 8);
}

EXPORT(sim_signal)
int sim_signal(int signal) { return signal >= 0 && signal < C3S_MAX_SIGNALS ? trace[signal] : 0; }

/* field: 0 signals, 1 cells, 2 the first cell's signal number. */
EXPORT(sim_shape)
int sim_shape(int which, int field) {
  const c3s_prog *p = which == 0 ? &core : &policy;
  return field == 0 ? p->n_signals : field == 1 ? p->n_cells : 2 + p->n_in;
}

/* One cell: field 0 its first operand signal, 1 its second, 2 whether it is a latch. */
EXPORT(sim_cell)
int sim_cell(int which, int i, int field) {
  const c3s_prog *p = which == 0 ? &core : &policy;
  if (i < 0 || i >= p->n_cells) return -1;
  return field == 0 ? (int)p->cells[i].a : field == 1 ? (int)p->cells[i].b : p->cells[i].latch;
}

EXPORT(sim_encode)
int sim_encode(double theta_deg, double dtheta_dps, double azimuth_deg) {
  return (int)c3s_encode(theta_deg, dtheta_dps, azimuth_deg);
}

EXPORT(sim_start)
void sim_start(double l_over_v_ms, double azimuth_deg) { c3s_run_start(&run, l_over_v_ms, azimuth_deg); }

EXPORT(sim_tick)
int sim_tick(void) { return c3s_run_tick(&run, &core, &policy, &last); }

/* 0 tick, 1 theta, 2 dtheta, 3 sensory bits, 4 pathways, 5 latch state, 6 motor, 7 next state */
EXPORT(sim_last)
double sim_last(int field) {
  switch (field) {
    case 0: return last.tick;
    case 1: return last.theta_deg;
    case 2: return last.dtheta_dps;
    case 3: return last.x;
    case 4: return last.pathways;
    case 5: return last.state;
    case 6: return last.motor;
    case 7: return last.next_state;
    default: return -1;
  }
}
