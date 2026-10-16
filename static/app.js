const AUTOMATIC_FALLBACK_LANGS = [
  "es",
  "en",
  "ja",
  "pt",
  "fr",
  "de",
  "it",
  "ko",
  "ru",
  "zh-Hans",
];

const state = {
  audioSource: "original",
  subtitleType: "none",
  externalSubMode: "url",
  uploadedSrt: null,
  uploadedSrtName: null,
  mainInfo: null,
  logCursor: 0,
  currentDir: "",
};

const $ = (id) => document.getElementById(id);

function localLog(message, level = "info") {
  addLog({
    time: new Date().toLocaleTimeString(),
    level,
    message,
  });
}

async function api(url, options = {}) {
  const opts = { ...options };

  if (opts.body && !(opts.body instanceof FormData)) {
    opts.headers = {
      "Content-Type": "application/json",
      ...(opts.headers || {}),
    };
  }

  const res = await fetch(url, opts);

  let data = {};
  try {
    data = await res.json();
  } catch {
    // ignorar
  }

  if (!res.ok) {
    throw new Error(data.detail || data.error || `HTTP ${res.status}`);
  }

  return data;
}

// ============================================================
// Logs (ahora vía WebSocket, sin polling)
// ============================================================

const seenLogIds = new Set();

function addLog(entry) {
  const log = $("log");

  const nearBottom =
    log.scrollHeight - log.scrollTop - log.clientHeight < 80;

  const line = document.createElement("div");
  line.className = `log-line ${entry.level || "info"}`;
  line.textContent = `[${entry.time}] ${entry.message}`;

  log.appendChild(line);

  // Limitar nodos en DOM para no crecer indefinidamente
  while (log.children.length > 3000) {
    log.removeChild(log.firstChild);
  }

  if (nearBottom) {
    log.scrollTop = log.scrollHeight;
  }
}

function addLogDedup(entry) {
  if (entry && typeof entry.id === "number") {
    if (seenLogIds.has(entry.id)) return;
    seenLogIds.add(entry.id);
  }
  addLog(entry);
}

// ============================================================
// WebSocket: logs + estado + progreso en tiempo real
// ============================================================

let ws = null;
let wsReconnectAttempts = 0;
let wsReconnectTimer = null;
let wsPingTimer = null;
let wsManuallyClosed = false;

function wsUrl() {
  const proto = location.protocol === "https:" ? "wss:" : "ws:";
  return `${proto}//${location.host}/ws/logs`;
}

function setWsIndicator(connected) {
  const wrap = $("progressWrap");
  if (!wrap) return;
  wrap.dataset.ws = connected ? "online" : "offline";
}

function connectWebSocket() {
  if (ws && (ws.readyState === WebSocket.OPEN || ws.readyState === WebSocket.CONNECTING)) {
    return;
  }

  try {
    ws = new WebSocket(wsUrl());
  } catch (e) {
    scheduleWsReconnect();
    return;
  }

  ws.addEventListener("open", () => {
    wsReconnectAttempts = 0;
    setWsIndicator(true);
    localLog("Conexión en tiempo real establecida", "success");

    // Keep-alive para evitar cortes por inactividad
    clearInterval(wsPingTimer);
    wsPingTimer = setInterval(() => {
      if (ws && ws.readyState === WebSocket.OPEN) {
        try {
          ws.send("ping");
        } catch {
          // ignorar
        }
      }
    }, 25000);
  });

  ws.addEventListener("message", (event) => {
    let payload;
    try {
      payload = JSON.parse(event.data);
    } catch {
      return; // mensaje no-JSON: ignorar
    }
    handleWsEvent(payload);
  });

  ws.addEventListener("close", () => {
    setWsIndicator(false);
    clearInterval(wsPingTimer);
    wsPingTimer = null;

    if (!wsManuallyClosed) {
      localLog("Conexión en tiempo real perdida. Reintentando...", "warning");
      scheduleWsReconnect();
    }
  });

  ws.addEventListener("error", () => {
    // 'close' se disparará después; aquí solo marcamos el indicador.
    setWsIndicator(false);
  });
}

