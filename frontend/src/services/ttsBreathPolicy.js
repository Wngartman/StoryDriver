const EXPRESSIVE_BREATHS = new Set(["shaky_inhale", "recovering_breath", "gasp"]);

function rawBreathEligible(response, phase) {
  if (!response?.breath_reference_used || !response?.breath_audio_url) return false;
  const mode = response.breathing_mode || "natural";
  if (mode === "off" || !response?.[phase]) return false;
  const threshold = mode === "cinematic" ? 0.82 : 0.9;
  return Number(response.breath_confidence || 0) >= threshold;
}

function minimumGapSeconds(mode, event) {
  const expressive = EXPRESSIVE_BREATHS.has(event);
  if (mode === "cinematic") return expressive ? 12 : 25;
  return expressive ? 18 : 45;
}

export function breathScheduleForTimeline({ responses = [], speechDurations = [], pauseDurations = [] } = {}) {
  const schedule = responses.map(() => ({ breath_before: false, breath_after: false }));
  let speechClock = 0;
  let lastAcceptedAt = Number.NEGATIVE_INFINITY;

  for (let index = 0; index < responses.length; index += 1) {
    const response = responses[index];
    const speechDuration = Math.max(0, Number(speechDurations[index]) || 0);
    for (const phase of ["breath_before", "breath_after"]) {
      if (!rawBreathEligible(response, phase)) continue;
      const candidateAt = speechClock + (phase === "breath_after" ? speechDuration : 0);
      const event = response[phase];
      const mode = response.breathing_mode || "natural";
      if (candidateAt - lastAcceptedAt < minimumGapSeconds(mode, event)) continue;
      schedule[index][phase] = true;
      lastAcceptedAt = candidateAt;
    }
    speechClock += speechDuration + Math.max(0, Number(pauseDurations[index]) || 0);
  }
  return schedule;
}
