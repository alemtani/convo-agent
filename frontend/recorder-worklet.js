// AudioWorklet recorder for Phase 1 push-to-talk.
//
// Runs on the audio thread and forwards raw mono PCM frames (Float32) to the
// main thread, which buffers them and WAV-encodes on release. The node produces
// no output (process leaves the output buffers silent), so wiring it to the
// destination just drives the graph without echoing the mic to the speakers.
class RecorderProcessor extends AudioWorkletProcessor {
  process(inputs) {
    const channel = inputs[0] && inputs[0][0];
    if (channel) {
      // slice() copies — the engine reuses the underlying buffer across calls.
      this.port.postMessage(channel.slice(0));
    }
    return true;
  }
}

registerProcessor("recorder-processor", RecorderProcessor);
