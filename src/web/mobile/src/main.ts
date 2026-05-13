import './style.css'
import { ImpactStyle } from '@capacitor/haptics';

// --- State ---
let activePC: any = null;
let devices: any[] = [];
let authToken = localStorage.getItem("gesturelink_token");
let hapticsEnabled = localStorage.getItem("gesturelink_haptics") !== "false";

function getHubBaseUrl(): string {
  const params = new URLSearchParams(window.location.search);
  const hubParam = params.get("hub") || localStorage.getItem("gesturelink_hub_url");
  if (hubParam) {
    const normalized = hubParam.startsWith("http://") || hubParam.startsWith("https://")
      ? hubParam
      : `https://${hubParam}`;
    localStorage.setItem("gesturelink_hub_url", normalized);
    return normalized.replace(/\/$/, "");
  }
  return window.location.origin.replace(/\/$/, "");
}

const HUB_BASE_URL = getHubBaseUrl();
const HUB_HOSTNAME = new URL(HUB_BASE_URL).hostname;

function isHubSelfTarget(target?: string | null, hostname?: string | null): boolean {
  if (hostname && /^hub\b/i.test(String(hostname).trim())) return true;
  if (!target) return true;
  const normalized = String(target).trim().toLowerCase();
  if (!normalized) return true;
  if (normalized === HUB_HOSTNAME.toLowerCase() || normalized === "localhost" || normalized === "127.0.0.1") return true;
  // Tunnel hostnames rotate. Treat any tunnel hostname as self when this app is already connected via a tunnel Hub URL.
  if (normalized.includes("trycloudflare.com") && HUB_HOSTNAME.toLowerCase().includes("trycloudflare.com")) return true;
  return false;
}

function hubApi(path: string): string {
  return `${HUB_BASE_URL}${path.startsWith("/") ? path : `/${path}`}`;
}

// WebRTC State
let peerConn: RTCPeerConnection | null = null;
let dataChannel: RTCDataChannel | null = null;
let myPeerId = Math.random().toString(36).substring(7);

// DOM Elements
const pairingOverlay = document.getElementById("pairingOverlay")!;
const pinInputs = document.querySelectorAll<HTMLInputElement>(".pin-box");
const pairBtn = document.getElementById("pairBtn")!;
const pairError = document.getElementById("pairError")!;
const pairStatusText = document.getElementById("pairStatusText")!;
const connBadge = document.getElementById("connBadge")!;
const activeDeviceName = document.getElementById("activeDeviceName")!;
const activeDeviceIP = document.getElementById("activeDeviceIP")!;
const remoteGestureStatus = document.getElementById("remoteGestureStatus")!;
const touchZone = document.getElementById("touchZone")!;
const deviceList = document.getElementById("deviceList")!;
const navItems = document.querySelectorAll(".nav-item");
const tabContents = document.querySelectorAll(".tab-content");
const appModal = document.getElementById("appModal")!;
const appSelect = document.getElementById("appSelect") as HTMLSelectElement;
const customTarget = document.getElementById("customTarget") as HTMLInputElement;
const saveAppShortcut = document.getElementById("saveAppShortcut")!;
const closeAppModal = document.getElementById("closeAppModal")!;
const keyboardInput = document.getElementById("keyboardInput") as HTMLInputElement;
const copyBtn = document.getElementById("copyBtn")!;
const pasteBtn = document.getElementById("pasteBtn")!;
const kbBtn = document.getElementById("kbBtn")!;

