const statusContainer = document.getElementById("connection-status");
const statusText = document.getElementById("status-text");
const assistantState = document.getElementById("assistant-state");
const activityText = document.getElementById("activity-text");
const conversationStatus = document.getElementById("conversation-status-text");
const conversationStatusWrap = document.querySelector(".conversation-status");
const chatLog = document.getElementById("chat-log");
const emptyState = document.getElementById("empty-state");
const connectButton = document.getElementById("connect-button");
const disconnectButton = document.getElementById("disconnect-button");
const micButton = document.getElementById("mic-button");
const cameraButton = document.getElementById("camera-button");
const screenButton = document.getElementById("screen-button");
const stopShareButton = document.getElementById("stop-share-button");
const videoPreview = document.getElementById("video-preview");
const videoPreviewWrap = document.getElementById("video-preview-wrap");
const videoPreviewLabel = document.getElementById("video-preview-label");
const messageForm = document.getElementById("message-form");
const textInput = document.getElementById("text-input");
const sendButton = document.getElementById("send-button");
const errorBanner = document.getElementById("error-banner");

const mediaHandler = new MediaHandler();
const toolActivities = new Map();
let callState = "idle";
let activeUserStream = null;
let activeAssistantStream = null;

const voiceClient = new VoiceClient({
  onOpen: handleConnect,
  onMessage: handleSocketMessage,
  onClose: handleDisconnect,
  onError: () => showError("Connection error. Check the server and try again."),
});

document.getElementById("current-year").textContent = new Date().getFullYear();
connectButton.addEventListener("click", connectAssistant);
disconnectButton.addEventListener("click", disconnectAssistant);
micButton.addEventListener("click", toggleMicrophone);
cameraButton.addEventListener("click", () => startVideoShare("camera"));
screenButton.addEventListener("click", () => startVideoShare("screen"));
stopShareButton.addEventListener("click", stopVideoShare);

messageForm.addEventListener("submit", (event) => {
  event.preventDefault();
  const text = textInput.value.trim();
  if (!text || !voiceClient.sendText(text)) return;
  appendMessage("user", text);
  textInput.value = "";
});

window.addEventListener("beforeunload", () => {
  mediaHandler.stopAudio();
  mediaHandler.stopVideo(videoPreview);
  voiceClient.disconnect();
});

async function connectAssistant() {
  if (callState !== "idle") return;
  callState = "connecting";
  connectButton.disabled = true;
  setConnectionStatus("connecting", "Connecting");
  assistantState.textContent = "CONNECTING";
  activityText.textContent = "Opening a private session with your assistant.";
  conversationStatus.textContent = "Connecting";
  clearError();

  try {
    await mediaHandler.preparePlayback();
  } catch (error) {
    console.info("Audio playback will be enabled when supported:", error);
  }
  voiceClient.connect();
}

function handleConnect() {
  callState = "active";
  connectButton.classList.add("hidden");
  disconnectButton.classList.remove("hidden");
  disconnectButton.disabled = false;
  micButton.disabled = false;
  cameraButton.disabled = false;
  screenButton.disabled = false;
  textInput.disabled = false;
  sendButton.disabled = false;
  setConnectionStatus("connected", "Connected");
  assistantState.textContent = "SESSION CONNECTED";
  activityText.textContent = "Ask a question, or start your microphone to speak.";
  conversationStatus.textContent = "Ready to help";
  conversationStatusWrap.classList.add("connected");
  appendMessage(
    "assistant",
    "Namaste! I’m ready to help with settlements, customer khata, and local context.",
  );
  textInput.focus();
}

function handleDisconnect() {
  const wasConnected = callState !== "idle";
  finishSession();
  if (wasConnected) appendSystemMessage("Your session has ended.");
}

function disconnectAssistant() {
  if (callState === "idle") return;
  voiceClient.disconnect();
  finishSession();
  appendSystemMessage("Your session has ended.");
}

function finishSession() {
  callState = "idle";
  mediaHandler.stopAudio();
  mediaHandler.stopAudioPlayback();
  mediaHandler.stopVideo(videoPreview);
  activeUserStream = null;
  activeAssistantStream = null;
  toolActivities.clear();

  connectButton.classList.remove("hidden");
  connectButton.disabled = false;
  disconnectButton.classList.add("hidden");
  disconnectButton.disabled = false;
  micButton.disabled = true;
  micButton.innerHTML = `${microphoneIcon()}<span>Start microphone</span>`;
  cameraButton.disabled = true;
  screenButton.disabled = true;
  stopShareButton.classList.add("hidden");
  videoPreviewWrap.classList.add("hidden");
  textInput.disabled = true;
  sendButton.disabled = true;
  setConnectionStatus("", "Offline");
  assistantState.textContent = "READY WHEN YOU ARE";
  activityText.textContent = "Connect to ask a question or start a voice conversation.";
  conversationStatus.textContent = "Waiting to connect";
  conversationStatusWrap.classList.remove("connected");
}

