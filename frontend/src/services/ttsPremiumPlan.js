const DIALOGUE_PATTERN = /["\u201c][^"\u201d\n]+["\u201d]/;
const ATTRIBUTION_PATTERN = /^(?:(?:he|she|they|we|i)|[A-Z][\w'-]*)\s+(?:said|asked|answered|replied|whispered|murmured|breathed|hissed|shouted|yelled|screamed|sobbed|called|barked)\b/i;
const DELIVERY_CUE_PATTERN = /\b(?:whisper(?:s|ed|ing)?|under (?:her|his|their) breath|barely audible|breathed the words|hissed quietly|said softly|spoke softly|softly replied|murmured|shout(?:s|ed|ing)?|yell(?:s|ed|ing)?|scream(?:s|ed|ing)?|cried out|raised (?:her|his|their) voice|barked the order|sob(?:s|bed|bing)?|voice broke|choked out|trembling voice|fought back tears|panicked|spoke close to (?:her|him|them)|voice warm and private|murmured against (?:her|his|their) ear|mournful|grief-stricken|hollow voice|exhausted sadness|clipped voice|strained|tightly controlled|urgent but quiet|fear held in check)\b/i;
const ASSOCIATED_ACTION_PATTERN = /\b(?:leaned closer|drew closer|(?:her|his|their|[A-Z][\w'-]*['\u2019]s) breath hitched|caught (?:her|his|their) breath|drew a(?: slow| deep)? breath|took a(?: slow| deep)? breath|breathed in|panting|hesitated|paused|swallowed)\b/i;
const SCENE_BREAK_PATTERN = /^(?:\*\s*\*\s*\*|-{3,}|_{3,})$/;

function splitSentences(block) {
  const raw = block
    .split(/(?<=[.!?]["'\u201d\u2019]?)\s+(?=[A-Z0-9"'\u201c\u2018])/)
    .map((sentence) => sentence.trim())
    .filter(Boolean);
  const merged = [];
  for (const sentence of raw) {
    const previous = merged.at(-1);
    if (previous && /[.!?]["'\u201d\u2019]$/.test(previous) && ATTRIBUTION_PATTERN.test(sentence)) {
      merged[merged.length - 1] = `${previous} ${sentence}`;
    } else {
      merged.push(sentence);
    }
  }
  const contextual = [];
  for (let index = 0; index < merged.length; index += 1) {
    const sentence = merged[index];
    const next = merged[index + 1];
    if (
      next &&
      sentence.length <= 100 &&
      ASSOCIATED_ACTION_PATTERN.test(sentence) &&
      !DIALOGUE_PATTERN.test(sentence) &&
      DIALOGUE_PATTERN.test(next)
    ) {
      contextual.push(`${sentence} ${next}`);
      index += 1;
    } else {
      contextual.push(sentence);
    }
  }
  return contextual;
}

function splitLongUnit(text, target) {
  if (text.length <= target || DIALOGUE_PATTERN.test(text)) return [text];
  const clauses = text.split(/(?<=[,;:])\s+/).filter(Boolean);
  const parts = clauses.length > 1 ? clauses : text.split(/\s+/).filter(Boolean);
  const output = [];
  let current = "";
  for (const part of parts) {
    const separator = current ? " " : "";
    if (current && current.length + separator.length + part.length > target) {
      output.push(current);
      current = part;
    } else {
      current = `${current}${separator}${part}`;
    }
  }
  if (current) output.push(current);
  return output;
}

export function premiumNarrationUnits(text, maxChars = 240) {
  const target = Math.max(160, Number(maxChars) || 240);
  const source = String(text || "").replace(/\r/g, "");
  const rawBlocks = source
    .split(/\n{2,}/)
    .map((block) => block.trim())
    .filter(Boolean);
  const blocks = [];
  let sceneBreakPending = false;
  for (const block of rawBlocks) {
    if (SCENE_BREAK_PATTERN.test(block)) {
      if (blocks.length) blocks[blocks.length - 1].sceneBreakAfter = true;
      else sceneBreakPending = true;
      continue;
    }
    blocks.push({ text: block, sceneBreakAfter: sceneBreakPending });
    sceneBreakPending = false;
  }
  if (!blocks.length) return [];

  const output = [];
  for (let blockIndex = 0; blockIndex < blocks.length; blockIndex += 1) {
    const block = blocks[blockIndex];
    const blockStart = output.length;
    const sentences = splitSentences(block.text).flatMap((sentence) => splitLongUnit(sentence, target));
    let packed = "";
    const flushPacked = () => {
      if (!packed) return;
      output.push({ text: packed, containsDialogue: false, explicitDeliveryCue: false });
      packed = "";
    };
    for (const sentence of sentences) {
      const containsDialogue = DIALOGUE_PATTERN.test(sentence);
      const explicitDeliveryCue = DELIVERY_CUE_PATTERN.test(sentence);
      if (containsDialogue || explicitDeliveryCue) {
        flushPacked();
        output.push({ text: sentence, containsDialogue, explicitDeliveryCue });
        continue;
      }
      const separator = packed ? " " : "";
      if (packed && packed.length + separator.length + sentence.length > target) {
        flushPacked();
        packed = sentence;
      } else {
        packed = `${packed}${separator}${sentence}`;
      }
    }
    flushPacked();
    for (let index = blockStart; index < output.length; index += 1) {
      output[index].blockIndex = blockIndex;
    }
    const blockUnits = output.filter((unit) => unit.blockIndex === blockIndex);
    const last = blockUnits.at(-1);
    if (last) {
      last.paragraphBreakAfter = blockIndex < blocks.length - 1 && !block.sceneBreakAfter;
      last.sceneBreakAfter = Boolean(block.sceneBreakAfter);
    }
  }
  if (sceneBreakPending && output.length) output.at(-1).sceneBreakAfter = true;
  for (let index = 0; index < output.length; index += 1) {
    const current = output[index];
    const next = output[index + 1];
    current.index = index;
    current.paragraphBreakAfter = Boolean(current.paragraphBreakAfter);
    current.sceneBreakAfter = Boolean(current.sceneBreakAfter);
    current.speakerChangeAfter = Boolean(
      current.containsDialogue &&
      next?.containsDialogue &&
      current.blockIndex === next.blockIndex,
    );
  }
  return output;
}

export function premiumNarrationChunks(text, maxChars = 240) {
  return premiumNarrationUnits(text, maxChars).map((unit) => unit.text);
}

export function premiumBatchStart(index, batchSize = 4) {
  const size = Math.max(1, Number(batchSize) || 4);
  return Math.floor(Math.max(0, Number(index) || 0) / size) * size;
}

export function premiumBufferCoversNextBatch(responses, reserveSeconds = 6) {
  const items = Array.isArray(responses) ? responses.filter(Boolean) : [];
  if (!items.length) return false;
  const bufferedSeconds = items.reduce((total, item) => total + Math.max(0, Number(item.duration) || 0), 0);
  const measuredBatchSeconds = Math.max(...items.map((item) => Math.max(0, Number(item.synthesis_seconds) || 0)));
  return bufferedSeconds >= measuredBatchSeconds + Math.max(0, Number(reserveSeconds) || 0);
}