// --- Initialization ---
async function init() {
  setupNav();
  setupTouchpad();
  setupPinInputs();
  setupShortcuts();
  setupKeyboardToolbar();

  closeAppModal.onclick = () => {
    appModal.style.display = 'none';
  };

  // Auto-pairing from QR: Always prioritize auto-pin if provided
  const urlParams = new URLSearchParams(globalThis.location.search);
  const autoPin = urlParams.get('pin');
  if (autoPin?.length === 6) {
    await autoPair(autoPin);
    authToken = localStorage.getItem("gesturelink_token");
  }

  // Validate stored token against server — catches stale tokens after server restart
  if (authToken && authToken !== "undefined") {
    try {
      const vRes = await fetch(hubApi("/api/validate-token"), {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ token: authToken })
      });
      const vData = await vRes.json();
      if (vData.valid) {
        pairingOverlay.classList.add('hidden');
        setTimeout(() => pairingOverlay.style.display = 'none', 500);
        startApp();
      } else {
        // Token expired/server restarted — force re-pair cleanly
        localStorage.removeItem("gesturelink_token");
        authToken = null;
        pairingOverlay.style.display = 'flex';
      }
    } catch (_) {
      // Server unreachable — still show overlay
      pairingOverlay.style.display = 'flex';
    }
  } else {
    pairingOverlay.style.display = 'flex';
  }

  // Haptic toggle
  document.getElementById("hapticToggle")?.addEventListener('change', (e: any) => {
    hapticsEnabled = e.target.checked;
    localStorage.setItem("gesturelink_haptics", hapticsEnabled.toString());
    if (hapticsEnabled) triggerHaptic();
  });

  // Camera toggle
  const pcCameraToggle = document.getElementById("pcCameraToggle") as HTMLInputElement;
  const hubVideoContainer = document.getElementById("hubVideoContainer")!;
  const hubVideoPlayer = document.getElementById("hubVideoPlayer") as HTMLVideoElement;

  pcCameraToggle?.addEventListener('change', async (e: any) => {
    if (!activePC) {
      alert("Connect to a PC first!");
      pcCameraToggle.checked = false;
      return;
    }
    try {
      const active = e.target.checked;
      // Don't pass target when connected via tunnel - Hub will control itself
      const targetParam = isHubSelfTarget(activePC?.ip, activePC?.hostname)
        ? ""
        : `?target=${encodeURIComponent(activePC.ip)}`;
      const res = await fetch(hubApi(`/api/hub/camera/toggle${targetParam}`), {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ active })
      });
      const data = await res.json();
      if (!data.ok) throw new Error(data.error);
      
      if (active) {
        hubVideoContainer.style.display = 'block';
        setupHubWebRTC();
      } else {
        hubVideoContainer.style.display = 'none';
        closeWebRTC();
      }

      if (remoteGestureStatus) remoteGestureStatus.textContent = active ? "CAMERA ON" : "CAMERA OFF";
      console.log("[DEBUG] Camera toggle successful:", { active, target: targetParam });
    } catch (err) {
      console.error("[DEBUG] Camera toggle error:", err);
      alert(`Failed to toggle camera: ${err}`);
      pcCameraToggle.checked = !e.target.checked;
    }
  });

  async function setupHubWebRTC() {
    closeWebRTC(); // Reset
    
    // Enhanced ICE configuration for hotspot support
    const iceServers = [
      { urls: ["stun:stun.l.google.com:19302"] },
      { urls: ["stun:stun1.l.google.com:19302"] },
      // TURN server for double-NAT traversal (hotspot fallback)
      {
        urls: ["turn:numb.viagenie.ca"],
        username: "webrtc@example.com",
        credential: "webrtcpassword"
      }
    ];
    
    peerConn = new RTCPeerConnection({
        iceServers: iceServers
    });

    dataChannel = peerConn.createDataChannel("gestures", { ordered: false });
    
    peerConn.ontrack = (event) => {
        hubVideoPlayer.srcObject = event.streams[0];
    };

    const offer = await peerConn.createOffer();
    await peerConn.setLocalDescription(offer);

    const res = await fetch(hubApi("/api/webrtc/offer"), {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ sdp: peerConn.localDescription?.sdp, type: peerConn.localDescription?.type })
    });
    const answer = await res.json();
    await peerConn.setRemoteDescription(new RTCSessionDescription(answer));
  }

  function closeWebRTC() {
    if (peerConn) {
        peerConn.close();
        peerConn = null;
        dataChannel = null;
    }
  }

  // Vision Mode Buttons
  const modeBtns = document.querySelectorAll(".mode-btn");
  modeBtns.forEach(btn => {
    btn.addEventListener('click', async () => {
      const mode = parseInt((btn as HTMLElement).dataset.mode || "0");
      try {
        const res = await fetch(hubApi("/api/hub/mode"), {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ mode })
        });
        const data = await res.json();
        if (data.ok) {
          modeBtns.forEach(b => b.classList.remove('active'));
          btn.classList.add('active');
          triggerHaptic(ImpactStyle.Medium);
        }
      } catch (err) {
        console.error("Failed to set mode:", err);
      }
    });
  });

  document.getElementById("saveBtn")?.addEventListener('click', saveSettings);

  document.getElementById("logoutBtn")?.addEventListener('click', async () => {
    if (confirm("Disconnect and reset this session?")) {
      await logout();
    }
  });

  document.getElementById("addManualBtn")!.onclick = () => {
    const ip = prompt("Enter Hub IP (e.g. 192.168.1.5):");
    if (ip) addDeviceToList(ip, "Manual PC");
  };

  // Scan Network
  const scanBtn = document.getElementById("scanBtn");
  const scanRipple = document.getElementById("scanRipple");
  scanBtn?.addEventListener('click', async () => {
    scanBtn.setAttribute('disabled', 'true');
    scanRipple?.classList.add('active');
    try {
      const res = await fetch(hubApi("/api/discovered"));
      const data = await res.json();
      const discovered: Record<string, string> = data.devices || {};
      let foundNew = false;
      Object.entries(discovered).forEach(([ip, hostname]) => {
        if (!devices.some(d => d.ip === ip)) {
          addDeviceToList(ip, hostname as string);
          foundNew = true;
        }
      });
      if (!foundNew && Object.keys(discovered).length > 0) {
        renderDeviceList();
      }
    } finally {
      scanBtn.removeAttribute('disabled');
      scanRipple?.classList.remove('active');
    }
  });

  // Load shortcuts from server
  try {
    const r = await fetch(hubApi("/api/shortcuts"));
    const d = await r.json();
    renderShortcuts(d.shortcuts || {});
  } catch (_) { /* use defaults */ }
}