function setConnectionStatus(state, label) {
  statusContainer.className = `connection-status ${state}`.trim();
  statusText.textContent = label;
}

async function toggleMicrophone() {
  if (callState !== "active") return;
  if (mediaHandler.isRecording) {
    mediaHandler.stopAudio();
    micButton.classList.remove("recording");
    micButton.innerHTML = `${microphoneIcon()}<span>Start microphone</span>`;
    activityText.textContent = "Microphone off. You can still type a message.";
    return;
  }

  try {
    await mediaHandler.startAudio((audio) => voiceClient.send(audio));
    micButton.classList.add("recording");
    micButton.innerHTML = `${microphoneOffIcon()}<span>Stop microphone</span>`;
    activityText.textContent = "Listening. Speak naturally.";
  } catch (error) {
    showError(`Could not start the microphone: ${error.message}`);
    activityText.textContent = "Check your microphone permission and try again.";
  }
}

async function startVideoShare(mode) {
  if (callState !== "active") return;
  cameraButton.disabled = true;
  screenButton.disabled = true;
  try {
    await mediaHandler.startVideo(
      mode,
      videoPreview,
      (frame) => voiceClient.sendImage(frame),
      () => finishVideoShare(),
    );
    videoPreviewLabel.textContent =
      mode === "screen" ? "Screen preview" : "Camera preview";
    videoPreviewWrap.classList.remove("hidden");
    stopShareButton.classList.remove("hidden");
    activityText.textContent = "Visual sharing is on. Ask about what you shared.";
  } catch (error) {
    showError(`Could not start sharing: ${error.message}`);
  } finally {
    if (callState === "active") {
      cameraButton.disabled = false;
      screenButton.disabled = false;
    }
  }
}

function stopVideoShare() {
  mediaHandler.stopVideo(videoPreview);
  finishVideoShare();
}

function finishVideoShare() {
  videoPreviewWrap.classList.add("hidden");
  stopShareButton.classList.add("hidden");
  if (callState === "active") {
    activityText.textContent = "Visual sharing stopped.";
  }
}

function handleSocketMessage(event) {
  if (event.data instanceof ArrayBuffer) {
    mediaHandler.playAudio(event.data);
    return;
  }

  let message;
  try {
    message = JSON.parse(event.data);
  } catch (error) {
    console.error("Could not read assistant event:", error);
    return;
  }

  switch (message.type) {
    case "text":
    case "gemini":
      updateStreamMessage("assistant", message.text, message.finished);
      break;
    case "user":
      updateStreamMessage("user", message.text, message.finished);
      break;
    case "tool_start":
      startToolActivity(message);
      break;
    case "tool_call":
      finishToolActivity(message);
      break;
    case "interrupted":
      mediaHandler.stopAudioPlayback();
      activeAssistantStream = null;
      activityText.textContent = "Listening.";
      break;
    case "turn_complete":
    case "generation_complete":
      activeUserStream = null;
      activeAssistantStream = null;
      if (callState === "active") {
        activityText.textContent = mediaHandler.isRecording
          ? "Listening. Speak naturally."
          : "Ready for your next question.";
      }
      break;
    case "error":
      showError(message.error || "The session encountered an error.");
      appendSystemMessage(message.error || "The session encountered an error.");
      break;
    default:
      break;
  }
}

function updateStreamMessage(role, text, finished = false) {
  if (typeof text !== "string" || !text) return;
  let stream =
    role === "user" ? activeUserStream : activeAssistantStream;
  if (!stream) {
    stream = appendMessage(role, "");
    if (role === "user") activeUserStream = stream;
    else activeAssistantStream = stream;
  }

  const previous = stream.dataset.transcript || "";
  const previousNormalized = previous.trim().toLocaleLowerCase();
  const incomingNormalized = text.trim().toLocaleLowerCase();
  if (!previous) {
    stream.dataset.transcript = text;
  } else if (incomingNormalized.startsWith(previousNormalized)) {
    stream.dataset.transcript = text;
  } else if (!previousNormalized.startsWith(incomingNormalized)) {
    const separator = /\s$/.test(previous) || /^\s/.test(text) ? "" : " ";
    stream.dataset.transcript = previous + separator + text;
  }

  stream.querySelector(".message-bubble").textContent =
    stream.dataset.transcript;
  if (finished) {
    if (role === "user") activeUserStream = null;
    else activeAssistantStream = null;
  }
}

function startToolActivity(message) {
  const activity = appendActivity(toolLabel(message.name));
  if (message.id) toolActivities.set(message.id, activity);
  activityText.textContent = "Checking your business details.";
}

function finishToolActivity(message) {
  toolActivities.get(message.id)?.remove();
  toolActivities.delete(message.id);
  renderToolResult(message.name, message.result);
  if (callState === "active") {
    activityText.textContent = "Putting the answer together.";
  }
}

