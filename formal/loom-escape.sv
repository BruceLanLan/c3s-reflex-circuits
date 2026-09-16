// Temporal properties of the LoomEscape-16 escape core, proven by Yosys through
// scripts/check_properties.py. One module per property, so a failing proof names
// the property; `dut` wires the two committed netlists, read as BLIF, into vector
// ports (a formal tool cannot reach inside a BLIF module, so the core's six latch
// bits are exported as s0..s5 and the policy runs alongside it to supply gf).
//
// Motor codes: 0 hold, 1 raising wings, 2 short-mode takeoff, 3 long-mode takeoff.
// State: a 3-bit wing-raise counter, then a 3-bit refractory timer, LSB first.
//
// WING and REFR must equal wing_raise_ticks and refractory_ticks in
// circuits/loom-escape/decision-table.json; check_properties.py refuses to run
// when they differ, so this spec cannot drift away from the calibrated teacher.
`define WING 4
`define REFR 7

module dut (
  input [16:0] x,
  output [1:0] motor,
  output gf,
  output parallel,
  output [2:0] raise_c,
  output [2:0] refr
);
  c3s_core core (
    .x0(x[0]), .x1(x[1]), .x2(x[2]), .x3(x[3]), .x4(x[4]), .x5(x[5]),
    .x6(x[6]), .x7(x[7]), .x8(x[8]), .x9(x[9]), .x10(x[10]), .x11(x[11]),
    .x12(x[12]), .x13(x[13]), .x14(x[14]), .x15(x[15]), .x16(x[16]),
    .y0(motor[0]), .y1(motor[1]),
    .s0(raise_c[0]), .s1(raise_c[1]), .s2(raise_c[2]),
    .s3(refr[0]), .s4(refr[1]), .s5(refr[2])
  );
  c3s_policy policy (
    .x0(x[0]), .x1(x[1]), .x2(x[2]), .x3(x[3]), .x4(x[4]), .x5(x[5]),
    .x6(x[6]), .x7(x[7]), .x8(x[8]), .x9(x[9]), .x10(x[10]), .x11(x[11]),
    .x12(x[12]), .x13(x[13]), .x14(x[14]), .x15(x[15]),
    .y0(gf), .y1(parallel)
  );
endmodule

// P1: a takeoff is followed by no takeoff for the next REFR ticks.
module p1 (input [16:0] x);
  wire [1:0] motor;
  wire gf, parallel;
  wire [2:0] raise_c, refr;
  dut d (x, motor, gf, parallel, raise_c, refr);

  wire takeoff = motor == 2'd2 || motor == 2'd3;
  // Ticks elapsed since the last takeoff, saturating: 1 on the tick right after a
  // takeoff, so a takeoff needs REFR + 1 elapsed ticks (REFR blocked ones between).
  reg [3:0] since = 4'd15;  // no takeoff has happened at reset
  always @($global_clock) since <= takeoff ? 4'd1 : (since > `REFR ? since : since + 4'd1);
  always @* assert (!takeoff || since > `REFR);
endmodule

// P2: a long-mode takeoff is immediately preceded by WING raising ticks.
module p2 (input [16:0] x);
  wire [1:0] motor;
  wire gf, parallel;
  wire [2:0] raise_c, refr;
  dut d (x, motor, gf, parallel, raise_c, refr);

  reg [2:0] run = 3'd0;  // consecutive raising ticks just before this one, saturating
  always @($global_clock) run <= (motor == 2'd1) ? (run >= `WING ? run : run + 3'd1) : 3'd0;
  always @* assert (motor != 2'd3 || run >= `WING);
endmodule

// P3: not standing, or refractory: the core holds and clears the wing counter.
module p3 (input [16:0] x);
  wire [1:0] motor;
  wire gf, parallel;
  wire [2:0] raise_c, refr;
  dut d (x, motor, gf, parallel, raise_c, refr);

  wire quiet = !x[16] || refr != 3'd0;
  reg prev_quiet = 1'b0;
  always @($global_clock) prev_quiet <= quiet;
  always @* assert (!quiet || motor == 2'd0);
  always @* assert (!prev_quiet || raise_c == 3'd0);
endmodule

// P4: a short-mode takeoff needs the giant fiber and fewer than WING raises.
module p4 (input [16:0] x);
  wire [1:0] motor;
  wire gf, parallel;
  wire [2:0] raise_c, refr;
  dut d (x, motor, gf, parallel, raise_c, refr);

  always @* assert (motor != 2'd2 || (gf && raise_c < `WING));
endmodule

// P5: the state invariant that carves the 12 reachable states out of 64.
module p5 (input [16:0] x);
  wire [1:0] motor;
  wire gf, parallel;
  wire [2:0] raise_c, refr;
  dut d (x, motor, gf, parallel, raise_c, refr);

  always @* assert (raise_c == 3'd0 || (refr == 3'd0 && raise_c <= `WING));
endmodule
