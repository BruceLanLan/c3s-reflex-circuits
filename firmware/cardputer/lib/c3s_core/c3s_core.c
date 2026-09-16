#include "c3s_core.h"

#include <math.h>
#include <string.h>

#include "c3s_data.h"

/* The geometry must round like the Python it mirrors: no fused multiply-add. */
#ifdef __clang__
#pragma STDC FP_CONTRACT OFF
#endif

#define OP_NAND 0x00
#define OP_LATCH 0x01
#define OP_REF 0x02

static uint32_t u24(const uint8_t *b) { return (uint32_t)b[0] << 16 | (uint32_t)b[1] << 8 | (uint32_t)b[2]; }

int c3s_decode(c3s_prog *p, const uint8_t *bytes, size_t len, int n_in, int n_out) {
  size_t i = 0;
  uint32_t s;
  int k;
  memset(p, 0, sizeof *p);
  if (n_in < 0 || n_in > C3S_MAX_INPUTS || n_out < 1 || n_out > 32) return C3S_ERR_PORTS;
  p->n_in = n_in;
  p->n_out = n_out;
  s = 2 + (uint32_t)n_in;
  while (i < len) {
    c3s_cell *c;
    if (p->n_cells == C3S_MAX_CELLS) return C3S_ERR_TOO_LARGE;
    c = &p->cells[p->n_cells];
    if (bytes[i] == OP_NAND) {
      if (len - i < 7) return C3S_ERR_TRUNCATED;
      c->a = u24(bytes + i + 1);
      c->b = u24(bytes + i + 4);
      if (c->a >= s || c->b >= s) return C3S_ERR_SIGNAL;
      i += 7;
    } else if (bytes[i] == OP_LATCH) {
      if (len - i < 4) return C3S_ERR_TRUNCATED;
      if (p->n_state == C3S_MAX_STATE) return C3S_ERR_TOO_LARGE;
      c->a = u24(bytes + i + 1); /* d may name a later signal */
      c->b = (uint32_t)p->n_state;
      c->latch = 1;
      p->latch_d[p->n_state++] = c->a;
      i += 4;
    } else if (bytes[i] == OP_REF) {
      return C3S_ERR_REF;
    } else {
      return C3S_ERR_OPCODE;
    }
    p->n_cells++;
    s++;
  }
  p->n_signals = (int)s;
  if (p->n_cells < n_out) return C3S_ERR_PORTS;
  for (k = 0; k < p->n_state; k++)
    if (p->latch_d[k] >= s) return C3S_ERR_SIGNAL;
  return C3S_OK;
}

uint32_t c3s_step(const c3s_prog *p, uint32_t inputs, uint32_t state, uint32_t *next_state) {
  uint8_t s[C3S_MAX_SIGNALS];
  uint32_t out = 0, nxt = 0;
  int i, k = 2 + p->n_in;
  s[0] = 0;
  s[1] = 1;
  for (i = 0; i < p->n_in; i++) s[2 + i] = (uint8_t)((inputs >> i) & 1u);
  for (i = 0; i < p->n_cells; i++, k++) {
    const c3s_cell *c = &p->cells[i];
    s[k] = c->latch ? (uint8_t)((state >> c->b) & 1u) : (uint8_t)!(s[c->a] & s[c->b]);
  }
  for (i = 0; i < p->n_out; i++) out |= (uint32_t)s[p->n_signals - p->n_out + i] << i;
  for (i = 0; i < p->n_state; i++) nxt |= (uint32_t)s[p->latch_d[i]] << i;
  if (next_state) *next_state = nxt;
  return out;
}

/* ---- SHA-256 (FIPS 180-4) ---------------------------------------------------- */

