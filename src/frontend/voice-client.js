class VoiceClient {
  constructor({ onOpen, onMessage, onClose, onError }) {
    this.websocket = null;
    this.onOpen = onOpen;
    this.onMessage = onMessage;
    this.onClose = onClose;
    this.onError = onError;
  }

  connect() {
    if (this.websocket && this.websocket.readyState < WebSocket.CLOSING) return;

    const protocol = window.location.protocol === "https:" ? "wss:" : "ws:";
    const socket = new WebSocket(`${protocol}//${window.location.host}/ws`);
    socket.binaryType = "arraybuffer";
    this.websocket = socket;

    socket.onopen = () => this.onOpen?.();
    socket.onmessage = (event) => this.onMessage?.(event);
    socket.onerror = (event) => this.onError?.(event);
    socket.onclose = (event) => {
      if (this.websocket === socket) this.websocket = null;
      this.onClose?.(event);
    };
  }

  send(data) {
    if (!this.isConnected()) return false;
    this.websocket.send(data);
    return true;
  }

  sendText(text) {
    return this.send(JSON.stringify({ text }));
  }

  sendImage(base64Data, mimeType = "image/jpeg") {
    return this.send(
      JSON.stringify({
        type: "image",
        mime_type: mimeType,
        data: base64Data,
      })
    );
  }

  disconnect() {
    if (this.websocket) {
      this.websocket.close(1000, "Session ended by user");
      this.websocket = null;
    }
  }

  isConnected() {
    return this.websocket?.readyState === WebSocket.OPEN;
  }
}
