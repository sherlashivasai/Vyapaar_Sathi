class MediaHandler {
  constructor() {
    this.audioContext = null;
    this.audioStream = null;
    this.audioSource = null;
    this.audioWorkletNode = null;
    this.muteGain = null;
    this.videoStream = null;
    this.videoInterval = null;
    this.scheduledSources = [];
    this.nextStartTime = 0;
    this.isRecording = false;
    this.videoCanvas = document.createElement("canvas");
    this.canvasContext = this.videoCanvas.getContext("2d");
  }

  async preparePlayback() {
    const AudioContextClass = window.AudioContext || window.webkitAudioContext;
    if (!AudioContextClass) throw new Error("Audio playback is not supported.");
    if (!this.audioContext) this.audioContext = new AudioContextClass();
    if (this.audioContext.state === "suspended") {
      await this.audioContext.resume();
    }
  }

  async startAudio(onAudioData) {
    await this.preparePlayback();
    await this.audioContext.audioWorklet.addModule("/static/pcm-processor.js");

    this.audioStream = await navigator.mediaDevices.getUserMedia({
      audio: {
        channelCount: 1,
        echoCancellation: true,
        noiseSuppression: true,
        autoGainControl: true,
      },
    });

    this.audioSource = this.audioContext.createMediaStreamSource(this.audioStream);
    this.audioWorkletNode = new AudioWorkletNode(
      this.audioContext,
      "pcm-processor",
      { numberOfInputs: 1, numberOfOutputs: 1, channelCount: 1 },
    );
    this.muteGain = this.audioContext.createGain();
    this.muteGain.gain.value = 0;
    this.audioSource.connect(this.audioWorkletNode);
    this.audioWorkletNode.connect(this.muteGain);
    this.muteGain.connect(this.audioContext.destination);

    this.audioWorkletNode.port.onmessage = ({ data }) => {
      if (!this.isRecording || !data?.length) return;
      const downsampled = this.downsample(data, this.audioContext.sampleRate, 16000);
      onAudioData(this.toPCM16(downsampled));
    };
    this.isRecording = true;
  }

  stopAudio() {
    this.isRecording = false;
    this.audioWorkletNode?.port.close();
    this.audioWorkletNode?.disconnect();
    this.audioSource?.disconnect();
    this.muteGain?.disconnect();
    this.audioStream?.getTracks().forEach((track) => track.stop());
    this.audioWorkletNode = null;
    this.audioSource = null;
    this.muteGain = null;
    this.audioStream = null;
  }

  async startVideo(mode, videoElement, onFrame, onEnded) {
    this.stopVideo(videoElement);
    this.videoStream =
      mode === "screen"
        ? await navigator.mediaDevices.getDisplayMedia({ video: true, audio: false })
        : await navigator.mediaDevices.getUserMedia({ video: true, audio: false });

    videoElement.srcObject = this.videoStream;
    await videoElement.play();

    const videoTrack = this.videoStream.getVideoTracks()[0];
    videoTrack.addEventListener(
      "ended",
      () => {
        this.stopVideo(videoElement);
        onEnded?.();
      },
      { once: true },
    );

    this.videoInterval = window.setInterval(() => {
      const frame = this.captureFrame(videoElement);
      if (frame) onFrame(frame);
    }, 2000);
  }

  stopVideo(videoElement) {
    if (this.videoInterval !== null) {
      window.clearInterval(this.videoInterval);
      this.videoInterval = null;
    }
    this.videoStream?.getTracks().forEach((track) => track.stop());
    this.videoStream = null;
    if (videoElement) videoElement.srcObject = null;
  }

  captureFrame(videoElement) {
    if (
      !this.videoStream ||
      videoElement.readyState < HTMLMediaElement.HAVE_CURRENT_DATA ||
      !this.canvasContext
    ) {
      return null;
    }

    const sourceWidth = videoElement.videoWidth;
    const sourceHeight = videoElement.videoHeight;
    if (!sourceWidth || !sourceHeight) return null;

    const scale = Math.min(1, 640 / sourceWidth, 480 / sourceHeight);
    this.videoCanvas.width = Math.round(sourceWidth * scale);
    this.videoCanvas.height = Math.round(sourceHeight * scale);
    this.canvasContext.drawImage(
      videoElement,
      0,
      0,
      this.videoCanvas.width,
      this.videoCanvas.height,
    );
    return this.videoCanvas.toDataURL("image/jpeg", 0.65).split(",")[1];
  }

  playAudio(arrayBuffer) {
    if (!this.audioContext || !(arrayBuffer instanceof ArrayBuffer)) return;
    if (this.audioContext.state === "suspended") {
      this.audioContext.resume().catch((error) => {
        console.error("Could not resume audio playback:", error);
      });
    }

    const sampleCount = Math.floor(arrayBuffer.byteLength / Int16Array.BYTES_PER_ELEMENT);
    if (!sampleCount) return;
    const pcm = new Int16Array(arrayBuffer, 0, sampleCount);
    const audioBuffer = this.audioContext.createBuffer(1, sampleCount, 24000);
    const channel = audioBuffer.getChannelData(0);
    for (let index = 0; index < sampleCount; index += 1) {
      channel[index] = pcm[index] / 32768;
    }

    const source = this.audioContext.createBufferSource();
    source.buffer = audioBuffer;
    source.connect(this.audioContext.destination);
    this.nextStartTime = Math.max(
      this.nextStartTime,
      this.audioContext.currentTime,
    );
    source.start(this.nextStartTime);
    this.nextStartTime += audioBuffer.duration;
    this.scheduledSources.push(source);
    source.onended = () => {
      this.scheduledSources = this.scheduledSources.filter(
        (scheduled) => scheduled !== source,
      );
    };
  }

  stopAudioPlayback() {
    for (const source of this.scheduledSources) {
      try {
        source.stop();
      } catch {
        // A source may already have finished.
      }
    }
    this.scheduledSources = [];
    if (this.audioContext) {
      this.nextStartTime = this.audioContext.currentTime;
    }
  }

  downsample(buffer, sampleRate, targetRate) {
    if (targetRate >= sampleRate) return buffer;
    const ratio = sampleRate / targetRate;
    const output = new Float32Array(Math.floor(buffer.length / ratio));
    for (let outputIndex = 0; outputIndex < output.length; outputIndex += 1) {
      const start = Math.floor(outputIndex * ratio);
      const end = Math.min(Math.floor((outputIndex + 1) * ratio), buffer.length);
      let sum = 0;
      for (let inputIndex = start; inputIndex < end; inputIndex += 1) {
        sum += buffer[inputIndex];
      }
      output[outputIndex] = sum / Math.max(1, end - start);
    }
    return output;
  }

  toPCM16(samples) {
    const pcm = new Int16Array(samples.length);
    for (let index = 0; index < samples.length; index += 1) {
      const sample = Math.max(-1, Math.min(1, samples[index]));
      pcm[index] = sample < 0 ? sample * 0x8000 : sample * 0x7fff;
    }
    return pcm.buffer;
  }
}
