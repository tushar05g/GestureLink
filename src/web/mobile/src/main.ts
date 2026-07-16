import './style.css'
import { ImpactStyle } from '@capacitor/haptics';
import { initializeApp } from "firebase/app";
import { getDatabase, ref, set, onValue } from "firebase/database";

const firebaseConfig = {
  apiKey: import.meta.env.VITE_FIREBASE_API_KEY,
  authDomain: "gesturelink-5db9c.firebaseapp.com",
  databaseURL: "https://gesturelink-5db9c-default-rtdb.firebaseio.com",
  projectId: "gesturelink-5db9c",
  storageBucket: "gesturelink-5db9c.firebasestorage.app",
  messagingSenderId: "744761635621",
  appId: "1:744761635621:web:66ccd1d53b405c059f60cc"
};

const fbApp = initializeApp(firebaseConfig);
const db = getDatabase(fbApp);

// --- State ---
let activePC: any = null;
let devices: any[] = [];
let authToken = localStorage.getItem("gesturelink_token");
let hapticsEnabled = localStorage.getItem("gesturelink_haptics") !== "false";
let cameraPollInterval: any = null;

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
// Expose for inline Meet Paint scripts in index.html
(window as any)._hubBase = HUB_BASE_URL;

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
// myPeerId removed

// DOM Elements
const pairingOverlay = document.getElementById("pairingOverlay")!;
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
const leftArrowBtn = document.getElementById("leftArrowBtn")!;
const rightArrowBtn = document.getElementById("rightArrowBtn")!;
const kbBtn = document.getElementById("kbBtn")!;

// --- Scanner / Discovery State ---
const SAVED_HUB_KEY = "gesturelink_hub_saved";
const SAVED_HUB_NAME_KEY = "gesturelink_hub_name";

function getSavedHub(): { url: string; name: string } | null {
  const url = localStorage.getItem(SAVED_HUB_KEY);
  const name = localStorage.getItem(SAVED_HUB_NAME_KEY) || "Your PC";
  if (url) return { url, name };
  return null;
}

function saveHub(url: string, name: string) {
  localStorage.setItem(SAVED_HUB_KEY, url);
  localStorage.setItem(SAVED_HUB_NAME_KEY, name);
}

function forgetHub() {
  localStorage.removeItem(SAVED_HUB_KEY);
  localStorage.removeItem(SAVED_HUB_NAME_KEY);
}

