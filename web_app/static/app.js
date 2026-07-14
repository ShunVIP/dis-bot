const state = {
  me: null,
  voice: {
    room: null,
    stream: null,
    muted: false,
    deafened: false,
    connected: false,
    livekit: null,
    screenSharing: false,
  },
  platform: {
    scope: "channel",
    targetId: 0,
    title: "",
  },
  streams: {
    chat: null,
    platform: null,
  },
  attachments: {
    chat: [],
    platform: [],
  },
  messages: {
    chat: [],
    platform: [],
  },
  deferredInstallPrompt: null,
  server: null,
  profile: null,
};

const $ = (id) => document.getElementById(id);

if ("serviceWorker" in navigator) {
  navigator.serviceWorker.register("/static/service-worker.js").catch(console.error);
}

async function api(path, options = {}) {
  const response = await fetch(path, {
    credentials: "same-origin",
    headers: {
      "Content-Type": "application/json",
      ...(options.headers || {}),
    },
    ...options,
  });
  if (!response.ok) {
    const text = await response.text();
    let payload = {};
    try { payload = JSON.parse(text); } catch {}
    const error = new Error(payload.detail || payload.error || text || `HTTP ${response.status}`);
    error.payload = payload;
    error.status = response.status;
    error.retryAfter = Number(response.headers.get("Retry-After") || payload.retry_after || 0);
    throw error;
  }
  return response.json();
}

function chatErrorText(error) {
  if (error?.status === 429) {
    return `Слишком часто. Подожди ${Math.max(1, error.retryAfter || 1)} сек.`;
  }
  if (error?.payload?.error === "target_forbidden" || error?.payload?.error === "message_forbidden") {
    return "Нет доступа к этому каналу или сообщению.";
  }
  if (error?.payload?.error === "bad_message") return "Сообщение или вложение не прошло проверку.";
  return "Не удалось выполнить действие. Обнови страницу и попробуй ещё раз.";
}

async function uploadFiles(files) {
  const form = new FormData();
  [...files].forEach((file) => form.append("file", file));
  const response = await fetch("/api/uploads", {
    method: "POST",
    credentials: "same-origin",
    body: form,
  });
  if (!response.ok) {
    const text = await response.text();
    throw new Error(text || `HTTP ${response.status}`);
  }
  const data = await response.json();
  return data.files || [];
}

function renderAttachmentTray(scope) {
  const tray = scope === "chat" ? $("chatAttachmentTray") : $("platformAttachmentTray");
  if (!tray) return;
  const items = state.attachments[scope] || [];
  tray.innerHTML = items.map((item, index) => `
    <button type="button" class="attachment-pill" data-scope="${scope}" data-attachment-index="${index}">
      ${escapeHtml(item.name || "file")}
    </button>
  `).join("");
}

function setUploadBusy(scope, busy) {
  const button = scope === "chat" ? $("chatAttachButton") : $("platformAttachButton");
  if (!button) return;
  button.disabled = busy;
  button.textContent = busy ? "..." : (scope === "chat" ? "Файл" : "+");
}

async function handleFilePick(scope, files) {
  if (!files?.length) return;
  setUploadBusy(scope, true);
  try {
    const uploaded = await uploadFiles(files);
    state.attachments[scope] = [...state.attachments[scope], ...uploaded].slice(0, 10);
    renderAttachmentTray(scope);
  } finally {
    setUploadBusy(scope, false);
  }
}

function removePendingAttachment(event) {
  const button = event.target.closest("[data-attachment-index]");
  if (!button) return;
  const scope = button.dataset.scope;
  const index = Number(button.dataset.attachmentIndex);
  state.attachments[scope].splice(index, 1);
  renderAttachmentTray(scope);
}

function setView(name) {
  document.querySelectorAll(".view").forEach((item) => item.classList.remove("active"));
  document.querySelectorAll(".nav").forEach((item) => item.classList.remove("active"));
  $(name).classList.add("active");
  document.querySelector(`.nav[data-view="${name}"]`)?.classList.add("active");
}

async function applyInitialVoiceInvite() {
  const params = new URLSearchParams(location.search);
  const voiceRoomId = Number(params.get("voice") || 0);
  if (!voiceRoomId || !state.me?.authenticated) return;
  setView("voice");
  await loadVoiceRooms();
  await joinVoiceRoom(voiceRoomId, params.get("voice_invite") || "");
}

async function applyInitialView() {
  const view = new URLSearchParams(location.search).get("view");
  const allowed = new Set(["overview", "profile", "server", "members", "chat", "voice", "games", "settings"]);
  if (!view || !allowed.has(view)) return;
  setView(view);
  if (view === "profile") await loadUnifiedProfile().catch(console.error);
  if (view === "server") await loadPlatform().catch(console.error);
  if (view === "members") await loadCommunity().catch(console.error);
  if (view === "chat") await loadChat().catch(console.error);
  if (view === "voice") await loadVoiceRooms().catch(console.error);
  if (view === "games") await loadLol().catch(console.error);
  if (view === "settings") await loadSettings().catch(console.error);
}

function renderMe(data) {
  state.me = data;
  const signed = Boolean(data.authenticated);
  const oauthAvailable = Boolean(data.auth_methods?.discord_oauth);
  $("loginLink").classList.toggle("hidden", signed || !oauthAvailable);
  $("logoutLink").classList.toggle("hidden", !signed);
  $("localLoginForm").classList.toggle("hidden", signed);
  $("discordCodeForm").classList.toggle("hidden", signed);
  $("loginProfileForm").classList.toggle("hidden", !signed);
  $("discordStatus").textContent = signed ? "online" : "guest";
  document.querySelectorAll("[data-admin-only]").forEach((item) => {
    item.classList.toggle("hidden", signed && !data.is_admin);
  });
  if (!signed) {
    $("hello").textContent = "Кабинет ViPik";
    $("authState").textContent = oauthAvailable
      ? "Войди через Discord, чтобы открыть профиль, чат и настройки."
      : "Получи одноразовый код командой /приложение на сервере Discord.";
    $("riotStatus").textContent = "нет входа";
    $("connectionsList").textContent = "Вход не выполнен";
    $("loginProfileStatus").textContent = "Первый раз зайди через Discord, потом задай email и пароль для резервного входа.";
    return;
  }
  const user = data.user;
  $("hello").textContent = `Привет, ${user.global_name || user.username}`;
  $("authState").textContent = `Discord ID: ${user.id}`;
  const riot = data.riot_connections || [];
  $("riotStatus").textContent = riot.length ? "connected" : "not linked";
  $("connectionsList").innerHTML = data.user.connections?.length
    ? data.user.connections.map((item) => `<span class="pill">${item.type}: ${item.name}</span>`).join("")
    : "Discord connections не найдены или scope не выдан.";
  const profile = user.login_profile || {};
  $("profileEmail").value = profile.email || "";
  $("profileLogin").value = profile.login_name || user.global_name || user.username || "";
  $("loginProfileStatus").textContent = profile.has_password
    ? "Резервный вход настроен."
    : "Задай пароль, чтобы входить без Discord.";
}

