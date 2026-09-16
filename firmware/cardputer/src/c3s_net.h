// W11: the same physical key, reached over the person's own Wi-Fi instead of a cable.
//
// The one property this must not lose is the reason the device exists: a model can write
// a request, but it cannot press a key on a desk. So:
//
//   * the device never listens. There is no server on it, no subscription, nothing the
//     network can send that makes it write. It connects out, polls the console's frame,
//     and posts exactly one thing: what a physical key press produced.
//   * the device says who it is with a device token, paired once in front of a person by
//     matching four digits, kept in NVS and never in the image.
//   * the network is not trusted, and neither is its absence: when the radio drops, the
//     polls stop, the screen says so and the console's heartbeat goes with them — which,
//     with a heartbeat halt installed, stops every agent. Nothing here ever fails open.
//
// USB stays the default and the fallback: when both links are live, a key press goes down
// the cable. See docs/FIRMWARE.md ("Over Wi-Fi") and reflex-console/cardputer_relay.py.

#ifndef C3S_NET_H
#define C3S_NET_H

#include <stddef.h>
#include <stdint.h>

// One line of a console frame, in the `S|I|C|L|E` wire format, from either link.
// Defined in main.cpp; `wifi` says which link carried it, because the Agent page says so.
void c3s_host_line(char *line, bool wifi);

namespace c3s_net {

enum Link { LinkOff, LinkConnecting, LinkOnline, LinkFailed };

void begin();  // read what is stored; connect if a network was stored
void loop();   // non-blocking: connect, poll, retry. Never holds up the 5 ms tick for long.

bool configured();  // a network is stored
bool paired();      // a device token is stored
Link link();
const char *ssid();
const char *ip();
int rssi();
const char *note();  // one short line for the screen: what the link is doing, or why not
const char *deviceId();
const char *consoleHost();
bool frameFresh();  // a frame arrived over Wi-Fi recently

// -- provisioning, from the Network page. Credentials are typed here and stored here;
// -- the build refuses to compile a source that carries any (scripts/check_credentials.py).
void scanStart();
int scanCount();  // -1 while the scan runs
const char *scanSsid(int i);
int scanRssi(int i);
void saveNetwork(const char *network, const char *secret);
void forgetNetwork();
void setConsole(const char *hostport);

bool pairStart(char *codeOut, size_t codeSize);  // POST /api/device/pair, outbound
void unpair();                                   // wipe the token from this device

// -- what a key press does over Wi-Fi. `agentJson` and `reasonJson` are the JSON string
// -- literals the console put in the frame's `C|` line, spliced in as they are.
bool postBit(const char *bit, int value, const char *agentJson, const char *reasonJson, const char *code);
const char *lastPostNote();

}  // namespace c3s_net

#endif
