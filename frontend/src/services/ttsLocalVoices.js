export function localBrowserVoices(synthesis) {
  return Array.from(synthesis?.getVoices?.() || []).filter((voice) => voice.localService === true);
}

export function selectLocalBrowserVoice(synthesis, requested) {
  const voices = localBrowserVoices(synthesis);
  return voices.find((voice) => voice.name === requested || voice.voiceURI === requested)
    || voices.find((voice) => voice.default)
    || voices.find((voice) => /^en(?:-|$)/i.test(voice.lang))
    || voices[0]
    || null;
}
