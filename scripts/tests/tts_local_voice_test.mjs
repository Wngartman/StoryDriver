import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import vm from "node:vm";
import { localBrowserVoices, selectLocalBrowserVoice } from "../../frontend/src/services/ttsLocalVoices.js";

const cloud = { name: "Online", voiceURI: "online", lang: "en-US", default: true, localService: false };
const unknown = { name: "Unknown", lang: "en-US" };
const local = { name: "Offline", voiceURI: "offline", lang: "en-US", localService: true };
let voices = [cloud, unknown, local];
const synthesis = { getVoices: () => voices };
assert.deepEqual(localBrowserVoices(synthesis), [local]);
assert.equal(selectLocalBrowserVoice(synthesis, "Online"), local);
assert.equal(selectLocalBrowserVoice(synthesis, "offline"), local);
assert.deepEqual(localBrowserVoices(undefined), []);
voices = [cloud, unknown];
assert.equal(selectLocalBrowserVoice(synthesis, "Online"), null);

// Execute the actual controller function: no utterance may be constructed without a local voice.
const source = readFileSync(new URL("../../frontend/src/services/ttsController.js", import.meta.url), "utf8");
const body = source.slice(source.indexOf("function speakBrowserChunk("), source.indexOf("function playBrowser("));
const errors = [];
const updates = [];
const spoken = [];
const context = {
  playbackId: 1, browserChunks: ["Synthetic private narration."], browserChunkIndex: 0,
  suppressBrowserError: false, stopBrowserSpeech() {}, clearTimer() {}, browserSpeed: 1,
  browserVoice: "Online", utterance: null,
  selectedBrowserVoice: () => selectLocalBrowserVoice(synthesis, "Online"),
  onError: (value) => errors.push(value), onUpdate: (value) => updates.push(value),
  SpeechSynthesisUtterance: class { constructor(text) { this.text = text; } },
  window: { speechSynthesis: { speak: (value) => spoken.push(value) } },
};
vm.runInNewContext(body + "\nspeakBrowserChunk(1);", context);
assert.equal(spoken.length, 0);
assert.equal(context.utterance, null);
assert.match(errors[0], /No local browser voice/);
assert.equal(updates[0].narrationCursor, undefined);
voices = [cloud, local];
vm.runInNewContext(body + "\nspeakBrowserChunk(1);", context);
assert.equal(spoken.length, 1);
assert.equal(spoken[0].voice, local);
console.log("PASS: browser narration fails closed without an explicitly local voice and never selects a cloud default.");
