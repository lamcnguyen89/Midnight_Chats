"use strict";

const $ = (id) => document.getElementById(id);
const state = { id: null, name: "", model: "", messages: [], createdAt: null,
  updatedAt: null, dirty: false, busy: false, pending: false, models: [], hiddenModels: [], controller: null };
const dateFormat = new Intl.DateTimeFormat(undefined, { month: "short", day: "numeric", year: "numeric" });
const dateTimeFormat = new Intl.DateTimeFormat(undefined, { dateStyle: "medium", timeStyle: "short" });
let memoryRequest = false;

async function refreshMemory() {
  if (memoryRequest || document.hidden) return;
  memoryRequest = true;
  try {
    const response = await fetch("/api/system/vram", { cache: "no-store", signal: AbortSignal.timeout(5000) });
    if (!response.ok) throw new Error("Memory reporting is unavailable.");
    const data = await response.json();
    if (!data.available || !Number.isFinite(data.used_bytes) || !Number.isFinite(data.total_bytes) ||
        data.total_bytes <= 0 || data.used_bytes < 0 || data.used_bytes > data.total_bytes) {
      throw new Error("Memory reporting is unavailable.");
    }
    const shared = data.kind === "shared";
    const percent = Math.min(100, Math.max(0, data.used_bytes / data.total_bytes * 100));
    const gib = (bytes) => (bytes / 1024 ** 3).toLocaleString(undefined, { minimumFractionDigits: 1, maximumFractionDigits: 1 });
    $("vram-label").textContent = shared ? "SHARED VRAM" : "VRAM";
    $("vram-usage").textContent = `${gib(data.used_bytes)} / ${gib(data.total_bytes)} GiB`;
    $("vram-percent").textContent = `${percent.toFixed(0)}%`;
    $("vram-caption").textContent = shared ? "CPU + GPU shared memory" : "GPU memory in use";
    $("vram-card").classList.remove("unavailable");
    $("vram-card").title = shared
      ? "Jetson shares physical memory between CPU and GPU. Used = total minus available memory, including other apps and the operating system."
      : "Memory used across all NVIDIA GPUs on this device.";
    $("vram-meter").hidden = false;
    $("vram-meter").setAttribute("aria-label", shared ? "Shared CPU and GPU memory usage" : "GPU memory usage");
    $("vram-meter").setAttribute("aria-valuenow", percent.toFixed(1));
    $("vram-meter").setAttribute("aria-valuetext", $("vram-usage").textContent);
    $("vram-fill").style.width = `${percent}%`;
  } catch (_error) {
    $("vram-usage").textContent = "Unavailable";
    $("vram-percent").textContent = "—";
    $("vram-caption").textContent = "Memory reporting unavailable";
    $("vram-card").classList.add("unavailable");
    $("vram-card").title = "Memory reporting is temporarily unavailable. Retrying automatically.";
    $("vram-meter").hidden = true;
    $("vram-meter").removeAttribute("aria-valuenow");
  } finally { memoryRequest = false; }
}

async function api(path, options = {}) {
  const response = await fetch(path, { ...options, headers: { "Content-Type": "application/json", ...options.headers } });
  if (response.status === 204) return null;
  const data = await response.json();
  if (!response.ok) throw new Error(data.error || "Something went wrong. Please try again.");
  return data;
}

function notice(message = "") {
  $("notice").textContent = message;
  $("notice").hidden = !message;
}

function controls() {
  $("clear-model-memory").disabled = state.busy || state.pending;
  $("save-chat").disabled = state.busy || state.pending || !state.messages.length;
  $("prompt").disabled = state.busy || state.pending;
  $("save-chat").textContent = state.id ? "Save / rename" : "Save conversation ↗";
  $("send").disabled = state.busy || state.pending || !state.models.includes(state.model) || !$("prompt").value.trim();
  $("send").hidden = state.busy;
  $("stop").hidden = !state.busy;
  $("model").disabled = state.busy || state.pending || !(visibleModels().length || state.models.includes(state.model));
  $("refresh-models").disabled = state.busy || state.pending;
  $("manage-models").disabled = state.busy || state.pending;
  $("show-model-manager").disabled = state.busy || state.pending;
  $("new-chat").disabled = state.busy || state.pending;
  $("delete-chat").disabled = state.busy || state.pending;
  $("delete-chat").hidden = !state.id;
  $("conversation-title").textContent = state.name || "New conversation";
  const meta = state.id
    ? `Created ${dateTimeFormat.format(new Date(state.createdAt))} · Saved ${dateTimeFormat.format(new Date(state.updatedAt))}`
    : "Your next thought starts here.";
  $("conversation-meta").textContent = state.dirty ? `${state.id ? "Unsaved changes" : "Not saved yet"} · Save when you’re ready.` : meta;
  document.title = `${state.name || "Midnight Chats"}${state.dirty ? " •" : ""}${state.name ? " · Midnight Chats" : ""}`;
}

