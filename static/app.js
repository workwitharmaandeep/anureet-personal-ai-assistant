// Anureet Voice AI Assistant - Mobile Web App Logic
let ws = null;
let reconnectTimer = null;
let reconnectDelayMs = 2000;
let currentPath = '.';
let filesLoadedOnce = false;
let isRecording = false;
let recognition = null;
let ttsEnabled = true;
let cachedVoices = [];

document.addEventListener('DOMContentLoaded', () => {
  initTabs();
  initChatControls();
  initFileManagerControls();
  initSpeechSynthesis();
  initSpeechRecognition();
  initWebSocket();
});

// Tabs (ARIA selection by activation + Left/Right keyboard navigation)
function initTabs() {
  const tabs = Array.from(document.querySelectorAll('[role="tab"]'));
  tabs.forEach((tab, index) => {
    tab.addEventListener('click', () => switchTab(tab.dataset.tab));
    tab.addEventListener('keydown', (e) => {
      if (e.key === 'ArrowRight' || e.key === 'ArrowLeft') {
        e.preventDefault();
        const dir = e.key === 'ArrowRight' ? 1 : -1;
        const nextTab = tabs[(index + dir + tabs.length) % tabs.length];
        nextTab.focus();
      } else if (e.key === 'Home') {
        e.preventDefault();
        tabs[0].focus();
      } else if (e.key === 'End') {
        e.preventDefault();
        tabs[tabs.length - 1].focus();
      }
    });
  });
}

function switchTab(tabName) {
  const tabs = document.querySelectorAll('[role="tab"]');
  const panels = document.querySelectorAll('[role="tabpanel"]');

  tabs.forEach((tab) => {
    const selected = tab.dataset.tab === tabName;
    tab.setAttribute('aria-selected', String(selected));
    tab.tabIndex = selected ? 0 : -1;
  });

  panels.forEach((panel) => {
    const active = panel.id === `content-${tabName}`;
    panel.hidden = !active;
    panel.classList.toggle('active', active);
  });

  if (tabName === 'filemanager' && !filesLoadedOnce) {
    filesLoadedOnce = true;
    loadFiles(currentPath);
  }
}

// Chat & Quick Commands
function initChatControls() {
  const form = document.getElementById('chat-form');
  form.addEventListener('submit', (e) => {
    e.preventDefault();
    sendMessage();
  });

  document.getElementById('mic-btn').addEventListener('click', toggleVoiceInput);

  document.querySelector('.quick-chips').addEventListener('click', (e) => {
    const chip = e.target.closest('.chip');
    if (chip && chip.dataset.command) {
      sendQuickCommand(chip.dataset.command);
    }
  });

  const ttsBtn = document.getElementById('tts-btn');
  ttsBtn.addEventListener('click', () => {
    ttsEnabled = !ttsEnabled;
    if (!ttsEnabled && 'speechSynthesis' in window) {
      window.speechSynthesis.cancel();
    }
    ttsBtn.setAttribute('aria-pressed', String(ttsEnabled));
    ttsBtn.setAttribute('aria-label', ttsEnabled ? 'Mute voice responses' : 'Unmute voice responses');
    ttsBtn.textContent = ttsEnabled ? '🔊 Voice On' : '🔇 Voice Off';
  });

  document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape' && 'speechSynthesis' in window) {
      window.speechSynthesis.cancel();
    }
  });
}

// WebSocket Setup with single-timer exponential backoff
function initWebSocket() {
  if (reconnectTimer) {
    clearTimeout(reconnectTimer);
    reconnectTimer = null;
  }
  if (ws && (ws.readyState === WebSocket.OPEN || ws.readyState === WebSocket.CONNECTING)) {
    return;
  }

  const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
  const wsUrl = `${protocol}//${window.location.host}/ws/chat`;

  updateConnectionStatus('connecting', 'Connecting...');

  try {
    ws = new WebSocket(wsUrl);

    ws.onopen = () => {
      reconnectDelayMs = 2000;
      updateConnectionStatus('online', 'Online');
    };

    ws.onmessage = (event) => {
      try {
        const data = JSON.parse(event.data);
        handleServerMessage(data);
      } catch (err) {
        console.error('WebSocket message parse error:', err);
      }
    };

    ws.onclose = () => {
      ws = null;
      updateConnectionStatus('offline', 'REST Mode');
      scheduleReconnect();
    };

    ws.onerror = () => {
      updateConnectionStatus('offline', 'REST Mode');
    };
  } catch (e) {
    ws = null;
    updateConnectionStatus('offline', 'REST Mode');
    scheduleReconnect();
  }
}

function scheduleReconnect() {
  if (reconnectTimer) return;
  reconnectTimer = setTimeout(() => {
    reconnectTimer = null;
    initWebSocket();
  }, reconnectDelayMs);
  reconnectDelayMs = Math.min(reconnectDelayMs * 2, 30000);
}

