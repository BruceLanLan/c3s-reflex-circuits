/* math.h for the freestanding WebAssembly build (docs/sim). atan and tan are imported
 * from the page's JavaScript Math; fabs is the f64.abs instruction. */
#ifndef C3S_WASM_MATH_H
#define C3S_WASM_MATH_H

#define fabs __builtin_fabs

__attribute__((import_module("env"), import_name("atan"))) double atan(double);
__attribute__((import_module("env"), import_name("tan"))) double tan(double);

#endif