function renderChat(messages) {
  const box = $("chatMessages");
  state.messages.chat = messages || [];
  const query = $("chatSearch")?.value?.trim().toLowerCase() || "";
  const visible = query ? state.messages.chat.filter((msg) => messageMatches(msg, query)) : state.messages.chat;
  box.innerHTML = visible.map((msg) => {
    const time = new Date(msg.created_at).toLocaleString();
    return `
      <div class="msg" data-message-id="${msg.id}" data-scope="chat">
        <div class="message-head">
          <b>${escapeHtml(msg.author_name || msg.discord_user_id)}</b>
          <time>${time}${msg.edited_at ? " · edited" : ""}</time>
        </div>
        <div class="message-body">${msg.deleted_at ? "<em>Сообщение удалено</em>" : renderRichContent(msg.content)}</div>
        ${msg.deleted_at ? "" : renderAttachments(msg.attachments || [])}
        ${renderReactions(msg.reactions || [])}
        ${renderMessageActions()}
      </div>
    `;
  }).join("");
  box.scrollTop = box.scrollHeight;
}

function messageMatches(msg, query) {
  const haystack = [
    msg.author_name,
    msg.content,
    ...(msg.attachments || []).map((item) => item.name),
  ].join(" ").toLowerCase();
  return haystack.includes(query);
}

function renderMessageActions() {
  return `
    <div class="message-actions">
      <button type="button" data-action="react">+</button>
      <button type="button" data-action="edit">edit</button>
      <button type="button" data-action="delete">del</button>
    </div>
  `;
}

function renderAttachments(attachments) {
  if (!attachments?.length) return "";
  return `
    <div class="attachment-list">
      ${attachments.map((item) => renderAttachment(item)).join("")}
    </div>
  `;
}

function renderAttachment(item) {
  const url = escapeHtml(safeAttachmentUrl(item.url));
  const name = escapeHtml(item.name || "file");
  const type = String(item.content_type || "");
  const size = item.size ? ` · ${formatBytes(item.size)}` : "";
  if (type.startsWith("image/")) {
    return `<a class="attachment-card image-attachment" href="${url}" target="_blank" rel="noopener noreferrer"><img src="${url}" alt=""><span>${name}${size}</span></a>`;
  }
  if (type.startsWith("video/")) {
    return `<div class="attachment-card"><video src="${url}" controls></video><a href="${url}" target="_blank" rel="noopener noreferrer">${name}${size}</a></div>`;
  }
  if (type.startsWith("audio/")) {
    return `<div class="attachment-card"><audio src="${url}" controls></audio><a href="${url}" target="_blank" rel="noopener noreferrer">${name}${size}</a></div>`;
  }
  return `<a class="attachment-card file-attachment" href="${url}" target="_blank" rel="noopener noreferrer">${name}${size}</a>`;
}

function safeAttachmentUrl(value) {
  const raw = String(value || "").trim();
  if (/^\/uploads\/[A-Za-z0-9._-]+$/.test(raw)) return raw;
  try {
    const parsed = new URL(raw);
    return parsed.protocol === "https:" ? parsed.href : "#";
  } catch {
    return "#";
  }
}

function formatBytes(value) {
  const size = Number(value || 0);
  if (size < 1024) return `${size} B`;
  if (size < 1024 * 1024) return `${Math.round(size / 1024)} KB`;
  return `${(size / 1024 / 1024).toFixed(1)} MB`;
}

function renderReactions(reactions) {
  if (!reactions.length) return `<div class="reaction-row"></div>`;
  return `
    <div class="reaction-row">
      ${reactions.map((item) => `<button type="button" data-action="react" data-emoji="${escapeHtml(item.emoji)}">${escapeHtml(item.emoji)} ${item.count}</button>`).join("")}
    </div>
  `;
}