function updateConnectionStatus(state, text) {
  const badge = document.getElementById('connection-status');
  const statusText = document.getElementById('status-text');

  statusText.textContent = text;
  badge.dataset.state = state;
}

// Handle Incoming Server Messages via WebSocket
function handleServerMessage(data) {
  if (data.type === 'log') {
    console.log('[Agent Log]:', data.message);
  } else if (data.type === 'status') {
    updateTypingIndicator(true, data.message);
  } else if (data.type === 'response' && data.data) {
    updateTypingIndicator(false);
    appendAgentMessage(data.data.response_text, data.data.executed_tools);
    speakResponse(data.data.response_text);
  } else if (data.type === 'reminder' && data.message) {
    appendAgentMessage(`⏰ ${data.message}`);
    speakResponse(data.message);
  }
}

// Speech-to-Text Voice Input
function initSpeechRecognition() {
  const SpeechRecognition = window.SpeechRecognition || window.webkitSpeechRecognition;
  if (!SpeechRecognition) {
    console.warn('Web Speech API not supported on this browser.');
    return;
  }

  recognition = new SpeechRecognition();
  recognition.continuous = false;
  recognition.interimResults = false;
  const langSelect = document.getElementById('lang-select');
  recognition.lang = langSelect ? langSelect.value : 'en-IN';
  if (langSelect) {
    langSelect.addEventListener('change', () => {
      if (recognition) {
        recognition.lang = langSelect.value;
      }
    });
  }

  recognition.onstart = () => {
    isRecording = true;
    const micBtn = document.getElementById('mic-btn');
    micBtn.classList.add('recording');
    micBtn.setAttribute('aria-pressed', 'true');
    micBtn.setAttribute('aria-label', 'Stop voice input');
  };

  recognition.onresult = (event) => {
    const transcript = event.results?.[0]?.[0]?.transcript || '';
    if (transcript.trim()) {
      document.getElementById('user-input').value = transcript;
      sendMessage();
    }
  };

  recognition.onerror = (event) => {
    console.error('Speech recognition error:', event.error);
    stopRecording();
  };

  recognition.onend = () => {
    stopRecording();
  };
}

function toggleVoiceInput() {
  if (!recognition) {
    appendAgentMessage('Voice recognition is not supported on this browser. Try Chrome or Safari.');
    return;
  }

  if (isRecording) {
    recognition.stop();
    stopRecording();
  } else {
    try {
      recognition.start();
    } catch (err) {
      console.warn('Speech recognition start ignored:', err);
    }
  }
}

function stopRecording() {
  isRecording = false;
  const micBtn = document.getElementById('mic-btn');
  micBtn.classList.remove('recording');
  micBtn.setAttribute('aria-pressed', 'false');
  micBtn.setAttribute('aria-label', 'Start voice input');
}

// Text-to-Speech Voice Synthesis
function initSpeechSynthesis() {
  if (!('speechSynthesis' in window)) return;
  const refreshVoices = () => {
    cachedVoices = window.speechSynthesis.getVoices();
  };
  refreshVoices();
  window.speechSynthesis.addEventListener('voiceschanged', refreshVoices);
}

function speakResponse(text) {
  if (!ttsEnabled || !text || !('speechSynthesis' in window)) return;

  window.speechSynthesis.cancel();

  const cleanText = String(text)
    .replace(/\*\*/g, '')
    .replace(/```[\s\S]*?```/g, 'Code output complete.');
  const utterance = new SpeechSynthesisUtterance(cleanText);
  utterance.rate = 1.0;
  utterance.pitch = 1.0;

  const voices = cachedVoices.length ? cachedVoices : window.speechSynthesis.getVoices();
  const hasDevanagari = /[\u0900-\u097F]/.test(cleanText);
  const preferredVoice = hasDevanagari
    ? voices.find((v) => v.lang.includes('hi-IN') || v.name.includes('Hindi'))
    : voices.find(
        (v) =>
          v.lang.includes('en-IN') ||
          v.name.includes('India') ||
          v.name.includes('Zira') ||
          v.name.includes('Google UK English Female')
      );
  if (preferredVoice) {
    utterance.voice = preferredVoice;
    utterance.lang = preferredVoice.lang;
  } else {
    utterance.lang = hasDevanagari ? 'hi-IN' : 'en-IN';
  }

  window.speechSynthesis.speak(utterance);
}

// Messaging Logic
function sendQuickCommand(text) {
  document.getElementById('user-input').value = text;
  sendMessage();
}

