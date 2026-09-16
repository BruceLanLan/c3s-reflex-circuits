// The c3s escape core on an M5Stack Cardputer ADV.
//
// The TapeOut netlist of core-hand-abc (173 NAND + 6 LATCH) is embedded byte for byte
// and evaluated gate by gate by lib/c3s_core, the C code that tests/test_firmware.py
// checks on the host over all 2^23 (input, state) rows. At boot the firmware hashes
// the bytes, replays the reference episodes and checks the core against the policy at
// rest. It then approaches the fly with a looming disc, encodes the disc into 16
// sensory bits every 5 ms tick and shows what the circuit does with them.

#include <M5Cardputer.h>

#include "c3s_core.h"
#include "c3s_data.h"
#include "c3s_brain.h"

namespace {

c3s_prog core;
c3s_prog policy;
c3s_report report;
bool passed = false;
bool runnable = false;
uint32_t selfTestMs = 0;

// Whole-domain digest, computed on request: it takes seconds, so the page is drawn
// first and the work happens in the next loop pass.
char digestHex[65] = "";
uint32_t digestRows = 0, digestMs = 0;
bool digestPending = false;

M5Canvas canvas(&M5Cardputer.Display);

const int kLv[] = {10, 12, 15, 20, 25, 30, 35, 40, 50, 60, 70, 80, 100, 140};  // l/v, ms
const int kNumLv = sizeof(kLv) / sizeof(kLv[0]);
const int kSlow[] = {1, 4, 10, 40};  // slow-motion factors on the 5 ms tick

int lvIndex = 7;   // 40 ms
int azimuth = 0;   // degrees, negative is left
int slowIndex = 2;
bool autoDemo = true;
bool paused = false;
bool sound = true;

enum class Page { Main, SelfTest, Help, Digest, Lattice, Agent, Menu, Brain };
enum class Phase { Idle, Looming, TookOff, Ended };
Page page = Page::SelfTest;
Phase phase = Phase::Idle;
uint32_t pageUntil = 0;

c3s_run run;
c3s_tick_info last;
bool haveTick = false;
int runLv = 40;
int runAz = 0;
int takeoffMotor = -1;
uint32_t lastTickMs = 0, phaseMs = 0, lastDrawMs = 0;
bool dirty = true;

// Set by frames from the boundary console (agent page below).
uint32_t hostSeenMs = 0;
bool hostEver = false;
int waitingCount = 0;
bool hostLive() { return hostEver && millis() - hostSeenMs < 4000; }

uint32_t rgb(uint8_t r, uint8_t g, uint8_t b) { return (uint32_t)r << 16 | (uint32_t)g << 8 | b; }
const uint32_t kBg = rgb(12, 14, 18);
const uint32_t kPanel = rgb(28, 32, 40);
const uint32_t kDim = rgb(60, 66, 76);
const uint32_t kText = rgb(225, 228, 234);
const uint32_t kMuted = rgb(140, 148, 158);
const uint32_t kSensory = rgb(80, 170, 255);
const uint32_t kGf = rgb(255, 96, 72);
const uint32_t kPar = rgb(255, 190, 60);
const uint32_t kLatch = rgb(170, 130, 255);
const uint32_t kOk = rgb(70, 200, 120);
const uint32_t kBad = rgb(240, 70, 70);
const uint32_t kFly = rgb(236, 206, 150);
const uint32_t kLoomFill = rgb(74, 80, 94);
const uint32_t kLoomEdge = rgb(190, 196, 208);

uint32_t motorColor(int motor) {
  switch (motor) {
    case C3S_MOTOR_RAISING: return kLatch;
    case C3S_MOTOR_SHORT: return kGf;
    case C3S_MOTOR_LONG: return kPar;
    default: return kMuted;
  }
}

void text(int x, int y, uint32_t color, const char *s) {
  canvas.setTextColor(color);
  canvas.drawString(s, x, y);
}

void lamp(int x, int y, bool on, uint32_t color) {
  if (on) {
    canvas.fillRect(x, y, 7, 7, color);
  } else {
    canvas.fillRect(x, y, 7, 7, kPanel);
    canvas.drawRect(x, y, 7, 7, kDim);
  }
}

// A row of lamps, most significant bit first so it reads as a binary number.
void bits(int x, int y, const char *label, uint32_t value, int n, uint32_t color) {
  char buf[8];
  text(x, y, kMuted, label);
  for (int i = 0; i < n; i++) lamp(x + 50 + i * 9, y, (value >> (n - 1 - i)) & 1, color);
  snprintf(buf, sizeof buf, "%lu", (unsigned long)value);
  text(x + 53 + n * 9, y, kText, buf);
}

// ---- simulation -----------------------------------------------------------------

void beep(int motor) {
  if (sound) M5Cardputer.Speaker.tone(motor == C3S_MOTOR_SHORT ? 2400 : 1500, 60);
}

void launch() {
  if (!runnable) return;
  runLv = kLv[lvIndex];
  runAz = azimuth;
  c3s_run_start(&run, runLv, runAz);
  phase = Phase::Looming;
  haveTick = false;
  takeoffMotor = -1;
  paused = false;
  lastTickMs = phaseMs = millis();
  dirty = true;
  Serial.printf("loom l/v %d ms, azimuth %d deg\n", runLv, runAz);
}

void advance() {
  c3s_tick_info t;
  if (!c3s_run_tick(&run, &core, &policy, &t)) {
    phase = Phase::Ended;
    phaseMs = millis();
    Serial.println("disc reached its end size without a takeoff");
    dirty = true;
    return;
  }
  last = t;
  haveTick = true;
  dirty = true;
  Serial.printf("tick %3d size %7.3f speed %9.2f x %05lx gf %lu par %lu latch %02lx motor %lu %s\n", t.tick,
                t.theta_deg, t.dtheta_dps, (unsigned long)t.x, (unsigned long)(t.pathways & 1),
                (unsigned long)(t.pathways >> 1), (unsigned long)t.state, (unsigned long)t.motor,
                c3s_motor_names[t.motor & 3]);
  if (t.motor == C3S_MOTOR_SHORT || t.motor == C3S_MOTOR_LONG) {
    takeoffMotor = (int)t.motor;
    phase = Phase::TookOff;
    phaseMs = millis();
    beep(takeoffMotor);
  }
}

void manual() {
  autoDemo = false;
  dirty = true;
}

// ---- drawing --------------------------------------------------------------------

void drawTopBar() {
  char buf[48];
  canvas.fillRect(0, 0, 240, 11, kPanel);
  snprintf(buf, sizeof buf, "%s %d NAND+%d LATCH", C3S_CORE_NAME, C3S_CORE_NAND, C3S_CORE_LATCHES);
  text(3, 2, kText, buf);
  if (hostLive() && waitingCount) {
    snprintf(buf, sizeof buf, "%d wait", waitingCount);
    text(164, 2, kPar, buf);
  }
  canvas.fillRect(206, 1, 33, 9, passed ? kOk : kBad);
  canvas.setTextColor(kBg);
  canvas.drawString(passed ? "PASS" : "FAIL", 211, 2);
}

void drawFly(int cx, int cy, double dx, double dy) {
  double k = 0;
  if (takeoffMotor >= 0) {
    k = (millis() - phaseMs) / 700.0;
    if (phase == Phase::Ended || k > 1) k = 1;
  }
  // takeoff carries the fly away from the disc
  int fx = cx - (int)(dx * 70 * k * k);
  int fy = cy - (int)(dy * 70 * k * k);
  uint32_t raise = haveTick ? (last.next_state & 7) : 0;
  double w = takeoffMotor == C3S_MOTOR_LONG ? 1.0 : (double)(raise > 4 ? 4 : raise) / 4.0;
  if (takeoffMotor == C3S_MOTOR_SHORT) w = 0;  // short mode leaves without raising the wings
  for (int s = -1; s <= 1; s += 2) {
    int tipX = fx + s * (int)(3 + 11 * w);
    int tipY = fy + (int)(13 - 19 * w);
    canvas.fillTriangle(fx + s * 2, fy - 4, tipX, tipY, fx + s * 1, fy + 3, rgb(150, 170, 190));
  }
  canvas.fillEllipse(fx, fy + 2, 4, 8, kFly);
  canvas.fillCircle(fx, fy - 8, 4, kFly);
  bool left = haveTick && (last.x & 0xff);
  bool right = haveTick && ((last.x >> 8) & 0xff);
  canvas.fillCircle(fx - 3, fy - 9, 2, left ? kSensory : kDim);
  canvas.fillCircle(fx + 3, fy - 9, 2, right ? kSensory : kDim);
  if (takeoffMotor >= 0 && k < 1) {
    for (int i = 1; i <= 3; i++) canvas.drawCircle(fx, fy, 8 + i * 4, i == 1 ? motorColor(takeoffMotor) : kDim);
  }
}

void drawArena() {
  const int ax = 0, ay = 12, aw = 106, ah = 111;
  const int cx = ax + aw / 2, cy = ay + 66;
  const double dist = 38;
  int az = phase == Phase::Idle ? azimuth : runAz;
  double a = az * M_PI / 180;
  double dx = sin(a), dy = -cos(a);
  canvas.setClipRect(ax, ay, aw, ah - 12);
  canvas.fillRect(ax, ay, aw, ah, kBg);
  // edges of the binocular overlap: the left eye sees up to +30 deg, the right from -30 deg
  for (int s = -1; s <= 1; s += 2) {
    double e = s * C3S_OVERLAP_DEG * M_PI / 180;
    canvas.drawLine(cx, cy - 8, cx + (int)(90 * sin(e)), cy - 8 - (int)(90 * cos(e)), kPanel);
  }
  int lx = cx + (int)(dx * dist), ly = cy - 8 + (int)(dy * dist);
  if (phase != Phase::Idle && haveTick) {
    double r = dist * tan(last.theta_deg * M_PI / 360);  // a disc at this distance subtends theta
    if (r > 120) r = 120;
    canvas.fillCircle(lx, ly, (int)r, kLoomFill);
    canvas.drawCircle(lx, ly, (int)r, kLoomEdge);
  } else {
    canvas.drawCircle(lx, ly, 4, kLoomEdge);
    canvas.drawLine(lx - (int)(dx * 8), ly - (int)(dy * 8), lx - (int)(dx * 20), ly - (int)(dy * 20), kDim);
  }
  drawFly(cx, cy, dx, dy);
  canvas.clearClipRect();

  const char *caption = "ready";
  uint32_t color = kMuted;
  if (takeoffMotor >= 0) {
    caption = c3s_motor_names[takeoffMotor];
    color = motorColor(takeoffMotor);
  } else if (phase == Phase::Ended) {
    caption = "no takeoff";
  } else if (haveTick) {
    caption = c3s_motor_names[last.motor & 3];
    color = motorColor((int)last.motor);
  }
  canvas.fillRect(ax, ay + ah - 11, aw, 11, kBg);
  text(ax + 3, ay + ah - 10, color, caption);
}

void drawPanel() {
  const int x = 110;
  char buf[24];
  uint32_t xs = haveTick ? last.x : 0;
  snprintf(buf, sizeof buf, "SENSORS %05lx", (unsigned long)xs);
  text(x, 14, kText, buf);
  bits(x, 24, "size L", xs & 15, 4, kSensory);
  bits(x, 33, "speed L", (xs >> 4) & 15, 4, kSensory);
  bits(x, 42, "size R", (xs >> 8) & 15, 4, kSensory);
  bits(x, 51, "speed R", (xs >> 12) & 15, 4, kSensory);
  bits(x, 60, "standing", (xs >> C3S_FEATURES) & 1, 1, kSensory);

  text(x, 72, kText, "POLICY");
  uint32_t path = haveTick ? last.pathways : 0;
  text(x + 50, 72, kMuted, "GF");
  lamp(x + 64, 72, path & 1, kGf);
  text(x + 80, 72, kMuted, "PAR");
  lamp(x + 100, 72, path & 2, kPar);

  uint32_t q = haveTick ? last.state : 0;
  text(x, 84, kText, "LATCHES");
  bits(x, 94, "raise", q & 7, 3, kLatch);
  bits(x, 103, "refract", (q >> C3S_RAISE_BITS) & 7, 3, kLatch);

  int motor = haveTick ? (int)last.motor : 0;
  text(x, 114, kText, "MOTOR");
  lamp(x + 50, 114, motor & 2, motorColor(motor));
  lamp(x + 59, 114, motor & 1, motorColor(motor));
  snprintf(buf, sizeof buf, "%d", motor);
  text(x + 71, 114, kText, buf);
}

void drawBottomBar() {
  char buf[48];
  canvas.fillRect(0, 124, 240, 11, kPanel);
  if (phase == Phase::Idle || (phase == Phase::Ended && !autoDemo)) {
    snprintf(buf, sizeof buf, "l/v %dms az %+d  ENTER loom  h keys", kLv[lvIndex], azimuth);
  } else {
    snprintf(buf, sizeof buf, "l/v %d az %+d t%d %.0fdeg %dx%s%s", runLv, runAz, haveTick ? last.tick : 0,
             haveTick ? last.theta_deg : C3S_START_DEG, kSlow[slowIndex], paused ? " II" : "", autoDemo ? " A" : "");
  }
  text(3, 126, kText, buf);
}

void drawSelfTest() {
  char buf[64];
  text(4, 4, kText, "SELF-TEST");
  text(64, 4, passed ? kOk : kBad, passed ? "PASS" : "FAIL");
  snprintf(buf, sizeof buf, "%s %d B", C3S_CORE_NAME, C3S_CORE_BYTES);
  text(4, 20, kText, buf);
  snprintf(buf, sizeof buf, "sha256 %.16s.. %s", report.core_sha256, report.core_hash_ok ? "ok" : "BAD");
  text(10, 30, report.core_hash_ok ? kMuted : kBad, buf);
  snprintf(buf, sizeof buf, "%s %d B", C3S_POLICY_NAME, C3S_POLICY_BYTES);
  text(4, 44, kText, buf);
  snprintf(buf, sizeof buf, "sha256 %.16s.. %s", report.policy_sha256, report.policy_hash_ok ? "ok" : "BAD");
  text(10, 54, report.policy_hash_ok ? kMuted : kBad, buf);
  snprintf(buf, sizeof buf, "reference episodes %d/%d tick-exact", report.episodes_ok, report.episodes);
  text(4, 68, report.episodes_ok == report.episodes ? kText : kBad, buf);
  snprintf(buf, sizeof buf, "core vs policy at rest %lu/%lu", (unsigned long)report.rest_ok,
           (unsigned long)report.rest_rows);
  text(4, 80, report.rest_ok == report.rest_rows ? kText : kBad, buf);
  snprintf(buf, sizeof buf, "live geometry %d/%d (informational)", report.geometry_ok, report.episodes);
  text(4, 92, kMuted, buf);
  if (report.core_decode || report.policy_decode) {
    snprintf(buf, sizeof buf, "decode error core %d policy %d", report.core_decode, report.policy_decode);
    text(4, 104, kBad, buf);
  } else {
    snprintf(buf, sizeof buf, "took %lu ms on this device", (unsigned long)selfTestMs);
    text(4, 104, kMuted, buf);
  }
  text(4, 122, kMuted, "any key: menu");
}

void drawHelp() {
  static const char *const lines[] = {
      "KEYS",
      "enter, space, G0   loom",
      "; .   l/v up / down",
      ", /   azimuth left / right",
      "1-4   speed 1x 4x 10x 40x slower",
      "p pause   n one tick when paused",
      "a auto  m sound  t self-test  d digest",
      "w lattice  g agent  ` or DEL: menu",
      "any key: menu",
  };
  for (int i = 0; i < 9; i++) text(4, 4 + i * 14, i == 0 ? kText : kMuted, lines[i]);
}

// ---- circuit lattice -------------------------------------------------------------
// The netlist drawn as itself: one dot per cell, lines to the two signals each NAND
// reads, and every dot's brightness is that gate's value on the tick being shown.
// Nothing here is anatomy or a guess; the geometry is the circuit's logic depth.
uint8_t traceBuf[C3S_MAX_SIGNALS];
int16_t latY[C3S_MAX_CELLS], latZ[C3S_MAX_CELLS];
uint8_t latX[C3S_MAX_CELLS];
float latSpin = 0;
bool latReady = false;

void buildLattice() {
  static uint8_t depth[C3S_MAX_SIGNALS];
  const int first = 2 + core.n_in;
  for (int i = 0; i < first; i++) depth[i] = 0;
  int maxd = 1;
  for (int i = 0; i < core.n_cells; i++) {
    const c3s_cell *c = &core.cells[i];
    int d = 0;
    if (!c->latch) {
      int da = depth[c->a], db = depth[c->b];
      d = 1 + (da > db ? da : db);
      if (d > 120) d = 120;
    }
    depth[first + i] = (uint8_t)d;
    if (d > maxd) maxd = d;
  }
  for (int i = 0; i < core.n_cells; i++) {
    const float a = i * 2.39996f;  // golden angle, so cells of one depth spread out
    const float r = 30.0f + (i % 3) * 7.0f;
    latX[i] = (uint8_t)(16 + (200 * depth[first + i]) / maxd);
    latY[i] = (int16_t)(cos(a) * r);
    latZ[i] = (int16_t)(sin(a) * r);
  }
  latReady = true;
}

void latticeProject(int i, float c, float s, int *sx, int *sy) {
  const float y = latY[i] * c - latZ[i] * s;
  const float z = latY[i] * s + latZ[i] * c;
  const float k = 1.0f / (1.5f - z / 140.0f);
  *sx = latX[i];
  *sy = (int)(66 + y * k * 0.78f);
}

void drawLattice() {
  if (!latReady) buildLattice();
  char buf[64];
  const int first = 2 + core.n_in;
  c3s_step_trace(&core, haveTick ? last.x : 0u, haveTick ? last.state : 0u, NULL, traceBuf);
  const float c = cos(latSpin), s = sin(latSpin);
  const int outFirst = core.n_signals - core.n_out;

  for (int i = 0; i < core.n_cells; i++) {
    const c3s_cell *cell = &core.cells[i];
    if (cell->latch) continue;
    int sx, sy;
    latticeProject(i, c, s, &sx, &sy);
    for (int k = 0; k < 2; k++) {
      const uint32_t op = k ? cell->b : cell->a;
      if ((int)op < first) continue;
      int ox, oy;
      latticeProject((int)op - first, c, s, &ox, &oy);
      canvas.drawLine(ox, oy, sx, sy, traceBuf[op] ? kDim : kPanel);
    }
  }
  for (int i = 0; i < core.n_cells; i++) {
    const c3s_cell *cell = &core.cells[i];
    int sx, sy;
    latticeProject(i, c, s, &sx, &sy);
    const int sig = first + i;
    uint32_t col = traceBuf[sig] ? kText : kPanel;
    if (cell->latch) col = traceBuf[sig] ? kLatch : kPanel;
    if (sig >= outFirst) col = (sig == outFirst) ? kPar : kGf;
    canvas.fillRect(sx, sy, 2, 2, col);
  }

  snprintf(buf, sizeof buf, "%d NAND + %d LATCH, live", core.n_cells - core.n_state, core.n_state);
  text(4, 4, kText, buf);
  if (haveTick) {
    snprintf(buf, sizeof buf, "tick %d x %05lx motor %lu", last.tick, (unsigned long)last.x, (unsigned long)last.motor);
    text(4, 16, kMuted, buf);
  } else {
    text(4, 16, kMuted, "at rest; ENTER on the main page to drive it");
  }
  text(4, 122, kMuted, "logic depth left to right; any key: menu");
}

void drawDigest() {
  char buf[64];
  text(4, 4, kText, "WHOLE-DOMAIN DIGEST");
  text(4, 20, kMuted, "SHA-256 chain over every (input, state) row");
  snprintf(buf, sizeof buf, "%s: 8,388,608 rows", C3S_CORE_NAME);
  text(4, 34, kText, buf);
  if (!digestRows) {
    text(4, 52, kMuted, "computing on this device, a few seconds..");
    text(4, 122, kMuted, "any key: menu");
    return;
  }
  snprintf(buf, sizeof buf, "%.32s", digestHex);
  text(10, 52, kText, buf);
  snprintf(buf, sizeof buf, "%.32s", digestHex + 32);
  text(10, 62, kText, buf);
  snprintf(buf, sizeof buf, "%lu rows in %lu ms on this device", (unsigned long)digestRows, (unsigned long)digestMs);
  text(4, 80, kMuted, buf);
  text(4, 94, kMuted, "equal to Python, the browser and the EVM");
  text(4, 106, kMuted, "if it matches docs/CIRCUITS.md");
  text(4, 122, kMuted, "any key: menu");
}

// ---- agent page: this device as the physical confirm key ---------------------------
//
// The boundary console (reflex-console, cardputer_relay.py) sends a frame a second over
// USB serial: which circuits are installed, what is waiting for a person, the latest
// decision. ENTER writes `confirm` for the selected agent, `b` blocks it, `u` lifts the
// block. A model can write its own request; it cannot press this key. Nothing here
// talks to a network, and nothing is stored.

struct AgentItem {
  char agent[31];
  char cls[8];
  char why[61];
  char bit[10];
  int tick;
  bool armed;
};

const int kMaxItems = 4;
AgentItem items[kMaxItems], itemsIn[kMaxItems];
int itemCount = 0, itemsInCount = 0;
char summary[41] = "", chainState[10] = "", lastLine[100] = "";
bool lastGranted = false;
int nGranted = 0, nRefused = 0, selected = 0;
bool frameOpen = false;  // an S line started this frame
char serialLine[200];
int serialLen = 0;

// Split `line` on '|' into at most n fields, in place.
int fields(char *line, char **out, int n) {
  int k = 0;
  out[k++] = line;
  for (char *p = line; *p && k < n; p++)
    if (*p == '|') {
      *p = 0;
      out[k++] = p + 1;
    }
  return k;
}

void copyField(char *dst, size_t size, const char *src) {
  strncpy(dst, src, size - 1);
  dst[size - 1] = 0;
}

void onHostLine(char *line) {
  char *f[9];
  int n = fields(line, f, 9);
  hostSeenMs = millis();
  if (f[0][0] == 'S' && n >= 5) {
    copyField(summary, sizeof summary, f[1]);
    nGranted = atoi(f[2]);
    nRefused = atoi(f[3]);
    copyField(chainState, sizeof chainState, f[4]);
    itemsInCount = 0;
    frameOpen = true;
  } else if (f[0][0] == 'I' && n >= 8 && itemsInCount < kMaxItems) {
    AgentItem &it = itemsIn[itemsInCount++];
    copyField(it.agent, sizeof it.agent, f[2]);
    copyField(it.cls, sizeof it.cls, f[3]);
    it.tick = atoi(f[4]);
    copyField(it.why, sizeof it.why, f[5]);
    copyField(it.bit, sizeof it.bit, f[6]);
    it.armed = f[7][0] == '1';
  } else if (f[0][0] == 'L' && n >= 5) {
    lastGranted = f[1][0] == 'G';
    snprintf(lastLine, sizeof lastLine, "%s %s: %s", f[2], f[3], f[4]);
  } else if (f[0][0] == 'E') {
    // A frame that lost a line on the way (the USB buffer can overflow while the arena
    // animates) is dropped whole, never shown as "nothing waiting".
    const bool whole = frameOpen && n >= 2 && atoi(f[1]) == itemsInCount;
    frameOpen = false;
    if (!whole) return;
    // Keep the selection on the same agent across frames.
    char keep[31] = "";
    if (selected < itemCount) copyField(keep, sizeof keep, items[selected].agent);
    int waitingBefore = 0, waitingNow = 0;
    for (int i = 0; i < itemCount; i++) waitingBefore += !items[i].armed;
    memcpy(items, itemsIn, sizeof items);
    itemCount = itemsInCount;
    selected = 0;
    for (int i = 0; i < itemCount; i++) {
      if (keep[0] && strcmp(items[i].agent, keep) == 0) selected = i;
      waitingNow += !items[i].armed;
    }
    // Something new waiting for a person: a tone and a count on screen, never a page
    // switch — the device does not take the screen away from whoever is looking at it.
    if (hostEver && waitingNow > waitingBefore && sound) M5Cardputer.Speaker.tone(1900, 80);
    waitingCount = waitingNow;
    hostEver = true;
    dirty = true;
  }
}

void sendKey(const char *action) {
  if (selected >= itemCount) return;
  // One press, one event: the keyboard can report the same key on more than one scan.
  static char lastAction[12] = "";
  static uint32_t lastMs = 0;
  const uint32_t now = millis();
  if (strcmp(action, lastAction) == 0 && now - lastMs < 600) return;
  copyField(lastAction, sizeof lastAction, action);
  lastMs = now;
  Serial.printf("K|%s|%s\n", action, items[selected].agent);
  if (sound) M5Cardputer.Speaker.tone(strcmp(action, "block") == 0 ? 700 : 2600, 50);
}

void drawAgent() {
  char buf[72];
  const bool live = hostEver && millis() - hostSeenMs < 4000;
  text(4, 3, kText, "C3S CIRCUIT AGENT");
  text(live ? 190 : 172, 3, live ? kOk : kBad, live ? "host ok" : "no host");
  if (!live) {
    text(4, 30, kMuted, "waiting for the boundary console");
    text(4, 44, kMuted, "on the computer, over USB:");
    text(4, 60, kText, "REFLEX_CARDPUTER=1 python console.py");
    text(4, 84, kMuted, "this key only writes confirm/blocked;");
    text(4, 96, kMuted, "no network, nothing stored");
    text(4, 124, kMuted, "` or DEL: menu");
    return;
  }
  text(4, 15, kMuted, summary);
  snprintf(buf, sizeof buf, "granted %d  refused %d  chain %s", nGranted, nRefused, chainState);
  text(4, 26, kMuted, buf);
  canvas.drawFastHLine(0, 37, 240, kDim);

  if (itemCount == 0) {
    text(4, 58, kOk, "nothing is waiting for a person");
  }
  for (int i = 0; i < itemCount && i < 3; i++) {
    const AgentItem &it = items[i];
    const int y = 41 + i * 23;
    if (i == selected) {
      canvas.fillRect(0, y - 1, 240, 22, kPanel);
      canvas.fillRect(0, y - 1, 2, 22, kPar);
    }
    snprintf(buf, sizeof buf, "%.22s %s t%d", it.agent, it.cls, it.tick);
    text(5, y, i == selected ? kText : kMuted, buf);
    if (it.armed) {
      snprintf(buf, sizeof buf, "%s given; waits for its next call", it.bit);
      text(5, y + 10, kOk, buf);
    } else {
      snprintf(buf, sizeof buf, "%.38s", it.why);
      text(5, y + 10, kPar, buf);
    }
  }
  if (itemCount > 3) {
    snprintf(buf, sizeof buf, "+%d more", itemCount - 3);
    text(196, 26, kMuted, buf);
  }
  canvas.drawFastHLine(0, 110, 240, kDim);
  snprintf(buf, sizeof buf, "%c %.37s", lastGranted ? '+' : 'x', lastLine);
  text(4, 113, lastGranted ? kOk : kBad, buf);
  text(4, 125, kMuted, "ENT confirm  b block  u unblock  ` menu");
}

// ---- fly brain: the published cells, lit by the circuit ---------------------------
//
// 26 cells from the MaleCNS v1.0 release (c3s_brain.h, built by
// scripts/build_firmware_brain.py from the simulator's skeletons): both giant fibers
// and, per fiber, its six strongest LC4 and LPLC2 inputs. Their shapes and positions
// are the release's. What lights them is the circuit running on this chip, and only at
// the resolution the circuit has: an eye's speed field lights that eye's LC4
// population, its size field the LPLC2 population, the GF pathway both giant fibers.
// Lighting single cells would claim a resolution the circuit does not have. The faint
// outline is where these cells are (derived from them), not a neuropil mesh.

int16_t brainX[C3S_BRAIN_POINTS], brainY[C3S_BRAIN_POINTS];
uint32_t brainFrames = 0, brainMsSum = 0;

uint32_t mix(uint32_t a, uint32_t b, float k) {
  const auto ch = [&](int sh) { return (uint32_t)(((a >> sh) & 255) * (1 - k) + ((b >> sh) & 255) * k) & 255; };
  return ch(16) << 16 | ch(8) << 8 | ch(0);
}

void drawBrain() {
  const uint32_t t0 = millis();
  const float ang = 0.62f * sin(t0 / 2600.0f);  // a slow sway, never edge-on
  const float ca = cos(ang), sa = sin(ang);
  const float k = 86.0f, cx = 120.0f, cy = 66.0f;
  const auto project = [&](float x, float y, float z, int16_t *sx, int16_t *sy) {
    const float rx = x * ca - z * sa, rz = x * sa + z * ca;
    const float s = 1.9f / (1.9f - 0.55f * rz);
    *sx = (int16_t)(cx + rx * k * s);
    *sy = (int16_t)(cy + y * k * s);
  };

  // outline: where these cells are
  const uint32_t kOutline = rgb(38, 44, 56);
  for (int e = 0; e < 3; e++) {
    const c3s_brain_ellipse &el = c3s_brain_outline[e];
    int16_t px = 0, py = 0;
    for (int i = 0; i <= 40; i++) {
      const float a = i * 2 * M_PI / 40;
      int16_t sx, sy;
      project(el.cx + el.rx * cos(a), el.cy + el.ry * sin(a), el.cz, &sx, &sy);
      if (i) canvas.drawLine(px, py, sx, sy, kOutline);
      px = sx;
      py = sy;
    }
  }

  for (int i = 0; i < C3S_BRAIN_POINTS; i++)
    project(c3s_brain_points[3 * i] / 127.0f, c3s_brain_points[3 * i + 1] / 127.0f,
            c3s_brain_points[3 * i + 2] / 127.0f, &brainX[i], &brainY[i]);

  const uint32_t x = haveTick ? last.x : 0, path = haveTick ? last.pathways : 0;
  const bool flash = takeoffMotor >= 0 && phase == Phase::TookOff;
  for (int c = 0; c < C3S_BRAIN_CELLS; c++) {
    const c3s_brain_cell &cell = c3s_brain_cells[c];
    uint32_t col;
    if (cell.type == 0) {
      col = flash ? rgb(210, 255, 225) : ((path & 1) ? rgb(123, 224, 168) : rgb(85, 96, 110));
    } else {
      const uint32_t field = cell.type == 1 ? (cell.side == 0 ? (x >> 4) & 15 : (x >> 12) & 15)
                                            : (cell.side == 0 ? x & 15 : (x >> 8) & 15);
      const uint32_t off = cell.type == 1 ? rgb(74, 85, 104) : rgb(83, 86, 106);
      const uint32_t on = cell.type == 1 ? rgb(255, 138, 101) : rgb(255, 196, 84);
      col = field ? mix(off, on, 0.35f + 0.65f * field / 15.0f) : off;
    }
    const int end = (cell.first_segment + cell.segments) * 2;
    for (int j = cell.first_segment * 2; j < end; j += 2) {
      const uint16_t a = c3s_brain_segments[j], b = c3s_brain_segments[j + 1];
      canvas.drawLine(brainX[a], brainY[a], brainX[b], brainY[b], col);
    }
  }

  char buf[48];
  text(4, 3, kText, "FLY BRAIN");
  text(64, 3, kMuted, "26 real cells");
  if (haveTick) {
    snprintf(buf, sizeof buf, "%s", c3s_motor_names[last.motor & 3]);
    text(236 - 6 * (int)strlen(buf), 3, motorColor((int)last.motor), buf);
  }
  snprintf(buf, sizeof buf, "L %lu/%lu  R %lu/%lu  GF %s", (unsigned long)((x >> 4) & 15), (unsigned long)(x & 15),
           (unsigned long)((x >> 12) & 15), (unsigned long)((x >> 8) & 15), (path & 1) ? "on" : "off");
  text(4, 114, kMuted, buf);
  text(4, 125, kDim, "MaleCNS v1.0 CC-BY  ENT loom  ` menu");

  brainMsSum += millis() - t0;
  if (++brainFrames == 60) {
    Serial.printf("brain view: %lu ms per frame over 60 frames\n", (unsigned long)(brainMsSum / 60));
  }
}

// ---- menu -------------------------------------------------------------------------

struct MenuEntry {
  char key;
  const char *label;
  const char *note;
  Page page;
};
const MenuEntry kMenu[] = {
    {'1', "Fly escape reflex", "the circuit decides takeoff, live", Page::Main},
    {'2', "Fly brain, real cells", "26 published cells lit by the circuit", Page::Brain},
    {'3', "Agent confirm key", "approve what the boundary held back", Page::Agent},
    {'4', "Gate lattice", "173 NAND gates, each lit by its value", Page::Lattice},
    {'5', "Whole-domain digest", "8,388,608 rows on this chip, ~10 s", Page::Digest},
    {'6', "Self-test", "hashes and reference episodes", Page::SelfTest},
    {'7', "Keys", "every key on every page", Page::Help},
};
const int kMenuCount = sizeof(kMenu) / sizeof(kMenu[0]);
int menuIndex = 0;

void drawMenu() {
  char buf[48];
  text(4, 3, kText, "C3S CIRCUIT AGENT");
  text(206, 3, passed ? kOk : kBad, passed ? "PASS" : "FAIL");
  for (int i = 0; i < kMenuCount; i++) {
    const int y = 16 + i * 14;
    if (i == menuIndex) {
      canvas.fillRect(0, y - 2, 240, 13, kPanel);
      canvas.fillRect(0, y - 2, 2, 13, kPar);
    }
    snprintf(buf, sizeof buf, "%c  %s", kMenu[i].key, kMenu[i].label);
    text(6, y, i == menuIndex ? kText : kMuted, buf);
    if (kMenu[i].page == Page::Agent) {
      if (hostLive() && waitingCount) {
        snprintf(buf, sizeof buf, "%d waiting", waitingCount);
        text(172, y, kPar, buf);
      } else {
        text(172, y, hostLive() ? kOk : kDim, hostLive() ? "host ok" : "no host");
      }
    }
  }
  text(4, 120, kMuted, kMenu[menuIndex].note);
  canvas.fillRect(0, 130, 240, 1, kDim);
}

void enterPage(Page p) {
  page = p;
  pageUntil = 0;
  if (p == Page::Digest) {
    digestRows = 0;
    digestPending = true;
  }
  if (p == Page::Main || p == Page::Brain) phaseMs = millis();
  if (p == Page::Brain) brainFrames = brainMsSum = 0;
  dirty = true;
}

void draw() {
  canvas.fillScreen(kBg);
  if (page == Page::SelfTest) {
    drawSelfTest();
  } else if (page == Page::Help) {
    drawHelp();
  } else if (page == Page::Digest) {
    drawDigest();
  } else if (page == Page::Lattice) {
    drawLattice();
  } else if (page == Page::Agent) {
    drawAgent();
  } else if (page == Page::Menu) {
    drawMenu();
  } else if (page == Page::Brain) {
    drawBrain();
  } else {
    drawTopBar();
    drawArena();
    drawPanel();
    drawBottomBar();
  }
  canvas.pushSprite(0, 0);
}

// ---- input ----------------------------------------------------------------------

void onKey(char c) {
  switch (c) {
    case ';': lvIndex = lvIndex + 1 < kNumLv ? lvIndex + 1 : lvIndex; manual(); break;
    case '.': lvIndex = lvIndex > 0 ? lvIndex - 1 : 0; manual(); break;
    case ',': azimuth = azimuth > -90 ? azimuth - 10 : azimuth; manual(); break;
    case '/': azimuth = azimuth < 90 ? azimuth + 10 : azimuth; manual(); break;
    case '1': case '2': case '3': case '4': slowIndex = c - '1'; break;
    case 'p': paused = !paused; lastTickMs = millis(); break;
    case 'n': if (paused && phase == Phase::Looming) advance(); break;
    case 'a': autoDemo = !autoDemo; phaseMs = millis(); break;
    case 'm': sound = !sound; break;
    case 'd': page = Page::Digest; pageUntil = 0; digestRows = 0; digestPending = true; break;
    case 'w': page = Page::Lattice; pageUntil = 0; break;
    case 't': page = Page::SelfTest; pageUntil = 0; break;
    case 'h': page = Page::Help; break;
    case 'g': page = Page::Agent; break;
    default: break;
  }
  dirty = true;
}

void readInput() {
  bool go = M5Cardputer.BtnA.wasPressed();
  if (page == Page::Agent) {
    if (go) sendKey("confirm");  // the side button is a confirm key here too
    if (M5Cardputer.Keyboard.isChange() && M5Cardputer.Keyboard.isPressed()) {
      auto &keys = M5Cardputer.Keyboard.keysState();
      if (keys.enter || keys.space) {
        // A two-key rule shows which key is missing; ENTER writes that one.
        if (selected < itemCount) sendKey(items[selected].bit);
      }
      for (char c : keys.word) {
        if (c == ';' && selected > 0) selected--;
        else if (c == '.' && selected + 1 < itemCount) selected++;
        else if (c == 'b') sendKey("block");
        else if (c == 'u') sendKey("unblock");
        else if (c == '`' || c == 'h') page = Page::Menu;
      }
      if (keys.del) page = Page::Menu;
      dirty = true;
    }
    return;
  }
  if (page == Page::Brain) {
    if (M5Cardputer.Keyboard.isChange() && M5Cardputer.Keyboard.isPressed()) {
      auto &keys = M5Cardputer.Keyboard.keysState();
      if (keys.enter || keys.space) go = true;
      else page = Page::Menu;
      dirty = true;
    }
    if (go) {
      autoDemo = false;
      lvIndex = random(kNumLv);
      azimuth = (int)random(-9, 10) * 10;
      launch();
    }
    return;
  }
  if (page == Page::Menu) {
    if (go) enterPage(kMenu[menuIndex].page);
    if (M5Cardputer.Keyboard.isChange() && M5Cardputer.Keyboard.isPressed()) {
      auto &keys = M5Cardputer.Keyboard.keysState();
      if (keys.enter || keys.space) enterPage(kMenu[menuIndex].page);
      for (char c : keys.word) {
        if (c == ';' && menuIndex > 0) menuIndex--;
        else if (c == '.' && menuIndex + 1 < kMenuCount) menuIndex++;
        for (int i = 0; i < kMenuCount; i++)
          if (c == kMenu[i].key) enterPage(kMenu[i].page);
      }
      dirty = true;
    }
    return;
  }
  if (M5Cardputer.Keyboard.isChange() && M5Cardputer.Keyboard.isPressed()) {
    auto &keys = M5Cardputer.Keyboard.keysState();
    if (page != Page::Main) {  // help, digest, lattice, self-test: any key returns to the menu
      page = Page::Menu;
      dirty = true;
      return;
    }
    bool back = keys.del;
    for (char c : keys.word) back = back || c == '`';
    if (back) {
      page = Page::Menu;
      dirty = true;
      return;
    }
    go = go || keys.enter || keys.space;
    for (char c : keys.word)
      if (c != ' ') onKey(c);
  }
  if (go) {
    autoDemo = false;
    launch();
  }
}

}  // namespace