function resizePrompt() {
  $("prompt").style.height = "auto";
  $("prompt").style.height = `${Math.min($("prompt").scrollHeight, 180)}px`;
  controls();
}

function renderContent(target, content) {
  // All model and user content is inserted as text, never interpreted as HTML.
  target.replaceChildren();
  const parts = content.split("```");
  parts.forEach((part, index) => {
    if (index % 2 === 0) {
      const prose = document.createElement("div");
      prose.className = "message-prose";
      prose.textContent = part;
      target.append(prose);
    } else {
      const pre = document.createElement("pre");
      const newline = part.indexOf("\n");
      const language = newline >= 0 ? part.slice(0, newline).trim() : "";
      if (language) {
        const label = document.createElement("div");
        label.className = "code-label";
        label.textContent = language;
        pre.append(label);
      }
      const code = document.createElement("code");
      code.textContent = newline >= 0 ? part.slice(newline + 1) : part;
      pre.append(code);
      target.append(pre);
    }
  });
}

function messageElement(message) {
  const article = document.createElement("article");
  article.className = `message ${message.role}`;
  const heading = document.createElement("div");
  heading.className = "message-header";
  const avatar = document.createElement("span");
  avatar.className = "avatar";
  avatar.setAttribute("aria-hidden", "true");
  avatar.textContent = message.role === "user" ? "Y" : "☾";
  const name = document.createElement("span");
  name.textContent = message.role === "user" ? "You" : "Assistant";
  heading.append(avatar, name);
  const body = document.createElement("div");
  body.className = "message-body";
  renderContent(body, message.content);
  const status = document.createElement("div");
  status.className = "message-state";
  status.textContent = message.interrupted ? "Response interrupted" : "";
  article.append(heading, body, status);
  $("messages").append(article);
  return { body, status };
}

function renderMessages() {
  $("welcome").hidden = state.messages.length > 0;
  $("messages").replaceChildren();
  state.messages.forEach(messageElement);
  scrollToEnd();
  controls();
}

function scrollToEnd() { $("chat-scroll").scrollTop = $("chat-scroll").scrollHeight; }
function nearEnd() { const el = $("chat-scroll"); return el.scrollHeight - el.scrollTop - el.clientHeight < 100; }

function visibleModels() { return state.models.filter((name) => !state.hiddenModels.includes(name)); }

function renderModels() {
  $("model").replaceChildren();
  const names = visibleModels();
  const allHidden = state.models.length > 0 && !names.length;
  $("model-visibility-notice").hidden = !allHidden;
  // Preserve the current model, including a hidden one, without switching or
  // unloading it. Saved conversations may also use models absent from Ollama.
  if (state.model && !names.includes(state.model)) names.unshift(state.model);
  if (!names.length) {
    $("model").add(new Option(allHidden ? "All models are hidden" : "No models available", ""));
  } else {
    names.forEach((name) => {
      const installed = state.models.includes(name);
      const suffix = !installed ? " (not installed)" : state.hiddenModels.includes(name) ? " (hidden)" : "";
      const option = new Option(`${name}${suffix}`, name);
      option.disabled = !installed;
      $("model").add(option);
    });
    $("model").value = state.model;
  }
  controls();
}

async function openModelManager() {
  if (state.busy || state.pending) return;
  // Refresh both installation and visibility state, including changes saved
  // from another browser. If either request fails, keep the dialog closed.
  if (!await loadModels()) return;
  $("model-visibility-list").replaceChildren();
  for (const name of state.models) {
    const label = document.createElement("label");
    label.className = "model-visibility-row";
    const checkbox = document.createElement("input");
    checkbox.type = "checkbox";
    checkbox.value = name;
    checkbox.checked = !state.hiddenModels.includes(name);
    checkbox.defaultChecked = checkbox.checked;
    checkbox.setAttribute("aria-label", `Show ${name} in picker`);
    const text = document.createElement("span");
    text.textContent = name;
    label.append(checkbox, text);
    $("model-visibility-list").append(label);
  }
  $("models-empty").hidden = state.models.length > 0;
  $("model-visibility-options").hidden = !state.models.length;
  $("models-error").hidden = true;
  $("save-models").disabled = true;
  $("models-dialog").showModal();
}