static const uint32_t SHA_K[64] = {
    0x428a2f98, 0x71374491, 0xb5c0fbcf, 0xe9b5dba5, 0x3956c25b, 0x59f111f1, 0x923f82a4, 0xab1c5ed5,
    0xd807aa98, 0x12835b01, 0x243185be, 0x550c7dc3, 0x72be5d74, 0x80deb1fe, 0x9bdc06a7, 0xc19bf174,
    0xe49b69c1, 0xefbe4786, 0x0fc19dc6, 0x240ca1cc, 0x2de92c6f, 0x4a7484aa, 0x5cb0a9dc, 0x76f988da,
    0x983e5152, 0xa831c66d, 0xb00327c8, 0xbf597fc7, 0xc6e00bf3, 0xd5a79147, 0x06ca6351, 0x14292967,
    0x27b70a85, 0x2e1b2138, 0x4d2c6dfc, 0x53380d13, 0x650a7354, 0x766a0abb, 0x81c2c92e, 0x92722c85,
    0xa2bfe8a1, 0xa81a664b, 0xc24b8b70, 0xc76c51a3, 0xd192e819, 0xd6990624, 0xf40e3585, 0x106aa070,
    0x19a4c116, 0x1e376c08, 0x2748774c, 0x34b0bcb5, 0x391c0cb3, 0x4ed8aa4a, 0x5b9cca4f, 0x682e6ff3,
    0x748f82ee, 0x78a5636f, 0x84c87814, 0x8cc70208, 0x90befffa, 0xa4506ceb, 0xbef9a3f7, 0xc67178f2};

#define ROTR(x, n) (((x) >> (n)) | ((x) << (32 - (n))))

static void sha256_block(uint32_t h[8], const uint8_t *m) {
  uint32_t w[64], a, b, c, d, e, f, g, hh, t1, t2;
  int i;
  for (i = 0; i < 16; i++) w[i] = u24(m + 4 * i) << 8 | m[4 * i + 3];
  for (i = 16; i < 64; i++)
    w[i] = (ROTR(w[i - 2], 17) ^ ROTR(w[i - 2], 19) ^ (w[i - 2] >> 10)) + w[i - 7] +
           (ROTR(w[i - 15], 7) ^ ROTR(w[i - 15], 18) ^ (w[i - 15] >> 3)) + w[i - 16];
  a = h[0], b = h[1], c = h[2], d = h[3], e = h[4], f = h[5], g = h[6], hh = h[7];
  for (i = 0; i < 64; i++) {
    t1 = hh + (ROTR(e, 6) ^ ROTR(e, 11) ^ ROTR(e, 25)) + ((e & f) ^ (~e & g)) + SHA_K[i] + w[i];
    t2 = (ROTR(a, 2) ^ ROTR(a, 13) ^ ROTR(a, 22)) + ((a & b) ^ (a & c) ^ (b & c));
    hh = g, g = f, f = e, e = d + t1, d = c, c = b, b = a, a = t1 + t2;
  }
  h[0] += a, h[1] += b, h[2] += c, h[3] += d, h[4] += e, h[5] += f, h[6] += g, h[7] += hh;
}

void c3s_sha256(const uint8_t *data, size_t len, uint8_t digest[32]) {
  uint32_t h[8] = {0x6a09e667, 0xbb67ae85, 0x3c6ef372, 0xa54ff53a, 0x510e527f, 0x9b05688c, 0x1f83d9ab, 0x5be0cd19};
  uint8_t tail[128];
  uint64_t bits = (uint64_t)len * 8;
  size_t i, rem, n;
  int j;
  for (i = 0; len - i >= 64; i += 64) sha256_block(h, data + i);
  rem = len - i;
  memset(tail, 0, sizeof tail);
  memcpy(tail, data + i, rem);
  tail[rem] = 0x80;
  n = rem + 9 <= 64 ? 64 : 128;
  for (j = 0; j < 8; j++) tail[n - 1 - j] = (uint8_t)(bits >> (8 * j));
  sha256_block(h, tail);
  if (n == 128) sha256_block(h, tail + 64);
  for (j = 0; j < 32; j++) digest[j] = (uint8_t)(h[j / 4] >> (24 - 8 * (j % 4)));
}