function scheduleWsReconnect() {
  if (wsReconnectTimer || wsManuallyClosed) return;

  wsReconnectAttempts += 1;
  const delay = Math.min(30000, 1000 * Math.pow(2, Math.min(wsReconnectAttempts, 5)));

  wsReconnectTimer = setTimeout(() => {
    wsReconnectTimer = null;
    connectWebSocket();
  }, delay);
}

function handleWsEvent(payload) {
  const type = payload.type;
  const data = payload.data || {};

  switch (type) {
    case "history": {
      // Historial al conectar: reconstruir el registro y fijar cursor
      const log = $("log");
      if (data.logs && data.logs.length && log.children.length === 0) {
        data.logs.forEach(addLogDedup);
      }
      if (typeof data.next === "number") {
        state.logCursor = data.next;
      }
      break;
    }

    case "log":
      addLogDedup(data);
      break;

    case "status":
      applyBusyState(Boolean(data.busy));
      break;

    case "stage":
      showProgressBar(stageLabel(data.stage));
      updateProgressText(stageLabel(data.stage));
      break;

    case "progress":
      onProgressEvent(data);
      break;

    case "progress_done":
      onProgressDone(data);
      break;

    case "pong":
    default:
      break;
  }
}

function stageLabel(stage) {
  switch (stage) {
    case "video":
      return "Descargando video...";
    case "audio":
      return "Descargando audio...";
    case "merge":
      return "Combinando con FFmpeg...";
    default:
      return "Procesando...";
  }
}

// ============================================================
// Barra de progreso
// ============================================================

let currentStage = "";

function showProgressBar(label) {
  currentStage = label || currentStage;
  const wrap = $("progressWrap");
  const bar = $("downloadProgress");

  wrap.classList.remove("hidden");
  bar.removeAttribute("indeterminate");
  bar.max = 100;
  bar.value = 0;
  updateProgressText(currentStage || "Iniciando...");
}

function hideProgressBar() {
  const wrap = $("progressWrap");
  const bar = $("downloadProgress");

  wrap.classList.add("hidden");
  bar.value = 0;
  bar.removeAttribute("indeterminate");
  updateProgressText("0%");
}

function updateProgressText(text) {
  $("progressText").textContent = text;
}

function onProgressEvent(data) {
  const wrap = $("progressWrap");
  const bar = $("downloadProgress");

  if (wrap.classList.contains("hidden")) {
    wrap.classList.remove("hidden");
  }

  const percent = data.percent;

  if (percent === null || percent === undefined) {
    // Progreso indeterminado (p. ej. merge FFmpeg)
    bar.setAttribute("indeterminate", "");
    bar.removeAttribute("value");
    updateProgressText(data.eta || "Procesando...");
    return;
  }

  bar.removeAttribute("indeterminate");
  bar.max = 100;
  bar.value = Math.min(100, Math.max(0, percent));

  const parts = [`${percent.toFixed(1)}%`];
  if (data.size) parts.push(String(data.size));
  if (data.speed) parts.push(`${data.speed}/s`);
  if (data.eta) parts.push(`ETA ${data.eta}`);
  if (currentStage) parts.unshift(currentStage);

  updateProgressText(parts.join(" · "));
}

function onProgressDone(data) {
  const bar = $("downloadProgress");

  if (data.ok) {
    bar.removeAttribute("indeterminate");
    bar.max = 100;
    bar.value = 100;
    updateProgressText("¡Completado!");
    setTimeout(hideProgressBar, 2500);
  } else {
    hideProgressBar();
  }

  currentStage = "";
}

// ============================================================
// Estado (busy) — ahora impulsado por WebSocket; API como fallback
// ============================================================

function applyBusyState(busy) {
  document.body.classList.toggle("busy", busy);

  $("downloadBtn").disabled = busy;
  $("downloadBtn").textContent = busy ? "Procesando..." : "Iniciar Descarga";
  $("stopBtn").disabled = !busy;

  if (!busy) {
    // Seguridad: si por cualquier motivo perdimos el evento progress_done,
    // aseguramos que la barra no quede colgada al terminar el trabajo.
    setTimeout(() => {
      fetch("/api/status")
        .then((r) => r.json())
        .then((d) => {
          if (!d.busy && !$("progressWrap").classList.contains("hidden")) {
            const value = Number($("downloadProgress").value);
            if (!(value >= 100)) hideProgressBar();
          }
        })
        .catch(() => {});
    }, 1000);
  }
}

