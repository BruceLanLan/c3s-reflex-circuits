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
#include "c3s_net.h"

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

enum class Page { Main, SelfTest, Help, Digest, Lattice, Agent, Menu, Brain, Network };
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

// Set by frames from the boundary console (agent page below). A frame reaches this device
// over the cable or over Wi-Fi; the page reads the same fields either way and says which
// link brought them. When both are live the cable wins: it is the stronger arrangement.
uint32_t hostSeenMs = 0, usbSeenMs = 0, wifiSeenMs = 0;
bool hostEver = false;
int waitingCount = 0;
int blockedCount = 0;  // agents the console reports blocked
bool hostLive() { return hostEver && millis() - hostSeenMs < 4000; }
bool usbLive() { return usbSeenMs && millis() - usbSeenMs < 4000; }
bool wifiLive() { return wifiSeenMs && millis() - wifiSeenMs < 4000; }
const char *linkName() { return usbLive() ? "USB" : wifiLive() ? "Wi-Fi" : "none"; }

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
      "g agent  8 network  s STOP all  ` menu",
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
// The boundary console (reflex-console, cardputer_relay.py) sends a frame a second, over
// USB serial or — once this device has been through the Network page — over Wi-Fi, where
// the device fetches it instead: which circuits are installed, what is waiting for a
// person, the latest decision. ENTER writes `confirm` for the selected agent, `b` blocks
// it, `u` lifts the block. A model can write its own request; it cannot press this key,
// on either link: over Wi-Fi nothing listens here, and the only request that writes
// anything is the one a finger on this keyboard produces.
//
// What the radio costs is honest bookkeeping, not a property: a provisioned device holds
// the network's password and a device token in its flash (docs/FIRMWARE.md), and a key
// press over Wi-Fi is a person's bit arriving over HTTP rather than down a wire nobody
// else can reach. USB needs neither, which is why it stays the default and the fallback,
// and why the page says which link a frame came in on.

