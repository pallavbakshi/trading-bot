/**
 * AI Chat drawer — right-side overlay panel.
 * Uses OpenRouter API for multi-model support with chart screenshot sharing.
 */

const MODELS = [
  { id: "google/gemini-3.1-flash-lite-preview", name: "Gemini 3.1 Flash Lite" },
  { id: "google/gemini-3.1-pro-preview", name: "Gemini 3.1 Pro" },
  { id: "anthropic/claude-sonnet-4.6", name: "Claude Sonnet 4.6" },
  { id: "openai/gpt-5.4", name: "GPT-5.4" },
  { id: "z-ai/glm-5", name: "GLM-5" },
  { id: "minimax/minimax-m2.5", name: "MiniMax M2.5" },
];

interface ContentPart {
  type: "text" | "image_url";
  text?: string;
  image_url?: { url: string };
}

interface Message {
  role: "user" | "assistant" | "system";
  content: string | ContentPart[];
}

let messages: Message[] = [];
let pendingScreenshot: string | null = null;
let pendingCsv: string | null = null;
let drawer: HTMLElement;
let messagesEl: HTMLElement;
let inputEl: HTMLTextAreaElement;
let modelSelect: HTMLSelectElement;
let sendBtn: HTMLButtonElement;
let previewEl: HTMLElement;
let contextFn: () => string;
let csvFn: () => string;

export function initChat(getContext: () => string, getCsv: () => string) {
  contextFn = getContext;
  csvFn = getCsv;
  createUI();
}

function getApiKey(): string | null {
  const envKey = (import.meta as any).env?.OPENROUTER_API_KEY;
  if (envKey) return envKey;
  return localStorage.getItem("openrouter_api_key");
}