void setup() {
  Serial.setRxBufferSize(2048);  // a console frame must fit while the arena animates
  auto cfg = M5.config();
  cfg.serial_baudrate = 115200;
  M5Cardputer.begin(cfg, true);
  M5Cardputer.Display.setRotation(1);
  M5Cardputer.Speaker.setVolume(80);
  canvas.setColorDepth(16);
  canvas.createSprite(M5Cardputer.Display.width(), M5Cardputer.Display.height());
  canvas.setTextSize(1);

  canvas.fillScreen(kBg);
  text(4, 4, kText, "SELF-TEST");
  text(4, 20, kMuted, "hashing and replaying the netlists...");
  canvas.pushSprite(0, 0);
  uint32_t t0 = millis();
  passed = c3s_self_test(&core, &policy, &report) != 0;
  selfTestMs = millis() - t0;
  runnable = report.core_decode == C3S_OK && report.policy_decode == C3S_OK;
  pageUntil = passed ? millis() + 3500 : 0;
  randomSeed(esp_random());

  Serial.printf("c3s escape core on Cardputer: self-test %s in %lu ms\n", passed ? "PASS" : "FAIL",
                (unsigned long)selfTestMs);
  Serial.printf("  %s sha256 %s %s\n", C3S_CORE_NAME, report.core_sha256, report.core_hash_ok ? "ok" : "MISMATCH");
  Serial.printf("  %s sha256 %s %s\n", C3S_POLICY_NAME, report.policy_sha256,
                report.policy_hash_ok ? "ok" : "MISMATCH");
  Serial.printf("  reference episodes %d/%d, core vs policy at rest %lu/%lu, live geometry %d/%d\n",
                report.episodes_ok, report.episodes, (unsigned long)report.rest_ok, (unsigned long)report.rest_rows,
                report.geometry_ok, report.episodes);
  draw();
}

