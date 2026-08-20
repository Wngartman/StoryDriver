import assert from "node:assert/strict";
import {
  buildFollowAudioChunkPlan,
  buildTtsFollowPlan,
  normalizeTtsFollowMode,
} from "../frontend/src/services/ttsFollowPlan.js";

const sample = [
  "Mara lifted the lantern, its brass frame warm against her palm, and listened.",
  "\"The door is breathing,\" Tomas said; he kept one hand on the map.",
  "",
  "Beyond the archive desk, dust curled through the moonlit stacks -- slow, deliberate, almost alive.",
].join("\n");

assert.equal(normalizeTtsFollowMode({ tts_follow_highlight: "subtle" }), "phrase");
assert.equal(normalizeTtsFollowMode({ tts_follow_mode: "word" }), "word_estimate");
assert.equal(normalizeTtsFollowMode({ tts_follow_mode: "bogus" }), "phrase");

const phrasePlan = buildTtsFollowPlan(sample, "phrase");
assert.ok(phrasePlan.units.length >= 5, "phrase plan should split punctuation-heavy prose");
for (const unit of phrasePlan.units) {
  assert.ok(unit.end > unit.start, "unit offsets must be non-empty");
  assert.equal(sample.slice(unit.start, unit.end).trim(), sample.slice(unit.start, unit.end));
}

const sentencePlan = buildTtsFollowPlan(sample, "sentence");
assert.ok(sentencePlan.units.length < phrasePlan.units.length, "sentence mode should be coarser than phrase mode");

const phraseAudio = buildFollowAudioChunkPlan(sample, { followMode: "phrase", maxChars: 900, speed: 1 });
assert.ok(phraseAudio.chunks.length > 1, "phrase audio plan should create follow chunks");
for (const [index, chunk] of phraseAudio.chunks.entries()) {
  assert.equal(chunk.index, index);
  assert.ok(chunk.isFollowChunk, "audio chunks should be marked as follow chunks");
  assert.ok(chunk.textStart >= 0 && chunk.textEnd > chunk.textStart, "chunk offsets must point into display text");
  assert.equal(chunk.normalized_tts_text, sample.slice(chunk.textStart, chunk.textEnd).replace(/\s+/g, " ").trim());
  if (index > 0) {
    assert.ok(chunk.textStart >= phraseAudio.chunks[index - 1].textEnd, "chunks must remain ordered");
  }
}

const wordPlan = buildTtsFollowPlan(sample, "word_estimate");
assert.ok(wordPlan.units.length > phrasePlan.units.length, "word estimate should be finer than phrase mode");

const longText = Array.from({ length: 80 }, (_, index) =>
  `Sentence ${index + 1} carries a quiet detail, a pause, and a second image for narration follow testing.`,
).join(" ");
const longAudio = buildFollowAudioChunkPlan(longText, { followMode: "phrase", maxChars: 900, speed: 1 });
assert.ok(longAudio.chunks.length > 5, "long text should be chunked for progressive follow");
assert.ok(longAudio.chunks.every((chunk) => chunk.text.length <= 950), "follow chunks should stay bounded");

console.log(
  JSON.stringify(
    {
      phraseUnits: phrasePlan.units.length,
      sentenceUnits: sentencePlan.units.length,
      phraseChunks: phraseAudio.chunks.length,
      longChunks: longAudio.chunks.length,
      ok: true,
    },
    null,
    2,
  ),
);