void c3s_hex(const uint8_t *bytes, size_t len, char *out) {
  static const char digits[] = "0123456789abcdef";
  size_t i;
  for (i = 0; i < len; i++) {
    out[2 * i] = digits[bytes[i] >> 4];
    out[2 * i + 1] = digits[bytes[i] & 15];
  }
  out[2 * len] = '\0';
}

/* Bit-sliced whole-domain digest. One uint32 per signal holds 32 consecutive rows,
 * eight such slices fill the 256-row blocks that the SHA-256 chain hashes. Within a
 * block, plane byte 31 - (j >> 3), bit j & 7, is row j: a 32-byte big-endian word
 * whose bit j is row base + j, as in the Python and Solidity evaluators. */
uint32_t c3s_domain_digest(const c3s_prog *p, uint8_t digest[32]) {
  static const uint32_t slice[5] = {0xaaaaaaaau, 0xccccccccu, 0xf0f0f0f0u, 0xff00ff00u, 0xffff0000u};
  static uint32_t sig[C3S_MAX_SIGNALS];
  uint8_t buf[32 + 64 * 32];
  const int bits = p->n_in + p->n_state, nplanes = p->n_out + p->n_state;
  uint32_t rows, base, row, w;
  int i, k, m, t;
  if (bits < 8 || bits > 24 || p->n_in < 5 || nplanes > 64) return 0;
  rows = 1u << bits;
  memset(digest, 0, 32);
  sig[0] = 0;
  sig[1] = 0xffffffffu;
  for (base = 0; base < rows; base += 256) {
    memcpy(buf, digest, 32);
    memset(buf + 32, 0, (size_t)nplanes * 32);
    for (t = 0; t < 8; t++) {
      row = base + (uint32_t)t * 32;
      for (i = 0; i < p->n_in; i++)
        sig[2 + i] = i < 5 ? slice[i] : (((row >> i) & 1u) ? 0xffffffffu : 0u);
      for (i = 0, k = 2 + p->n_in; i < p->n_cells; i++, k++) {
        const c3s_cell *c = &p->cells[i];
        sig[k] = c->latch ? ((((row >> (p->n_in + c->b)) & 1u)) ? 0xffffffffu : 0u) : ~(sig[c->a] & sig[c->b]);
      }
      for (i = 0; i < nplanes; i++) {
        w = i < p->n_out ? sig[p->n_signals - p->n_out + i] : sig[p->latch_d[i - p->n_out]];
        for (m = 0; m < 4; m++) buf[32 + i * 32 + (31 - 4 * t - m)] = (uint8_t)((w >> (8 * m)) & 0xffu);
      }
    }
    c3s_sha256(buf, (size_t)(32 + nplanes * 32), digest);
  }
  return rows;
}

/* ---- Looming stimulus and encoding ------------------------------------------ */

/* CPython's math.degrees and math.radians multiply by these two constants. */
#define DEG_PER_RAD (180.0 / 3.14159265358979323846)
#define RAD_PER_DEG (3.14159265358979323846 / 180.0)

double c3s_theta_deg(double l_over_v_s, double time_to_contact_s) {
  double t = time_to_contact_s > 1e-9 ? time_to_contact_s : 1e-9;
  return 2.0 * atan(l_over_v_s / t) * DEG_PER_RAD;
}

double c3s_dtheta_dps(double l_over_v_s, double time_to_contact_s) {
  double a = l_over_v_s, t = time_to_contact_s > 1e-9 ? time_to_contact_s : 1e-9;
  return 2.0 * a / (t * t + a * a) * DEG_PER_RAD;
}

void c3s_loom_start(c3s_loom *l, double l_over_v_ms) {
  l->a = l_over_v_ms / 1000.0;
  l->t = l->a / tan(C3S_START_DEG * RAD_PER_DEG / 2.0);
  l->t_end = l->a / tan(C3S_END_DEG * RAD_PER_DEG / 2.0);
  l->dt = C3S_TICK_MS / 1000.0;
  l->tick = 0;
}