function setupNav() {
  navItems.forEach(item => {
    item.addEventListener('click', (e) => {
      e.preventDefault();
      const tab = (item as HTMLElement).dataset.tab;
      navItems.forEach(n => n.classList.remove('active'));
      item.classList.add('active');
      tabContents.forEach(c => {
        c.classList.remove('active');
        if (c.id === tab + 'Tab') c.classList.add('active');
      });
    });
  });
}

function addDeviceToList(ip: string, hostname: string) {
  if (devices.some(d => d.ip === ip)) return;
  devices.push({ ip, hostname, ws: null });
  renderDeviceList();
}

function renderDeviceList() {
  if (devices.length === 0) {
    deviceList.innerHTML = `
      <div class="empty-state">
        <i class="fas fa-satellite-dish"></i>
        <span>Tap Scan to discover PCs</span>
      </div>`;
    return;
  }
  deviceList.innerHTML = devices.map((d, i) => {
    const isActive = activePC?.ip === d.ip;
    return `
    <div id="device-card-${i}" style="display: flex; justify-content: space-between; align-items: center; padding: 14px; background: var(--glass); border-radius: 14px; border: 1px solid ${isActive ? 'rgba(0,255,149,0.3)' : 'var(--border)'}; margin-bottom: 8px; transition: all 0.2s;">
      <div style="display: flex; align-items: center; gap: 12px; flex:1; min-width:0;">
        <div class="device-icon" style="width:38px; height:38px; font-size:1rem;">💻</div>
        <div style="min-width:0;">
          <div style="font-weight: 600; font-size: 0.88rem; display:flex; align-items:center; gap:6px;">
            <span id="device-name-${i}" style="cursor:pointer; text-decoration: underline; text-decoration-style: dashed; text-underline-offset: 3px; text-decoration-color: rgba(255,255,255,0.2);" onclick="globalThis.renameDevice(${i})" title="Tap to rename">${d.hostname}</span>
          </div>
          <div style="font-size: 0.7rem; color: var(--text-secondary); margin-top: 2px; font-family: monospace;">${d.ip}</div>
        </div>
      </div>
      <button id="connect-btn-${i}" onclick="globalThis.connectToPC(${i})" class="device-connect-btn ${isActive ? 'connected' : ''}">${isActive ? '✓ Active' : 'Connect'}</button>
    </div>`;
  }).join("");
}

// @ts-ignore
globalThis.renameDevice = async (i: number) => {
  const d = devices[i];
  const newName = prompt(`Rename "${d.hostname}":`, d.hostname);
  if (!newName || !newName.trim() || newName.trim() === d.hostname) return;
  const trimmed = newName.trim();
  try {
    await fetch(hubApi("/api/devices/rename"), {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ ip: d.ip, name: trimmed })
    });
    devices[i].hostname = trimmed;
    renderDeviceList();
  } catch (e) {
    console.error('Rename failed', e);
  }
};