function renderRichContent(content) {
  const text = String(content || "");
  const urls = [...text.matchAll(/https?:\/\/[^\s<>"']+/gi)].map((match) => match[0]);
  let html = escapeHtml(text).replace(/https?:\/\/[^\s<>"']+/gi, (url) => {
    const safe = escapeHtml(url);
    return `<a href="${safe}" target="_blank" rel="noopener noreferrer">${safe}</a>`;
  });
  const firstUrl = urls[0];
  if (firstUrl) html += renderLinkPreview(firstUrl);
  return html;
}

function renderLinkPreview(url) {
  let parsed;
  try {
    parsed = new URL(url);
  } catch {
    return "";
  }
  const safe = escapeHtml(url);
  const host = escapeHtml(parsed.hostname.replace(/^www\./, ""));
  const path = escapeHtml(decodeURIComponent(parsed.pathname).slice(0, 90));
  const isImage = /\.(png|jpe?g|gif|webp|avif)(\?.*)?$/i.test(url);
  if (isImage) {
    return `<a class="link-preview image-preview" href="${safe}" target="_blank" rel="noopener noreferrer"><img src="${safe}" alt=""></a>`;
  }
  return `
    <a class="link-preview" href="${safe}" target="_blank" rel="noopener noreferrer">
      <strong>${host}</strong>
      <span>${path || parsed.protocol}</span>
    </a>
  `;
}

function closeStream(name) {
  if (state.streams[name]) {
    state.streams[name].close();
    state.streams[name] = null;
  }
}

function openChatStream() {
  if (!state.me?.authenticated || state.streams.chat) return;
  const stream = new EventSource("/api/chat/stream?limit=80");
  stream.onmessage = (event) => {
    try {
      const data = JSON.parse(event.data);
      renderChat(data.messages || []);
    } catch (error) {
      console.error(error);
    }
  };
  stream.onerror = () => {
    closeStream("chat");
    setTimeout(openChatStream, 3000);
  };
  state.streams.chat = stream;
}

function openPlatformStream(scope, targetId) {
  if (!state.me?.authenticated || !targetId) return;
  closeStream("platform");
  const url = `/api/platform/messages/stream?scope=${encodeURIComponent(scope)}&target_id=${encodeURIComponent(targetId)}`;
  const stream = new EventSource(url);
  stream.onmessage = (event) => {
    try {
      const data = JSON.parse(event.data);
      renderPlatformMessages(data.messages || []);
      if (state.platform.scope === "dm" && state.platform.targetId) {
        markPlatformDmRead(state.platform.targetId).catch(console.error);
      }
    } catch (error) {
      console.error(error);
    }
  };
  stream.onerror = () => {
    closeStream("platform");
    if (state.platform.scope === scope && state.platform.targetId === targetId) {
      setTimeout(() => openPlatformStream(scope, targetId), 3000);
    }
  };
  state.streams.platform = stream;
}

function renderLol(payload) {
  const box = $("lolProfile");
  const status = $("lolStatus");
  const account = payload.account;
  const model = payload.model;
  if (!account) {
    box.classList.add("muted");
    status.textContent = "";
    box.textContent = "LoL профиль не привязан. Укажи Riot ID выше, чтобы связать аккаунт.";
    return;
  }
  box.classList.remove("muted");
  status.textContent = `Привязан: ${account.display_name}`;
  const labels = model?.labels || {};
  const features = model?.features || {};
  const items = [
    ["Riot", account.display_name],
    ["Тип", labels.primary || "нет анализа"],
    ["Второй тип", labels.secondary || "-"],
    ["Матчей", features.matches ?? 0],
    ["Winrate", `${features.winrate ?? 0}%`],
    ["KDA", features.avg_kda ?? 0],
    ["Роль", features.role_main || "unknown"],
    ["CS/min", features.avg_cs_per_min ?? 0],
    ["Vision", features.avg_vision_score ?? 0],
  ];
  box.innerHTML = items.map(([label, value]) => `<div class="profile-item"><span>${label}</span><strong>${escapeHtml(String(value))}</strong></div>`).join("");
}

async function loadMe() {
  const data = await api("/api/me");
  renderMe(data);
}

async function localLogin() {
  const data = await api("/auth/local", {
    method: "POST",
    body: JSON.stringify({
      email: $("localEmail").value.trim(),
      password: $("localPassword").value,
    }),
  });
  renderMe({ authenticated: true, user: data.user, riot_connections: [] });
  await Promise.allSettled([loadChat(), loadVoiceRooms(), loadLol()]);
}

async function discordCodeLogin() {
  await api("/auth/code", {
    method: "POST",
    body: JSON.stringify({ code: $("discordLoginCode").value.trim() }),
  });
  location.reload();
}

async function saveLoginProfile() {
  const data = await api("/api/me/login-profile", {
    method: "PATCH",
    body: JSON.stringify({
      email: $("profileEmail").value.trim(),
      login_name: $("profileLogin").value.trim(),
      password: $("profilePassword").value,
    }),
  });
  $("profilePassword").value = "";
  renderMe({ authenticated: true, user: data.user, riot_connections: [] });
}

async function loadChat() {
  if (!state.me?.authenticated) return;
  const data = await api("/api/chat?limit=80");
  renderChat(data.messages || []);
  openChatStream();
}

async function loadLol() {
  if (!state.me?.authenticated) return;
  const data = await api("/api/lol/profile");
  renderLol(data);
}

function groupedChannels(channels) {
  return channels.reduce((acc, channel) => {
    const category = channel.category || "Текстовые";
    acc[category] = acc[category] || [];
    acc[category].push(channel);
    return acc;
  }, {});
}

function serverInitials(server) {
  const source = (server?.icon || server?.name || "LC").trim();
  return source
    .split(/\s+/)
    .map((part) => part.slice(0, 1))
    .join("")
    .slice(0, 3)
    .toUpperCase() || "LC";
}

function renderServerSettings(server) {
  if (!server) return;
  state.server = server;
  const banner = String(server.banner || "midnight").replace(/[^a-z0-9_-]/gi, "") || "midnight";
  const name = server.name || "Ламповый Чай";
  const description = server.description || "Приватная платформа";
  if ($("platformServerName")) $("platformServerName").textContent = name;
  if ($("serverName")) $("serverName").value = name;
  if ($("serverBanner")) $("serverBanner").value = server.banner || "midnight";
  if ($("serverIcon")) $("serverIcon").value = server.icon || "";
  if ($("serverDescription")) $("serverDescription").value = server.description || "";
  if ($("serverPreview")) $("serverPreview").className = `server-preview banner-${banner}`;
  if ($("serverPreviewIcon")) $("serverPreviewIcon").textContent = serverInitials(server);
  if ($("serverPreviewName")) $("serverPreviewName").textContent = name;
  if ($("serverPreviewDescription")) $("serverPreviewDescription").textContent = description;
  if ($("serverSettingsStatus")) $("serverSettingsStatus").textContent = "загружено";
}

function renderPlatformBootstrap(data) {
  renderServerSettings(data.server || data.servers?.[0]);
  const channelBox = $("textChannelList");
  const groups = groupedChannels(data.channels || []);
  channelBox.innerHTML = Object.entries(groups).map(([category, channels]) => `
    <div class="channel-category">${escapeHtml(category)}</div>
    ${channels.map((channel) => `
      <button class="channel-button" type="button" data-scope="channel" data-target-id="${channel.id}" data-title="# ${escapeHtml(channel.name)}"># ${escapeHtml(channel.name)}</button>
    `).join("")}
  `).join("");

  const dmBox = $("dmThreadList");
  dmBox.innerHTML = (data.dms || []).map((thread) => `
    <button class="channel-button" type="button" data-scope="dm" data-target-id="${thread.id}" data-title="@ ${escapeHtml(thread.title)}">
      <span>@ ${escapeHtml(thread.title)}</span>
      ${thread.unread_count ? `<span class="dm-unread" aria-label="Непрочитанных: ${thread.unread_count}">${thread.unread_count}</span>` : ""}
    </button>
  `).join("") || `<div class="muted">ЛС пока нет</div>`;

  const dmSelect = $("dmPeerId");
  if (dmSelect) {
    const currentUserId = Number(state.me?.user?.id || 0);
    const peers = (data.members || []).filter((member) => Number(member.id) !== currentUserId);
    dmSelect.innerHTML = `<option value="">Выбрать участника</option>${peers.map((member) => {
      const profile = member.profile || {};
      const name = profile.display_name || member.global_name || member.username || String(member.id);
      return `<option value="${member.id}">${escapeHtml(name)}</option>`;
    }).join("")}`;
  }

  document.querySelectorAll(".channel-button").forEach((button) => {
    button.addEventListener("click", () => {
      selectPlatformTarget(button.dataset.scope, Number(button.dataset.targetId), button.dataset.title);
    });
  });

  renderActivity(data.activities || []);
  renderPresence(data.members || []);

  if (!state.platform.targetId && data.channels?.length) {
    const first = data.channels.find((item) => item.name === "general") || data.channels[0];
    selectPlatformTarget("channel", first.id, `# ${first.name}`);
  }
}

function renderActivity(items) {
  const box = $("activityList");
  if (!items.length) {
    box.classList.add("muted");
    box.textContent = "Нет активности";
    return;
  }
  box.classList.remove("muted");
  box.innerHTML = items.map((item) => `
    <div class="activity-item">
      <strong>${escapeHtml(item.global_name || item.username || String(item.discord_user_id))}</strong>
      <div>${escapeHtml(item.title || "Играет")}</div>
      <span>${escapeHtml(item.subtitle || item.activity_type || "")}</span>
    </div>
  `).join("");
}

function renderPresence(members) {
  const box = $("presenceMembers");
  if (!members.length) {
    box.classList.add("muted");
    box.textContent = "Нет участников";
    return;
  }
  box.classList.remove("muted");
  const currentUserId = Number(state.me?.user?.id || 0);
  box.innerHTML = members.map((member) => {
    const profile = member.profile || {};
    const name = profile.display_name || member.global_name || member.username || String(member.id);
    const roles = member.roles || [];
    return `
      <div class="presence-item">
        <strong style="color:${escapeHtml(profile.accent_color || "#f4f7f8")}">${escapeHtml(name)}</strong>
        <span>${roles.map((role) => role.name).join(", ") || "участник"}</span>
        ${Number(member.id) === currentUserId ? "" : `<button type="button" class="presence-dm" data-start-dm="${member.id}">Написать</button>`}
      </div>
    `;
  }).join("");
  box.querySelectorAll("[data-start-dm]").forEach((button) => {
    button.addEventListener("click", () => {
      startDmWithPeer(Number(button.dataset.startDm)).catch(console.error);
    });
  });
}

function renderPlatformMessages(messages) {
  const box = $("platformMessages");
  state.messages.platform = messages || [];
  const query = $("platformSearch")?.value?.trim().toLowerCase() || "";
  const visible = query ? state.messages.platform.filter((msg) => messageMatches(msg, query)) : state.messages.platform;
  if (!visible.length) {
    box.classList.add("muted");
    box.textContent = query ? "Ничего не найдено" : "Сообщений пока нет";
    return;
  }
  box.classList.remove("muted");
  box.innerHTML = visible.map((msg) => {
    const name = msg.author_name || String(msg.author_id);
    return `
      <div class="platform-message" data-message-id="${msg.id}" data-scope="platform">
        <div class="platform-avatar">${escapeHtml(name.slice(0, 1).toUpperCase())}</div>
        <div>
          <div class="platform-author">${escapeHtml(name)}<time>${new Date(msg.created_at).toLocaleString()}${msg.edited_at ? " · edited" : ""}</time></div>
          <div class="message-body">${msg.deleted_at ? "<em>Сообщение удалено</em>" : renderRichContent(msg.content)}</div>
          ${msg.deleted_at ? "" : renderAttachments(msg.attachments || [])}
          ${renderReactions(msg.reactions || [])}
          ${renderMessageActions()}
        </div>
      </div>
    `;
  }).join("");
  box.scrollTop = box.scrollHeight;
}

async function handleMessageAction(event) {
  const button = event.target.closest("[data-action]");
  if (!button) return;
  const message = event.target.closest("[data-message-id]");
  if (!message) return;
  const messageId = Number(message.dataset.messageId);
  const scope = message.dataset.scope;
  const base = scope === "chat" ? `/api/chat/${messageId}` : `/api/platform/messages/${messageId}`;
  const action = button.dataset.action;
  if (action === "react") {
    const emoji = button.dataset.emoji || prompt("Реакция", "+") || "+";
    await api(`${base}/reactions`, {
      method: "POST",
      body: JSON.stringify({ emoji }),
    });
  }
  if (action === "edit") {
    const current = message.querySelector(".message-body")?.innerText || "";
    const content = prompt("Новый текст", current);
    if (!content) return;
    await api(base, {
      method: "PATCH",
      body: JSON.stringify({ content }),
    });
  }
  if (action === "delete") {
    if (!confirm("Удалить сообщение?")) return;
    await api(base, { method: "DELETE" });
  }
  if (scope === "chat") await loadChat();
  if (scope === "platform" && state.platform.targetId) {
    await selectPlatformTarget(state.platform.scope, state.platform.targetId, state.platform.title);
  }
}

async function loadPlatform() {
  if (!state.me?.authenticated) return;
  const data = await api("/api/platform/bootstrap");
  renderPlatformBootstrap(data);
}

async function selectPlatformTarget(scope, targetId, title) {
  state.platform = { scope, targetId, title };
  $("platformChatTitle").textContent = title;
  document.querySelectorAll(".channel-button").forEach((button) => {
    button.classList.toggle("active", button.dataset.scope === scope && Number(button.dataset.targetId) === targetId);
  });
  const data = await api(`/api/platform/messages?scope=${encodeURIComponent(scope)}&target_id=${targetId}`);
  renderPlatformMessages(data.messages || []);
  if (scope === "dm") await markPlatformDmRead(targetId);
  openPlatformStream(scope, targetId);
}

async function markPlatformDmRead(threadId) {
  if (!threadId) return;
  await api(`/api/platform/dms/${threadId}/read`, { method: "POST", body: "{}" });
  const button = document.querySelector(`.channel-button[data-scope="dm"][data-target-id="${threadId}"]`);
  button?.querySelector(".dm-unread")?.remove();
}

async function sendPlatformMessage() {
  const content = $("platformMessageText").value.trim();
  const attachments = state.attachments.platform;
  if ((!content && !attachments.length) || !state.platform.targetId) return;
  await api("/api/platform/messages", {
    method: "POST",
    body: JSON.stringify({
      scope: state.platform.scope,
      target_id: state.platform.targetId,
      content,
      attachments,
    }),
  });
  $("platformMessageText").value = "";
  state.attachments.platform = [];
  renderAttachmentTray("platform");
  await selectPlatformTarget(state.platform.scope, state.platform.targetId, state.platform.title);
}

async function createPlatformChannel() {
  const name = $("newChannelName").value.trim();
  if (!name) return;
  await api("/api/platform/channels", {
    method: "POST",
    body: JSON.stringify({ server_id: 0, category: "Текстовые", name }),
  });
  $("newChannelName").value = "";
  await loadPlatform();
}

async function createPlatformDm() {
  const peerId = Number($("dmPeerId").value || 0);
  if (!peerId) return;
  await startDmWithPeer(peerId);
  $("dmPeerId").value = "";
}

async function startDmWithPeer(peerId) {
  if (!peerId) return;
  const data = await api("/api/platform/dms", {
    method: "POST",
    body: JSON.stringify({ peer_id: peerId }),
  });
  await loadPlatform();
  setView("server");
  await selectPlatformTarget("dm", data.thread.id, `@ ${data.thread.title}`);
}

function renderRoles(roles, targetId = "roleCatalog") {
  const box = $(targetId);
  if (!box) return;
  if (!roles?.length) {
    box.classList.add("muted");
    box.textContent = "Роли пока не настроены";
    return;
  }
  box.classList.remove("muted");
  box.innerHTML = roles.map((role) => (
    `<span class="role-chip" title="${escapeHtml(role.slug || "")}" style="border-color:${escapeHtml(role.color)};color:${escapeHtml(role.color)}">${escapeHtml(role.name)}</span>`
  )).join("");
}

function renderMembers(members) {
  const box = $("memberGrid");
  if (!members?.length) {
    box.classList.add("muted");
    box.textContent = "Пока нет участников";
    return;
  }
  box.classList.remove("muted");
  box.innerHTML = members.map((member) => {
    const profile = member.profile || {};
    const roles = member.roles || [];
    const name = profile.display_name || member.global_name || member.username || String(member.id);
    const color = profile.accent_color || "#4fc3b1";
    const status = profile.status_text || "на платформе";
    const badges = profile.badges || [];
    return `
      <article class="member-card">
        <div class="member-banner" style="background:${escapeHtml(color)}"></div>
        <div class="member-body">
          <div class="member-name">${escapeHtml(name)}</div>
          <div class="member-status">${escapeHtml(status)}</div>
          <div class="role-row">
            ${roles.map((role) => `<span class="role-chip" style="border-color:${escapeHtml(role.color)};color:${escapeHtml(role.color)}">${escapeHtml(role.name)}</span>`).join("") || "<span class=\"role-chip\">без роли</span>"}
          </div>
          <div class="badge-row">
            ${badges.map((badge) => `<span class="badge-chip">${escapeHtml(badge)}</span>`).join("")}
          </div>
        </div>
      </article>
    `;
  }).join("");
}

function fillCommunityProfile(payload) {
  const profile = payload.profile || {};
  $("communityDisplay").value = profile.display_name || state.me?.user?.global_name || state.me?.user?.username || "";
  $("communityStatus").value = profile.status_text || "";
  $("communityAccent").value = profile.accent_color || "#4fc3b1";
  $("communityBanner").value = profile.banner_preset || "midnight";
  $("communityDecoration").value = profile.avatar_decoration || "";
  $("communityBadges").value = (profile.badges || []).join(", ");
  $("communityBio").value = profile.bio || "";
}

async function loadCommunity() {
  if (!state.me?.authenticated) return;
  const [meData, memberData] = await Promise.all([
    api("/api/community/me"),
    api("/api/community/members"),
  ]);
  fillCommunityProfile(meData);
  renderRoles(memberData.roles || []);
  renderMembers(memberData.members || []);
}

function renderUnifiedProfile(payload) {
  const profile = payload || {};
  state.profile = profile;
  const community = profile.community || {};
  const economy = profile.economy || {};
  const economyProfile = economy.profile || {};
  const games = profile.games || {};
  const steam = games.steam;
  const lol = games.lol || {};
  const wwm = games.wwm;
  const birthday = profile.birthday || {};
  const ai = profile.ai || {};
  const aiConversation = ai.conversation || {};
  const gamerProfile = ai.gamer_profile || {};
  const displayName = community.display_name || state.me?.user?.global_name || state.me?.user?.username || "Мой профиль";

  $("unifiedProfileName").textContent = displayName;
  $("unifiedProfileStatus").textContent = community.status_text || "Личность, игры и активность в одном месте.";
  $("unifiedProfileHero").style.setProperty("--profile-accent", community.accent_color || "#4fc3b1");
  $("unifiedProfileRoles").innerHTML = (profile.roles || []).map((role) => (
    `<span class="role-chip" style="border-color:${escapeHtml(role.color)};color:${escapeHtml(role.color)}">${escapeHtml(role.name)}</span>`
  )).join("") || `<span class="role-chip">без роли</span>`;

  $("unifiedDisplayName").value = community.display_name || displayName;
  $("unifiedStatusText").value = community.status_text || "";
  $("unifiedBio").value = community.bio || "";
  $("unifiedBirthday").value = birthday.birthday || "";
  $("unifiedAccent").value = /^#[0-9a-f]{6}$/i.test(community.accent_color || "") ? community.accent_color : "#4fc3b1";
  $("unifiedGender").value = economyProfile.gender || "";
  $("unifiedAgeConfirmed").checked = Boolean(economyProfile.age_confirmed);
  $("unifiedGamerTags").value = (aiConversation.gamer_tags || []).join(", ");
  $("unifiedAiMemory").checked = Boolean(aiConversation.memory_opt_in);
  $("unifiedAiTraining").checked = Boolean(aiConversation.training_opt_in);

  $("unifiedBalance").textContent = String(economy.balance || 0);
  $("unifiedEconomyState").textContent = economyProfile.age_confirmed ? "Экономический профиль активен" : "Заполни профиль и подтверди 18+";
  $("unifiedSteam").textContent = steam ? `Steam ${steam.steam_id}` : "Не привязан";
  $("unifiedSteamMeta").textContent = steam ? `${steam.cached_games} игр · ${Math.round((steam.playtime_minutes || 0) / 60)} ч` : "Привязка доступна через бота";
  $("unifiedLol").textContent = lol.account?.display_name || "Не привязан";
  const lolLabel = lol.model?.labels ? Object.values(lol.model.labels).filter(Boolean).join(", ") : "";
  $("unifiedLolMeta").textContent = lolLabel || (lol.account ? "Профиль связан, обнови игровую модель" : "Riot ID и модель игрока");
  $("unifiedWwm").textContent = wwm?.game_nick || "Не привязан";
  $("unifiedWwmMeta").textContent = wwm ? (wwm.nick_synced ? "Ник синхронизирован с Discord" : "Ник сохранён") : "Игровой ник не указан";
  const archetypes = (gamerProfile.archetypes || []).slice(0, 3).map((item) => item.label).filter(Boolean);
  $("unifiedAiProfile").textContent = archetypes.join(", ") || "Без игрового профиля";
  $("unifiedAiMeta").textContent = `Память: ${aiConversation.memory_opt_in ? "да" : "нет"} · обучение: ${aiConversation.training_opt_in ? "да" : "нет"}`;
}

async function loadUnifiedProfile() {
  if (!state.me?.authenticated) return;
  const data = await api("/api/profile");
  renderUnifiedProfile(data.profile);
}

async function saveUnifiedProfile() {
  const gender = $("unifiedGender").value;
  const data = await api("/api/profile", {
    method: "PATCH",
    body: JSON.stringify({
      community: {
        display_name: $("unifiedDisplayName").value.trim(),
        status_text: $("unifiedStatusText").value.trim(),
        bio: $("unifiedBio").value.trim(),
        accent_color: $("unifiedAccent").value,
      },
      birthday: $("unifiedBirthday").value.trim(),
      economy: gender ? {
        gender,
        age_confirmed: $("unifiedAgeConfirmed").checked,
      } : null,
      ai: {
        memory_opt_in: $("unifiedAiMemory").checked,
        training_opt_in: $("unifiedAiTraining").checked,
        gamer_tags: $("unifiedGamerTags").value.trim(),
      },
    }),
  });
  renderUnifiedProfile(data.profile);
  $("unifiedProfileMessage").textContent = "Профиль сохранён и доступен боту, админке и приложению.";
}

async function forgetAiProfile() {
  if (!window.confirm("Удалить сохранённые диалоги, согласия и игровой AI-профиль?")) return;
  const data = await api("/api/profile/forget-ai", { method: "POST" });
  renderUnifiedProfile(data.profile);
  $("unifiedProfileMessage").textContent = "Диалоги, согласия и AI-профиль удалены.";
}

async function saveCommunityProfile() {
  const badges = $("communityBadges").value
    .split(",")
    .map((item) => item.trim())
    .filter(Boolean);
  await api("/api/community/me", {
    method: "PATCH",
    body: JSON.stringify({
      display_name: $("communityDisplay").value.trim(),
      status_text: $("communityStatus").value.trim(),
      accent_color: $("communityAccent").value.trim(),
      banner_preset: $("communityBanner").value,
      avatar_decoration: $("communityDecoration").value.trim(),
      badges,
      bio: $("communityBio").value.trim(),
    }),
  });
  $("communityStatusLine").textContent = "Косметика сохранена.";
  await loadCommunity();
}

function renderVoiceRooms(rooms) {
  const box = $("voiceRooms");
  box.innerHTML = rooms.map((room) => {
    const active = state.voice.room?.id === room.id ? " active" : "";
    const privacy = room.is_private ? " voice-room-private" : "";
    return `<button class="voice-room${active}${privacy}" type="button" data-room-id="${room.id}"># ${escapeHtml(room.name)}</button>`;
  }).join("");
  box.querySelectorAll(".voice-room").forEach((button) => {
    button.addEventListener("click", () => joinVoiceRoom(Number(button.dataset.roomId)).catch(console.error));
  });
}

function renderVoiceMembers() {
  const box = $("voiceMembers");
  if (!state.voice.connected || !state.me?.authenticated) {
    box.innerHTML = `<div class="voice-empty">Пока никто не подключён</div>`;
    return;
  }
  const local = state.voice.livekit?.localParticipant;
  const remote = state.voice.livekit ? [...state.voice.livekit.remoteParticipants.values()] : [];
  const participants = [
    {
      name: local?.name || state.me.user.global_name || state.me.user.username || String(state.me.user.id),
      identity: local?.identity || String(state.me.user.id),
      local: true,
      speaking: Boolean(local?.isSpeaking),
      mic: !state.voice.muted,
    },
    ...remote.map((participant) => ({
      name: participant.name || participant.identity,
      identity: participant.identity,
      local: false,
      speaking: Boolean(participant.isSpeaking),
      mic: Boolean(participant.isMicrophoneEnabled),
    })),
  ];
  box.innerHTML = participants.map((participant) => `
    <div class="voice-member">
      <strong>${escapeHtml(participant.name)}${participant.local ? " (вы)" : ""}</strong>
      <span>${participant.mic ? (participant.speaking ? "говорит" : "микрофон включён") : "микрофон выключен"}${participant.local && state.voice.deafened ? " · звук выключен" : ""}</span>
    </div>
  `).join("");
}

function setVoiceControls(enabled) {
  ["voiceMute", "voiceDeafen", "voiceScreen", "voiceSettings", "voiceLeave"].forEach((id) => {
    $(id).disabled = !enabled;
  });
  $("voiceMute").classList.toggle("active", state.voice.muted);
  $("voiceDeafen").classList.toggle("active", state.voice.deafened);
  $("voiceScreen").classList.toggle("active", state.voice.screenSharing);
}

async function loadVoiceRooms() {
  if (!state.me?.authenticated) return;
  const data = await api("/api/voice/rooms?guild_id=0");
  renderVoiceRooms(data.rooms || []);
}

async function createVoiceRoom(name, isPrivate = false) {
  await api("/api/voice/rooms", {
    method: "POST",
    body: JSON.stringify({ guild_id: 0, name, private: isPrivate }),
  });
  await loadVoiceRooms();
}

function setVoiceStatus(text) {
  $("voiceStatus").textContent = text;
}

function removeVoiceTrack(trackSid) {
  if (!trackSid) return;
  document.querySelectorAll(`[data-track-sid="${trackSid}"]`).forEach((item) => item.remove());
  $("voiceMedia").classList.toggle("hidden", !$("voiceMedia").children.length);
}

function attachVoiceTrack(track, publication, participant) {
  const trackSid = publication?.trackSid || track?.sid;
  if (!trackSid) return;
  removeVoiceTrack(trackSid);
  const element = track.attach();
  element.autoplay = true;
  if (track.kind === "audio") {
    element.muted = state.voice.deafened;
    element.dataset.trackSid = trackSid;
    $("voiceAudio").appendChild(element);
    return;
  }
  const tile = document.createElement("div");
  tile.className = "voice-video";
  tile.dataset.trackSid = trackSid;
  element.playsInline = true;
  if (participant === state.voice.livekit?.localParticipant) element.muted = true;
  const label = document.createElement("span");
  label.textContent = `${participant?.name || participant?.identity || "Участник"} · экран`;
  tile.append(element, label);
  $("voiceMedia").appendChild(tile);
  $("voiceMedia").classList.remove("hidden");
}

function bindLiveKitRoom(room) {
  const { RoomEvent } = window.LivekitClient;
  const refresh = () => renderVoiceMembers();
  room.on(RoomEvent.ParticipantConnected, refresh);
  room.on(RoomEvent.ParticipantDisconnected, refresh);
  room.on(RoomEvent.ActiveSpeakersChanged, refresh);
  room.on(RoomEvent.TrackMuted, refresh);
  room.on(RoomEvent.TrackUnmuted, refresh);
  room.on(RoomEvent.TrackSubscribed, (track, publication, participant) => {
    attachVoiceTrack(track, publication, participant);
    refresh();
  });
  room.on(RoomEvent.TrackUnsubscribed, (track, publication) => {
    track.detach().forEach((element) => element.remove());
    removeVoiceTrack(publication?.trackSid || track?.sid);
    refresh();
  });
  room.on(RoomEvent.LocalTrackPublished, (publication, participant) => {
    if (publication.track?.kind === "video") attachVoiceTrack(publication.track, publication, participant);
    refresh();
  });
  room.on(RoomEvent.LocalTrackUnpublished, (publication) => {
    removeVoiceTrack(publication?.trackSid);
    refresh();
  });
  room.on(RoomEvent.Disconnected, () => leaveVoiceRoom({ fromRoom: true }).catch(console.error));
  room.on(RoomEvent.MediaDevicesError, () => setVoiceStatus("Ошибка устройства: проверь доступ браузера к микрофону или экрану."));
}

async function joinVoiceRoom(roomId, invite = "") {
  if (!state.me?.authenticated) return;
  await leaveVoiceRoom({ silent: true });
  const payload = await api("/api/voice/token", {
    method: "POST",
    body: JSON.stringify({ guild_id: 0, room_id: roomId, invite }),
  });
  state.voice.room = payload.room;
  $("voiceRoomTitle").textContent = payload.room.name;
  if (!payload.configured) {
    setVoiceStatus("Голосовой сервер пока не настроен. Комната доступна, но подключение отключено.");
    await loadVoiceRooms();
    return;
  }
  if (!window.LivekitClient?.Room) throw new Error("LiveKit SDK is unavailable");
  setVoiceStatus("Подключаемся к голосовой комнате…");
  const room = new window.LivekitClient.Room({ adaptiveStream: true, dynacast: true });
  bindLiveKitRoom(room);
  try {
    await room.connect(payload.livekit_url, payload.token);
    state.voice.livekit = room;
    await room.localParticipant.setMicrophoneEnabled(true);
    state.voice.connected = true;
    state.voice.muted = false;
    state.voice.deafened = false;
    setVoiceStatus("Подключено. Микрофон и показ экрана работают внутри приватной сети.");
    if (invite) {
      const url = new URL(location.href);
      url.searchParams.delete("voice_invite");
      history.replaceState({}, "", url);
    }
  } catch (error) {
    room.disconnect();
    state.voice.livekit = null;
    setVoiceStatus(`Не удалось подключиться: ${error?.message || "ошибка голосового сервера"}`);
    throw error;
  }
  setVoiceControls(true);
  renderVoiceMembers();
  await loadVoiceRooms();
}

async function leaveVoiceRoom(options = {}) {
  const room = state.voice.livekit;
  if (room && !options.fromRoom) room.disconnect();
  $("voiceAudio").replaceChildren();
  $("voiceMedia").replaceChildren();
  $("voiceMedia").classList.add("hidden");
  $("voiceDevicePanel").classList.add("hidden");
  state.voice = {
    room: null,
    stream: null,
    muted: false,
    deafened: false,
    connected: false,
    livekit: null,
    screenSharing: false,
  };
  setVoiceControls(false);
  renderVoiceMembers();
  if (!options.silent) {
    $("voiceRoomTitle").textContent = "Голос";
    $("voiceStatus").textContent = "Выбери комнату слева, чтобы подключиться.";
    await loadVoiceRooms().catch(console.error);
  }
}

async function toggleMute() {
  if (!state.voice.connected) return;
  state.voice.muted = !state.voice.muted;
  await state.voice.livekit.localParticipant.setMicrophoneEnabled(!state.voice.muted);
  $("voiceMute").title = state.voice.muted ? "Включить микрофон" : "Выключить микрофон";
  setVoiceControls(true);
  renderVoiceMembers();
}

async function toggleDeafen() {
  if (!state.voice.connected) return;
  state.voice.deafened = !state.voice.deafened;
  if (state.voice.deafened && !state.voice.muted) {
    state.voice.muted = true;
    await state.voice.livekit.localParticipant.setMicrophoneEnabled(false);
  }
  $("voiceAudio").querySelectorAll("audio").forEach((audio) => { audio.muted = state.voice.deafened; });
  $("voiceDeafen").title = state.voice.deafened ? "Включить звук" : "Выключить звук";
  setVoiceControls(true);
  renderVoiceMembers();
}

async function toggleScreenShare() {
  if (!state.voice.connected) return;
  const enabled = !state.voice.screenSharing;
  await state.voice.livekit.localParticipant.setScreenShareEnabled(enabled, { audio: true });
  state.voice.screenSharing = enabled;
  $("voiceScreen").title = enabled ? "Остановить показ экрана" : "Показать экран";
  setVoiceControls(true);
}

async function loadVoiceDevices() {
  const devices = await navigator.mediaDevices.enumerateDevices();
  const fill = (id, kind, fallback) => {
    const select = $(id);
    select.innerHTML = devices.filter((device) => device.kind === kind).map((device, index) =>
      `<option value="${escapeHtml(device.deviceId)}">${escapeHtml(device.label || `${fallback} ${index + 1}`)}</option>`
    ).join("");
  };
  fill("voiceInputDevice", "audioinput", "Микрофон");
  fill("voiceOutputDevice", "audiooutput", "Динамики");
}

async function linkLol() {
  const riotId = $("riotId").value.trim();
  if (!riotId) return;
  $("lolStatus").textContent = "Привязываем Riot ID...";
  await api("/api/lol/link", {
    method: "POST",
    body: JSON.stringify({
      riot_id: riotId,
      platform: $("riotPlatform").value.trim() || "ru",
      regional: $("riotRegional").value.trim() || "europe",
    }),
  });
  $("lolStatus").textContent = "Riot ID привязан. Обновляем статистику...";
  await refreshLol();
}

async function refreshLol() {
  $("lolStatus").textContent = "Обновляем LoL статистику через Riot API...";
  const data = await api("/api/lol/refresh", {
    method: "POST",
    body: JSON.stringify({ matches: 20 }),
  });
  renderLol(data);
}

async function unlinkLol() {
  $("lolStatus").textContent = "Отвязываем Riot ID...";
  await api("/api/lol/unlink", { method: "POST", body: JSON.stringify({}) });
  $("riotId").value = "";
  renderLol({ account: null, model: null });
}

async function loadSettings() {
  if (!state.me?.authenticated) return;
  const guild = $("settingsGuild").value || "0";
  const [settingsData, serverData, memberData] = await Promise.all([
    api(`/api/settings?guild_id=${encodeURIComponent(guild)}`),
    api("/api/platform/server"),
    api("/api/community/members"),
  ]);
  $("settingsOutput").textContent = JSON.stringify(settingsData, null, 2);
  renderServerSettings(serverData.server);
  renderRoles(memberData.roles || [], "settingsRoleCatalog");
}

async function saveServerSettings() {
  const payload = {
    name: $("serverName").value.trim(),
    banner: $("serverBanner").value,
    icon: $("serverIcon").value.trim(),
    description: $("serverDescription").value.trim(),
  };
  $("serverSettingsStatus").textContent = "сохраняем...";
  const data = await api("/api/platform/server", {
    method: "PATCH",
    body: JSON.stringify(payload),
  });
  renderServerSettings(data.server);
  $("serverSettingsStatus").textContent = "сохранено";
  await loadPlatform().catch(console.error);
}

async function saveRoleSettings() {
  const payload = {
    slug: $("roleSlug").value.trim(),
    name: $("roleName").value.trim(),
    color: $("roleColor").value.trim() || "#9aa7b0",
    position: Number($("rolePosition").value || 0),
  };
  if (!payload.slug || !payload.name) return;
  $("roleSettingsStatus").textContent = "сохраняем...";
  const data = await api("/api/community/roles", {
    method: "POST",
    body: JSON.stringify(payload),
  });
  renderRoles(data.roles || [], "settingsRoleCatalog");
  renderRoles(data.roles || []);
  $("roleSettingsStatus").textContent = "сохранено";
  $("roleSlug").value = "";
  $("roleName").value = "";
  $("roleColor").value = "";
  $("rolePosition").value = "";
}

function escapeHtml(value) {
  return String(value ?? "").replace(/[&<>"']/g, (ch) => ({
    "&": "&amp;",
    "<": "&lt;",
    ">": "&gt;",
    '"': "&quot;",
    "'": "&#039;",
  }[ch]));
}

document.querySelectorAll(".nav").forEach((button) => {
  button.addEventListener("click", async () => {
    setView(button.dataset.view);
    if (button.dataset.view === "profile") await loadUnifiedProfile().catch(console.error);
    if (button.dataset.view === "server") await loadPlatform().catch(console.error);
    if (button.dataset.view === "members") await loadCommunity().catch(console.error);
    if (button.dataset.view === "chat") await loadChat().catch(console.error);
    if (button.dataset.view === "voice") await loadVoiceRooms().catch(console.error);
    if (button.dataset.view === "games") await loadLol().catch(console.error);
    if (button.dataset.view === "settings") await loadSettings().catch(console.error);
  });
});

window.addEventListener("beforeinstallprompt", (event) => {
  event.preventDefault();
  state.deferredInstallPrompt = event;
  $("installApp")?.classList.remove("hidden");
});

$("installApp").addEventListener("click", async () => {
  if (!state.deferredInstallPrompt) return;
  state.deferredInstallPrompt.prompt();
  await state.deferredInstallPrompt.userChoice.catch(() => {});
  state.deferredInstallPrompt = null;
  $("installApp").classList.add("hidden");
});

$("logoutLink").addEventListener("click", async () => {
  await api("/auth/logout", { method: "POST", body: "{}" });
  location.reload();
});

$("chatSearch").addEventListener("input", () => renderChat(state.messages.chat));
$("platformSearch").addEventListener("input", () => renderPlatformMessages(state.messages.platform));

$("chatForm").addEventListener("submit", async (event) => {
  event.preventDefault();
  const text = $("chatText").value.trim();
  const attachments = state.attachments.chat;
  if (!text && !attachments.length) return;
  $("chatStatus").textContent = "";
  try {
    await api("/api/chat", {
      method: "POST",
      body: JSON.stringify({ content: text, attachments }),
    });
    $("chatText").value = "";
    state.attachments.chat = [];
    renderAttachmentTray("chat");
    await loadChat();
  } catch (error) {
    console.error(error);
    $("chatStatus").textContent = chatErrorText(error);
  }
});

$("chatMessages").addEventListener("click", (event) => {
  handleMessageAction(event).catch(console.error);
});

$("chatAttachButton").addEventListener("click", () => $("chatFileInput").click());
$("chatFileInput").addEventListener("change", async (event) => {
  await handleFilePick("chat", event.target.files).catch(console.error);
  event.target.value = "";
});
$("chatAttachmentTray").addEventListener("click", removePendingAttachment);

$("settingsForm").addEventListener("submit", async (event) => {
  event.preventDefault();
  const guild = Number($("settingsGuild").value || 0);
  const feature = $("settingsFeature").value.trim();
  const mode = $("settingsMode").value;
  const channel = Number($("settingsChannel").value || 0);
  if (!feature || !channel) return;
  await api(`/api/guilds/${guild}/features/${encodeURIComponent(feature)}/channels/${mode}/${channel}`, {
    method: "PUT",
    body: JSON.stringify({ reason: "web panel" }),
  });
  await loadSettings();
});

$("serverSettingsForm").addEventListener("submit", async (event) => {
  event.preventDefault();
  await saveServerSettings().catch((error) => {
    console.error(error);
    $("serverSettingsStatus").textContent = "ошибка сохранения";
  });
});

$("roleSettingsForm").addEventListener("submit", async (event) => {
  event.preventDefault();
  await saveRoleSettings().catch((error) => {
    console.error(error);
    $("roleSettingsStatus").textContent = "ошибка сохранения";
  });
});

$("platformMessageForm").addEventListener("submit", async (event) => {
  event.preventDefault();
  $("platformChatStatus").textContent = "";
  await sendPlatformMessage().catch((error) => {
    console.error(error);
    $("platformChatStatus").textContent = chatErrorText(error);
  });
});

$("platformMessages").addEventListener("click", (event) => {
  handleMessageAction(event).catch(console.error);
});

$("platformAttachButton").addEventListener("click", () => $("platformFileInput").click());
$("platformFileInput").addEventListener("change", async (event) => {
  await handleFilePick("platform", event.target.files).catch(console.error);
  event.target.value = "";
});
$("platformAttachmentTray").addEventListener("click", removePendingAttachment);

$("channelCreateForm").addEventListener("submit", async (event) => {
  event.preventDefault();
  await createPlatformChannel().catch(console.error);
});

$("dmCreateForm").addEventListener("submit", async (event) => {
  event.preventDefault();
  $("platformChatStatus").textContent = "";
  await createPlatformDm().catch((error) => {
    console.error(error);
    $("platformChatStatus").textContent = chatErrorText(error);
  });
});

$("communityProfileForm").addEventListener("submit", async (event) => {
  event.preventDefault();
  await saveCommunityProfile().catch((error) => {
    console.error(error);
    $("communityStatusLine").textContent = "Не удалось сохранить косметику.";
  });
});

$("unifiedProfileForm").addEventListener("submit", async (event) => {
  event.preventDefault();
  await saveUnifiedProfile().catch((error) => {
    console.error(error);
    $("unifiedProfileMessage").textContent = "Не удалось сохранить профиль. Проверь дату и обязательные поля.";
  });
});

$("reloadProfile").addEventListener("click", () => loadUnifiedProfile().catch(console.error));
$("unifiedAiForget").addEventListener("click", () => forgetAiProfile().catch(console.error));
document.querySelectorAll(".profile-jump").forEach((button) => {
  button.addEventListener("click", async () => {
    setView(button.dataset.targetView);
    if (button.dataset.targetView === "games") await loadLol().catch(console.error);
  });
});

$("reloadMembers").addEventListener("click", () => loadCommunity().catch(console.error));

$("localLoginForm").addEventListener("submit", async (event) => {
  event.preventDefault();
  await localLogin().catch((error) => {
    console.error(error);
    $("loginProfileStatus").textContent = "Не удалось войти по email и паролю.";
  });
});

$("discordCodeForm").addEventListener("submit", async (event) => {
  event.preventDefault();
  await discordCodeLogin().catch((error) => {
    console.error(error);
    $("loginProfileStatus").textContent = "Код неверный, просрочен или уже использован.";
  });
});

$("loginProfileForm").addEventListener("submit", async (event) => {
  event.preventDefault();
  await saveLoginProfile().catch((error) => {
    console.error(error);
    $("loginProfileStatus").textContent = "Не удалось сохранить резервный вход. Пароль минимум 6 символов.";
  });
});

$("voiceRoomForm").addEventListener("submit", async (event) => {
  event.preventDefault();
  const name = $("voiceRoomName").value.trim();
  if (!name) return;
  await createVoiceRoom(name, $("voiceRoomPrivate").checked).catch(console.error);
  $("voiceRoomName").value = "";
  $("voiceRoomPrivate").checked = false;
});

$("voiceMute").addEventListener("click", () => toggleMute().catch(console.error));
$("voiceDeafen").addEventListener("click", () => toggleDeafen().catch(console.error));
$("voiceScreen").addEventListener("click", () => toggleScreenShare().catch((error) => {
  setVoiceStatus(`Показ экрана не запущен: ${error?.message || "доступ отменён"}`);
}));
$("voiceLeave").addEventListener("click", () => leaveVoiceRoom().catch(console.error));
$("voiceSettings").addEventListener("click", async () => {
  await loadVoiceDevices();
  $("voiceDevicePanel").classList.toggle("hidden");
});
$("voiceInvite").addEventListener("click", async () => {
  if (!state.voice.room) return;
  const url = new URL(location.origin);
  url.searchParams.set("voice", state.voice.room.id);
  if (state.voice.room.is_private) {
    const data = await api("/api/voice/invite", {
      method: "POST",
      body: JSON.stringify({ guild_id: 0, room_id: state.voice.room.id }),
    });
    url.searchParams.set("voice_invite", data.invite);
  }
  await navigator.clipboard?.writeText(url.toString()).catch(() => {});
  setVoiceStatus(state.voice.room.is_private
    ? "Приватная ссылка на 10 входов скопирована; она действует 24 часа."
    : "Ссылка на голосовой вход скопирована.");
});
$("voiceInputDevice").addEventListener("change", async (event) => {
  if (!state.voice.connected) return;
  await state.voice.livekit.localParticipant.setMicrophoneEnabled(false);
  await state.voice.livekit.localParticipant.setMicrophoneEnabled(true, { deviceId: event.target.value });
  state.voice.muted = false;
  setVoiceControls(true);
});
$("voiceOutputDevice").addEventListener("change", async (event) => {
  if (!state.voice.connected) return;
  if (state.voice.livekit.switchActiveDevice) {
    await state.voice.livekit.switchActiveDevice("audiooutput", event.target.value);
  }
});

$("lolLinkForm").addEventListener("submit", async (event) => {
  event.preventDefault();
  await linkLol().catch((error) => {
    console.error(error);
    $("lolStatus").textContent = "Не удалось привязать Riot ID. Проверь формат Name#TAG и RIOT_API_KEY.";
  });
});

$("refreshLol").addEventListener("click", () => refreshLol().catch((error) => {
  console.error(error);
  $("lolStatus").textContent = "Не удалось обновить LoL статистику. Проверь RIOT_API_KEY и привязку.";
}));

$("unlinkLol").addEventListener("click", () => unlinkLol().catch((error) => {
  console.error(error);
  $("lolStatus").textContent = "Не удалось отвязать Riot ID.";
}));

loadMe()
  .then(() => Promise.allSettled([loadPlatform(), loadCommunity(), loadChat(), loadVoiceRooms(), loadLol()]))
  .then(() => applyInitialView())
  .then(() => applyInitialVoiceInvite())
  .catch((error) => {
    console.error(error);
    $("authState").textContent = "Не удалось загрузить состояние сайта.";
  });

setInterval(() => {
  if (document.querySelector("#chat.view.active")) loadChat().catch(console.error);
}, 5000);