// --- Initialization ---
async function init() {
  setupNav();
  setupTouchpad();
  setupScanBtn();
  setupShortcuts();
  setupKeyboardToolbar();

  closeAppModal.onclick = () => {
    appModal.style.display = 'none';
  };

  // ---- DETERMINE STARTUP FLOW ----
  const urlParams = new URLSearchParams(globalThis.location.search);
  const autoPin = urlParams.get('pin');
  const hubParam = urlParams.get('hub');

  // FLOW 1: QR Code URL — has ?pin= and ?hub= params → use legacy overlay flow
  if (autoPin?.length === 6 && hubParam) {
    // Show QR pairing overlay (legacy web flow)
    const scannerPage = document.getElementById('scannerPage')!;
    scannerPage.classList.add('hidden');
    pairingOverlay.style.display = 'flex';
    pairStatusText.innerHTML = '<i class="fas fa-spinner fa-spin" style="margin-right: 8px;"></i>Connecting to PC...';
    await autoPair(autoPin);
    authToken = localStorage.getItem("gesturelink_token");
    if (!authToken || authToken === "undefined") {
      pairingOverlay.style.display = 'flex';
    }
    setupScannerPage();
    return;
  }

  // FLOW 2: Returning user — has a saved hub → try auto-connect
  const saved = getSavedHub();
  if (saved && !autoPin) {
    const scannerPage = document.getElementById('scannerPage')!;
    scannerPage.classList.add('hidden');
    await tryAutoConnect(saved.url, saved.name);
    setupScannerPage();
    return;
  }

  // FLOW 3: New user — show scanner page
  setupScannerPage();
  startNetworkScan();

  // Haptic toggle
  document.getElementById("hapticToggle")?.addEventListener('change', (e: any) => {
    hapticsEnabled = e.target.checked;
    localStorage.setItem("gesturelink_haptics", hapticsEnabled.toString());
    if (hapticsEnabled) triggerHaptic();
  });

  // Camera toggle
  const pcCameraToggle = document.getElementById("pcCameraToggle") as HTMLInputElement;

  pcCameraToggle?.addEventListener('change', async (e: any) => {
    if (!activePC && !isCommandChannelOpen()) {
      alert("Connect to a PC first!");
      pcCameraToggle.checked = false;
      return;
    }
    
    const active = e.target.checked;
    const targetParam = isHubSelfTarget(activePC?.ip, activePC?.hostname)
      ? ""
      : `?target=${encodeURIComponent(activePC.ip)}`;

    // Immediately disable toggle and show visual indicator
    pcCameraToggle.disabled = true;
    if (active) {
      if (remoteGestureStatus) {
        remoteGestureStatus.innerHTML = '<i class="fas fa-spinner fa-spin" style="margin-right: 5px;"></i>STARTING...';
        remoteGestureStatus.style.color = '#ffaa00';
      }
    } else {
      if (remoteGestureStatus) {
        remoteGestureStatus.innerHTML = '<i class="fas fa-spinner fa-spin" style="margin-right: 5px;"></i>STOPPING...';
        remoteGestureStatus.style.color = 'var(--text-secondary)';
      }
    }

    try {
      if (isCommandChannelOpen()) {
        sendCommand({ type: 'camera_toggle', active });
        // In WebRTC mode, assume the toggle command went through successfully.
        if (active) {
          startCameraPolling(targetParam);
        } else {
          if (cameraPollInterval) {
            clearInterval(cameraPollInterval);
            cameraPollInterval = null;
          }
          pcCameraToggle.disabled = false;
          if (remoteGestureStatus) {
            remoteGestureStatus.innerHTML = "CAMERA OFF";
            remoteGestureStatus.style.color = "var(--text-secondary)";
          }
        }
      } else {
        const res = await fetch(hubApi(`/api/hub/camera/toggle${targetParam}`), {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ active })
        });
        const data = await res.json();
        if (!data.ok) throw new Error(data.error);
        
        if (active) {
          startCameraPolling(targetParam);
        } else {
          if (cameraPollInterval) {
            clearInterval(cameraPollInterval);
            cameraPollInterval = null;
          }
          pcCameraToggle.disabled = false;
          if (remoteGestureStatus) {
            remoteGestureStatus.innerHTML = "CAMERA OFF";
            remoteGestureStatus.style.color = "var(--text-secondary)";
          }
        }
      }
      console.log("[DEBUG] Camera toggle request handled:", { active, target: targetParam });
    } catch (err) {
      console.error("[DEBUG] Camera toggle error:", err);
      alert(`Failed to toggle camera: ${err}`);
      pcCameraToggle.disabled = false;
      pcCameraToggle.checked = !active;
      if (remoteGestureStatus) {
        remoteGestureStatus.innerHTML = !active ? "CAMERA ON" : "CAMERA OFF";
        remoteGestureStatus.style.color = !active ? "var(--accent)" : "var(--text-secondary)";
      }
    }
  });



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
          // Sync Meet Paint panel visibility
          if (typeof (window as any)._syncMeetPaintPanel === 'function') {
            (window as any)._syncMeetPaintPanel(mode);
          }
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

  document.getElementById("forgetHubBtn")?.addEventListener('click', async () => {
    if (confirm("Forget this PC? You will need to scan and reconnect on next launch.")) {
      forgetHub();
      localStorage.removeItem("gesturelink_token");
      location.reload();
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

  if (authToken === "firebase-webrtc-only") {
    console.log("Firebase-only mode detected. Bypassing WebSocket...");
    activatePC(d);
    initWebRTC(true);
    return;
  }

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

    let fallbackTriggered = false;
    const triggerFallback = () => {
      if (fallbackTriggered) return;
      fallbackTriggered = true;
      console.log("WebSocket failed or timed out. Falling back to Firebase WebRTC signaling.");
      if (connectBtn) {
        connectBtn.textContent = 'Connect';
        connectBtn.classList.remove('connecting');
      }
      activatePC(d);
      initWebRTC(true);
    };

    const wsTimeout = setTimeout(() => {
      if (ws.readyState !== WebSocket.OPEN) {
        console.warn("WebSocket connection timed out (3s).");
        ws.close();
        triggerFallback();
      }
    }, 3000);

    ws.onopen = () => {
      clearTimeout(wsTimeout);
      console.log(`✅ WebSocket connected to ${d.hostname} (${d.ip})`);
      d.ws = ws;
      activatePC(d);
      initWebRTC();
      renderDeviceList();
      hidePairingOverlay();
    };

    ws.onerror = (err) => {
      console.error("[DEBUG] WebSocket Connection Error:", err);
      triggerFallback();
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
        const connBadgeDevice = document.getElementById("connBadgeDevice");
        if (connBadgeDevice) {
          connBadgeDevice.textContent = "OFFLINE";
          connBadgeDevice.classList.remove('online');
        }
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
          const connBadgeDevice = document.getElementById("connBadgeDevice");
          if (connBadgeDevice) {
            connBadgeDevice.textContent = 'ERROR';
            connBadgeDevice.classList.remove('online');
          }
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

async function initWebRTC(isFallback = false) {
  if (peerConn) peerConn.close();
  
  // Commercial TURN Server (Metered.ca)
  const iceServers = [
    { urls: ["stun:stun.l.google.com:19302"] },
    { urls: ["stun:stun1.l.google.com:19302"] },
    {
      urls: ["turn:global.relay.metered.ca:80", "turn:global.relay.metered.ca:443"],
      username: "d120fb319dff30d8d011f0cf",
      credential: "W0oY1T/dC+8v3hQf"
    }
  ];
  
  peerConn = new RTCPeerConnection({ iceServers });

  // Mobile always creates DataChannel
  dataChannel = peerConn.createDataChannel("commands", { ordered: false, maxRetransmits: 0 });
  dataChannel.onopen = () => {
      console.log("WebRTC DataChannel OPEN! (0-latency mode active)");
      connBadge.textContent = "ONLINE (P2P)";
      connBadge.classList.add('online');
      if (isFallback) {
          activeDeviceName.textContent = "PC (Remote)";
          activeDeviceIP.textContent = "WebRTC";
          document.getElementById('disconnectBtn')?.classList.add('visible');
          hidePairingOverlay();
      }
  };
  dataChannel.onclose = () => dataChannel = null;

  dataChannel.onmessage = (event) => {
    try {
      const msg = JSON.parse(event.data);
      if (msg.type === "camera_status_result") {
        const isActive = msg.active || msg.status === "active";
        if (isActive) {
          if (cameraPollInterval) {
            clearInterval(cameraPollInterval);
            cameraPollInterval = null;
          }
          const pcCameraToggle = document.getElementById("pcCameraToggle") as HTMLInputElement;
          if (pcCameraToggle) {
            pcCameraToggle.disabled = false;
            pcCameraToggle.checked = true;
          }
          const remoteGestureStatus = document.getElementById('remoteGestureStatus');
          if (remoteGestureStatus) {
            remoteGestureStatus.innerHTML = "CAMERA ON";
            remoteGestureStatus.style.color = "var(--accent)";
          }
        }
      }
    } catch (e) {
      console.error("WebRTC message parsing error:", e);
    }
  };


  const offer = await peerConn.createOffer();
  await peerConn.setLocalDescription(offer);
  
  // Wait for ICE gathering to complete so STUN/TURN candidates are included in SDP
  await new Promise<void>((resolve) => {
    if (peerConn.iceGatheringState === 'complete') {
      resolve();
    } else {
      const checkState = () => {
        if (peerConn.iceGatheringState === 'complete') {
          peerConn.removeEventListener('icegatheringstatechange', checkState);
          resolve();
        }
      };
      peerConn.addEventListener('icegatheringstatechange', checkState);
      setTimeout(() => {
        peerConn.removeEventListener('icegatheringstatechange', checkState);
        resolve();
      }, 3000);
    }
  });

  const finalOffer = peerConn.localDescription || offer;
  
  // Get current PIN
  let pin = "";
  const autoPin = new URLSearchParams(globalThis.location.search).get('pin');
  if (autoPin) pin = autoPin;
  else pin = Array.from(document.querySelectorAll<HTMLInputElement>(".pin-box")).map(i => i.value).join("");
  
  if (!pin) {
      console.error("No PIN available for Firebase signaling");
      return;
  }

  // Write offer to Firebase
  console.log("Writing offer to Firebase...");
  const sessionRef = ref(db, `sessions/${pin}/mobile`);
  await set(sessionRef, {
      offer: { sdp: finalOffer.sdp, type: finalOffer.type },
      timestamp: Date.now()
  });

  // Listen for Hub's answer
  const answerRef = ref(db, `sessions/${pin}/hub`);
  const unsubscribe = onValue(answerRef, async (snapshot) => {
      const data = snapshot.val();
      if (data && data.answer) {
          console.log("Received answer from Firebase!");
          if (peerConn && peerConn.signalingState !== "stable") {
              await peerConn.setRemoteDescription(new RTCSessionDescription(data.answer));
              unsubscribe(); // Stop listening once answered
          }
      }
  });
}

// sendSignal removed because Firebase is used now

function startCameraPolling(targetParam: string) {
  if (cameraPollInterval) clearInterval(cameraPollInterval);
  const pcCameraToggle = document.getElementById("pcCameraToggle") as HTMLInputElement;
  if (pcCameraToggle) pcCameraToggle.disabled = true;
  
  let attempts = 0;
  const maxAttempts = 15;
  cameraPollInterval = setInterval(async () => {
    attempts++;
    try {
      if (isCommandChannelOpen()) {
        sendCommand({ type: 'camera_status' });
        // Response will be handled in dataChannel.onmessage
      } else {
        const statusRes = await fetch(hubApi(`/api/hub/camera/status${targetParam}`));
        const statusData = await statusRes.json();
        if (statusData.status === "active" || statusData.active) {
          clearInterval(cameraPollInterval);
          cameraPollInterval = null;
          if (pcCameraToggle) {
            pcCameraToggle.disabled = false;
            pcCameraToggle.checked = true;
          }
          if (remoteGestureStatus) {
            remoteGestureStatus.innerHTML = "CAMERA ON";
            remoteGestureStatus.style.color = "var(--accent)";
          }
          console.log("[DEBUG] Camera is now fully active");
        } else if (attempts >= maxAttempts) {
          clearInterval(cameraPollInterval);
          cameraPollInterval = null;
          if (pcCameraToggle) {
            pcCameraToggle.disabled = false;
            pcCameraToggle.checked = false;
          }
          if (remoteGestureStatus) {
            remoteGestureStatus.innerHTML = "CAMERA OFF";
            remoteGestureStatus.style.color = "var(--text-secondary)";
          }
          alert("Camera initialization timed out. Please check camera connections.");
        }
      }
    } catch (pollErr) {
      console.error("Camera status poll error:", pollErr);
      if (attempts >= maxAttempts) {
        clearInterval(cameraPollInterval);
        cameraPollInterval = null;
        if (pcCameraToggle) {
          pcCameraToggle.disabled = false;
          pcCameraToggle.checked = false;
        }
        if (remoteGestureStatus) {
          remoteGestureStatus.innerHTML = "CAMERA OFF";
          remoteGestureStatus.style.color = "var(--text-secondary)";
        }
      }
    }
  }, 1000);
}

async function activatePC(d: any) {
  activePC = d;
  activeDeviceName.textContent = d.hostname;
  activeDeviceIP.textContent = d.ip;
  connBadge.textContent = "ONLINE";
  connBadge.classList.add('online');
  const connBadgeDevice = document.getElementById("connBadgeDevice");
  if (connBadgeDevice) {
    connBadgeDevice.textContent = "ONLINE";
    connBadgeDevice.classList.add('online');
  }
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
    // Sync Meet Paint panel visibility
    if (typeof (window as any)._syncMeetPaintPanel === 'function') {
      (window as any)._syncMeetPaintPanel(modeRes.mode);
    }

    const pcCameraToggle = document.getElementById("pcCameraToggle") as HTMLInputElement;
    const gestureStatusEl = document.getElementById("remoteGestureStatus");
    
    if (camRes.status === "starting") {
      if (pcCameraToggle) {
        pcCameraToggle.checked = true;
        pcCameraToggle.disabled = true;
        const parent = pcCameraToggle.closest('.setting-item');
        if (parent) (parent as HTMLElement).style.display = 'flex';
      }
      if (gestureStatusEl) {
        gestureStatusEl.innerHTML = '<i class="fas fa-spinner fa-spin" style="margin-right: 5px;"></i>STARTING...';
        gestureStatusEl.style.color = '#ffaa00';
      }
      startCameraPolling(camStatusTarget);
    } else {
      if (cameraPollInterval) {
        clearInterval(cameraPollInterval);
        cameraPollInterval = null;
      }
      if (pcCameraToggle) {
        pcCameraToggle.checked = camRes.active;
        pcCameraToggle.disabled = false;
        const parent = pcCameraToggle.closest('.setting-item');
        if (parent) (parent as HTMLElement).style.display = 'flex';
      }
      if (gestureStatusEl) {
        gestureStatusEl.innerHTML = camRes.active ? "CAMERA ON" : "CAMERA OFF";
        gestureStatusEl.style.color = camRes.active ? "var(--accent)" : "var(--text-secondary)";
      }
    }

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
  // Only add QR Remote entry if it's a different host from what we're already on
  if (hubParam) {
    const hubIp = hubParam.replace('https://', '').replace('http://', '');
    if (hubIp !== HUB_HOSTNAME) {
      addDeviceToList(hubIp, "Hub (QR Remote)");
    }
  }

  // Get hub info to find the Local LAN IP
  try {
    const res = await fetch(hubApi("/api/hub/info"), {
      signal: AbortSignal.timeout(3000)
    });
    const data = await res.json();
    
    // 1. Add the current domain as Hub (Primary)
    addDeviceToList(HUB_HOSTNAME, "Hub (Primary)");
    
    // 2. Add the Local LAN IP(s) (if different from the cloud tunnel)
    const allIps: string[] = data.all_ips || (data.lan_ip ? [data.lan_ip] : []);
    for (const ip of allIps) {
      if (ip && ip !== HUB_HOSTNAME) {
        addDeviceToList(ip, "Hub (Local LAN)");
      }
    }

    // AUTO-CONNECT STRATEGY:
    // Try each available IP for a zero-latency direct connection, fall back to tunnel
    const proto = data.ssl_active ? "https" : "http";
    const port = data.port || 8000;

    for (const ip of allIps) {
      if (!ip || ip === HUB_HOSTNAME) continue;
      console.log(`🔍 Probing LAN IP ${ip} for zero-latency connection...`);
      try {
        const lanUrl = `${proto}://${ip}:${port}/api/ping`;
        const probe = await fetch(lanUrl, {
          signal: AbortSignal.timeout(1500),
          headers: { 'Accept': 'application/json' }
        });
        if (probe.ok) {
          console.log(`✅ Local LAN reached at ${ip}! Switching to 0-latency mode.`);
          const lanIdx = devices.findIndex(d => d.ip === ip);
          if (lanIdx !== -1) {
            // @ts-ignore
            globalThis.connectToPC(lanIdx);
            return;
          }
        }
      } catch (e: any) {
        console.log(`⚠️  LAN probe failed for ${ip}: ${e.message}`);
      }
    }

    // Fallback: find Hub (Primary) by hostname and connect to it
    console.log("📡 Using Cloud Tunnel / Hub (Primary)");
    const primaryIdx = devices.findIndex(d => d.ip === HUB_HOSTNAME || d.hostname === "Hub (Primary)");
    // @ts-ignore
    globalThis.connectToPC(primaryIdx !== -1 ? primaryIdx : 0);

  } catch (e) {
    console.error("Hybrid start failed:", e);
    addDeviceToList(HUB_HOSTNAME, "Hub (Primary)");
    const primaryIdx = devices.findIndex(d => d.ip === HUB_HOSTNAME || d.hostname === "Hub (Primary)");
    // @ts-ignore
    globalThis.connectToPC(primaryIdx !== -1 ? primaryIdx : 0);
  }
}


// ============================================================
// AUTO-CONNECT (Returning Users)
// ============================================================

async function tryAutoConnect(hubUrl: string, hubName: string) {
  const splash = document.getElementById('autoConnectSplash')!;
  const splashTitle = document.getElementById('autoConnectTitle')!;
  const splashSub = document.getElementById('autoConnectSub')!;
  const cancelBtn = document.getElementById('autoConnectCancelBtn')!;

  splashTitle.textContent = `Connecting to ${hubName}...`;
  splashSub.textContent = 'Reaching your last connected Hub';
  splash.classList.add('active');

  let cancelled = false;
  cancelBtn.onclick = () => {
    cancelled = true;
    splash.classList.remove('active');
    forgetHub();
    const scannerPage = document.getElementById('scannerPage')!;
    scannerPage.classList.remove('hidden');
    startNetworkScan();
  };

  // Try to validate existing token with saved hub
  const savedToken = localStorage.getItem('gesturelink_token');
  if (savedToken && savedToken !== 'undefined') {
    try {
      const vRes = await fetch(`${hubUrl}/api/validate-token`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ token: savedToken }),
        signal: AbortSignal.timeout(3000)
      });
      const vData = await vRes.json();
      if (!cancelled && vData.valid) {
        authToken = savedToken;
        splash.classList.remove('active');
        splashTitle.textContent = 'Connected!';
        addDeviceToList(new URL(hubUrl).hostname, hubName);
        // @ts-ignore
        globalThis.connectToPC(0);
        return;
      }
    } catch (_) { /* timeout or unreachable */ }
  }

  if (cancelled) return;
  splash.classList.remove('active');
  // Could not auto-connect — go to scanner
  const scannerPage = document.getElementById('scannerPage')!;
  scannerPage.classList.remove('hidden');
  const statusEl = document.getElementById('scanStatusText')!;
  statusEl.textContent = `Couldn't reach ${hubName}. Scanning...`;
  startNetworkScan();
}

// ============================================================
// SCANNER PAGE SETUP
// ============================================================
function setupScannerPage() {
  const manualBtn = document.getElementById('scanManualBtn')!;
  const cancelWaitBtn = document.getElementById('cancelPairWaitBtn')!;

  manualBtn.addEventListener('click', () => {
    const ip = prompt('Enter Hub IP address (e.g. 192.168.1.10):');
    if (ip?.trim()) addScanResult(ip.trim(), 'Manual PC');
  });

  cancelWaitBtn.addEventListener('click', () => {
    document.getElementById('pairingWaitScreen')!.classList.remove('active');
    document.getElementById('scannerFooter')!.style.display = 'flex';
    document.getElementById('scanResultList')!.style.display = 'flex';
  });

  document.getElementById('pairBtn')?.addEventListener('click', () => {
    pairingOverlay.style.display = 'none';
    document.getElementById('scannerPage')!.classList.remove('hidden');
    startNetworkScan();
  });
}

// ============================================================
// NETWORK SCAN
// ============================================================
async function startNetworkScan() {
  const statusEl = document.getElementById('scanStatusText')!;
  const resultList = document.getElementById('scanResultList')!;
  statusEl.textContent = 'Scanning network...';
  resultList.innerHTML = '';

  // Show a trust-cert helper button if we're on a HTTPS origin (Cloudflare tunnel etc.)
  // so users can approve the Hub's self-signed cert before probing
  let trustBtnShown = false;

  // Ask hub (if reachable via HUB_BASE_URL) for its list of discovered peers
  try {
    const res = await fetch(`${HUB_BASE_URL}/api/hub/info`, {
      signal: AbortSignal.timeout(2000)
    });
    if (res.ok) {
      const data = await res.json();
      addScanResult(HUB_BASE_URL, data.hostname || 'Hub PC');
      const allIps: string[] = data.all_ips || (data.lan_ip ? [data.lan_ip] : []);
      for (const ip of allIps) {
        if (ip) {
          addScanResult(`https://${ip}:${data.port || 8000}`, (data.hostname || 'Hub PC') + ' (Local)');
        }
      }
      // Show trust-cert button if on a different origin than the Hub IP
      if (allIps.length > 0 && !trustBtnShown) {
        trustBtnShown = true;
        const ip = allIps[0];
        const port = data.port || 8000;
        showTrustCertBanner(`https://${ip}:${port}`);
      }
    }
  } catch (_) { /* not reachable via current HUB_BASE_URL */ }

  // Probe LAN subnet for Hubs (192.168.x.1-254 on port 8000)
  const localIP = await detectLocalSubnet();
  const probes: Promise<void>[] = [];

  // Always probe the Windows hotspot gateway first (192.168.137.1)
  // This is the default when PC creates a mobile hotspot
  probes.push(probeHub('192.168.137.1'));

  if (localIP) {
    const parts = localIP.split('.');
    const subnet = `${parts[0]}.${parts[1]}.${parts[2]}`;
    statusEl.textContent = `Scanning ${subnet}.0/24...`;
    for (let i = 1; i <= 254; i++) {
      const ip = `${subnet}.${i}`;
      probes.push(probeHub(ip));
    }
  }
  await Promise.allSettled(probes);

  const count = document.querySelectorAll('.scan-device-card').length;
  statusEl.textContent = count > 0
    ? `Found ${count} device${count > 1 ? 's' : ''}`
    : 'No devices found. Ensure Hub is running and tap the key icon to trust the certificate.';
}

function showTrustCertBanner(hubHttpsUrl: string) {
  if (document.getElementById('trustCertBanner')) return; // don't show twice
  const list = document.getElementById('scanResultList')!;
  const banner = document.createElement('div');
  banner.id = 'trustCertBanner';
  banner.style.cssText = 'padding:12px 16px; background:rgba(255,170,0,0.12); border:1px solid rgba(255,170,0,0.3); border-radius:12px; margin-bottom:12px; font-size:0.82rem; display:flex; align-items:center; gap:10px;';
  banner.innerHTML = `
    <i class="fas fa-shield-alt" style="color:#ffaa00; font-size:1.1rem; flex-shrink:0"></i>
    <span style="flex:1">If no devices appear, tap <strong style="color:#ffaa00">Trust Hub Certificate</strong> to allow your browser to connect securely.</span>
    <button onclick="window.open('${hubHttpsUrl}', '_blank')" style="padding:6px 10px; background:#ffaa00; color:#000; border:none; border-radius:8px; font-size:0.75rem; font-weight:700; cursor:pointer; white-space:nowrap">Trust Certificate</button>
  `;
  list.parentElement?.insertBefore(banner, list);
}

async function detectLocalSubnet(): Promise<string | null> {
  return new Promise(resolve => {
    try {
      const pc = new RTCPeerConnection({ iceServers: [] });
      pc.createDataChannel('');
      pc.createOffer().then(o => pc.setLocalDescription(o));
      pc.onicecandidate = e => {
        if (!e.candidate) { pc.close(); resolve(null); return; }
        const match = e.candidate.candidate.match(/(\d+\.\d+\.\d+\.\d+)/);
        if (match && !match[1].startsWith('169.254')) {
          pc.close();
          resolve(match[1]);
        }
      };
      setTimeout(() => { pc.close(); resolve(null); }, 3000);
    } catch (_) { resolve(null); }
  });
}

async function probeHub(ip: string) {
  // Try HTTPS first (Hub uses self-signed HTTPS on port 8000)
  // Also try HTTP as fallback (may work on non-browser environments or after cert trust)
  const candidates = [
    `https://${ip}:8000/api/ping`,
    `http://${ip}:8000/api/ping`,
  ];
  for (const url of candidates) {
    try {
      const res = await fetch(url, {
        signal: AbortSignal.timeout(1500),
        headers: { 'Accept': 'application/json' }
      });
      if (res.ok) {
        const data = await res.json();
        if (data.service === 'gesturelink-hub') {
          const baseUrl = url.replace('/api/ping', '');
          addScanResult(baseUrl, data.hostname || ip);
          return; // found it, stop trying
        }
      }
    } catch (_) { /* try next */ }
  }
}

function addScanResult(url: string, name: string) {
  const list = document.getElementById('scanResultList')!;
  const existing = Array.from(list.querySelectorAll('.scan-device-card'))
    .find(el => el.getAttribute('data-url') === url);
  if (existing) return;

  const card = document.createElement('div');
  card.className = 'scan-device-card';
  card.setAttribute('data-url', url);
  card.innerHTML = `
    <div class="scan-device-icon">💻</div>
    <div class="scan-device-info">
      <div class="scan-device-name">${name}</div>
      <div class="scan-device-ip">${url}</div>
    </div>
    <i class="fas fa-chevron-right scan-device-arrow"></i>
  `;
  card.addEventListener('click', () => requestPairingFromHub(url, name));
  list.appendChild(card);
}

// ============================================================
// PAIRING REQUEST (PIN-less APK flow)
// ============================================================
async function requestPairingFromHub(hubUrl: string, hubName: string) {
  const waitScreen = document.getElementById('pairingWaitScreen')!;
  const waitTitle = document.getElementById('pairingWaitTitle')!;
  const footer = document.getElementById('scannerFooter')!;
  const resultList = document.getElementById('scanResultList')!;

  waitTitle.textContent = `Connecting to ${hubName}...`;
  waitScreen.classList.add('active');
  footer.style.display = 'none';
  resultList.style.display = 'none';

  try {
    const res = await fetch(`${hubUrl}/api/pair-request`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        hostname: `${navigator.userAgent.includes('Android') ? 'Android' : 'Mobile'} Controller`,
        device_id: getDeviceId()
      }),
      signal: AbortSignal.timeout(5000)
    });
    const data = await res.json();

    if (data.status === 'approved' && data.token) {
      // Auto-approved (trusted device)
      authToken = data.token;
      localStorage.setItem('gesturelink_token', data.token);
      saveHub(hubUrl, hubName);
      document.getElementById('scannerPage')!.classList.add('hidden');
      addDeviceToList(new URL(hubUrl).hostname, hubName);
      // @ts-ignore
      globalThis.connectToPC(0);
    } else if (data.status === 'pending') {
      waitTitle.textContent = 'Waiting for approval on PC...';
      pollForApproval(data.request_id, hubUrl, hubName);
    } else {
      throw new Error(data.error || 'Rejected');
    }
  } catch (e: any) {
    waitScreen.classList.remove('active');
    footer.style.display = 'flex';
    resultList.style.display = 'flex';
    alert(`Connection failed: ${e.message || 'Could not reach Hub'}`);
  }
}