function createUI() {
  // Toggle button
  const toggle = document.createElement("button");
  toggle.id = "chat-toggle";
  toggle.innerHTML = "&#128172;";
  toggle.title = "Open AI Chat";
  document.body.appendChild(toggle);

  // Drawer
  drawer = document.createElement("div");
  drawer.id = "chat-drawer";
  drawer.innerHTML = `
    <div id="chat-resize"></div>
    <div class="chat-header">
      <span class="chat-title">AI Chat</span>
      <select id="chat-model"></select>
      <button id="chat-close" title="Close">&times;</button>
    </div>
    <div class="chat-api-key" id="chat-api-key-bar">
      <input type="password" id="chat-api-input" placeholder="OpenRouter API Key" />
      <button id="chat-api-save">Save</button>
    </div>
    <div id="chat-messages"></div>
    <div id="chat-preview"></div>
    <div class="chat-input-bar">
      <button id="chat-screenshot" title="Attach chart screenshot">&#128247;</button>
      <button id="chat-csv" title="Attach visible bar data as CSV">&#128202;</button>
      <textarea id="chat-input" placeholder="Ask about the chart..." rows="2"></textarea>
      <button id="chat-send" title="Send">&#9654;</button>
    </div>
  `;
  document.body.appendChild(drawer);

  // Populate models
  modelSelect = drawer.querySelector("#chat-model")!;
  for (const m of MODELS) {
    const opt = document.createElement("option");
    opt.value = m.id;
    opt.textContent = m.name;
    modelSelect.appendChild(opt);
  }
  const savedModel = localStorage.getItem("openrouter_model");
  if (savedModel) modelSelect.value = savedModel;

  messagesEl = drawer.querySelector("#chat-messages")!;
  inputEl = drawer.querySelector("#chat-input")!;
  sendBtn = drawer.querySelector("#chat-send")!;
  previewEl = drawer.querySelector("#chat-preview")!;

  const screenshotBtn = drawer.querySelector("#chat-screenshot")! as HTMLButtonElement;
  const apiKeyBar = drawer.querySelector("#chat-api-key-bar")! as HTMLElement;
  const apiInput = drawer.querySelector("#chat-api-input")! as HTMLInputElement;
  const apiSave = drawer.querySelector("#chat-api-save")! as HTMLButtonElement;

  // Hide API key bar if key exists
  if (getApiKey()) apiKeyBar.style.display = "none";

  apiSave.addEventListener("click", () => {
    const key = apiInput.value.trim();
    if (key) {
      localStorage.setItem("openrouter_api_key", key);
      apiKeyBar.style.display = "none";
    }
  });
  apiInput.addEventListener("keydown", (e) => {
    if (e.key === "Enter") { e.preventDefault(); apiSave.click(); }
  });

  toggle.addEventListener("click", () => drawer.classList.add("open"));
  drawer.querySelector("#chat-close")!.addEventListener("click", () => drawer.classList.remove("open"));
  modelSelect.addEventListener("change", () => localStorage.setItem("openrouter_model", modelSelect.value));

  const csvBtn = drawer.querySelector("#chat-csv")! as HTMLButtonElement;

  csvBtn.addEventListener("click", () => {
    const csv = csvFn();
    if (!csv) return;
    pendingCsv = csv;
    const lines = csv.split("\n").length - 1; // minus header
    updatePreview();
  });

  screenshotBtn.addEventListener("click", async () => {
    screenshotBtn.disabled = true;
    screenshotBtn.textContent = "\u23F3";
    try {
      const el = document.getElementById("snapshot-region");
      if (!el) return;
      const { default: h2c } = await import("html2canvas");
      const canvas = await h2c(el, { backgroundColor: "#ffffff", scale: 1.5 });
      pendingScreenshot = canvas.toDataURL("image/png");
      updatePreview();
    } catch (err) {
      console.error("Screenshot failed:", err);
    } finally {
      screenshotBtn.disabled = false;
      screenshotBtn.textContent = "\uD83D\uDCF7";
    }
  });

  sendBtn.addEventListener("click", sendMessage);
  inputEl.addEventListener("keydown", (e) => {
    e.stopPropagation(); // prevent chart keyboard shortcuts
    if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); sendMessage(); }
  });

  // Also stop propagation on the API key input
  apiInput.addEventListener("keydown", (e) => e.stopPropagation());

  // Resize handle
  const resizeHandle = drawer.querySelector("#chat-resize")! as HTMLElement;
  let resizing = false;
  resizeHandle.addEventListener("mousedown", (e) => {
    e.preventDefault();
    resizing = true;
    resizeHandle.classList.add("active");
    drawer.style.transition = "none";
  });
  document.addEventListener("mousemove", (e) => {
    if (!resizing) return;
    const w = window.innerWidth - e.clientX;
    drawer.style.width = `${Math.max(280, Math.min(w, window.innerWidth * 0.8))}px`;
  });
  document.addEventListener("mouseup", () => {
    if (!resizing) return;
    resizing = false;
    resizeHandle.classList.remove("active");
    drawer.style.transition = "";
    localStorage.setItem("chat_drawer_width", drawer.style.width);
  });

  // Restore saved width
  const savedWidth = localStorage.getItem("chat_drawer_width");
  if (savedWidth) drawer.style.width = savedWidth;
}

function clearPreview() {
  pendingScreenshot = null;
  pendingCsv = null;
  previewEl.style.display = "none";
  previewEl.innerHTML = "";
}

function updatePreview() {
  const parts: string[] = [];
  if (pendingScreenshot) {
    parts.push(`<img src="${pendingScreenshot}" class="chat-preview-img" />`);
  }
  if (pendingCsv) {
    const lines = pendingCsv.split("\n").length - 1;
    parts.push(`<span class="chat-preview-csv">\uD83D\uDCCA ${lines} bars</span>`);
  }
  if (parts.length) {
    previewEl.innerHTML = parts.join("") + `<button id="chat-remove-preview" title="Remove">&times;</button>`;
    previewEl.style.display = "block";
    previewEl.querySelector("#chat-remove-preview")!.addEventListener("click", clearPreview);
  } else {
    clearPreview();
  }
}