int c3s_loom_next(c3s_loom *l, double *theta_deg, double *dtheta_dps) {
  if (!(l->t > l->t_end)) return 0;
  /* The onset sample is the start size by definition, as in the Python. */
  *theta_deg = l->tick == 0 ? C3S_START_DEG : c3s_theta_deg(l->a, l->t);
  *dtheta_dps = c3s_dtheta_dps(l->a, l->t);
  l->t -= l->dt;
  l->tick++;
  return 1;
}

void c3s_visible(double azimuth_deg, int *see_left, int *see_right) {
  *see_left = azimuth_deg <= C3S_OVERLAP_DEG;
  *see_right = azimuth_deg >= -C3S_OVERLAP_DEG;
}

/* Number of edges <= x (bisect_right), capped at the top bin. */
static int bin_of(const double *edges, double x) {
  int k = 0;
  while (k < C3S_LEVELS && edges[k] <= x) k++;
  return k < C3S_LEVELS ? k : C3S_LEVELS - 1;
}

int c3s_size_bin(double theta_deg) { return bin_of(c3s_size_edges, theta_deg); }
int c3s_speed_bin(double dtheta_dps) { return bin_of(c3s_speed_edges, dtheta_dps); }

uint32_t c3s_encode(double theta_deg, double dtheta_dps, double azimuth_deg) {
  uint32_t sb = (uint32_t)c3s_size_bin(theta_deg), vb = (uint32_t)c3s_speed_bin(dtheta_dps), x = 0;
  int see_l, see_r;
  c3s_visible(azimuth_deg, &see_l, &see_r);
  if (see_l) x |= sb | vb << C3S_BITS;
  if (see_r) x |= sb << 2 * C3S_BITS | vb << 3 * C3S_BITS;
  return x;
}

void c3s_run_start(c3s_run *run, double l_over_v_ms, double azimuth_deg) {
  c3s_loom_start(&run->loom, l_over_v_ms);
  run->azimuth_deg = azimuth_deg;
  run->state = 0;
  run->tick = 0;
  run->done = 0;
}

int c3s_run_tick(c3s_run *run, const c3s_prog *core, const c3s_prog *policy, c3s_tick_info *t) {
  if (run->done || !c3s_loom_next(&run->loom, &t->theta_deg, &t->dtheta_dps)) {
    run->done = 1;
    return 0;
  }
  t->tick = run->tick++;
  t->x = c3s_encode(t->theta_deg, t->dtheta_dps, run->azimuth_deg) | 1u << C3S_FEATURES;
  t->pathways = c3s_step(policy, t->x, 0, NULL);
  t->state = run->state;
  t->motor = c3s_step(core, t->x, run->state, &t->next_state);
  run->state = t->next_state;
  if (t->motor == C3S_MOTOR_SHORT || t->motor == C3S_MOTOR_LONG) run->done = 1;
  return 1;
}

/* ---- Self-test -------------------------------------------------------------- */

int c3s_replay(const c3s_prog *core, const c3s_ref_episode *e) {
  uint32_t state = 0, motor = C3S_MOTOR_HOLD, nxt;
  int i;
  for (i = 0; i < e->n_ticks; i++) {
    uint32_t x;
    if (i >= e->n_samples) return i;
    x = c3s_encode(e->samples[2 * i], e->samples[2 * i + 1], e->azimuth_deg) | 1u << C3S_FEATURES;
    motor = c3s_step(core, x, state, &nxt);
    if (x != e->ticks[i].x || motor != e->ticks[i].motor || nxt != e->ticks[i].state) return i;
    state = nxt;
  }
  /* The reference stops at the first takeoff or when the samples run out. */
  if (e->n_ticks < e->n_samples && motor != C3S_MOTOR_SHORT && motor != C3S_MOTOR_LONG) return e->n_ticks;
  return -1;
}