// @ts-ignore
globalThis.connectToPC = async (i: number) => {
  const d = devices[i];
  if (!d) return;

  const connectBtn = document.getElementById(`connect-btn-${i}`);
  if (connectBtn) {
    connectBtn.textContent = 'Connecting…';
    connectBtn.classList.add('connecting');
  }

  const proto = HUB_BASE_URL.startsWith("https:") ? "wss:" : "ws:";
  const targetParam = isHubSelfTarget(d.ip, d.hostname) ? "" : `&target=${encodeURIComponent(d.ip)}`;
  const wsUrl = `${proto}//${new URL(HUB_BASE_URL).host}/ws?token=${authToken}${targetParam}`;

  console.log(`[DEBUG] Connecting to device #${i}:`, {
    hostname: d.hostname,
    ip: d.ip,
    selfTarget: isHubSelfTarget(d.ip, d.hostname),
    wsUrl: wsUrl,
    protocol: proto,
    authToken: authToken?.substring(0, 8) + "..."
  });

  try {
    if (d.ws) d.ws.close();
    const ws = new WebSocket(wsUrl);
    ws.binaryType = "arraybuffer";

    ws.onopen = () => {
      console.log(`✅ WebSocket connected to ${d.hostname} (${d.ip})`);
      d.ws = ws;
      activatePC(d);
      initWebRTC();
      renderDeviceList();
    };

    ws.onerror = (err) => {
      console.error("[DEBUG] WebSocket Connection Error:", err);
      console.error("[DEBUG] Network Details:", {
        location: HUB_BASE_URL,
        target_ip: d.ip,
        protocol: proto,
        hostname: HUB_HOSTNAME,
        wsUrl: wsUrl
      });
      alert(`⚠️ Could not connect to ${d.hostname}.\n\nEnsure the Hub is running and on the same network.`);
      if (connectBtn) {
        connectBtn.textContent = 'Connect';
        connectBtn.classList.remove('connecting');
      }
      renderDeviceList();
    };

    ws.onclose = (event) => {
      console.log(`📴 WebSocket closed for ${d.hostname}:`, {
        code: event.code,
        reason: event.reason,
        wasClean: event.wasClean
      });
      d.ws = null;
      if (activePC === d) {
        connBadge.textContent = "OFFLINE";
        connBadge.classList.remove('online');
        if (event.code === 4003 || event.code === 1008) {
          console.log("Token rejected - forcing re-pair");
          localStorage.removeItem("gesturelink_token");
          authToken = null;
          pairingOverlay.style.display = 'flex';
          pairingOverlay.classList.remove('hidden');
          document.getElementById('disconnectBtn')?.classList.remove('visible');
        }
      }
      renderDeviceList();
    };

    ws.onmessage = (msg) => {
      if (activePC !== d) return;
      try {
        const data = JSON.parse(msg.data);
        if (data.type === 'error') {
          console.error("[DEBUG] Server error:", data.message);
          connBadge.textContent = 'ERROR';
          connBadge.classList.remove('online');
          alert(`⚠️ ${data.message || 'Connection error'}`);
          return;
        }
        if (data.gesture) {
          const old = remoteGestureStatus.textContent;
          remoteGestureStatus.textContent = data.gesture;
          if (data.gesture !== old && data.gesture !== 'IDLE') triggerHaptic(ImpactStyle.Light);
        }
      } catch (_) { }
    };
  } catch (err) {
    console.error("[DEBUG] WebSocket Creation Error:", err);
    if (connectBtn) {
      connectBtn.textContent = 'Connect';
      connectBtn.classList.remove('connecting');
    }
    alert("Connection Failed");
  }
};

async function initWebRTC() {
  if (peerConn) peerConn.close();
  
  // Enhanced ICE configuration for hotspot support
  const iceServers = [
    { urls: ["stun:stun.l.google.com:19302"] },
    { urls: ["stun:stun1.l.google.com:19302"] },
    // TURN server for double-NAT traversal (hotspot fallback)
    {
      urls: ["turn:numb.viagenie.ca"],
      username: "webrtc@example.com",
      credential: "webrtcpassword"
    }
  ];
  
  peerConn = new RTCPeerConnection({ iceServers });

  peerConn.onicecandidate = (e) => {
    if (e.candidate) sendSignal({ type: "candidate", candidate: e.candidate });
  };

  dataChannel = peerConn.createDataChannel("commands", { ordered: false, maxRetransmits: 0 });
  dataChannel.onopen = () => console.log("WebRTC DataChannel OPEN! (0-latency mode active)");
  dataChannel.onclose = () => dataChannel = null;

  const offer = await peerConn.createOffer();
  await peerConn.setLocalDescription(offer);
  sendSignal({ type: "offer", sdp: offer.sdp });

  (async () => {
    while (peerConn) {
      try {
        const res = await fetch(hubApi(`/api/webrtc/signal/${myPeerId}`));
        const data = await res.json();
        if (data.ok && data.signal) {
          if (data.signal.type === "answer") await peerConn.setRemoteDescription(new RTCSessionDescription(data.signal));
          else if (data.signal.type === "candidate") await peerConn.addIceCandidate(new RTCIceCandidate(data.signal.candidate));
        }
      } catch (e) { }
      await new Promise(r => setTimeout(r, 100)); // 100ms polling for instant handshake
    }
  })();
}