void loop() {
  M5Cardputer.update();
  readInput();
  // Serial carries two things: a bare `d` asks for the digest (so its timing can be
  // recorded off-device), and `X|...` lines are frames from the boundary console.
  while (Serial.available()) {
    const char c = (char)Serial.read();
    if (c == '\n' || c == '\r') {
      serialLine[serialLen] = 0;
      if (serialLen >= 2 && serialLine[1] == '|') onHostLine(serialLine);
      serialLen = 0;
    } else if (c == 'd' && serialLen == 0) {
      page = Page::Digest;
      pageUntil = 0;
      digestRows = 0;
      digestPending = true;
      dirty = true;
    } else if (serialLen < (int)sizeof serialLine - 1) {
      serialLine[serialLen++] = c;
    }
  }
  uint32_t now = millis();
  // The host status line has to notice silence even when no frame arrives.
  static bool wasLive = false;
  const bool live = hostLive();
  if (live != wasLive) dirty = true;
  wasLive = live;

  if (page == Page::SelfTest && pageUntil && now > pageUntil) {
    page = Page::Menu;
    dirty = true;
  }
  if (phase == Phase::Looming && !paused) {
    uint32_t period = (uint32_t)(C3S_TICK_MS * kSlow[slowIndex]);
    if (now - lastTickMs > 500) lastTickMs = now - period;  // after a stall, do not race to catch up
    while (phase == Phase::Looming && now - lastTickMs >= period) {
      lastTickMs += period;
      advance();
    }
  }
  if (phase == Phase::TookOff) {
    dirty = true;  // animation
    if (now - phaseMs > 700) {
      phase = Phase::Ended;
      phaseMs = now;
    }
  }
  if (autoDemo && runnable && (page == Page::Main || page == Page::Brain) && (phase == Phase::Idle || phase == Phase::Ended) &&
      now - phaseMs > 2000) {
    lvIndex = random(kNumLv);
    azimuth = (int)random(-9, 10) * 10;
    launch();
  }
  if (dirty && now - lastDrawMs >= 33) {
    draw();
    lastDrawMs = now;
    dirty = false;
  }
  if (page == Page::Brain) dirty = true;  // it sways
  if (page == Page::Lattice) {
    latSpin += 0.03f;
    dirty = true;  // the lattice turns, so it needs a frame even when nothing ticks
  }
  // Only once the "computing" frame is on the screen: this blocks for seconds.
  if (digestPending && page == Page::Digest && !dirty) {
    uint8_t d[32];
    uint32_t t0 = millis();
    digestRows = c3s_domain_digest(&core, d);
    digestMs = millis() - t0;
    c3s_hex(d, 32, digestHex);
    digestPending = false;
    dirty = true;
    Serial.printf("digest %s over %lu rows in %lu ms\n", digestHex, (unsigned long)digestRows,
                  (unsigned long)digestMs);
  }
  delay(1);
}
