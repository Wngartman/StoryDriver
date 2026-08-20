const FOLLOW_MODES = new Set(["off", "phrase", "sentence", "word_estimate", "exact_word"]);
const CHUNKING_PROFILES = {
  fast: {
    phraseTargetSeconds: 7,
    sentenceTargetSeconds: 10,
    phraseMinSeconds: 3.5,
    sentenceMinSeconds: 5,
    phraseMaxSeconds: 14,
    sentenceMaxSeconds: 16,
    phraseHardMax: 760,
    sentenceHardMax: 1050,
  },
  natural: {
    phraseTargetSeconds: 9,
    sentenceTargetSeconds: 13,
    phraseMinSeconds: 4.5,
    sentenceMinSeconds: 6,
    phraseMaxSeconds: 18,
    sentenceMaxSeconds: 20,
    phraseHardMax: 950,
    sentenceHardMax: 1400,
  },
  audiobook: {
    phraseTargetSeconds: 12,
    sentenceTargetSeconds: 16,
    phraseMinSeconds: 6,
    sentenceMinSeconds: 8,
    phraseMaxSeconds: 22,
    sentenceMaxSeconds: 26,
    phraseHardMax: 1250,
    sentenceHardMax: 1800,
  },
};

export function normalizeTtsFollowMode(settingsOrMode = {}, fallback = "phrase") {
  const raw =
    typeof settingsOrMode === "string"
      ? settingsOrMode
      : settingsOrMode.tts_follow_mode ||
        settingsOrMode.tts_follow_highlight ||
        (settingsOrMode.highlight_narration ? "phrase" : "off");
  const normalized =
    {
      subtle: "phrase",
      strong: "sentence",
      word: "word_estimate",
      word_estimated: "word_estimate",
      exact: "exact_word",
    }[String(raw || "").trim().toLowerCase()] || String(raw || fallback).trim().toLowerCase();
  return FOLLOW_MODES.has(normalized) ? normalized : fallback;
}

function proseParagraphBlocks(text) {
  const blocks = [];
  let offset = 0;
  for (const part of String(text || "").replace(/\r/g, "").split(/(\n{2,})/)) {
    if (!part) continue;
    if (/^\n{2,}$/.test(part)) {
      offset += part.length;
      continue;
    }
    const leading = part.match(/^\s*/)?.[0]?.length || 0;
    const paragraph = part.trim();
    if (paragraph) {
      blocks.push({ paragraph, start: offset + leading });
    }
    offset += part.length;
  }
  return blocks;
}

function trimmedRange(text, start, end, baseStart) {
  let nextStart = start;
  let nextEnd = end;
  while (nextStart < nextEnd && /\s/.test(text[nextStart])) nextStart += 1;
  while (nextEnd > nextStart && /\s/.test(text[nextEnd - 1])) nextEnd -= 1;
  return nextEnd > nextStart ? { start: baseStart + nextStart, end: baseStart + nextEnd } : null;
}