async function refreshStatus() {
  // Fallback puntual (se usa al cargar y cuando el WS está caído).
  try {
    const data = await api("/api/status");
    applyBusyState(data.busy);
  } catch {
    // silencioso
  }
}

// ============================================================
// Utilidades UI
// ============================================================

function clearSelect(select, placeholder) {
  select.innerHTML = "";

  const option = document.createElement("option");
  option.value = "";
  option.textContent = placeholder;
  option.disabled = true;
  option.selected = true;

  select.appendChild(option);
  select.disabled = true;
}

function fillAudioSelect(select, tracks) {
  clearSelect(
    select,
    tracks && tracks.length ? "Selecciona pista" : "-Sin pistas-"
  );

  if (!tracks || !tracks.length) {
    return;
  }

  tracks.forEach((track) => {
    const option = document.createElement("option");
    option.value = track.value;
    option.textContent = track.label;
    select.appendChild(option);
  });

  select.disabled = false;
  select.selectedIndex = 1;
}

function fillQualitySelect(select, tracks) {
  // Igual que fillAudioSelect pero con "Mejor calidad" por defecto
  fillAudioSelect(select, tracks);

  if (select.disabled) {
    return;
  }

  // Seleccionar "__best__" si existe (viene de serie en la posición 1)
  for (let i = 0; i < select.options.length; i += 1) {
    if (select.options[i].value === "__best__") {
      select.selectedIndex = i;
      break;
    }
  }
}

function fillLangSelect(select, langs, includeAll = true) {
  clearSelect(
    select,
    langs && langs.length ? "Selecciona idioma" : "-No disponibles-"
  );

  if (!langs || !langs.length) {
    return;
  }

  if (includeAll && langs.length > 1) {
    const all = document.createElement("option");
    all.value = "__all__";
    all.textContent = "[Insertar Todos]";
    select.appendChild(all);
  }

  langs.forEach((lang) => {
    const option = document.createElement("option");
    option.value = lang;
    option.textContent = lang;
    select.appendChild(option);
  });

  select.disabled = false;
  select.selectedIndex = 1;
}

// ============================================================
// Info principal
// ============================================================

async function fetchMainInfo() {
  const url = $("videoUrl").value.trim();

  if (!url) {
    alert("Escribe la URL del video principal");
    return;
  }

  try {
    const data = await api("/api/info", {
      method: "POST",
      body: JSON.stringify({
        url,
        kind: "main",
      }),
    });

    state.mainInfo = data;

    $("videoTitle").textContent = data.title
      ? `Título: ${data.title}`
      : "";

    fillAudioSelect($("originalAudioSelect"), data.audio_tracks);
    fillQualitySelect($("videoQualitySelect"), data.video_tracks);
    updateSubtitleLangSelect();

    localLog(`Información obtenida: ${data.title || url}`, "success");
  } catch (e) {
    alert(e.message);
  }
}

// ============================================================
// Audio externo
// ============================================================

async function fetchAudioInfo() {
  const url = $("audioUrl").value.trim();

  if (!url) {
    alert("Escribe la URL del video para audio externo");
    return;
  }

  try {
    const data = await api("/api/info", {
      method: "POST",
      body: JSON.stringify({
        url,
        kind: "audio",
      }),
    });

    fillAudioSelect($("externalAudioSelect"), data.audio_tracks);
    localLog("Pistas de audio externo obtenidas", "success");
  } catch (e) {
    alert(e.message);
  }
}

// ============================================================
// Subtítulos externos por URL
// ============================================================

async function fetchSubInfo() {
  const url = $("subtitleUrl").value.trim();

  if (!url) {
    alert("Escribe la URL para buscar subtítulos externos");
    return;
  }

  try {
    const data = await api("/api/info", {
      method: "POST",
      body: JSON.stringify({
        url,
        kind: "subs",
      }),
    });

    fillLangSelect($("externalSubLangSelect"), data.subtitles, true);
    localLog("Subtítulos externos obtenidos", "success");
  } catch (e) {
    alert(e.message);
  }
}