function visibilityChanges() {
  return Object.fromEntries([...$("model-visibility-list").querySelectorAll("input")]
    .filter((checkbox) => checkbox.checked !== checkbox.defaultChecked)
    .map((checkbox) => [checkbox.value, checkbox.checked]));
}

async function saveModelVisibility(event) {
  event.preventDefault();
  if (state.busy || state.pending) return;
  const visibility = visibilityChanges();
  if (!Object.keys(visibility).length) return;
  state.pending = true;
  controls();
  $("model-visibility-options").disabled = true;
  $("cancel-models").disabled = true;
  $("save-models").disabled = true;
  $("models-error").hidden = true;
  try {
    // Send only changed models so another browser's unrelated choices survive.
    const data = await api("/api/model-preferences", { method: "PATCH", body: JSON.stringify({ visibility }) });
    state.hiddenModels = data.hidden_models;
    renderModels();
    $("models-dialog").close();
    notice("Model visibility saved. Your current conversation is unchanged.");
  } catch (error) {
    $("models-error").textContent = error.message;
    $("models-error").hidden = false;
  } finally {
    state.pending = false;
    $("model-visibility-options").disabled = false;
    $("cancel-models").disabled = false;
    $("save-models").disabled = !Object.keys(visibilityChanges()).length;
    controls();
  }
}

async function unloadPreviousModel(nextModel) {
  if (!state.model || state.model === nextModel) return;
  notice(`Unloading ${state.model} from memory…`);
  const result = await api("/api/models/unload", {
    method: "POST", body: JSON.stringify({ model: state.model })
  });
  if (result.unloaded !== true) throw new Error("Could not confirm that the previous model was unloaded. Please try switching again.");
  refreshMemory();
}

async function clearModelMemory() {
  if (state.busy || state.pending) return;
  state.pending = true;
  controls();
  $("clear-model-memory").textContent = "Clearing…";
  $("clear-model-memory").setAttribute("aria-busy", "true");
  notice("Clearing all Ollama models from memory…");
  try {
    const result = await api("/api/models/unload-all", { method: "POST", body: "{}" });
    if (result.cleared !== true) throw new Error("Could not confirm that all Ollama models were unloaded.");
    notice(result.unloaded_models.length
      ? "All Ollama models cleared from memory. Your next message will reload the selected model."
      : "No Ollama models are currently loaded in memory.");
  } catch (error) { notice(error.message); }
  finally {
    state.pending = false;
    $("clear-model-memory").textContent = "Clear model memory";
    $("clear-model-memory").removeAttribute("aria-busy");
    controls();
    refreshMemory();
  }
}

async function changeModel(nextModel) {
  if (state.busy || state.pending || nextModel === state.model || !visibleModels().includes(nextModel)) {
    renderModels();
    return;
  }
  state.pending = true;
  controls();
  try {
    await unloadPreviousModel(nextModel);
    state.model = nextModel;
    if (state.messages.length) state.dirty = true;
    notice("Model changed. The previous model has been unloaded.");
  } catch (error) {
    notice(`Model selection kept. ${error.message}`);
  } finally {
    state.pending = false;
    renderModels();
  }
}

async function loadModels() {
  if (state.busy || state.pending) return false;
  state.pending = true;
  controls();
  try {
    const [data, preferences] = await Promise.all([api("/api/models"), api("/api/model-preferences")]);
    state.models = data.models;
    state.hiddenModels = preferences.hidden_models;
    if (!state.model) state.model = visibleModels()[0] || "";
    $("connection-dot").className = "status-dot online";
    $("connection-label").textContent = "Ollama connected";
    if (!state.models.length) notice("Ollama is running, but no models are installed. Install a chat model on the device with ollama pull <model>, then refresh.");
    else notice();
    return true;
  } catch (error) {
    state.models = [];
    $("connection-dot").className = "status-dot offline";
    $("connection-label").textContent = "Ollama offline";
    notice(error.message);
    return false;
  } finally { state.pending = false; renderModels(); }
}

async function loadConversations() {
  const data = await api("/api/conversations");
  $("chat-count").textContent = data.conversations.length;
  $("saved-chats").replaceChildren();
  if (!data.conversations.length) {
    const empty = document.createElement("p");
    empty.className = "sidebar-empty";
    empty.textContent = "A place for your conversations. Save a chat to find it here.";
    $("saved-chats").append(empty);
  }
  data.conversations.forEach((chat) => {
    const button = document.createElement("button");
    button.className = `saved-item${chat.id === state.id ? " active" : ""}`;
    if (chat.id === state.id) button.setAttribute("aria-current", "true");
    button.title = chat.name;
    const name = document.createElement("span");
    name.className = "saved-name";
    name.textContent = chat.name;
    const date = document.createElement("span");
    date.className = "saved-date";
    date.textContent = dateFormat.format(new Date(chat.created_at));
    button.append(name, date);
    button.addEventListener("click", () => openConversation(chat.id));
    $("saved-chats").append(button);
  });
}