function addBubble(role: "user" | "assistant", text: string, screenshot?: string): HTMLElement {
  const bubble = document.createElement("div");
  bubble.className = `chat-bubble chat-${role}`;

  if (screenshot) {
    const img = document.createElement("img");
    img.src = screenshot;
    img.className = "chat-bubble-img";
    bubble.appendChild(img);
  }

  const textEl = document.createElement("div");
  textEl.className = "chat-bubble-text";
  textEl.textContent = text;
  bubble.appendChild(textEl);

  messagesEl.appendChild(bubble);
  messagesEl.scrollTop = messagesEl.scrollHeight;
  return textEl;
}

async function sendMessage() {
  const text = inputEl.value.trim();
  if (!text && !pendingScreenshot && !pendingCsv) return;

  const apiKey = getApiKey();
  if (!apiKey) {
    const bar = drawer.querySelector("#chat-api-key-bar")! as HTMLElement;
    bar.style.display = "flex";
    (bar.querySelector("input") as HTMLInputElement).focus();
    return;
  }

  // Build content parts
  const content: ContentPart[] = [];
  const screenshot = pendingScreenshot;
  if (screenshot) {
    content.push({ type: "image_url", image_url: { url: screenshot } });
  }
  const csv = pendingCsv;
  const userText = text || (screenshot ? "What do you see in this chart?" : "Analyze this data.");
  let msgText = `[Context: ${contextFn()}]\n\n${userText}`;
  if (csv) {
    msgText += `\n\n[Bar Data CSV]\n${csv}`;
  }
  content.push({ type: "text", text: msgText });

  const bubbleLabel = userText + (csv ? ` [📊 ${csv.split("\n").length - 1} bars]` : "");
  addBubble("user", bubbleLabel, screenshot ?? undefined);
  inputEl.value = "";
  clearPreview();

  messages.push({ role: "user", content });

  const systemMsg: Message = {
    role: "system",
    content: "You are a trading chart analyst. The user shares screenshots of a pattern backtesting chart with candlesticks, volume, and technical indicators (SMA 50/200, RSI 14, AVWAP, Volume Profile, S/R zones, geometric patterns). Provide concise, actionable analysis referencing specific price levels, patterns, and indicators visible in the chart.",
  };

  const textEl = addBubble("assistant", "...");
  sendBtn.disabled = true;

  try {
    const resp = await fetch("https://openrouter.ai/api/v1/chat/completions", {
      method: "POST",
      headers: {
        "Authorization": `Bearer ${apiKey}`,
        "Content-Type": "application/json",
      },
      body: JSON.stringify({
        model: modelSelect.value,
        messages: [systemMsg, ...messages],
        stream: true,
      }),
    });

    if (!resp.ok) {
      const err = await resp.text();
      textEl.textContent = `Error ${resp.status}: ${err}`;
      return;
    }

    const reader = resp.body!.getReader();
    const decoder = new TextDecoder();
    let buffer = "";
    let fullText = "";

    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      const lines = buffer.split("\n");
      buffer = lines.pop()!;

      for (const line of lines) {
        if (!line.startsWith("data: ")) continue;
        const data = line.slice(6).trim();
        if (data === "[DONE]") continue;
        try {
          const parsed = JSON.parse(data);
          const delta = parsed.choices?.[0]?.delta?.content;
          if (delta) {
            fullText += delta;
            textEl.textContent = fullText;
            messagesEl.scrollTop = messagesEl.scrollHeight;
          }
        } catch { /* skip malformed chunks */ }
      }
    }

    messages.push({ role: "assistant", content: fullText });
  } catch (err) {
    textEl.textContent = `Error: ${err}`;
  } finally {
    sendBtn.disabled = false;
    inputEl.focus();
  }
}