function renderToolResult(name, result) {
  if (!result || result.status === "error") {
    appendInsight(
      "Couldn’t retrieve that detail",
      result?.message || "The assistant could not complete this request.",
      "You can try again or ask another question.",
    );
    return;
  }

  const descriptions = {
    khata_ledger: "Customer credit and due amounts from your khata.",
    execute_data_analysis: "Calculated from settlement records.",
    get_shop_environment_intelligence:
      "Weather is live; local prices and events may not be independently verified.",
  };
  const title =
    name === "khata_ledger"
      ? "Khata update"
      : name === "execute_data_analysis"
        ? "Settlement insight"
        : "Local context";
  const content =
    result.result ||
    result.message ||
    formatStructuredResult(name, result);
  appendInsight(title, content, descriptions[name] || "");
}

function formatStructuredResult(name, result) {
  if (name === "khata_ledger" && result.customer_record) {
    const records = Array.isArray(result.customer_record)
      ? result.customer_record
      : [result.customer_record];
    return records
      .map((record) => `${record.name}: ${formatCurrency(record.amount)} pending`)
      .join("\n");
  }
  if (name === "get_shop_environment_intelligence") {
    const weather = result.live_weather || {};
    const temperature = Number(weather.current_temp_c);
    const summary = [
      weather.current_condition,
      Number.isFinite(temperature) ? `${temperature}°C` : null,
      ...(result.local_intelligence?.top_actionable_suggestions || []),
    ];
    return summary.filter(Boolean).join("\n") || "No local details are available.";
  }
  return "The tool completed successfully.";
}

function formatCurrency(amount) {
  return new Intl.NumberFormat("en-IN", {
    style: "currency",
    currency: "INR",
    maximumFractionDigits: 0,
  }).format(Number(amount) || 0);
}

function appendMessage(role, text) {
  const article = document.createElement("article");
  article.className = `message ${role}`;

  const meta = document.createElement("span");
  meta.className = "message-meta";
  meta.textContent = role === "user" ? "YOU" : "VYAPAR SAATHI";

  const bubble = document.createElement("div");
  bubble.className = "message-bubble";
  bubble.textContent = String(text || "");
  article.append(meta, bubble);
  appendToChat(article);
  return article;
}

function appendSystemMessage(text) {
  const message = document.createElement("div");
  message.className = "message activity";
  message.textContent = text;
  appendToChat(message);
}

function appendActivity(text) {
  const row = document.createElement("div");
  row.className = "message activity";
  const indicator = document.createElement("span");
  indicator.className = "activity-indicator";
  indicator.setAttribute("aria-hidden", "true");
  const label = document.createElement("span");
  label.textContent = text;
  row.append(indicator, label);
  appendToChat(row);
  return row;
}

function appendInsight(title, content, footnote = "") {
  const article = document.createElement("article");
  article.className = "message insight";

  const meta = document.createElement("span");
  meta.className = "message-meta";
  meta.textContent = "BUSINESS DETAIL";

  const card = document.createElement("div");
  card.className = "insight-card";
  const heading = document.createElement("p");
  heading.className = "insight-title";
  heading.textContent = title;
  const body = document.createElement("p");
  body.className = "insight-content";
  body.textContent = String(content || "No details were returned.");
  card.append(heading, body);

  if (footnote) {
    const note = document.createElement("p");
    note.className = "insight-footnote";
    note.textContent = footnote;
    card.append(note);
  }

  article.append(meta, card);
  appendToChat(article);
}

function appendToChat(element) {
  emptyState.classList.add("hidden");
  chatLog.appendChild(element);
  chatLog.scrollTop = chatLog.scrollHeight;
}

function showError(message) {
  errorBanner.textContent = message;
  errorBanner.classList.remove("hidden");
  setConnectionStatus("error", "Connection issue");
}

function clearError() {
  errorBanner.textContent = "";
  errorBanner.classList.add("hidden");
}

function toolLabel(name) {
  const labels = {
    khata_ledger: "Checking khata",
    execute_data_analysis: "Reviewing settlements",
    get_shop_environment_intelligence: "Checking local context",
  };
  return labels[name] || "Checking a business detail";
}

function microphoneIcon() {
  return '<span class="button-icon" aria-hidden="true"><svg viewBox="0 0 20 20" fill="none"><rect x="7" y="2.5" width="6" height="10" rx="3" stroke="currentColor" stroke-width="1.5"/><path d="M4.5 9.5v1a5.5 5.5 0 0 0 11 0v-1M10 16v2m-3 0h6" stroke="currentColor" stroke-width="1.5" stroke-linecap="round"/></svg></span>';
}

function microphoneOffIcon() {
  return '<span class="button-icon" aria-hidden="true"><svg viewBox="0 0 20 20" fill="none"><path d="m3 3 14 14M8 8v4a2 2 0 0 0 3.4 1.4M13 8V5.5a3 3 0 0 0-5.8-1M4.5 9.5v1a5.5 5.5 0 0 0 9.8 3.4M10 16v2m-3 0h6" stroke="currentColor" stroke-width="1.5" stroke-linecap="round"/></svg></span>';
}