async function sendSignal(data: any) {
  await fetch(hubApi("/api/webrtc/signal/hub_pc"), {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ from: myPeerId, ...data })
  });
}

async function activatePC(d: any) {
  activePC = d;
  activeDeviceName.textContent = d.hostname;
  activeDeviceIP.textContent = d.ip;
  connBadge.textContent = "ONLINE";
  connBadge.classList.add('online');
  document.getElementById('disconnectBtn')?.classList.add('visible');
  syncSettings();

  try {
    const camStatusTarget = isHubSelfTarget(d.ip, d.hostname) ? "" : `?target=${encodeURIComponent(d.ip)}`;
    const [modeRes, camRes] = await Promise.all([
      fetch(hubApi("/api/hub/mode")).then(r => r.json()),
      fetch(hubApi(`/api/hub/camera/status${camStatusTarget}`)).then(r => r.json()).catch(() => ({ active: false }))
    ]);

    const modeBtns = document.querySelectorAll(".mode-btn");
    modeBtns.forEach(b => {
      if (parseInt((b as HTMLElement).dataset.mode || "0") === modeRes.mode) b.classList.add('active');
      else b.classList.remove('active');
    });

    const pcCameraToggle = document.getElementById("pcCameraToggle") as HTMLInputElement;
    if (pcCameraToggle) pcCameraToggle.checked = camRes.active;
    const gestureStatusEl = document.getElementById("remoteGestureStatus");
    if (gestureStatusEl) gestureStatusEl.textContent = camRes.active ? "CAMERA ON" : "CAMERA OFF";

  } catch (_) {}
}

async function syncSettings() {
  if (!activePC) return;
  try {
    const [shortcutsRes, setRes] = await Promise.all([
      fetch(hubApi("/api/shortcuts")).then(r => r.json()),
      fetch(hubApi("/api/settings")).then(r => r.json())
    ]);
    const sens = document.getElementById("sensRange") as HTMLInputElement;
    const scroll = document.getElementById("scrollRange") as HTMLInputElement;
    const sensVal = document.getElementById("sensVal");
    const scrollVal = document.getElementById("scrollVal");
    if (sens) {
      sens.value = setRes.sensitivity || 50;
      if (sensVal) sensVal.textContent = sens.value;
    }
    if (scroll) {
      scroll.value = setRes.scroll_speed || 12;
      if (scrollVal) scrollVal.textContent = scroll.value;
    }
    renderShortcuts(shortcutsRes.shortcuts || {});
  } catch (_) { }
}

function renderShortcuts(shortcuts: Record<string, any>) {
  document.querySelectorAll<HTMLElement>('[data-shortcut]').forEach(el => {
    const key = el.dataset.shortcut!;
    if (shortcuts[key]) {
      const binding = shortcuts[key];
      el.textContent = binding.target || "None";
    }
  });
}

let activeShortcutSlot = "";
(globalThis as any).editShortcut = async (slot: string) => {
  activeShortcutSlot = slot;
  appModal.style.display = 'flex';

  try {
    const targetIp = activePC ? activePC.ip : "";
    const res = await fetch(hubApi(`/api/apps?ip=${targetIp}`));
    const data = await res.json();
    appSelect.innerHTML = '<option value="">— Choose from device —</option>';
    data.apps.forEach((app: any) => {
      const opt = document.createElement("option");
      opt.value = app.target;
      opt.textContent = app.name;
      appSelect.appendChild(opt);
    });
  } catch (e) {
    console.error("Failed to load apps", e);
  }
};

saveAppShortcut.onclick = async () => {
  const target = customTarget.value.trim() || appSelect.value;
  if (!target) { alert("Please select or type an app/command."); return; }

  try {
    const res = await fetch(hubApi("/api/shortcuts"), {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ shortcuts: { [activeShortcutSlot]: { target, enabled: true } } })
    });
    if (!res.ok) throw new Error("Server error");

    const keyEl = document.querySelector(`.shortcut-key[data-shortcut="${activeShortcutSlot}"]`);
    if (keyEl) keyEl.textContent = target;

    appModal.style.display = 'none';
    customTarget.value = "";
    triggerHaptic(ImpactStyle.Medium);
  } catch (_) {
    alert("Failed to save shortcut. Check connection.");
  }
};

