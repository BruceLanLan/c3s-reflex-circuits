/* string.h for the freestanding WebAssembly build (docs/sim). With -mbulk-memory the
 * memset and memcpy builtins become memory.fill and memory.copy instructions. */
#ifndef C3S_WASM_STRING_H
#define C3S_WASM_STRING_H

#include <stddef.h>

#define memset __builtin_memset
#define memcpy __builtin_memcpy

static inline int strcmp(const char *a, const char *b) {
  while (*a && *a == *b) a++, b++;
  return (unsigned char)*a - (unsigned char)*b;
}

#endif