async function sendMessage() {
  const inputEl = document.getElementById('user-input');
  const query = inputEl.value.trim();
  if (!query) return;

  if ('speechSynthesis' in window) {
    window.speechSynthesis.cancel();
  }
  appendUserMessage(query);
  inputEl.value = '';

  updateTypingIndicator(true, 'Anureet is thinking & executing tools...');

  if (ws && ws.readyState === WebSocket.OPEN) {
    ws.send(JSON.stringify({ query }));
    return;
  }

  try {
    const response = await fetch('/api/chat', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ query }),
    });
    const data = await response.json();
    updateTypingIndicator(false);

    if (!response.ok) {
      appendAgentMessage(`Error: ${data.detail || response.statusText}`);
      return;
    }

    appendAgentMessage(data.response_text, data.executed_tools);
    speakResponse(data.response_text);
  } catch (err) {
    updateTypingIndicator(false);
    appendAgentMessage('Error communicating with Anureet AI gateway: ' + err.message);
  }
}

function scrollChatToBottom() {
  const chatBox = document.getElementById('chat-box');
  requestAnimationFrame(() => {
    chatBox.scrollTop = chatBox.scrollHeight;
  });
}

function appendUserMessage(text) {
  const chatBox = document.getElementById('chat-box');
  const msgDiv = document.createElement('div');
  msgDiv.className = 'message user';
  const bubble = document.createElement('div');
  bubble.className = 'bubble';
  bubble.textContent = text;
  msgDiv.appendChild(bubble);
  chatBox.appendChild(msgDiv);
  scrollChatToBottom();
}

