/** Decode audio independently of animation downloads or clip preparation. */
export async function decodeSpeechAudio(audioContext, result) {
  if (!result.audio_base64) throw new Error("Speech response does not contain audio_base64.");
  if (![result.words, result.wtimes, result.wdurations].every(Array.isArray)) {
    throw new Error("Speech response is missing word timing information.");
  }
  const binary = atob(result.audio_base64);
  const bytes = new Uint8Array(binary.length);
  for (let index = 0; index < binary.length; index++) bytes[index] = binary.charCodeAt(index);
  if (audioContext.state === "suspended" || audioContext.state === "interrupted") {
    await audioContext.resume();
  }
  return audioContext.decodeAudioData(bytes.buffer);
}
