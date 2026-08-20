import assert from "node:assert/strict";
import {
  contiguousGeneratedRange,
  resolvePerformanceSeek,
  resolveGeneratedSeek,
} from "../../frontend/src/services/ttsSeekMath.js";
import { breathScheduleForTimeline } from "../../frontend/src/services/ttsBreathPolicy.js";

const durations = [8, 12, 10, 9];
const all = [true, true, true, true];

assert.deepEqual(contiguousGeneratedRange(durations, all), {
  start: 0,
  end: 39,
  startIndex: 0,
  endIndex: 3,
  generatedChunkCount: 4,
});
assert.deepEqual(resolveGeneratedSeek(19, durations, all).chunkIndex, 1);
assert.equal(resolveGeneratedSeek(19, durations, all).chunkTime, 11);
assert.equal(resolveGeneratedSeek(8, durations, all).chunkIndex, 1);
assert.equal(resolveGeneratedSeek(-20, durations, all).target, 0);
assert.equal(resolveGeneratedSeek(90, durations, [true, true, false, false]).target, 19.95);
assert.equal(resolveGeneratedSeek(17, durations, [false, true, true, false]).chunkIndex, 1);
assert.equal(resolveGeneratedSeek(17, durations, [false, true, true, false]).chunkTime, 9);
assert.equal(resolveGeneratedSeek(5, durations, [false, true, true, false]).target, 8);
assert.equal(resolveGeneratedSeek(10, durations, [true, false, true, true]).range.end, 8);
assert.equal(resolveGeneratedSeek(0, durations, [false, false, false, false]), null);

const performance = {
  speech: [8, 12, 10],
  before: [0.7, 0, 0],
  after: [0, 0.6, 0],
  pauses: [0.2, 0.3, 0],
  generated: [true, true, true],
};
assert.equal(resolvePerformanceSeek(0.25, performance.speech, performance.before, performance.after, performance.pauses, performance.generated).phase, "breath_before");
assert.equal(resolvePerformanceSeek(0.25, performance.speech, performance.before, performance.after, performance.pauses, performance.generated).breathTime, 0.25);
assert.equal(resolvePerformanceSeek(1.7, performance.speech, performance.before, performance.after, performance.pauses, performance.generated).phase, "speech");
assert.equal(resolvePerformanceSeek(1.7, performance.speech, performance.before, performance.after, performance.pauses, performance.generated).chunkTime, 1);
assert.equal(resolvePerformanceSeek(20.95, performance.speech, performance.before, performance.after, performance.pauses, performance.generated).phase, "breath_after");
assert.equal(resolvePerformanceSeek(20.95, performance.speech, performance.before, performance.after, performance.pauses, performance.generated).chunkIndex, 1);
assert.ok(resolvePerformanceSeek(20.95, performance.speech, performance.before, performance.after, performance.pauses, performance.generated).breathTime > 0);

const breathResponse = (event, mode = "natural") => ({
  breath_before: event,
  breath_after: null,
  breath_confidence: 0.98,
  breath_reference_used: true,
  breath_audio_url: "/local-breath.wav",
  breathing_mode: mode,
});
const repeatedNatural = breathScheduleForTimeline({
  responses: Array.from({ length: 12 }, () => breathResponse("normal_inhale")),
  speechDurations: Array(12).fill(10),
  pauseDurations: Array(12).fill(0),
});
assert.deepEqual(repeatedNatural.map((entry, index) => entry.breath_before ? index : null).filter((index) => index !== null), [0, 5, 10]);
const repeatedStrain = breathScheduleForTimeline({
  responses: Array.from({ length: 6 }, () => breathResponse("recovering_breath")),
  speechDurations: Array(6).fill(10),
  pauseDurations: Array(6).fill(0),
});
assert.deepEqual(repeatedStrain.map((entry, index) => entry.breath_before ? index : null).filter((index) => index !== null), [0, 2, 4]);
const offSchedule = breathScheduleForTimeline({
  responses: [breathResponse("normal_inhale", "off")],
  speechDurations: [10],
  pauseDurations: [0],
});
assert.equal(offSchedule[0].breath_before, false);

console.log("PASS: buffered seeking uses actual speech and breath durations, crosses chunks, and clamps to contiguous generated audio.");
