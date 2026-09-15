/* Host build of the firmware's evaluator, driven by tests/test_firmware.py.
 *
 *   c3s_host selftest              run the on-boot self-test and print its report
 *   c3s_host table core|policy F   write the step relation over every (input, state)
 *                                  row to F, two bytes per row: outputs, next state
 *   c3s_host run LV AZ             one looming episode as the firmware runs it, one line per tick
 *   c3s_host encode                read "theta dtheta azimuth" lines, print the sensory bits
 */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "c3s_core.h"
#include "c3s_data.h"

static c3s_prog core, policy;

int main(int argc, char **argv) {
  const char *cmd = argc > 1 ? argv[1] : "";
  c3s_report r;
  int pass = c3s_self_test(&core, &policy, &r);

  if (!strcmp(cmd, "selftest")) {
    printf("decode %d %d\n", r.core_decode, r.policy_decode);
    printf("core_sha256 %s\npolicy_sha256 %s\n", r.core_sha256, r.policy_sha256);
    printf("hashes_ok %d %d\n", r.core_hash_ok, r.policy_hash_ok);
    printf("episodes %d/%d\n", r.episodes_ok, r.episodes);
    printf("geometry %d/%d\n", r.geometry_ok, r.episodes);
    printf("rest %lu/%lu\n", (unsigned long)r.rest_ok, (unsigned long)r.rest_rows);
    printf("pass %d\n", pass);
    return pass ? 0 : 1;
  }
  if (r.core_decode != C3S_OK || r.policy_decode != C3S_OK) {
    fprintf(stderr, "decode failed: core %d, policy %d\n", r.core_decode, r.policy_decode);
    return 2;
  }
  if (!strcmp(cmd, "table") && argc == 4) {
    const c3s_prog *p = !strcmp(argv[2], "core") ? &core : &policy;
    uint32_t rows = 1u << (p->n_in + p->n_state), mask = (1u << p->n_in) - 1, row;
    FILE *f = fopen(argv[3], "wb");
    if (!f) return 2;
    for (row = 0; row < rows; row++) {
      uint32_t nxt;
      unsigned char b[2];
      b[0] = (unsigned char)c3s_step(p, row & mask, row >> p->n_in, &nxt);
      b[1] = (unsigned char)nxt;
      fwrite(b, 1, 2, f);
    }
    return fclose(f) ? 2 : 0;
  }
  if (!strcmp(cmd, "run") && argc == 4) {
    c3s_run run;
    c3s_tick_info t;
    c3s_run_start(&run, strtod(argv[2], NULL), strtod(argv[3], NULL));
    while (c3s_run_tick(&run, &core, &policy, &t))
      printf("%d %.17g %.17g %lu %lu %lu %lu %lu\n", t.tick, t.theta_deg, t.dtheta_dps, (unsigned long)t.x,
             (unsigned long)t.pathways, (unsigned long)t.state, (unsigned long)t.motor, (unsigned long)t.next_state);
    return 0;
  }
  if (!strcmp(cmd, "encode")) {
    double th, dth, az;
    while (scanf("%lf %lf %lf", &th, &dth, &az) == 3) printf("%lu\n", (unsigned long)c3s_encode(th, dth, az));
    return 0;
  }
  fprintf(stderr, "usage: c3s_host selftest | table core|policy FILE | run LV AZ | encode\n");
  return 2;
}