// ============================================================
// Actualización de selects de subtítulos
// ============================================================

function updateSubtitleLangSelect() {
  const select = $("subtitleLangSelect");

  if (!state.mainInfo) {
    clearSelect(select, "-Primero Obtener Info-");
    return;
  }

  let langs = [];

  if (state.subtitleType === "internal") {
    langs = state.mainInfo.subtitles || [];
  }

  if (state.subtitleType === "automatic") {
    // Lista REAL de idiomas automáticos devuelta por /api/info
    // (yt-dlp: clave "automatic_captions"). La lista estática solo se
    // usa como último recurso si la API aún no devolvió datos.
    const apiLangs = state.mainInfo.automatic_captions || [];

    langs = apiLangs.length ? apiLangs : AUTOMATIC_FALLBACK_LANGS;

    if (!apiLangs.length) {
      localLog(
        "El video no reporta subtítulos automáticos; usando lista genérica",
        "warning",
      );
    }
  }

  fillLangSelect(select, langs, true);
}

// ============================================================
// Audio source
// ============================================================

function setAudioSource(source) {
  state.audioSource = source;

  $("audioOriginalBtn").classList.toggle("active", source === "original");
  $("audioExternalBtn").classList.toggle("active", source === "external");

  $("audioOriginalPanel").classList.toggle("hidden", source !== "original");
  $("audioExternalPanel").classList.toggle("hidden", source !== "external");
}

// ============================================================
// Subtitle type
// ============================================================

function setSubtitleType(type) {
  state.subtitleType = type;

  document.querySelectorAll(".sub").forEach((btn) => {
    btn.classList.toggle("active", btn.dataset.sub === type);
  });

  $("subtitleOptions").classList.toggle("hidden", type === "none");

  $("internalAutomaticPanel").classList.toggle(
    "hidden",
    !["internal", "automatic"].includes(type)
  );

  $("externalPanel").classList.toggle("hidden", type !== "external");

  if (["internal", "automatic"].includes(type)) {
    updateSubtitleLangSelect();
  }
}

// ============================================================
// External subtitle mode
// ============================================================

function setExternalSubMode(mode) {
  state.externalSubMode = mode;

  $("externalSubUrlTab").classList.toggle("active", mode === "url");
  $("externalSubFileTab").classList.toggle("active", mode === "file");

  $("externalSubUrlPanel").classList.toggle("hidden", mode !== "url");
  $("externalSubFilePanel").classList.toggle("hidden", mode !== "file");
}

// ============================================================
// Subida de SRT
// ============================================================

async function uploadSrt(file) {
  const formData = new FormData();
  formData.append("file", file);

  try {
    const data = await api("/api/upload-srt", {
      method: "POST",
      body: formData,
    });

    state.uploadedSrt = data.filename;
    state.uploadedSrtName = data.original_name;

    $("srtFileName").textContent =
      `Archivo seleccionado: ${data.original_name}`;

    localLog(`SRT subido: ${data.original_name}`, "success");
  } catch (e) {
    alert(e.message);
  }
}

// ============================================================
// Descarga
// ============================================================