async function startApp() {
  // 0. Check for Hub URL in query params (from Vercel QR code)
  const urlParams = new URLSearchParams(window.location.search);
  const hubParam = urlParams.get('hub');
  if (hubParam) {
    const hubIp = hubParam.replace('https://', '').replace('http://', '');
    addDeviceToList(hubIp, "Hub (QR Remote)");
  }

  // Get hub info to find the Local LAN IP
  try {
    const res = await fetch(hubApi("/api/hub/info"));
    const data = await res.json();
    
    // 1. Add the current domain
    addDeviceToList(HUB_HOSTNAME, "Hub (Primary)");
    
    // 2. Add the Local LAN IP (if different)
    if (data.lan_ip && data.lan_ip !== HUB_HOSTNAME) {
      addDeviceToList(data.lan_ip, "Hub (Local LAN)");
    }

    // AUTO-CONNECT STRATEGY:
    // Try zero-latency direct connection first (LAN), then fall back to tunnel
    if (data.lan_ip && HUB_HOSTNAME !== data.lan_ip) {
       console.log("🔍 Probing Local LAN for zero-latency fallback...");
       try {
         // Use protocol and port from hub info response
         const proto = data.ssl_active ? "https" : "http";
         const port = data.port || 8000;
         const lanUrl = `${proto}://${data.lan_ip}:${port}/api/ping`;
         console.log(`[DEBUG] LAN Probe URL: ${lanUrl}`);
         
         const probe = await fetch(lanUrl, { 
           signal: AbortSignal.timeout(1000),
           headers: { 'Accept': 'application/json' }
         });
         
         if (probe.ok) {
           console.log("✅ Local LAN reached! Switching to 0-latency mode.");
           // Find the index of the LAN device
           const lanIdx = devices.findIndex(d => d.ip === data.lan_ip);
           if (lanIdx !== -1) {
             // @ts-ignore
             globalThis.connectToPC(lanIdx);
             return;
           }
         }
       } catch (e: any) {
         console.log(`⚠️  Local LAN probe failed (${e.message}). Falling back to Cloud Tunnel.`);
         console.log(`[DEBUG] Error details:`, e);
       }
    }

    // Fallback: connect to the device at index 0 (usually the current domain / cloud tunnel)
    console.log("📡 Using Cloud Tunnel (Cloudflare)");
    // @ts-ignore
    globalThis.connectToPC(0);

  } catch (e) {
    console.error("Hybrid start failed:", e);
    addDeviceToList(HUB_HOSTNAME, "Hub (Primary)");
    // @ts-ignore
    globalThis.connectToPC(0);
  }
}

async function logout() {
  const token = localStorage.getItem("gesturelink_token");
  if (token) {
    try {
      await fetch(hubApi("/api/logout"), {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ token })
      });
    } catch (_) { /* best effort */ }
  }
  localStorage.removeItem("gesturelink_token");
  localStorage.removeItem("gesturelink_ip");
  
  // Clear URL parameters to prevent auto-pairing on reload
  const url = new URL(window.location.href);
  url.search = "";
  window.history.replaceState({}, "", url.toString());
  
  location.reload();
}

async function triggerHaptic(style: ImpactStyle = ImpactStyle.Light) {
  if (!hapticsEnabled) return;
  if (navigator.vibrate) navigator.vibrate(style === ImpactStyle.Heavy ? 40 : 15);
}