struct AgentItem {
  char agent[31];
  char cls[8];
  char why[61];
  char bit[10];
  int tick;
  bool armed;
  // Only a Wi-Fi frame carries these (the `C|` line): the two digits shown beside the
  // call, and the agent and the reason as JSON string literals. The `I|` fields above are
  // display text — ASCII, cut to fit 240 px — and a confirm binds on the reason exactly,
  // so a write uses these and never what is on the screen.
  char code[4];
  char agentJson[160];
  char reasonJson[560];
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

void onHostLine(char *line, bool wifi) {
  char *f[10];
  int n = fields(line, f, 10);
  hostSeenMs = millis();
  if (wifi) {
    wifiSeenMs = hostSeenMs;
  } else {
    usbSeenMs = hostSeenMs;
  }
  if (f[0][0] == 'S' && n >= 5) {
    copyField(summary, sizeof summary, f[1]);
    nGranted = atoi(f[2]);
    nRefused = atoi(f[3]);
    copyField(chainState, sizeof chainState, f[4]);
    blockedCount = n >= 6 ? atoi(f[5]) : 0;
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
    it.code[0] = it.agentJson[0] = it.reasonJson[0] = 0;
  } else if (f[0][0] == 'C' && n >= 5 && itemsInCount > 0 && atoi(f[1]) == itemsInCount - 1) {
    // What a Wi-Fi write needs, sent right after the item it belongs to.
    AgentItem &it = itemsIn[itemsInCount - 1];
    copyField(it.code, sizeof it.code, f[2]);
    copyField(it.agentJson, sizeof it.agentJson, f[3]);
    copyField(it.reasonJson, sizeof it.reasonJson, f[4]);
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

// What the last key press did, for the bottom line of the Agent page. It says which link
// carried it, or why it went nowhere; it fades so the page goes back to the last decision.
char keyNote[40] = "";
uint32_t keyNoteMs = 0;

void setKeyNote(const char *fmt, ...) {
  va_list args;
  va_start(args, fmt);
  vsnprintf(keyNote, sizeof keyNote, fmt, args);
  va_end(args);
  keyNoteMs = millis();
}

bool keyNoteFresh() { return keyNote[0] && millis() - keyNoteMs < 6000; }

// Over Wi-Fi a key press is one HTTP POST the device makes outwards, carrying the
// console's own code and reason for the selected item. Lifting a block and resuming are
// not sent over the network at all — the console refuses them there, on purpose — so the
// page says "USB only" instead of pretending.
bool sendKeyOverWifi(const char *action) {
  const AgentItem &it = items[selected];
  if (strcmp(action, "unblock") == 0) {
    setKeyNote("lifting a block: USB only");
    return false;
  }
  if (!it.code[0] || !it.agentJson[0]) {
    setKeyNote("this frame carries no code");
    return false;
  }
  const bool blocking = strcmp(action, "block") == 0;
  const char *bit = blocking ? "blocked" : action;
  const bool ok = c3s_net::postBit(bit, 1, it.agentJson, blocking ? "" : it.reasonJson, it.code);
  setKeyNote("%s", c3s_net::lastPostNote());
  return ok;
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
  if (!usbLive() && wifiLive()) {
    if (!sendKeyOverWifi(action)) {
      dirty = true;
      return;
    }
  } else {
    // Confirms name the selected item, so the console binds them to that call and not the
    // agent's next one; block and unblock are about the agent.
    if (strncmp(action, "confirm", 7) == 0) Serial.printf("K|%s|%s|%d\n", action, items[selected].agent, selected);
    else Serial.printf("K|%s|%s\n", action, items[selected].agent);
    setKeyNote("%s sent over USB", action);
  }
  if (sound) M5Cardputer.Speaker.tone(strcmp(action, "block") == 0 ? 700 : 2600, 50);
  dirty = true;
}

// `s` from any page: block every agent the console knows. `r` on the agent page lifts
// the block (a sticky halt still needs a confirm per agent — that is the rule's point).
void sendAll(const char *action) {
  static uint32_t lastMs = 0;
  const uint32_t now = millis();
  if (now - lastMs < 600) return;
  lastMs = now;
  if (!usbLive() && wifiLive()) {
    // Over Wi-Fi there is no "every agent the console knows": a device token may write a
    // bit only for an agent that is waiting for a person, and only with that call's own
    // code. So this blocks the agents on this screen, and says exactly that. Resuming
    // them is a person's action on the console or over USB.
    if (strcmp(action, "stop_all") != 0) {
      setKeyNote("resuming: USB or the console");
      dirty = true;
      return;
    }
    int sent = 0;
    for (int i = 0; i < itemCount; i++) {
      if (!items[i].code[0] || !items[i].agentJson[0]) continue;
      if (c3s_net::postBit("blocked", 1, items[i].agentJson, "", items[i].code)) sent++;
    }
    setKeyNote("blocked %d of %d on this screen", sent, itemCount);
    if (sound) M5Cardputer.Speaker.tone(520, 160);
    dirty = true;
    return;
  }
  Serial.printf("K|%s|*\n", action);
  setKeyNote("%s sent over USB", action);
  if (sound) M5Cardputer.Speaker.tone(strcmp(action, "stop_all") == 0 ? 520 : 2200, 160);
  dirty = true;
}

void drawAgent() {
  char buf[72];
  const bool live = hostEver && millis() - hostSeenMs < 4000;
  text(4, 3, kText, "C3S CIRCUIT AGENT");
  if (live) {
    snprintf(buf, sizeof buf, "host ok %s", linkName());
    text(236 - 6 * (int)strlen(buf), 3, kOk, buf);
  } else {
    text(172, 3, kBad, "no host");
  }
  if (!live) {
    text(4, 26, kMuted, "waiting for the boundary console");
    text(4, 40, kMuted, "over USB, on the computer:");
    text(4, 54, kText, "REFLEX_CARDPUTER=1 python console.py");
    text(4, 70, kMuted, "or over Wi-Fi: see the Network page");
    if (c3s_net::configured()) {
      snprintf(buf, sizeof buf, "Wi-Fi: %.30s", c3s_net::note());
      text(4, 84, c3s_net::link() == c3s_net::LinkOnline ? kMuted : kPar, buf);
    }
    text(4, 98, kMuted, "this key only writes confirm/blocked,");
    text(4, 110, kMuted, "and only when a finger presses it");
    text(4, 124, kMuted, "` or DEL: menu");
    return;
  }
  text(4, 15, kMuted, summary);
  snprintf(buf, sizeof buf, "granted %d  refused %d  chain %s", nGranted, nRefused, chainState);
  text(4, 26, kMuted, buf);
  if (blockedCount) {
    snprintf(buf, sizeof buf, "%d BLOCKED", blockedCount);
    canvas.fillRect(166, 25, 72, 10, kBad);
    canvas.setTextColor(kBg);
    canvas.drawString(buf, 170, 26);
  }
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
  if (keyNoteFresh()) {
    snprintf(buf, sizeof buf, "%.39s", keyNote);
    text(4, 113, kPar, buf);
  } else {
    snprintf(buf, sizeof buf, "%c %.37s", lastGranted ? '+' : 'x', lastLine);
    text(4, 113, lastGranted ? kOk : kBad, buf);
  }
  if (!usbLive() && wifiLive()) text(4, 125, kMuted, "ENT ok b block s STOP (Wi-Fi) ` menu");
  else text(4, 125, kMuted, "ENT ok b/u block s STOP r resume ` menu");
}

// ---- network page: the cable, made optional ---------------------------------------
//
// Everything Wi-Fi needs is typed here and stored here: the network's name, its password,
// the console's address, and the device token a person paired by matching four digits.
// None of it is in the image — scripts/check_credentials.py fails the build if anyone
// writes a network or a password into a source file — and a flash dump of a device that
// has been through this page carries all of it, which is why docs/FIRMWARE.md says a
// provisioned device's flash is a secret and a released image is built unprovisioned.

enum class NetMode { Info, Scan, EnterName, EnterSecret, EnterConsole, Pairing };
NetMode netMode = NetMode::Info;
char entry[65] = "";
int entryLen = 0;
char chosenName[33] = "";
char pairCode[8] = "";
int netSel = 0;

const char *linkWord() {
  switch (c3s_net::link()) {
    case c3s_net::LinkOnline: return "online";
    case c3s_net::LinkConnecting: return "connecting";
    case c3s_net::LinkFailed: return "not joined";
    default: return "off";
  }
}

void enterEntry(NetMode mode) {
  netMode = mode;
  memset(entry, 0, sizeof entry);
  entryLen = 0;
  dirty = true;
}

void leaveEntry() {
  memset(entry, 0, sizeof entry);  // a password does not stay in RAM after it is used
  entryLen = 0;
  netMode = NetMode::Info;
  dirty = true;
}

void drawNetworkInfo() {
  char buf[64];
  text(4, 3, kText, "NETWORK");
  snprintf(buf, sizeof buf, "Wi-Fi %s", linkWord());
  text(236 - 6 * (int)strlen(buf), 3, c3s_net::link() == c3s_net::LinkOnline ? kOk : kMuted, buf);

  text(4, 17, kMuted, "network");
  text(62, 17, kText, c3s_net::configured() ? c3s_net::ssid() : "none stored");
  text(4, 27, kMuted, "address");
  if (c3s_net::link() == c3s_net::LinkOnline) {
    snprintf(buf, sizeof buf, "%s  %d dBm", c3s_net::ip(), c3s_net::rssi());
  } else {
    snprintf(buf, sizeof buf, "-");
  }
  text(62, 27, kText, buf);
  text(4, 37, kMuted, "console");
  text(62, 37, kText, c3s_net::consoleHost()[0] ? c3s_net::consoleHost() : "not set");
  text(4, 47, kMuted, "paired");
  if (c3s_net::paired()) {
    snprintf(buf, sizeof buf, "yes, as %s", c3s_net::deviceId());
    text(62, 47, kOk, buf);
  } else {
    text(62, 47, kPar, "no; the console cannot hear it");
  }
  snprintf(buf, sizeof buf, "%.39s", c3s_net::note());
  text(4, 60, kPar, buf);

  text(4, 76, kMuted, "1 pick a network   2 type its name");
  text(4, 87, kMuted, "3 console address  4 pair by code");
  text(4, 98, kMuted, "5 forget network   6 unpair");
  text(4, 112, kDim, "what you type stays in this flash only");
  text(4, 125, kMuted, "USB still works with no network at all");
}

void drawNetworkScan() {
  char buf[64];
  text(4, 3, kText, "PICK A NETWORK");
  const int n = c3s_net::scanCount();
  if (n < 0) {
    text(4, 30, kMuted, "looking for networks...");
    text(4, 125, kMuted, "DEL: back");
    return;
  }
  if (n == 0) text(4, 30, kMuted, "none found; DEL to go back, 2 to type it");
  for (int i = 0; i < n && i < 7; i++) {
    const int y = 16 + i * 14;
    if (i == netSel) {
      canvas.fillRect(0, y - 2, 240, 13, kPanel);
      canvas.fillRect(0, y - 2, 2, 13, kPar);
    }
    snprintf(buf, sizeof buf, "%.28s", c3s_net::scanSsid(i));
    text(6, y, i == netSel ? kText : kMuted, buf);
    snprintf(buf, sizeof buf, "%d dBm", c3s_net::scanRssi(i));
    text(190, y, kDim, buf);
  }
  text(4, 125, kMuted, "; . choose   ENTER password   DEL back");
}

void drawNetworkEntry() {
  char buf[72];
  const bool secret = netMode == NetMode::EnterSecret;
  text(4, 3, kText, secret ? "PASSWORD" : netMode == NetMode::EnterName ? "NETWORK NAME" : "CONSOLE ADDRESS");
  if (secret) {
    snprintf(buf, sizeof buf, "for %.30s", chosenName);
    text(4, 18, kMuted, buf);
  } else if (netMode == NetMode::EnterConsole) {
    text(4, 18, kMuted, "the console, e.g. 192.168.1.10:8765");
  } else {
    text(4, 18, kMuted, "exactly as the router shows it");
  }
  canvas.fillRect(4, 40, 232, 16, kPanel);
  if (secret) {
    char stars[65];
    const int n = entryLen < 37 ? entryLen : 37;
    for (int i = 0; i < n; i++) stars[i] = '*';
    stars[n] = 0;
    text(8, 44, kText, stars);
  } else {
    snprintf(buf, sizeof buf, "%.37s", entry);
    text(8, 44, kText, buf);
  }
  if (secret) {
    text(4, 70, kMuted, "it goes to this device's flash and to");
    text(4, 82, kMuted, "the router, nowhere else. It is never");
    text(4, 94, kMuted, "printed, logged or sent to the console.");
  } else {
    text(4, 70, kMuted, "the console answers on its own name, so");
    text(4, 82, kMuted, "use the address it printed at startup");
    text(4, 94, kDim, "it must be bound to 0.0.0.0");
  }
  text(4, 125, kMuted, "ENTER save   DEL erase   DEL empty: back");
}

void drawNetworkPairing() {
  text(4, 3, kText, "PAIR WITH THE CONSOLE");
  if (!pairCode[0]) {
    text(4, 30, kPar, c3s_net::note());
    text(4, 50, kMuted, "a person opens a window on the console:");
    text(4, 64, kText, "c3s pair-device");
    text(4, 125, kMuted, "any key: back");
    return;
  }
  text(4, 22, kMuted, "these four digits, on the console:");
  canvas.setTextSize(3);
  canvas.setTextColor(kOk);
  canvas.drawString(pairCode, 74, 44);
  canvas.setTextSize(1);
  text(4, 84, kMuted, "c3s pair-device --confirm <digits>");
  text(4, 98, kDim, "they match only if the console holds");
  text(4, 110, kDim, "the same token it handed this device");
  text(4, 125, kMuted, "any key: back");
}

void drawNetwork() {
  switch (netMode) {
    case NetMode::Scan: drawNetworkScan(); break;
    case NetMode::EnterName:
    case NetMode::EnterSecret:
    case NetMode::EnterConsole: drawNetworkEntry(); break;
    case NetMode::Pairing: drawNetworkPairing(); break;
    default: drawNetworkInfo(); break;
  }
}

void networkKeys() {
  if (!M5Cardputer.Keyboard.isChange() || !M5Cardputer.Keyboard.isPressed()) return;
  auto &keys = M5Cardputer.Keyboard.keysState();
  dirty = true;

  if (netMode == NetMode::EnterName || netMode == NetMode::EnterSecret || netMode == NetMode::EnterConsole) {
    if (keys.enter) {
      if (netMode == NetMode::EnterName) {
        copyField(chosenName, sizeof chosenName, entry);
        enterEntry(NetMode::EnterSecret);
      } else if (netMode == NetMode::EnterSecret) {
        c3s_net::saveNetwork(chosenName, entry);
        leaveEntry();
      } else {
        c3s_net::setConsole(entry);
        leaveEntry();
      }
      return;
    }
    if (keys.del) {
      if (entryLen == 0) {  // an empty line is the way back, so every character can be typed
        leaveEntry();
      } else {
        entry[--entryLen] = 0;
      }
      return;
    }
    for (char c : keys.word)
      if (c >= 32 && c < 127 && entryLen < (int)sizeof entry - 1) entry[entryLen++] = c;
    entry[entryLen] = 0;
    return;
  }

  if (netMode == NetMode::Scan) {
    const int n = c3s_net::scanCount();
    if (keys.del) {
      netMode = NetMode::Info;
      return;
    }
    if (keys.enter && n > 0 && netSel < n) {
      copyField(chosenName, sizeof chosenName, c3s_net::scanSsid(netSel));
      enterEntry(NetMode::EnterSecret);
      return;
    }
    for (char c : keys.word) {
      if (c == ';' && netSel > 0) netSel--;
      else if (c == '.' && netSel + 1 < n && netSel < 6) netSel++;
      else if (c == '2') enterEntry(NetMode::EnterName);
    }
    return;
  }

  if (netMode == NetMode::Pairing) {
    netMode = NetMode::Info;
    return;
  }

  if (keys.del) {
    page = Page::Menu;
    return;
  }
  for (char c : keys.word) {
    switch (c) {
      case '1': netSel = 0; c3s_net::scanStart(); netMode = NetMode::Scan; break;
      case '2': enterEntry(NetMode::EnterName); break;
      case '3': enterEntry(NetMode::EnterConsole); break;
      case '4':
        pairCode[0] = 0;
        c3s_net::pairStart(pairCode, sizeof pairCode);
        netMode = NetMode::Pairing;
        break;
      case '5': c3s_net::forgetNetwork(); break;
      case '6': c3s_net::unpair(); break;
      case '`': page = Page::Menu; break;
      default: break;
    }
  }
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
  text(4, 3, kText, "FLY CELLS");
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
    Serial.printf("cell view: %lu ms per frame over 60 frames\n", (unsigned long)(brainMsSum / 60));
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
    {'2', "Fly cells, real shapes", "26 published cells lit by the circuit", Page::Brain},
    {'3', "Agent confirm key", "approve what the boundary held back", Page::Agent},
    {'4', "Gate lattice", "173 NAND gates, each lit by its value", Page::Lattice},
    {'5', "Whole-domain digest", "8,388,608 rows on this chip, ~10 s", Page::Digest},
    {'6', "Self-test", "hashes and reference episodes", Page::SelfTest},
    {'7', "Keys", "every key on every page", Page::Help},
    {'8', "Network", "reach the console over Wi-Fi, no cable", Page::Network},
};
const int kMenuCount = sizeof(kMenu) / sizeof(kMenu[0]);
int menuIndex = 0;

void drawMenu() {
  char buf[48];
  text(4, 3, kText, "C3S CIRCUIT AGENT");
  text(206, 3, passed ? kOk : kBad, passed ? "PASS" : "FAIL");
  for (int i = 0; i < kMenuCount; i++) {
    const int y = 15 + i * 13;
    if (i == menuIndex) {
      canvas.fillRect(0, y - 2, 240, 12, kPanel);
      canvas.fillRect(0, y - 2, 2, 12, kPar);
    }
    snprintf(buf, sizeof buf, "%c  %s", kMenu[i].key, kMenu[i].label);
    text(6, y, i == menuIndex ? kText : kMuted, buf);
    if (kMenu[i].page == Page::Agent) {
      if (hostLive() && blockedCount) {
        snprintf(buf, sizeof buf, "%d blocked", blockedCount);
        text(172, y, kBad, buf);
      } else if (hostLive() && waitingCount) {
        snprintf(buf, sizeof buf, "%d waiting", waitingCount);
        text(172, y, kPar, buf);
      } else {
        text(172, y, hostLive() ? kOk : kDim, hostLive() ? "host ok" : "no host");
      }
    }
    if (kMenu[i].page == Page::Network) {
      text(172, y, c3s_net::link() == c3s_net::LinkOnline ? kOk : kDim, linkWord());
    }
  }
  text(4, 120, kMuted, hostLive() ? "s: STOP every agent" : kMenu[menuIndex].note);
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
  } else if (page == Page::Network) {
    drawNetwork();
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
    case '8': page = Page::Network; break;
    case 's': if (hostLive()) sendAll("stop_all"); break;
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
        else if (c == 's') sendAll("stop_all");
        else if (c == 'r') sendAll("resume_all");
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
      bool stop = false;
      for (char c : keys.word) stop = stop || c == 's';
      if (stop && hostLive()) sendAll("stop_all");
      else if (keys.enter || keys.space) go = true;
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
  if (page == Page::Network) {
    networkKeys();
    return;
  }
  if (page == Page::Menu) {
    if (go) enterPage(kMenu[menuIndex].page);
    if (M5Cardputer.Keyboard.isChange() && M5Cardputer.Keyboard.isPressed()) {
      auto &keys = M5Cardputer.Keyboard.keysState();
      if (keys.enter || keys.space) enterPage(kMenu[menuIndex].page);
      for (char c : keys.word) {
        if (c == 's' && hostLive()) sendAll("stop_all");
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

// A frame line that arrived over Wi-Fi (c3s_net::loop), handed to the same parser the
// cable's lines go through: the Agent page cannot tell them apart, except that it says
// which link brought them.
void c3s_host_line(char *line, bool wifi) { onHostLine(line, wifi); }

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
  // W11: whatever this device was told about a network, and nothing from the image.
  c3s_net::begin();
  Serial.printf("device %s: %s%s\n", c3s_net::deviceId(),
                c3s_net::configured() ? "a network is stored" : "no network stored; USB only",
                c3s_net::paired() ? ", paired with a console" : "");
  draw();
}

void loop() {
  M5Cardputer.update();
  readInput();
  c3s_net::loop();  // W11: connect, poll the console's frame, retry. Never listens.
  // Serial carries two things: a bare `d` asks for the digest (so its timing can be
  // recorded off-device), and `X|...` lines are frames from the boundary console.
  while (Serial.available()) {
    const char c = (char)Serial.read();
    if (c == '\n' || c == '\r') {
      serialLine[serialLen] = 0;
      if (serialLen >= 2 && serialLine[1] == '|') onHostLine(serialLine, false);
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
  // While a console is listening, say this device is present. A halt policy with a
  // heartbeat rule then stops every agent when the device is unplugged or goes quiet.
  static uint32_t lastBeatMs = 0;
  if (hostEver && now - lastBeatMs >= 2000) {
    Serial.print("H|\n");
    lastBeatMs = now;
  }
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
  if (page == Page::Network) dirty = true;  // the link, the scan and the signal all move
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