function sentenceRanges(paragraph, baseStart) {
  const ranges = [];
  let start = 0;
  for (let index = 0; index < paragraph.length; index += 1) {
    const char = paragraph[index];
    if (!/[.!?]/.test(char)) continue;
    let end = index + 1;
    while (end < paragraph.length && /["')\]]/.test(paragraph[end])) end += 1;
    const next = paragraph[end];
    if (next && !/\s/.test(next)) continue;
    const range = trimmedRange(paragraph, start, end, baseStart);
    if (range) ranges.push(range);
    start = end;
  }
  const finalRange = trimmedRange(paragraph, start, paragraph.length, baseStart);
  if (finalRange) ranges.push(finalRange);
  return ranges;
}

function splitLongRange(paragraph, range, baseStart, maxChars = 220) {
  if (range.end - range.start <= maxChars) return [range];
  const pieces = [];
  let start = range.start - baseStart;
  const absoluteEnd = range.end - baseStart;
  while (absoluteEnd - start > maxChars) {
    const target = start + Math.round(maxChars * 0.72);
    let split = paragraph.lastIndexOf(" ", Math.min(absoluteEnd - 1, target));
    if (split <= start + 60) split = paragraph.indexOf(" ", target);
    if (split <= start || split >= absoluteEnd) break;
    const piece = trimmedRange(paragraph, start, split, baseStart);
    if (piece) pieces.push(piece);
    start = split + 1;
  }
  const finalPiece = trimmedRange(paragraph, start, absoluteEnd, baseStart);
  if (finalPiece) pieces.push(finalPiece);
  return pieces.length ? pieces : [range];
}

function phraseRanges(paragraph, baseStart) {
  const phrases = [];
  for (const sentence of sentenceRanges(paragraph, baseStart)) {
    const relativeStart = sentence.start - baseStart;
    const relativeEnd = sentence.end - baseStart;
    let start = relativeStart;
    for (let index = relativeStart; index < relativeEnd; index += 1) {
      const char = paragraph[index];
      const isDash = char === "-" && paragraph[index + 1] === "-";
      if (![",", ";", ":"].includes(char) && !isDash) continue;
      if (char === ",") {
        const tail = paragraph.slice(index + 1, Math.min(relativeEnd, index + 90));
        if (/^\s*["')\]]?\s*(she|he|they|i|we|you|it|[A-Z][a-z]+)\s+(said|asked|whispered|murmured|called|shouted|replied|answered|breathed|hissed|growled|snapped)\b/.test(tail)) {
          continue;
        }
      }
      const end = index + (isDash ? 2 : 1);
      const range = trimmedRange(paragraph, start, end, baseStart);
      if (range) phrases.push(...splitLongRange(paragraph, range, baseStart));
      start = end;
      if (isDash) index += 1;
    }
    const range = trimmedRange(paragraph, start, relativeEnd, baseStart);
    if (range) phrases.push(...splitLongRange(paragraph, range, baseStart));
  }
  return phrases;
}

function wordEstimateRanges(paragraph, baseStart) {
  const ranges = [];
  const matcher = /\b[\w'-]+\b/g;
  let match = matcher.exec(paragraph);
  while (match) {
    ranges.push({ start: baseStart + match.index, end: baseStart + match.index + match[0].length });
    match = matcher.exec(paragraph);
  }
  return ranges;
}

function unitWeight(text, paragraphBreakAfter = false) {
  const trimmed = text.trim();
  let weight = Math.max(8, trimmed.length);
  if (/[.!?]["')\]]?$/.test(trimmed)) weight += 36;
  if (/[,;:]$/.test(trimmed)) weight += 18;
  if (/--$/.test(trimmed)) weight += 18;
  if (paragraphBreakAfter) weight += 42;
  return weight;
}

export function wordCount(text) {
  return ((text || "").match(/\b[\w'-]+\b/g) || []).length;
}

export function estimateSpeechDuration(text, speed = 1) {
  const words = wordCount(text);
  const rate = Math.max(80, 170 * (Number(speed) || 1));
  return Math.max(1.5, (words / rate) * 60);
}

export function buildTtsFollowPlan(text, mode = "phrase") {
  const followMode = normalizeTtsFollowMode(mode, "phrase");
  const paragraphs = proseParagraphBlocks(text);
  const units = [];
  const plannedParagraphs = paragraphs.map((block, paragraphIndex) => {
    const ranges =
      followMode === "word_estimate"
        ? wordEstimateRanges(block.paragraph, block.start)
        : followMode === "phrase" || followMode === "exact_word"
          ? phraseRanges(block.paragraph, block.start)
          : sentenceRanges(block.paragraph, block.start);
    const paragraphUnitIds = [];
    ranges.forEach((range, rangeIndex) => {
      const id = units.length;
      const unitText = text.slice(range.start, range.end);
      units.push({
        id,
        start: range.start,
        end: range.end,
        paragraphIndex,
        weight: unitWeight(unitText, rangeIndex === ranges.length - 1 && paragraphIndex < paragraphs.length - 1),
      });
      paragraphUnitIds.push(id);
    });
    const paragraphUnits = paragraphUnitIds.map((id) => units[id]);
    const segments = [];
    let cursor = 0;
    for (const unit of paragraphUnits) {
      const start = Math.max(0, unit.start - block.start);
      const end = Math.min(block.paragraph.length, unit.end - block.start);
      if (start > cursor) segments.push({ text: block.paragraph.slice(cursor, start), unitId: null });
      segments.push({ text: block.paragraph.slice(start, end), unitId: unit.id });
      cursor = end;
    }
    if (cursor < block.paragraph.length) segments.push({ text: block.paragraph.slice(cursor), unitId: null });
    return { paragraph: block.paragraph, segments };
  });
  return { paragraphs: plannedParagraphs, units };
}

function normalizeTtsText(text = "") {
  return String(text || "").replace(/\s+/g, " ").trim();
}

function chunkFromUnits(fullText, units, mode, speed, index) {
  const start = units[0]?.start ?? 0;
  const end = units.at(-1)?.end ?? start;
  const displayText = fullText.slice(start, end);
  const normalizedTtsText = normalizeTtsText(displayText);
  return {
    index,
    text: normalizedTtsText,
    raw_text: displayText,
    normalized_tts_text: normalizedTtsText,
    display_text: displayText,
    textStart: start,
    textEnd: end,
    unitIds: units.map((unit) => unit.id),
    paragraphIndexes: [...new Set(units.map((unit) => unit.paragraphIndex))],
    followMode: mode,
    isFollowChunk: true,
    estimatedDuration: estimateSpeechDuration(normalizedTtsText, speed),
  };
}

export function buildFollowAudioChunkPlan(text, { followMode = "phrase", maxChars = 1200, speed = 1, chunkingProfile = "natural" } = {}) {
  const normalizedMode = normalizeTtsFollowMode(followMode, "off");
  if (normalizedMode === "off") return { chunks: [], plan: null, mode: "off" };

  const unitMode = normalizedMode === "sentence" ? "sentence" : "phrase";
  const profile = CHUNKING_PROFILES[chunkingProfile] || CHUNKING_PROFILES.natural;
  const plan = buildTtsFollowPlan(text, unitMode);
  const units = plan.units || [];
  if (!units.length) return { chunks: [], plan, mode: unitMode };

  const profileHardMax = unitMode === "sentence" ? profile.sentenceHardMax : profile.phraseHardMax;
  const hardMaxChars = Math.max(220, Math.min(Number(maxChars) || 1200, profileHardMax));
  const targetSeconds = unitMode === "sentence" ? profile.sentenceTargetSeconds : profile.phraseTargetSeconds;
  const minSeconds = unitMode === "sentence" ? profile.sentenceMinSeconds : profile.phraseMinSeconds;
  const maxSeconds = unitMode === "sentence" ? profile.sentenceMaxSeconds : profile.phraseMaxSeconds;
  const chunks = [];
  let current = [];

  const flush = () => {
    if (!current.length) return;
    chunks.push(chunkFromUnits(text, current, unitMode, speed, chunks.length));
    current = [];
  };

  for (const unit of units) {
    if (!current.length) {
      current = [unit];
      continue;
    }

    const previous = current.at(-1);
    const crossesParagraph = previous && previous.paragraphIndex !== unit.paragraphIndex;
    const candidate = [...current, unit];
    const candidateText = normalizeTtsText(text.slice(candidate[0].start, candidate.at(-1).end));
    const candidateDuration = estimateSpeechDuration(candidateText, speed);
    const currentText = normalizeTtsText(text.slice(current[0].start, current.at(-1).end));
    const currentDuration = estimateSpeechDuration(currentText, speed);
    const tooLong = candidateText.length > hardMaxChars || candidateDuration > maxSeconds;
    const paragraphBreakReady = crossesParagraph && currentDuration >= minSeconds;
    const targetReached = currentDuration >= targetSeconds;

    if (tooLong || paragraphBreakReady || targetReached) {
      flush();
      current = [unit];
      continue;
    }

    current = candidate;
  }
  flush();

  return { chunks: chunks.filter((chunk) => chunk.text), plan, mode: unitMode };
}