function appendAgentMessage(text, executedTools = []) {
  const chatBox = document.getElementById('chat-box');
  const msgDiv = document.createElement('div');
  msgDiv.className = 'message agent';

  const bubble = document.createElement('div');
  bubble.className = 'bubble';

  const formattedText = escapeHtml(text || 'No response.')
    .replace(/\*\*(.*?)\*\*/g, '<b>$1</b>')
    .replace(/`([^`]+)`/g, '<code>$1</code>')
    .replace(/\n/g, '<br>');

  const textContainer = document.createElement('div');
  textContainer.innerHTML = formattedText;
  bubble.appendChild(textContainer);

  if (Array.isArray(executedTools) && executedTools.length > 0) {
    const toolsContainer = document.createElement('div');
    toolsContainer.className = 'tools-container';

    executedTools.forEach((tool) => {
      const toolName = tool.tool || tool.name || 'tool';
      const details = tool.details || '';
      const execMs = tool.execution_time_ms ?? 0;

      const badge = document.createElement('div');
      badge.className = 'tool-badge';

      const infoSpan = document.createElement('span');
      const strong = document.createElement('b');
      strong.textContent = toolName;
      infoSpan.append('⚙️ ', strong, `: ${details}`);

      const pill = document.createElement('span');
      pill.className = 'status-pill';
      pill.textContent = `⚡ ${execMs}ms`;

      badge.append(infoSpan, pill);
      toolsContainer.appendChild(badge);
    });

    bubble.appendChild(toolsContainer);
  }

  msgDiv.appendChild(bubble);
  chatBox.appendChild(msgDiv);
  scrollChatToBottom();
}

function updateTypingIndicator(show, text = 'Processing...') {
  let indicator = document.getElementById('typing-indicator');
  if (show) {
    if (!indicator) {
      const chatBox = document.getElementById('chat-box');
      indicator = document.createElement('div');
      indicator.id = 'typing-indicator';
      indicator.className = 'message agent';
      const bubble = document.createElement('div');
      bubble.className = 'bubble typing-bubble';
      indicator.appendChild(bubble);
      chatBox.appendChild(indicator);
    }
    indicator.firstElementChild.textContent = `⏳ ${text}`;
    scrollChatToBottom();
  } else if (indicator) {
    indicator.remove();
  }
}

// File Manager Logic (delegated events, safe path handling)
function initFileManagerControls() {
  document.getElementById('up-btn').addEventListener('click', navigateParent);
  document.getElementById('refresh-btn').addEventListener('click', () => loadFiles(currentPath));

  const fileInput = document.getElementById('file-input');
  document.getElementById('upload-trigger-btn').addEventListener('click', () => fileInput.click());
  fileInput.addEventListener('change', uploadSelectedFile);

  document.getElementById('file-list-box').addEventListener('click', (e) => {
    const downloadBtn = e.target.closest('[data-action="download"]');
    if (downloadBtn) {
      e.stopPropagation();
      downloadFile(downloadBtn.dataset.path);
      return;
    }

    const folderBtn = e.target.closest('[data-action="open-dir"]');
    if (folderBtn) {
      loadFiles(folderBtn.dataset.path);
    }
  });
}

function formatBytes(bytes) {
  if (typeof bytes !== 'number' || Number.isNaN(bytes)) return '';
  if (bytes < 1024) return `${bytes} B`;
  const units = ['KB', 'MB', 'GB', 'TB'];
  let value = bytes / 1024;
  for (const unit of units) {
    if (value < 1024 || unit === 'TB') {
      return `${value.toFixed(1)} ${unit}`;
    }
    value /= 1024;
  }
  return `${bytes} B`;
}

async function loadFiles(path = '.') {
  const listContainer = document.getElementById('file-list-box');
  const pathText = document.getElementById('current-path-text');

  const normalized = (path || '.').replace(/\\/g, '/');
  currentPath = normalized;
  pathText.textContent = normalized === '.' ? '/' : `/${normalized}`;
  listContainer.innerHTML = '<div class="empty-state">Loading files...</div>';

  try {
    const res = await fetch(`/api/files/browse?path=${encodeURIComponent(normalized)}`);
    const data = await res.json();

    if (!res.ok) {
      listContainer.innerHTML = `<div class="error-state">Error: ${escapeHtml(data.detail || res.statusText)}</div>`;
      return;
    }

    if (!data.items || data.items.length === 0) {
      listContainer.innerHTML = '<div class="empty-state">Folder is empty.</div>';
      return;
    }

    const fragment = document.createDocumentFragment();
    data.items.forEach((item) => {
      const row = document.createElement('div');
      row.className = 'file-item';

      if (item.is_dir) {
        const dirBtn = document.createElement('button');
        dirBtn.type = 'button';
        dirBtn.className = 'file-info file-dir-btn';
        dirBtn.dataset.action = 'open-dir';
        dirBtn.dataset.path = item.path;

        const icon = document.createElement('span');
        icon.className = 'file-icon';
        icon.setAttribute('aria-hidden', 'true');
        icon.textContent = '📁';

        const metaWrap = document.createElement('div');
        metaWrap.className = 'file-text';

        const nameEl = document.createElement('div');
        nameEl.className = 'file-name';
        nameEl.textContent = item.name;

        const metaEl = document.createElement('div');
        metaEl.className = 'file-meta';
        metaEl.textContent = 'Folder';

        metaWrap.append(nameEl, metaEl);
        dirBtn.append(icon, metaWrap);
        row.appendChild(dirBtn);
      } else {
        const info = document.createElement('div');
        info.className = 'file-info';

        const icon = document.createElement('span');
        icon.className = 'file-icon';
        icon.setAttribute('aria-hidden', 'true');
        icon.textContent = '📄';

        const metaWrap = document.createElement('div');
        metaWrap.className = 'file-text';

        const nameEl = document.createElement('div');
        nameEl.className = 'file-name';
        nameEl.textContent = item.name;

        const sizeLabel = item.size_formatted || formatBytes(item.size);
        const metaEl = document.createElement('div');
        metaEl.className = 'file-meta';
        metaEl.textContent = `${sizeLabel} • ${item.modified}`;

        metaWrap.append(nameEl, metaEl);
        info.append(icon, metaWrap);

        const dlBtn = document.createElement('button');
        dlBtn.type = 'button';
        dlBtn.className = 'btn-sm primary';
        dlBtn.dataset.action = 'download';
        dlBtn.dataset.path = item.path;
        dlBtn.setAttribute('aria-label', `Download ${item.name}`);
        dlBtn.textContent = '📥 Download';

        row.append(info, dlBtn);
      }

      fragment.appendChild(row);
    });

    listContainer.replaceChildren(fragment);
  } catch (err) {
    listContainer.innerHTML = '<div class="error-state">Failed to connect to file manager API.</div>';
  }
}

function navigateParent() {
  const normalized = (currentPath || '.').replace(/\\/g, '/').replace(/^\/+|\/+$/g, '');
  if (!normalized || normalized === '.' || !normalized.includes('/')) {
    loadFiles('.');
    return;
  }
  const parts = normalized.split('/');
  parts.pop();
  loadFiles(parts.join('/') || '.');
}

async function uploadSelectedFile(event) {
  const input = event.target;
  const file = input.files && input.files[0];
  if (!file) return;

  const statusEl = document.getElementById('upload-status');
  statusEl.textContent = `Uploading '${file.name}'...`;

  const formData = new FormData();
  formData.append('file', file);
  formData.append('target_dir', currentPath);

  try {
    const res = await fetch('/api/files/upload', {
      method: 'POST',
      body: formData,
    });
    const data = await res.json();
    if (!res.ok) {
      statusEl.textContent = `Upload failed: ${data.detail || res.statusText}`;
      return;
    }
    statusEl.textContent = `Uploaded '${data.filename}' to ${data.saved_path}`;
    loadFiles(currentPath);
  } catch (err) {
    statusEl.textContent = `File upload failed: ${err.message}`;
  } finally {
    input.value = '';
  }
}

function downloadFile(filePath) {
  window.open(`/api/files/download?path=${encodeURIComponent(filePath)}`, '_blank');
}

function escapeHtml(str) {
  if (!str) return '';
  return String(str)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#039;');
}
