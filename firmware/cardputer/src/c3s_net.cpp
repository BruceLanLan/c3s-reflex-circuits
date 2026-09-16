#include "c3s_net.h"

#include <HTTPClient.h>
#include <Preferences.h>
#include <WiFi.h>

#include <string.h>

namespace c3s_net {
namespace {

// One store, one copy. WiFi.persistent(false) keeps the radio driver from writing a
// second copy of the credentials into its own NVS namespace, so "forget" really does
// remove them and there is one place to say is secret (docs/FIRMWARE.md).
Preferences store;
const char *kStore = "c3s-net";

char g_network[33] = "";   // what was typed on this device, never in the image
char g_secret[65] = "";    // likewise; never printed, never sent anywhere but the router
char g_console[48] = "";   // <ip or name>:<port> of the boundary console
char g_token[64] = "";     // the device token, paired once in front of a person
char g_id[20] = "";
char g_ip[16] = "";
char g_note[40] = "no network stored";
char g_post[40] = "";

Link g_link = LinkOff;
uint32_t g_attemptMs = 0, g_pollMs = 0, g_frameMs = 0, g_retryAfterMs = 0;
int g_scanCount = -2;  // -2 idle, -1 running, >= 0 results
bool g_started = false;

const uint32_t kPollMs = 1000;        // one frame a second, as the cable sends
const uint32_t kConnectGiveUpMs = 20000;
const uint32_t kRetryMs = 15000;
const uint16_t kHttpTimeoutMs = 1500;  // a key press must not hang the tick loop for long
const uint32_t kFrameFreshMs = 4000;

void say(const char *text) { snprintf(g_note, sizeof g_note, "%s", text); }

// Stable, and not a secret: it is how the console's log and its device list name this
// device. Derived from the radio's own address, so it survives a re-flash.
void loadDeviceId() {
  uint8_t mac[6] = {0};
  WiFi.macAddress(mac);
  snprintf(g_id, sizeof g_id, "c3s-%02x%02x%02x%02x%02x%02x", mac[0], mac[1], mac[2], mac[3], mac[4], mac[5]);
}

void save(const char *key, const char *value) {
  store.begin(kStore, false);
  store.putString(key, value);
  store.end();
}

void drop(const char *key) {
  store.begin(kStore, false);
  store.remove(key);
  store.end();
}

void startConnecting() {
  if (!g_network[0]) {
    g_link = LinkOff;
    say("no network stored");
    return;
  }
  WiFi.mode(WIFI_STA);
  WiFi.begin(g_network, g_secret);
  g_link = LinkConnecting;
  g_attemptMs = millis();
  say("connecting");
}

// The console's own URL. Nothing else is ever contacted.
bool url(char *out, size_t n, const char *path) {
  if (!g_console[0]) return false;
  snprintf(out, n, "http://%s%s", g_console, path);
  return true;
}

// One field out of a small JSON object, without a JSON library: enough for the two
// strings a pairing reply carries, and nothing on the device parses anything else.
bool jsonField(const String &body, const char *name, char *out, size_t n) {
  char needle[24];
  snprintf(needle, sizeof needle, "\"%s\"", name);
  int at = body.indexOf(needle);
  if (at < 0) return false;
  at = body.indexOf('"', body.indexOf(':', at + (int)strlen(needle)) + 1);
  if (at < 0) return false;
  const int end = body.indexOf('"', at + 1);
  if (end < 0) return false;
  const size_t len = (size_t)(end - at - 1);
  if (len >= n) return false;
  memcpy(out, body.c_str() + at + 1, len);
  out[len] = 0;
  return true;
}

void pollFrame() {
  char address[96];
  if (!g_token[0] || !url(address, sizeof address, "/api/device/frame")) return;
  WiFiClient client;
  HTTPClient http;
  http.setConnectTimeout(kHttpTimeoutMs);
  http.setTimeout(kHttpTimeoutMs);
  if (!http.begin(client, address)) {
    say("console address?");
    return;
  }
  http.addHeader("X-Reflex-Device-Token", g_token);
  const int code = http.GET();
  if (code == 200) {
    // The same lines the cable carries, handed to the same parser. A frame is dropped
    // whole if a line is missing (the `E|` count), exactly as over USB.
    String body = http.getString();
    static char line[900];
    size_t len = 0;
    for (size_t i = 0; i <= (size_t)body.length(); i++) {
      const char c = i < (size_t)body.length() ? body[i] : '\n';
      if (c == '\n' || c == '\r') {
        line[len] = 0;
        if (len >= 2 && line[1] == '|') c3s_host_line(line, true);
        len = 0;
      } else if (len < sizeof line - 1) {
        line[len++] = c;
      }
    }
    g_frameMs = millis();
    say("online");
  } else if (code == 403) {
    say("console: not paired");
  } else if (code > 0) {
    snprintf(g_note, sizeof g_note, "console said %d", code);
  } else {
    say("no answer from the console");
  }
  http.end();
}

}  // namespace

void begin() {
  loadDeviceId();
  WiFi.persistent(false);
  store.begin(kStore, true);
  store.getString("net", g_network, sizeof g_network);
  store.getString("sec", g_secret, sizeof g_secret);
  store.getString("console", g_console, sizeof g_console);
  store.getString("token", g_token, sizeof g_token);
  store.end();
  g_started = true;
  if (g_network[0]) startConnecting();
}

void loop() {
  if (!g_started) return;
  const uint32_t now = millis();
  if (g_scanCount == -1 && WiFi.scanComplete() >= 0) g_scanCount = WiFi.scanComplete();
  if (g_link == LinkOff || g_scanCount == -1) return;

  if (g_link == LinkConnecting) {
    if (WiFi.status() == WL_CONNECTED) {
      g_link = LinkOnline;
      snprintf(g_ip, sizeof g_ip, "%s", WiFi.localIP().toString().c_str());
      say(g_token[0] ? "online" : "online; not paired yet");
      // The address and the signal, never the network's name or its password.
      Serial.printf("wifi: online, ip %s, rssi %d dBm\n", g_ip, WiFi.RSSI());
    } else if (now - g_attemptMs > kConnectGiveUpMs) {
      g_link = LinkFailed;
      g_retryAfterMs = now + kRetryMs;
      say("could not join; retrying");
      Serial.println("wifi: could not join the stored network; retrying");
    }
    return;
  }
  if (g_link == LinkFailed) {
    if (now > g_retryAfterMs) startConnecting();
    return;
  }
  // Online. A radio that went away is said so on the screen, and the polls stop with it:
  // the console's heartbeat is these polls, so no Wi-Fi is never "the person is there".
  if (WiFi.status() != WL_CONNECTED) {
    g_link = LinkFailed;
    g_ip[0] = 0;
    g_retryAfterMs = now + 2000;
    say("Wi-Fi dropped");
    Serial.println("wifi: dropped; the console's heartbeat stops with it");
    return;
  }
  if (g_token[0] && now - g_pollMs >= kPollMs) {
    g_pollMs = now;
    pollFrame();
  }
}

bool configured() { return g_network[0] != 0; }
bool paired() { return g_token[0] != 0; }
Link link() { return g_link; }
const char *ssid() { return g_network; }
const char *ip() { return g_ip; }
int rssi() { return g_link == LinkOnline ? WiFi.RSSI() : 0; }
const char *note() { return g_note; }
const char *deviceId() { return g_id; }
const char *consoleHost() { return g_console; }
bool frameFresh() { return g_frameMs && millis() - g_frameMs < kFrameFreshMs; }

void scanStart() {
  WiFi.mode(WIFI_STA);
  WiFi.scanDelete();
  g_scanCount = -1;
  WiFi.scanNetworks(true);
}

int scanCount() { return g_scanCount < 0 ? -1 : g_scanCount; }
const char *scanSsid(int i) {
  static String held;
  held = WiFi.SSID(i);
  return held.c_str();
}
int scanRssi(int i) { return WiFi.RSSI(i); }

void saveNetwork(const char *network, const char *secret) {
  snprintf(g_network, sizeof g_network, "%s", network ? network : "");
  snprintf(g_secret, sizeof g_secret, "%s", secret ? secret : "");
  save("net", g_network);
  save("sec", g_secret);
  startConnecting();
}

void forgetNetwork() {
  WiFi.disconnect(true, true);
  memset(g_secret, 0, sizeof g_secret);  // out of RAM as well as out of NVS
  g_network[0] = 0;
  g_ip[0] = 0;
  drop("net");
  drop("sec");
  g_link = LinkOff;
  say("network forgotten");
  Serial.println("wifi: the stored network and its password are gone from this device");
}

void setConsole(const char *hostport) {
  snprintf(g_console, sizeof g_console, "%s", hostport ? hostport : "");
  save("console", g_console);
}

bool pairStart(char *codeOut, size_t codeSize) {
  char address[96];
  if (codeSize) codeOut[0] = 0;
  if (g_link != LinkOnline) {
    say("join a network first");
    return false;
  }
  if (!url(address, sizeof address, "/api/device/pair")) {
    say("set the console address first");
    return false;
  }
  char body[160];
  snprintf(body, sizeof body, "{\"device_id\":\"%s\",\"name\":\"cardputer-adv\"}", g_id);
  WiFiClient client;
  HTTPClient http;
  http.setConnectTimeout(kHttpTimeoutMs);
  http.setTimeout(kHttpTimeoutMs);
  if (!http.begin(client, address)) {
    say("console address?");
    return false;
  }
  http.addHeader("Content-Type", "application/json");
  const int code = http.POST((uint8_t *)body, strlen(body));
  bool ok = false;
  if (code == 200) {
    String reply = http.getString();
    char token[64] = "";
    // Held only if the person confirms the digits: until then the console accepts it for
    // nothing at all. The token is never printed, here or anywhere.
    if (jsonField(reply, "token", token, sizeof token) && jsonField(reply, "code", codeOut, codeSize)) {
      snprintf(g_token, sizeof g_token, "%s", token);
      save("token", g_token);
      memset(token, 0, sizeof token);
      say("say the digits to the console");
      ok = true;
    } else {
      say("could not read the reply");
    }
  } else if (code == 403) {
    say("no pairing window open");
  } else if (code > 0) {
    snprintf(g_note, sizeof g_note, "console said %d", code);
  } else {
    say("no answer from the console");
  }
  http.end();
  return ok;
}

void unpair() {
  memset(g_token, 0, sizeof g_token);
  drop("token");
  g_frameMs = 0;
  say("this device is no longer paired");
  Serial.println("wifi: the device token is gone from this device");
}

bool postBit(const char *bit, int value, const char *agentJson, const char *reasonJson, const char *code) {
  char address[96];
  if (g_link != LinkOnline || !g_token[0]) {
    snprintf(g_post, sizeof g_post, "%s", g_token[0] ? "no Wi-Fi: nothing sent" : "not paired: nothing sent");
    return false;
  }
  if (!url(address, sizeof address, "/api/tool")) {
    snprintf(g_post, sizeof g_post, "no console address");
    return false;
  }
  // The agent and the reason are the console's own JSON literals, from the frame's `C|`
  // line: spliced in as they are, so a confirm binds to exactly the call on the screen.
  char body[900];
  // A block is about the agent, not about one call, so it carries no reason — `null`,
  // which the console reads as "bound to nothing", exactly as the cable's `K|block` is.
  const bool bound = reasonJson && reasonJson[0];
  const int n = snprintf(body, sizeof body,
                         "{\"agent\":%s,\"%s\":%d,\"for_reason\":%s,\"code\":\"%s\"}",
                         agentJson, bit, value, bound ? reasonJson : "null", code ? code : "");
  if (n <= 0 || n >= (int)sizeof body) {
    snprintf(g_post, sizeof g_post, "the call is too long to send");
    return false;
  }
  WiFiClient client;
  HTTPClient http;
  http.setConnectTimeout(kHttpTimeoutMs);
  http.setTimeout(kHttpTimeoutMs);
  if (!http.begin(client, address)) {
    snprintf(g_post, sizeof g_post, "console address?");
    return false;
  }
  http.addHeader("Content-Type", "application/json");
  http.addHeader("X-Reflex-Device-Token", g_token);
  const int status = http.POST((uint8_t *)body, (size_t)n);
  if (status == 200) {
    snprintf(g_post, sizeof g_post, "%s sent over Wi-Fi", bit);
  } else if (status == 403) {
    snprintf(g_post, sizeof g_post, "console refused it (403)");
  } else if (status > 0) {
    snprintf(g_post, sizeof g_post, "console said %d", status);
  } else {
    snprintf(g_post, sizeof g_post, "no answer: nothing was written");
  }
  http.end();
  Serial.printf("wifi: %s -> %s\n", bit, g_post);
  return status == 200;
}

const char *lastPostNote() { return g_post; }

}  // namespace c3s_net
