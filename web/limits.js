// Capacity limits for the web app, kept in a DOM-free module so they can be
// unit-tested (web/test_dom.mjs). app.js touches `document` at module scope, so
// nothing there is importable under plain node.
//
// Measured cost of encodeIntoGame() on the browser main thread, by payload:
//    5 kB ->  79 ms        50 kB ->  1.4 s
//   20 kB -> 331 ms       100 kB ->  5.6 s
//                      200 kB -> 53 s   (all of it blocked, none of it useful)
//
// Superlinear, because the coder's BigInt state is O(payload bits) and is
// touched once per ply. The decisive point is that an oversized payload can
// never fit anyway: a chess game runs out after roughly 50-140 plies, which at
// ~4.8 bits/ply carries about 180 bytes. So everything past a few hundred bytes
// is a guaranteed failure the user was being made to wait for.
//
// 1 KiB sits far above any achievable payload and costs well under a second in
// the worst case.
export const MAX_PAYLOAD_BYTES = 1024;

// Rough capacity of a fresh carrier game, used only for the error message.
export const TYPICAL_CARRIER_BYTES = 180;

// Returns a user-facing reason to refuse this payload, or null if it is fine.
export function payloadRejection(bytes) {
  if (bytes <= MAX_PAYLOAD_BYTES) return null;
  return (
    `That message is ${bytes} bytes. A chess game carries about ${TYPICAL_CARRIER_BYTES}, ` +
    `so the limit here is ${MAX_PAYLOAD_BYTES} bytes — anything longer cannot be embedded.`
  );
}