async function pollForApproval(reqId: string, hubUrl: string, hubName: string) {
  const interval = setInterval(async () => {
    try {
      const res = await fetch(`${hubUrl}/api/pair/status/${reqId}`, {
        signal: AbortSignal.timeout(3000)
      });
      const data = await res.json();
      if (data.status === 'approved' && data.token) {
        clearInterval(interval);
        authToken = data.token;
        localStorage.setItem('gesturelink_token', data.token);
        saveHub(hubUrl, hubName);
        document.getElementById('scannerPage')!.classList.add('hidden');
        addDeviceToList(new URL(hubUrl).hostname, hubName);
        triggerHaptic(ImpactStyle.Heavy);
        // @ts-ignore
        globalThis.connectToPC(0);
      } else if (data.status === 'rejected') {
        clearInterval(interval);
        document.getElementById('pairingWaitScreen')!.classList.remove('active');
        document.getElementById('scannerFooter')!.style.display = 'flex';
        document.getElementById('scanResultList')!.style.display = 'flex';
        alert('Connection denied by the PC user.');
      }
    } catch (_) { /* keep polling */ }
  }, 2000);

  // Stop after 60 seconds
  setTimeout(() => {
    clearInterval(interval);
    document.getElementById('pairingWaitScreen')!.classList.remove('active');
    document.getElementById('scannerFooter')!.style.display = 'flex';
    document.getElementById('scanResultList')!.style.display = 'flex';
  }, 60000);
}

