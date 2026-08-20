import assert from "node:assert/strict";
import {
  premiumBatchStart,
  premiumBufferCoversNextBatch,
  premiumNarrationChunks,
} from "../../frontend/src/services/ttsPremiumPlan.js";

const prose = [
  "Elena checked the brass latch and listened to rain cross the apartment windows.",
  "'You knew before Tuesday,' Priya said quietly, setting the blue notebook beside the lamp.",
  "The deliberately long final sentence stays ordered and complete, while a practical clause boundary lets the premium scheduler divide it without dropping words, repeating text, or splitting the quotation from its attribution; everyone remains in the same room until the decision is made.",
].join(" ");
const chunks = premiumNarrationChunks(prose, 180);
assert.ok(chunks.length >= 2);
assert.ok(chunks.length < prose.split(/(?<=[.!?])\s+/).length + 2);
assert.ok(chunks.every((chunk) => chunk.length <= 180));
assert.equal(chunks.join(" ").replace(/\s+/g, " "), prose.replace(/\s+/g, " "));
assert.equal(premiumBatchStart(0), 0);
assert.equal(premiumBatchStart(3), 0);
assert.equal(premiumBatchStart(4), 4);
assert.equal(premiumBatchStart(11), 8);
assert.equal(
  premiumBufferCoversNextBatch([
    { duration: 8, synthesis_seconds: 20 },
    { duration: 7, synthesis_seconds: 20 },
    { duration: 9, synthesis_seconds: 20 },
    { duration: 6, synthesis_seconds: 20 },
  ]),
  true,
);
assert.equal(premiumBufferCoversNextBatch([{ duration: 8, synthesis_seconds: 20 }]), false);

console.log("PASS: premium chunks preserve text, stay ordered in batches of four, and enforce a measured startup reserve.");