async function startDownload() {
  const audioSelect =
    state.audioSource === "original"
      ? $("originalAudioSelect")
      : $("externalAudioSelect");

  const qualitySelect = $("videoQualitySelect");

  const payload = {
    video_url: $("videoUrl").value.trim(),
    video_title: state.mainInfo?.title || null,
    output_dir: $("outputDir").value.trim(),

    video_format_id: qualitySelect.value || null,

    audio_source: state.audioSource,
    audio_url:
      state.audioSource === "external"
        ? $("audioUrl").value.trim()
        : null,
    audio_format_id: audioSelect.value || null,

    subtitle_type: state.subtitleType,

    burn_subtitles: $("burnSubs").checked,
    embed_subtitles: $("embedSubs").checked,
    keep_srt: $("keepSrt").checked,
  };

  if (!payload.video_url) {
    alert("Escribe la URL del video principal");
    return;
  }

  if (!payload.output_dir) {
    alert("Escribe o selecciona el directorio de salida");
    return;
  }

  if (!payload.audio_format_id) {
    alert("Selecciona una pista de audio");
    return;
  }

  if (state.subtitleType !== "none") {
    if (!payload.burn_subtitles && !payload.embed_subtitles) {
      payload.burn_subtitles = true;
    }

    if (state.subtitleType === "internal" || state.subtitleType === "automatic") {
      payload.subtitle_lang = $("subtitleLangSelect").value;

      if (!payload.subtitle_lang) {
        alert("Selecciona un idioma de subtítulos");
        return;
      }
    }

    if (state.subtitleType === "external") {
      payload.external_sub_source = state.externalSubMode;

      if (state.externalSubMode === "url") {
        payload.subtitle_url = $("subtitleUrl").value.trim();
        payload.subtitle_lang = $("externalSubLangSelect").value;

        if (!payload.subtitle_url) {
          alert("Escribe la URL de subtítulos externos");
          return;
        }

        if (!payload.subtitle_lang) {
          alert("Selecciona un idioma de subtítulos externos");
          return;
        }
      } else {
        payload.uploaded_srt = state.uploadedSrt;
        payload.uploaded_srt_original_name = state.uploadedSrtName;

        if (!payload.uploaded_srt) {
          alert("Selecciona un archivo .srt");
          return;
        }
      }
    }
  }

  try {
    await api("/api/download", {
      method: "POST",
      body: JSON.stringify(payload),
    });

    // Mostrar la barra de inmediato (el backend enviará eventos
    // "stage"/"progress" por WebSocket conforme avance el trabajo).
    showProgressBar("Iniciando descarga...");

    localLog("Descarga iniciada desde la interfaz web", "success");
  } catch (e) {
    hideProgressBar();
    alert(e.message);
  }
}

// ============================================================
// Explorador de carpetas
// ============================================================

async function openDirModal() {
  const current = $("outputDir").value.trim();
  await loadDirectory(current || "");
  $("dirModal").classList.remove("hidden");
}

async function loadDirectory(path) {
  try {
    const query = path ? `?path=${encodeURIComponent(path)}` : "";
    const data = await api(`/api/fs${query}`);

    state.currentDir = data.current;
    $("currentDir").textContent = data.current;

    const list = $("dirList");
    list.innerHTML = "";

    data.dirs.forEach((dir) => {
      const item = document.createElement("li");
      item.textContent = dir.name;
      item.title = dir.path;

      item.addEventListener("click", () => {
        loadDirectory(dir.path);
      });

      list.appendChild(item);
    });

    $("dirParent").disabled = !data.parent;

    $("dirParent").onclick = () => {
      if (data.parent) {
        loadDirectory(data.parent);
      }
    };

    $("dirHome").onclick = () => {
      loadDirectory(data.home);
    };
  } catch (e) {
    alert(e.message);
  }
}

function closeDirModal() {
  $("dirModal").classList.add("hidden");
}

// ============================================================
// Cookies
// ============================================================

function updateCookiesPanels() {
  const mode = $("cookiesMode").value;

  $("cookiesBrowserPanel").classList.toggle("hidden", mode !== "browser");
  $("cookiesFilePanel").classList.toggle("hidden", mode !== "file");
}

function updateCookiesFileStatus(exists) {
  $("cookiesFileStatus").textContent = exists
    ? "cookies.txt presente en el servidor."
    : "Todavía no hay cookies.txt subido.";
}

async function loadCookiesConfig() {
  try {
    const data = await api("/api/cookies");

    $("cookiesMode").value = data.mode || "none";
    $("cookiesBrowser").value = data.browser || "firefox";
    $("cookiesProfile").value = data.profile || "";

    updateCookiesPanels();
    updateCookiesFileStatus(data.file_exists);
  } catch {
    // silencioso
  }
}

async function saveCookiesConfig() {
  try {
    const data = await api("/api/cookies", {
      method: "POST",
      body: JSON.stringify({
        mode: $("cookiesMode").value,
        browser: $("cookiesBrowser").value,
        profile: $("cookiesProfile").value.trim() || null,
      }),
    });

    localLog(
      `Cookies configuradas. yt-dlp usará: ${data.args.join(" ") || "(nada)"}`,
      "success",
    );
  } catch (e) {
    alert(e.message);
  }
}