function setupTouchpad() {
  let lastX = 0, lastY = 0, startTime = 0;
  let lastTapTime = 0;
  let maxFingers = 0;
  let lastPinchDist = 0;
  let lastMoveTime = 0;
  let isMoving = false;
  let isDragging = false;
  let twoFingerStarted = false;
  let twoFingerMidY = 0;

  touchZone.addEventListener('touchstart', (e: any) => {
    maxFingers = Math.max(maxFingers, e.touches.length);
    lastX = e.touches[0].clientX;
    lastY = e.touches[0].clientY;
    startTime = Date.now();
    isMoving = false;

    if (e.touches.length === 2) {
      twoFingerStarted = true;
      twoFingerMidY = (e.touches[0].clientY + e.touches[1].clientY) / 2;
      lastPinchDist = Math.hypot(
        e.touches[0].clientX - e.touches[1].clientX,
        e.touches[0].clientY - e.touches[1].clientY
      );
    } else if (e.touches.length === 1) {
      twoFingerStarted = false;
    }

    if (e.touches.length === 1 && (startTime - lastTapTime) < 300) {
      isDragging = true;
      sendCommand({ type: 'click_down', button: 'left' });
    }
    e.preventDefault();
  }, { passive: false });

  touchZone.addEventListener('touchmove', (e: any) => {
    isMoving = true;
    maxFingers = Math.max(maxFingers, e.touches.length);

    if (e.touches.length >= 2 && !twoFingerStarted) {
      twoFingerStarted = true;
      twoFingerMidY = (e.touches[0].clientY + e.touches[1].clientY) / 2;
      lastPinchDist = Math.hypot(
        e.touches[0].clientX - e.touches[1].clientX,
        e.touches[0].clientY - e.touches[1].clientY
      );
    }

    if (twoFingerStarted && e.touches.length >= 2) {
      const currentDist = Math.hypot(
        e.touches[0].clientX - e.touches[1].clientX,
        e.touches[0].clientY - e.touches[1].clientY
      );
      const currentMidY = (e.touches[0].clientY + e.touches[1].clientY) / 2;
      const pinchDelta = currentDist - lastPinchDist;

      if (Math.abs(pinchDelta) > 8) {
        if (activePC?.ws?.readyState === 1) {
          sendCommand({ type: 'zoom', delta: pinchDelta });
        }
        lastPinchDist = currentDist;
      } else {
        const scrollDy = currentMidY - twoFingerMidY;
        if (Math.abs(scrollDy) > 2 && activePC?.ws?.readyState === 1) {
          sendCommand({ type: 'scroll', dy: scrollDy * -1.5 });
        }
      }
      twoFingerMidY = currentMidY;
      lastPinchDist = currentDist;

    } else if (!twoFingerStarted && e.touches.length === 1 && !isDragging) {
      const now = Date.now();
      if (now - lastMoveTime < 16) {
        e.preventDefault();
        return;
      }
      const dx = e.touches[0].clientX - lastX;
      const dy = e.touches[0].clientY - lastY;
      lastX = e.touches[0].clientX;
      lastY = e.touches[0].clientY;
      sendCommand({ type: 'move', dx, dy });
      lastMoveTime = now;
    } else if (!twoFingerStarted && e.touches.length === 1 && isDragging) {
      const dx = e.touches[0].clientX - lastX;
      const dy = e.touches[0].clientY - lastY;
      lastX = e.touches[0].clientX;
      lastY = e.touches[0].clientY;
      sendCommand({ type: 'move', dx, dy });
    }
    e.preventDefault();
  }, { passive: false });

  touchZone.addEventListener('touchend', (e: any) => {
    if (e.touches.length > 0) return;

    const now = Date.now();
    const duration = now - startTime;

    if (isDragging) {
      isDragging = false;
      if (activePC?.ws?.readyState === 1) {
        sendCommand({ type: 'click_up', button: 'left' });
      }
    } else if (duration < 250 && !isMoving) {
      if (activePC?.ws?.readyState === 1 && maxFingers < 3) {
        const button = maxFingers === 2 ? 'right' : 'left';
        sendCommand({ type: 'click', button });
        triggerHaptic(maxFingers === 2 ? ImpactStyle.Medium : ImpactStyle.Light);
      }
      lastTapTime = now;
    } else if (duration >= 1000 && !isMoving) {
      if (activePC?.ws?.readyState === 1 && (maxFingers === 3 || maxFingers === 4)) {
        sendCommand({ type: 'shortcut', slot: `touch_${maxFingers}_finger` });
        triggerHaptic(ImpactStyle.Heavy);
      }
    } else {
      lastTapTime = 0;
    }

    if (e.touches.length === 0) {
      maxFingers = 0;
      twoFingerStarted = false;
    }
  });
}

function setupKeyboardToolbar() {
  kbBtn.onclick = () => {
    keyboardInput.focus();
    triggerHaptic(ImpactStyle.Light);
  };

  copyBtn.onclick = () => {
    sendHotkey(['ctrl', 'c']);
  };

  pasteBtn.onclick = () => {
    sendHotkey(['ctrl', 'v']);
  };

  keyboardInput.addEventListener('keydown', (e) => {
    if (!activePC?.ws || activePC.ws.readyState !== 1) return;
    if (["Backspace", "Enter", "Tab", "Escape"].includes(e.key)) {
      sendCommand({ type: 'key', key: e.key });
      e.preventDefault();
    }
  });

  keyboardInput.addEventListener('input', () => {
    if (!activePC?.ws || activePC.ws.readyState !== 1) return;
    const val = keyboardInput.value;
    if (val.length > 0) {
      sendCommand({ type: 'key', key: val });
      keyboardInput.value = '';
    }
  });
}