function getDeviceId(): string {
  let id = localStorage.getItem('gesturelink_device_id');
  if (!id) {
    id = Math.random().toString(36).slice(2) + Date.now().toString(36);
    localStorage.setItem('gesturelink_device_id', id);
  }
  return id;
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
  forgetHub(); // Clear saved hub so scanner shows on next launch
  
  // Clear only the PIN parameter to prevent auto-pairing on reload, but keep the Hub address
  const url = new URL(window.location.href);
  url.searchParams.delete("pin");
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
        if (isCommandChannelOpen()) {
          sendCommand({ type: 'zoom', delta: pinchDelta });
        }
        lastPinchDist = currentDist;
      } else {
        const scrollDy = currentMidY - twoFingerMidY;
        if (Math.abs(scrollDy) > 2 && isCommandChannelOpen()) {
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
      if (isCommandChannelOpen()) {
        sendCommand({ type: 'click_up', button: 'left' });
      }
    } else if (duration < 250 && !isMoving) {
      if (isCommandChannelOpen() && maxFingers < 3) {
        const button = maxFingers === 2 ? 'right' : 'left';
        sendCommand({ type: 'click', button });
        triggerHaptic(maxFingers === 2 ? ImpactStyle.Medium : ImpactStyle.Light);
      }
      lastTapTime = now;
    } else if (duration >= 1000 && !isMoving) {
      if (isCommandChannelOpen() && (maxFingers === 3 || maxFingers === 4)) {
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

  leftArrowBtn.onclick = () => {
    sendHotkey(['left']);
  };

  rightArrowBtn.onclick = () => {
    sendHotkey(['right']);
  };

  keyboardInput.addEventListener('keydown', (e) => {
    if (!isCommandChannelOpen()) return;
    if (["Backspace", "Enter", "Tab", "Escape"].includes(e.key)) {
      sendCommand({ type: 'key', key: e.key });
      e.preventDefault();
    }
  });

  keyboardInput.addEventListener('input', () => {
    if (!isCommandChannelOpen()) return;
    const val = keyboardInput.value;
    if (val.length > 0) {
      sendCommand({ type: 'key', key: val });
      keyboardInput.value = '';
    }
  });
}

function isCommandChannelOpen(): boolean {
  if (dataChannel && dataChannel.readyState === 'open') return true;
  if (activePC?.ws && activePC.ws.readyState === 1) return true;
  return false;
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

function setupScanBtn() {
  pairBtn.onclick = async () => {
    // Hide the overlay
    pairingOverlay.classList.add('hidden');
    setTimeout(() => pairingOverlay.style.display = 'none', 500);
    
    // Switch to Devices tab
    const devicesTabBtn = document.querySelector('.nav-item[onclick*="devices"]') as HTMLElement;
    if (devicesTabBtn) devicesTabBtn.click();
    
    // Trigger network scan
    const scanBtn = document.getElementById("scanBtn");
    if (scanBtn) scanBtn.click();
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

  // Build a list of URLs to try: tunnel URL first, then any LAN IPs from hub/info
  const pairUrls: string[] = [baseUrl.replace(/\/$/, '')];

  // Try to get LAN IPs from hub info (works when accessible via tunnel)
  try {
    const infoRes = await fetch(`${baseUrl.replace(/\/$/, '')}/api/hub/info`, {
      signal: AbortSignal.timeout(2000)
    });
    if (infoRes.ok) {
      const info = await infoRes.json();
      const proto = info.ssl_active ? 'https' : 'http';
      const port = info.port || 8000;
      const allIps: string[] = info.all_ips || (info.lan_ip ? [info.lan_ip] : []);
      for (const ip of allIps) {
        if (ip) pairUrls.push(`${proto}://${ip}:${port}`);
      }
      // Always try the Windows hotspot default gateway
      pairUrls.push(`https://192.168.137.1:${port}`);
      pairUrls.push(`http://192.168.137.1:${port}`);
    }
  } catch (_) { /* tunnel may not be reachable if on local-only network */ }

  // Try each URL until one succeeds
  for (const tryUrl of pairUrls) {
    try {
      const controller = new AbortController();
      const timeoutId = setTimeout(() => controller.abort(), 2500);
      const res = await fetch(`${tryUrl}/api/pair`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ pin, hostname: "Mobile Controller" }),
        signal: controller.signal
      });
      clearTimeout(timeoutId);
      const data = await res.json();

      if (data.status === "approved" && data.token) {
        // Save whichever URL worked as the hub URL
        localStorage.setItem("gesturelink_hub_url", tryUrl);
        finalizePairing(data.token);
        return;
      } else if (data.status === "pending") {
        // Save whichever URL worked
        localStorage.setItem("gesturelink_hub_url", tryUrl);
        pairStatusText.innerHTML = '<i class="fas fa-spinner fa-spin" style="margin-right: 8px;"></i>Waiting for Hub approval...';
        pollPairingStatus(data.request_id);
        return;
      } else {
        pairError.style.display = 'block';
        document.getElementById('pairBtn')!.style.display = 'block';
        triggerHaptic(ImpactStyle.Medium);
        pairStatusText.textContent = "";
        return;
      }
    } catch (_) {
      // This URL failed, try the next one
      console.log(`[autoPair] ${tryUrl} unreachable, trying next...`);
    }
  }

  // All URLs failed — fall back to Firebase WebRTC directly
  console.log("All pair URLs failed. Falling back to Firebase WebRTC directly.");
  finalizePairing("firebase-webrtc-only");
}


function hidePairingOverlay() {
  // Hide legacy QR overlay
  pairingOverlay.classList.add('hidden');
  setTimeout(() => pairingOverlay.style.display = 'none', 500);
  // Also hide scanner page (covers all startup flows)
  document.getElementById('scannerPage')?.classList.add('hidden');
  document.getElementById('autoConnectSplash')?.classList.remove('active');
  // Navigate to control tab
  const controlNavItem = document.querySelector('.nav-item[data-tab="control"]') as HTMLElement;
  if (controlNavItem) controlNavItem.click();
}

function finalizePairing(token: string) {
  localStorage.setItem("gesturelink_token", token);
  authToken = token;
  pairStatusText.innerHTML = '<i class="fas fa-spinner fa-spin" style="margin-right: 8px;"></i>Connecting to PC...';
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
