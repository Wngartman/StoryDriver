export function chunkOffsets(durations = []) {
  const offsets = [];
  let total = 0;
  for (const duration of durations) {
    offsets.push(total);
    total += Math.max(0, Number(duration) || 0);
  }
  return { offsets, total };
}

export function contiguousGeneratedRange(durations = [], generated = []) {
  const ready = generated
    .map((value, index) => (value ? index : -1))
    .filter((index) => index >= 0);
  if (!ready.length) return { start: 0, end: 0, startIndex: null, endIndex: null, generatedChunkCount: 0 };
  const { offsets } = chunkOffsets(durations);
  const startIndex = ready[0];
  let endIndex = startIndex;
  for (const index of ready.slice(1)) {
    if (index !== endIndex + 1) break;
    endIndex = index;
  }
  return {
    start: offsets[startIndex] || 0,
    end: (offsets[endIndex] || 0) + Math.max(0, Number(durations[endIndex]) || 0),
    startIndex,
    endIndex,
    generatedChunkCount: endIndex - startIndex + 1,
  };
}

export function resolveGeneratedSeek(seconds, durations = [], generated = []) {
  const range = contiguousGeneratedRange(durations, generated);
  if (range.end <= range.start || range.startIndex === null) return null;
  const target = Math.max(range.start, Math.min(Number(seconds) || 0, Math.max(range.start, range.end - 0.05)));
  const { offsets } = chunkOffsets(durations);
  let chunkIndex = range.endIndex;
  for (let index = range.startIndex; index <= range.endIndex; index += 1) {
    if (target < offsets[index] + durations[index] || index === range.endIndex) {
      chunkIndex = index;
      break;
    }
  }
  return {
    target,
    chunkIndex,
    chunkTime: Math.max(0, target - offsets[chunkIndex]),
    range,
  };
}

export function resolvePerformanceSeek(
  seconds,
  speechDurations = [],
  breathBeforeDurations = [],
  breathAfterDurations = [],
  pauseDurations = [],
  generated = [],
) {
  const timelineDurations = speechDurations.map(
    (speech, index) =>
      Math.max(0, Number(breathBeforeDurations[index]) || 0) +
      Math.max(0, Number(speech) || 0) +
      Math.max(0, Number(breathAfterDurations[index]) || 0) +
      Math.max(0, Number(pauseDurations[index]) || 0),
  );
  const resolved = resolveGeneratedSeek(seconds, timelineDurations, generated);
  if (!resolved) return null;
  let chunkIndex = resolved.chunkIndex;
  const before = Math.max(0, Number(breathBeforeDurations[chunkIndex]) || 0);
  const speech = Math.max(0, Number(speechDurations[chunkIndex]) || 0);
  const after = Math.max(0, Number(breathAfterDurations[chunkIndex]) || 0);
  const segmentTime = resolved.chunkTime;
  let phase = "speech";
  let chunkTime = 0;
  let breathTime = 0;
  if (before > 0 && segmentTime < before) {
    phase = "breath_before";
    breathTime = segmentTime;
  } else if (segmentTime < before + speech) {
    chunkTime = Math.max(0, segmentTime - before);
  } else if (after > 0 && segmentTime < before + speech + after) {
    phase = "breath_after";
    chunkTime = speech;
    breathTime = Math.max(0, segmentTime - before - speech);
  } else if (chunkIndex < speechDurations.length - 1 && generated[chunkIndex + 1]) {
    chunkIndex += 1;
    phase = "start";
  } else {
    phase = "boundary_complete";
    chunkTime = speech;
  }
  return {
    ...resolved,
    chunkIndex,
    phase,
    chunkTime,
    breathTime,
    timelineDurations,
  };
}