async function uploadCookiesFile(file) {
  const formData = new FormData();
  formData.append("file", file);

  try {
    await api("/api/upload-cookies", {
      method: "POST",
      body: formData,
    });

    localLog("cookies.txt subido y activado automáticamente", "success");

    const data = await api("/api/cookies");
    $("cookiesMode").value = data.mode;
    updateCookiesPanels();
    updateCookiesFileStatus(data.file_exists);
  } catch (e) {
    alert(e.message);
  }
}

// ============================================================
// Inicialización
// ============================================================

async function init() {
  $("fetchMainInfo").addEventListener("click", fetchMainInfo);
  $("fetchAudioInfo").addEventListener("click", fetchAudioInfo);
  $("fetchSubInfo").addEventListener("click", fetchSubInfo);

  $("cookiesMode").addEventListener("change", () => {
    updateCookiesPanels();
    saveCookiesConfig();
  });

  $("cookiesBrowser").addEventListener("change", saveCookiesConfig);
  $("cookiesProfile").addEventListener("change", saveCookiesConfig);

  $("cookiesFile").addEventListener("change", (e) => {
    if (e.target.files && e.target.files[0]) {
      uploadCookiesFile(e.target.files[0]);
    }
  });

  await loadCookiesConfig();

  $("audioOriginalBtn").addEventListener("click", () => {
    setAudioSource("original");
  });

  $("audioExternalBtn").addEventListener("click", () => {
    setAudioSource("external");
  });

  document.querySelectorAll(".sub").forEach((btn) => {
    btn.addEventListener("click", () => {
      setSubtitleType(btn.dataset.sub);
    });
  });

  $("externalSubUrlTab").addEventListener("click", () => {
    setExternalSubMode("url");
  });

  $("externalSubFileTab").addEventListener("click", () => {
    setExternalSubMode("file");
  });

  $("srtFile").addEventListener("change", (e) => {
    if (e.target.files && e.target.files[0]) {
      uploadSrt(e.target.files[0]);
    }
  });

  $("downloadBtn").addEventListener("click", startDownload);

  $("stopBtn").addEventListener("click", async () => {
    try {
      await api("/api/stop", { method: "POST" });
      localLog("Solicitada detención del proceso", "warning");
    } catch (e) {
      alert(e.message);
    }
  });

  $("browseDir").addEventListener("click", openDirModal);
  $("closeDirModal").addEventListener("click", closeDirModal);

  $("selectDirBtn").addEventListener("click", () => {
    $("outputDir").value = state.currentDir;
    closeDirModal();
  });

  $("dirModal").addEventListener("click", (e) => {
    if (e.target.id === "dirModal") {
      closeDirModal();
    }
  });

  try {
    const health = await api("/api/health");

    if (!health.dependencies?.ok) {
      const missing = health.dependencies.missing.join(", ");
      localLog(`Faltan dependencias: ${missing}`, "error");
      alert(`Faltan dependencias: ${missing}`);
    }
  } catch {
    // ignorar
  }

  try {
    const config = await api("/api/config");

    if (config.last_output_dir) {
      $("outputDir").value = config.last_output_dir;
    }
  } catch {
    // ignorar
  }

  setAudioSource("original");
  setSubtitleType("none");
  setExternalSubMode("url");

  // Estado inicial una sola vez vía REST (el resto llega por WebSocket)
  refreshStatus();

  // Conexión WebSocket persistente: logs, estado y progreso en tiempo real.
  // Reemplaza al antiguo setInterval(refreshLogs, 1000).
  connectWebSocket();

  // Respaldo ligero de estado SOLO si el socket está caído,
  // para que los botones no queden bloqueados indefinidamente.
  setInterval(() => {
    const wsOpen = ws && ws.readyState === WebSocket.OPEN;
    const uiBusy = document.body.classList.contains("busy");

    if (!wsOpen || uiBusy) {
      refreshStatus();
    }
  }, 5000);

  window.addEventListener("beforeunload", () => {
    wsManuallyClosed = true;
    if (ws) {
      try {
        ws.close();
      } catch {
        // ignorar
      }
    }
  });
}

document.addEventListener("DOMContentLoaded", init);
