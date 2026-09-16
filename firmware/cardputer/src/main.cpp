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

enum class Page { Main, SelfTest, Help, Digest, Lattice };
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
  text(4, 122, kMuted, "any key to continue");
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
      "serial 115200 logs every tick",
      "any key to close",
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
  text(4, 122, kMuted, "logic depth left to right; any key to go back");
}

void drawDigest() {
  char buf[64];
  text(4, 4, kText, "WHOLE-DOMAIN DIGEST");
  text(4, 20, kMuted, "SHA-256 chain over every (input, state) row");
  snprintf(buf, sizeof buf, "%s: 8,388,608 rows", C3S_CORE_NAME);
  text(4, 34, kText, buf);
  if (!digestRows) {
    text(4, 52, kMuted, "computing on this device, a few seconds..");
    text(4, 122, kMuted, "any key to go back");
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
  text(4, 122, kMuted, "any key to go back");
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
    default: break;
  }
  dirty = true;
}

void readInput() {
  bool go = M5Cardputer.BtnA.wasPressed();
  if (M5Cardputer.Keyboard.isChange() && M5Cardputer.Keyboard.isPressed()) {
    auto &keys = M5Cardputer.Keyboard.keysState();
    if (page != Page::Main) {
      page = Page::Main;
      dirty = true;
      return;
    }
    go = go || keys.enter || keys.space;
    for (char c : keys.word)
      if (c != ' ') onKey(c);
  }
  if (go) {
    if (page != Page::Main) page = Page::Main;
    autoDemo = false;
    launch();
  }
}

}  // namespace

void setup() {
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
  // A host can ask for the digest over serial, so its timing can be recorded off-device.
  while (Serial.available()) {
    if ((char)Serial.read() == 'd') {
      page = Page::Digest;
      pageUntil = 0;
      digestRows = 0;
      digestPending = true;
      dirty = true;
    }
  }
  uint32_t now = millis();

  if (page == Page::SelfTest && pageUntil && now > pageUntil) {
    page = Page::Main;
    phaseMs = now;
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
  if (autoDemo && runnable && page == Page::Main && (phase == Phase::Idle || phase == Phase::Ended) &&
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
