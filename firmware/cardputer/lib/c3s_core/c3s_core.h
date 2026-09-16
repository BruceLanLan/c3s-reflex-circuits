/* Evaluator for TapeOut NAND/LATCH netlists, plus the looming stimulus and sensory
 * encoding of LoomEscape-16. Plain C99 without allocation: the same files build for
 * the Cardputer (firmware/cardputer) and for the host test (tests/test_firmware.py). */
#ifndef C3S_CORE_H
#define C3S_CORE_H

#include <stddef.h>
#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

#define C3S_MAX_INPUTS 32
#define C3S_MAX_STATE 32
#define C3S_MAX_CELLS 1024
#define C3S_MAX_SIGNALS (2 + C3S_MAX_INPUTS + C3S_MAX_CELLS)

enum {
  C3S_OK = 0,
  C3S_ERR_PORTS = -1,     /* port counts out of range, or fewer cells than outputs */
  C3S_ERR_TRUNCATED = -2, /* the bytes end inside a cell */
  C3S_ERR_OPCODE = -3,    /* unknown opcode */
  C3S_ERR_REF = -4,       /* REF cells are not supported */
  C3S_ERR_SIGNAL = -5,    /* a NAND reads a later signal, or a LATCH d is outside the signal space */
  C3S_ERR_TOO_LARGE = -6
};

enum { C3S_MOTOR_HOLD = 0, C3S_MOTOR_RAISING = 1, C3S_MOTOR_SHORT = 2, C3S_MOTOR_LONG = 3 };

typedef struct {
  uint32_t a, b; /* NAND: operand signals. LATCH: a is d, b is the latch slot. */
  uint8_t latch;
} c3s_cell;

typedef struct {
  int n_in, n_out, n_state, n_cells, n_signals;
  c3s_cell cells[C3S_MAX_CELLS];
  uint32_t latch_d[C3S_MAX_STATE];
} c3s_prog;

/* Signals: 0 is constant 0, 1 is constant 1, then the inputs, then one per cell.
 * NAND is 0x00 u24 a u24 b; LATCH is 0x01 u24 d, outputs its stored bit and stores
 * d at the end of the tick. Outputs are the last n_out signals. Returns C3S_OK or
 * a negative C3S_ERR_* code. */
int c3s_decode(c3s_prog *p, const uint8_t *bytes, size_t len, int n_in, int n_out);

/* One tick. Input bit i drives input i, state bit k is latch k in cell order.
 * Returns the outputs (bit j is output j) and, when next_state is not NULL,
 * stores the latch values after the tick. */
uint32_t c3s_step(const c3s_prog *p, uint32_t inputs, uint32_t state, uint32_t *next_state);

void c3s_sha256(const uint8_t *data, size_t len, uint8_t digest[32]);
void c3s_hex(const uint8_t *bytes, size_t len, char *out); /* 2 * len characters and a NUL */

/* Digest of the whole step relation: evaluates every (input, state) row bit-sliced,
 * in blocks of 256 rows, and chains SHA-256 over the output and next-state bit
 * planes exactly as scripts/export_evm_fixtures.py does, so the device, the host,
 * the browser, Python and the EVM evaluator can be compared by 32 bytes. Rows are
 * inputs first, then latch state. Returns the number of rows digested, or 0 when the
 * program's domain is outside the supported range. */
uint32_t c3s_domain_digest(const c3s_prog *p, uint8_t digest[32]);

/* Looming geometry of a disc of half-size l approaching at constant speed v
 * (c3s/loom.py theta_at and dtheta_at). */
double c3s_theta_deg(double l_over_v_s, double time_to_contact_s);
double c3s_dtheta_dps(double l_over_v_s, double time_to_contact_s);

/* Stimulus samples one tick at a time, as c3s/loom.py stimulus_samples. */
typedef struct {
  double a, t, t_end, dt;
  int tick;
} c3s_loom;
void c3s_loom_start(c3s_loom *l, double l_over_v_ms);
int c3s_loom_next(c3s_loom *l, double *theta_deg, double *dtheta_dps); /* 0 once the disc is at its end size */

void c3s_visible(double azimuth_deg, int *see_left, int *see_right);
int c3s_size_bin(double theta_deg);
int c3s_speed_bin(double dtheta_dps);
/* Sensory bits size_L | speed_L << 4 | size_R << 8 | speed_R << 12 (c3s/loom.py encode_features). */
uint32_t c3s_encode(double theta_deg, double dtheta_dps, double azimuth_deg);

/* One looming episode through the policy and the core, the fly standing throughout. */
typedef struct {
  int tick;
  double theta_deg, dtheta_dps;
  uint32_t x;          /* sensory bits, plus standing at bit C3S_FEATURES */
  uint32_t pathways;   /* policy outputs: bit 0 GF crosses, bit 1 parallel crosses */
  uint32_t state;      /* latch values during the tick */
  uint32_t motor;      /* C3S_MOTOR_* */
  uint32_t next_state; /* latch values after the tick */
} c3s_tick_info;

typedef struct {
  c3s_loom loom;
  double azimuth_deg;
  uint32_t state;
  int tick, done;
} c3s_run;

void c3s_run_start(c3s_run *run, double l_over_v_ms, double azimuth_deg);
/* Advances one tick. Returns 0 once the stimulus has ended or the fly has taken off. */
int c3s_run_tick(c3s_run *run, const c3s_prog *core, const c3s_prog *policy, c3s_tick_info *t);

/* Reference episodes computed in Python by scripts/build_demo.py. */
typedef struct {
  uint32_t x;
  uint8_t motor, state;
} c3s_ref_tick;

typedef struct {
  double l_over_v_ms, azimuth_deg;
  int n_samples;
  const double *samples; /* theta, dtheta pairs, rounded to 6 decimals */
  int n_ticks;
  const c3s_ref_tick *ticks;
} c3s_ref_episode;

/* Replays a reference episode through the encoding and the core. Returns -1 when every
 * tick matches, otherwise the index of the first tick that does not. */
int c3s_replay(const c3s_prog *core, const c3s_ref_episode *e);

typedef struct {
  int core_decode, policy_decode;
  int core_hash_ok, policy_hash_ok;
  char core_sha256[65], policy_sha256[65];
  int episodes, episodes_ok, bad_episode, bad_tick;
  int geometry_ok; /* episodes whose live geometry reproduces the embedded samples and bins */
  uint32_t rest_rows, rest_ok;
} c3s_report;

/* Decodes the embedded core and policy, then checks: SHA-256 of both netlists, every
 * reference episode tick by tick, and the core against the policy over all sensory
 * patterns at rest (latches clear, standing and not standing). geometry_ok is reported
 * but not required, since it depends on the maths library. Returns 1 if all passed. */
int c3s_self_test(c3s_prog *core, c3s_prog *policy, c3s_report *r);

#ifdef __cplusplus
}
#endif

#endif