function canLeave() {
  if (state.busy || state.pending) return false;
  return !(state.dirty || $("prompt").value.trim()) || window.confirm("Leave this conversation? Unsaved changes and your draft will be discarded.");
}

function sidebar(open) {
  document.body.classList.toggle("sidebar-open", open);
  $("sidebar-scrim").hidden = !open;
  $("toggle-sidebar").setAttribute("aria-expanded", String(open));
}

async function openConversation(id) {
  if (id === state.id || !canLeave()) return;
  state.pending = true;
  controls();
  try {
    const chat = await api(`/api/conversations/${id}`);
    await unloadPreviousModel(chat.model);
    Object.assign(state, { id: chat.id, name: chat.name, model: chat.model, messages: chat.messages,
      createdAt: chat.created_at, updatedAt: chat.updated_at, dirty: false });
    $("prompt").value = "";
    resizePrompt();
    renderModels();
    renderMessages();
    sidebar(false);
    notice(state.models.includes(state.model) ? "" : "This chat’s model is unavailable. Choose an installed model to continue.");
    await loadConversations();
  } catch (error) { notice(error.message); }
  finally { state.pending = false; controls(); }
}

async function newConversation() {
  if (!canLeave()) return;
  state.pending = true;
  controls();
  try {
    const names = visibleModels();
    const nextModel = names.includes(state.model) ? state.model : names[0] || "";
    await unloadPreviousModel(nextModel);
    Object.assign(state, { id: null, name: "", model: nextModel, messages: [], createdAt: null, updatedAt: null, dirty: false });
    $("prompt").value = "";
    resizePrompt();
    renderModels();
    renderMessages();
    sidebar(false);
    notice();
    await loadConversations();
  } catch (error) { notice(error.message); }
  finally {
    state.pending = false;
    controls();
    $("prompt").focus();
  }
}

async function sendMessage(event) {
  event.preventDefault();
  const prompt = $("prompt").value.trim();
  if (!prompt || state.busy || state.pending || !state.models.includes(state.model)) return;
  const previousDirty = state.dirty;
  state.busy = true;
  state.dirty = true;
  state.controller = new AbortController();
  const user = { role: "user", content: prompt };
  state.messages.push(user);
  const history = state.messages.map(({ role, content }) => ({ role, content }));
  const assistant = { role: "assistant", content: "" };
  state.messages.push(assistant);
  $("prompt").value = "";
  resizePrompt();
  $("welcome").hidden = true;
  messageElement(user);
  const view = messageElement(assistant);
  view.status.textContent = "Connecting to model…";
  scrollToEnd();
  notice();
  let completed = false;
  try {
    const response = await fetch("/api/chat", { method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ model: state.model, messages: history }), signal: state.controller.signal });
    if (!response.ok) {
      const data = await response.json();
      throw new Error(data.error || "Could not generate a reply.");
    }
    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";
    function consume(line) {
      if (!line.trim()) return;
      const chunk = JSON.parse(line);
      if (chunk.error) throw new Error(chunk.error);
      if (chunk.content) {
        const follow = nearEnd();
        assistant.content += chunk.content;
        renderContent(view.body, assistant.content);
        view.status.textContent = "Replying…";
        if (follow) scrollToEnd();
      } else if (chunk.thinking) view.status.textContent = "Thinking…";
      if (chunk.done) completed = true;
    }
    while (true) {
      const { value, done } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      let newline;
      while ((newline = buffer.indexOf("\n")) >= 0) {
        consume(buffer.slice(0, newline));
        buffer = buffer.slice(newline + 1);
      }
    }
    buffer += decoder.decode();
    consume(buffer);
    if (!completed) throw new Error("The response ended unexpectedly. Please try again.");
    if (!assistant.content.trim()) throw new Error("The model returned no text. Try another prompt or model.");
    view.status.textContent = "";
  } catch (error) {
    state.controller.abort();
    completed = false;
    notice(error.name === "AbortError" ? "Generation stopped." : error.message);
  } finally {
    if (!assistant.content.trim()) {
      state.messages.splice(-2);
      state.dirty = previousDirty;
      $("prompt").value = prompt;
      renderMessages();
    } else if (!completed) {
      assistant.interrupted = true;
      view.status.textContent = "Response interrupted · partial reply kept";
    }
    state.busy = false;
    state.controller = null;
    refreshMemory();
    resizePrompt();
    $("prompt").focus();
  }
}