function sendHotkey(keys: string[]) {
  sendCommand({ type: 'hotkey', keys });
  triggerHaptic(ImpactStyle.Medium);
}

function sendCommand(cmd: any) {
  // 🚀 Use WebRTC DataChannel for ultra-low latency if available
  if (dataChannel && dataChannel.readyState === 'open') {
    dataChannel.send(JSON.stringify(cmd));
    console.log("[DEBUG] Sent via DataChannel:", cmd);
    return;
  }
  
  // 🛡️ Fallback to WebSocket if WebRTC is still connecting or not supported
  if (activePC?.ws?.readyState === 1) {
    activePC.ws.send(JSON.stringify(cmd));
    console.log("[DEBUG] Sent via WebSocket:", cmd);
  } else {
    console.warn("[DEBUG] Neither DataChannel nor WebSocket available. activePC:", activePC, "dataChannel:", dataChannel);
  }
}

async function saveSettings() {
  if (!activePC) return;
  try {
    const sens = (document.getElementById("sensRange") as HTMLInputElement).value;
    const scroll = (document.getElementById("scrollRange") as HTMLInputElement).value;
    await fetch(hubApi("/api/settings"), {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        sensitivity: Number.parseInt(sens),
        scroll_speed: Number.parseInt(scroll)
      })
    });
    triggerHaptic(ImpactStyle.Medium);
    alert("Settings applied!");
  } catch (e) { alert("Save failed"); }
}

function setupPinInputs() {
  // Remove any duplicate input handler by cloning (the inline script in HTML handles focus only)
  pinInputs.forEach((input) => {
    // Only attach the pair-on-complete behavior; focus is handled by inline script
    input.addEventListener('keydown', (e) => {
      if (e.key === 'Enter') pairBtn.click();
    });
  });

  pairBtn.onclick = async () => {
    const pin = Array.from(pinInputs).map(i => i.value).join("");
    await autoPair(pin);
  };
}

async function setupShortcuts() {
  try {
    const res = await fetch(hubApi("/api/shortcuts"));
    const data = await res.json();
    renderShortcuts(data.shortcuts || {});
  } catch (_) { /* use defaults */ }
}

async function autoPair(pin: string) {
  if (pin.length !== 6) return;
  pairStatusText.textContent = "Verifying PIN…";

  // Determine Hub URL: query param > local
  const urlParams = new URLSearchParams(window.location.search);
  const hubUrl = urlParams.get('hub') || HUB_BASE_URL;
  const baseUrl = hubUrl.startsWith('http') ? hubUrl : `https://${hubUrl}`;

  try {
    const res = await fetch(`${baseUrl.replace(/\/$/, '')}/api/pair`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ pin, hostname: "Mobile Controller" })
    });
    const data = await res.json();

    if (data.status === "approved" && data.token) {
      finalizePairing(data.token);
    } else if (data.status === "pending") {
      pairStatusText.textContent = "Waiting for Hub approval…";
      pollPairingStatus(data.request_id);
    } else {
      pairError.style.opacity = '1';
      triggerHaptic(ImpactStyle.Medium);
      setTimeout(() => pairError.style.opacity = '0', 3000);
      pairStatusText.textContent = "";
    }
  } catch (e) {
    pairError.style.opacity = '1';
    pairStatusText.textContent = "";
  }
}

function finalizePairing(token: string) {
  localStorage.setItem("gesturelink_token", token);
  authToken = token;
  pairingOverlay.classList.add('hidden');
  setTimeout(() => pairingOverlay.style.display = 'none', 500);
  triggerHaptic(ImpactStyle.Heavy);
  startApp();
}

async function pollPairingStatus(reqId: string) {
  const interval = setInterval(async () => {
    try {
      const res = await fetch(hubApi(`/api/pair/status/${reqId}`));
      const data = await res.json();
      if (data.status === "approved") {
        clearInterval(interval);
        finalizePairing(data.token);
      } else if (data.status === "rejected") {
        clearInterval(interval);
        pairError.style.opacity = '1';
        setTimeout(() => pairError.style.opacity = '0', 3000);
        pairStatusText.textContent = "Request rejected";
      }
    } catch (e) {
      clearInterval(interval);
    }
  }, 2000);
}

init();
