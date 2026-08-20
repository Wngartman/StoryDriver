import assert from "node:assert/strict";
import test from "node:test";

import { premiumBufferCoversNextBatch, premiumNarrationChunks, premiumNarrationUnits } from "./ttsPremiumPlan.js";

test("packs short sentences without separating quoted speech from attribution", () => {
  const text = '"Are you sure?" Mara asked. "I am," Lena said softly. The room went still.';
  const chunks = premiumNarrationChunks(text, 160);
  assert.equal(chunks.length, 3);
  assert.match(chunks[0], /\?" Mara asked\./);
  assert.match(chunks[1], /" Lena said softly\./);
  assert.equal(chunks[2], "The room went still.");
});

test("keeps a short delivery action with the dialogue it controls", () => {
  const units = premiumNarrationUnits('She leaned closer. "I missed you." Then she waited.', 180);
  assert.equal(units[0].text, 'She leaned closer. "I missed you."');
  assert.equal(units[0].containsDialogue, true);
  assert.equal(units[1].text, "Then she waited.");
});

test("marks speaker and paragraph boundaries without putting tags in text", () => {
  const units = premiumNarrationUnits('"Stay," Mara whispered. "I cannot," Lena said.\n\nThe door closed.', 180);
  assert.equal(units.length, 3);
  assert.equal(units[0].speakerChangeAfter, true);
  assert.equal(units[1].paragraphBreakAfter, true);
  assert.ok(units.every((unit) => !/\[(?:whisper|softly|angry|short pause)\]/i.test(unit.text)));
});

test("preserves paragraph boundaries and caps ordinary chunks", () => {
  const text = `${"A measured sentence follows. ".repeat(9)}\n\n${"A second paragraph continues. ".repeat(8)}`;
  const chunks = premiumNarrationChunks(text, 180);
  assert.ok(chunks.length >= 3);
  assert.ok(chunks.every((chunk) => chunk.length <= 180));
});

test("requires synthesis time plus reserve before playback begins", () => {
  assert.equal(
    premiumBufferCoversNextBatch([
      { duration: 20, synthesis_seconds: 35 },
      { duration: 20, synthesis_seconds: 35 },
    ]),
    false,
  );
  assert.equal(
    premiumBufferCoversNextBatch([
      { duration: 22, synthesis_seconds: 35 },
      { duration: 22, synthesis_seconds: 35 },
    ]),
    true,
  );
});