$("composer").addEventListener("submit", sendMessage);
$("prompt").addEventListener("input", resizePrompt);
$("prompt").addEventListener("keydown", (event) => {
  if (event.key === "Enter" && !event.shiftKey && !event.isComposing) {
    event.preventDefault();
    $("composer").requestSubmit();
  }
});
$("stop").addEventListener("click", () => state.controller?.abort());
$("model").addEventListener("change", () => changeModel($("model").value));
$("refresh-models").addEventListener("click", loadModels);
$("manage-models").addEventListener("click", openModelManager);
$("show-model-manager").addEventListener("click", openModelManager);
$("cancel-models").addEventListener("click", () => $("models-dialog").close());
$("model-visibility-list").addEventListener("change", () => {
  $("save-models").disabled = !Object.keys(visibilityChanges()).length;
});
$("models-form").addEventListener("submit", saveModelVisibility);
$("models-dialog").addEventListener("cancel", (event) => { if (state.pending) event.preventDefault(); });
$("clear-model-memory").addEventListener("click", clearModelMemory);
$("new-chat").addEventListener("click", newConversation);
$("toggle-sidebar").addEventListener("click", () => sidebar(!document.body.classList.contains("sidebar-open")));
$("sidebar-scrim").addEventListener("click", () => sidebar(false));
document.addEventListener("keydown", (event) => { if (event.key === "Escape") sidebar(false); });
document.querySelectorAll("[data-prompt]").forEach((button) => button.addEventListener("click", () => {
  $("prompt").value = button.dataset.prompt;
  resizePrompt();
  $("prompt").focus();
}));
$("save-chat").addEventListener("click", () => {
  $("conversation-name").value = state.name;
  $("save-dialog-title").textContent = state.id ? "Save this conversation" : "Keep this conversation";
  $("save-error").hidden = true;
  $("save-dialog").showModal();
  $("conversation-name").focus();
});
$("cancel-save").addEventListener("click", () => $("save-dialog").close());
$("save-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const name = $("conversation-name").value.trim();
  if (!name || state.pending || state.busy) return;
  state.pending = true;
  controls();
  $("confirm-save").disabled = true;
  $("cancel-save").disabled = true;
  try {
    const chat = await api(`/api/conversations${state.id ? `/${state.id}` : ""}`, {
      method: state.id ? "PUT" : "POST", body: JSON.stringify({ name, model: state.model, messages: state.messages }) });
    Object.assign(state, { id: chat.id, name: chat.name, createdAt: chat.created_at, updatedAt: chat.updated_at, dirty: false });
    $("save-dialog").close();
    notice("Conversation saved on this device.");
    try { await loadConversations(); } catch (error) { notice(`Saved, but the conversation list could not refresh: ${error.message}`); }
  } catch (error) {
    $("save-error").textContent = error.message;
    $("save-error").hidden = false;
  } finally {
    state.pending = false;
    $("confirm-save").disabled = false;
    $("cancel-save").disabled = false;
    controls();
  }
});
$("save-dialog").addEventListener("cancel", (event) => { if (state.pending) event.preventDefault(); });
$("delete-chat").addEventListener("click", async () => {
  if (!state.id || state.busy || state.pending || !window.confirm(`Delete “${state.name}”? This will permanently remove the saved conversation.`)) return;
  state.pending = true;
  controls();
  try {
    await api(`/api/conversations/${state.id}`, { method: "DELETE" });
    Object.assign(state, { id: null, name: "", messages: [], createdAt: null, updatedAt: null, dirty: false });
    $("prompt").value = "";
    resizePrompt();
    renderMessages();
    await loadConversations();
    notice("Conversation deleted.");
  } catch (error) { notice(error.message); }
  finally { state.pending = false; controls(); }
});
window.addEventListener("beforeunload", (event) => {
  if (state.dirty || state.busy || state.pending || $("prompt").value.trim()) { event.preventDefault(); event.returnValue = ""; }
});

Promise.allSettled([loadModels(), loadConversations()]).then((results) => {
  results.forEach((result) => { if (result.status === "rejected") notice(result.reason.message); });
});
refreshMemory();
setInterval(refreshMemory, 5000);
document.addEventListener("visibilitychange", () => { if (!document.hidden) refreshMemory(); });