static int geometry_matches(const c3s_ref_episode *e) {
  c3s_loom l;
  double th, dth;
  int i = 0;
  c3s_loom_start(&l, e->l_over_v_ms);
  while (c3s_loom_next(&l, &th, &dth)) {
    const double *ref = e->samples + 2 * i;
    if (i >= e->n_samples || fabs(th - ref[0]) > 1e-6 || fabs(dth - ref[1]) > 1e-6) return 0;
    if (c3s_encode(th, dth, e->azimuth_deg) != c3s_encode(ref[0], ref[1], e->azimuth_deg)) return 0;
    i++;
  }
  return i == e->n_samples;
}

static int hash_matches(const uint8_t *bytes, size_t len, const char *want, char *hex) {
  uint8_t digest[32];
  c3s_sha256(bytes, len, digest);
  c3s_hex(digest, sizeof digest, hex);
  return strcmp(hex, want) == 0;
}

int c3s_self_test(c3s_prog *core, c3s_prog *policy, c3s_report *r) {
  uint32_t x, standing;
  int i;
  memset(r, 0, sizeof *r);
  r->bad_episode = r->bad_tick = -1;
  r->episodes = C3S_EPISODES;
  r->core_hash_ok = hash_matches(c3s_core_netlist, C3S_CORE_BYTES, C3S_CORE_SHA256, r->core_sha256);
  r->policy_hash_ok = hash_matches(c3s_policy_netlist, C3S_POLICY_BYTES, C3S_POLICY_SHA256, r->policy_sha256);
  r->core_decode = c3s_decode(core, c3s_core_netlist, C3S_CORE_BYTES, C3S_CORE_INPUTS, C3S_CORE_OUTPUTS);
  r->policy_decode = c3s_decode(policy, c3s_policy_netlist, C3S_POLICY_BYTES, C3S_POLICY_INPUTS, C3S_POLICY_OUTPUTS);
  if (r->core_decode != C3S_OK || r->policy_decode != C3S_OK) return 0;
  if (core->n_state != C3S_CORE_LATCHES || policy->n_state != 0) {
    r->core_decode = C3S_ERR_PORTS;
    return 0;
  }
  for (i = 0; i < C3S_EPISODES; i++) {
    int bad = c3s_replay(core, &c3s_episodes[i]);
    if (bad < 0) {
      r->episodes_ok++;
    } else if (r->bad_episode < 0) {
      r->bad_episode = i;
      r->bad_tick = bad;
    }
    r->geometry_ok += geometry_matches(&c3s_episodes[i]);
  }
  /* With every latch clear the wings are down and there is no refractory period, so
   * a standing fly takes off in short mode when GF crosses (arming the refractory
   * timer), starts raising its wings when only the parallel pathway crosses, and
   * otherwise holds. A fly that is not standing always holds. */
  for (standing = 0; standing < 2; standing++) {
    for (x = 0; x < 1u << C3S_FEATURES; x++) {
      uint32_t path = c3s_step(policy, x, 0, NULL), want_motor = C3S_MOTOR_HOLD, want_next = 0, nxt, motor;
      if (standing && (path & 1)) {
        want_motor = C3S_MOTOR_SHORT;
        want_next = (uint32_t)C3S_REFRACTORY_TICKS << C3S_RAISE_BITS;
      } else if (standing && (path & 2)) {
        want_motor = C3S_MOTOR_RAISING;
        want_next = 1;
      }
      motor = c3s_step(core, x | standing << C3S_FEATURES, 0, &nxt);
      r->rest_rows++;
      r->rest_ok += motor == want_motor && nxt == want_next;
    }
  }
  return r->core_hash_ok && r->policy_hash_ok && r->episodes_ok == r->episodes && r->rest_ok == r->rest_rows;
}
