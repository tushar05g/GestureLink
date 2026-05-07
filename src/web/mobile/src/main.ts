import './style.css'
import { ImpactStyle } from '@capacitor/haptics';

// --- State ---
let activePC: any = null;
let devices: any[] = [];
let authToken = localStorage.getItem("gesturelink_token");
let hapticsEnabled = localStorage.getItem("gesturelink_haptics") !== "false";
// WebRTC State
let peerConn: RTCPeerConnection | null = null;
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

// --- Initialization ---
async function init() {
  setupNav();
  setupTouchpad();
  setupPinInputs();
  setupShortcuts();

  // Issue 8 fix: removed duplicate logoutBtn.onclick here.
  // The event listener at line ~105 handles logout with a confirm dialog.

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
      const vRes = await fetch(`${location.origin}/api/validate-token`, {
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

  // Toggles
  document.getElementById("hapticToggle")?.addEventListener('change', (e: any) => {
    hapticsEnabled = e.target.checked;
    localStorage.setItem("gesturelink_haptics", hapticsEnabled.toString());
    if (hapticsEnabled) triggerHaptic();
  });

  const pcCameraToggle = document.getElementById("pcCameraToggle") as HTMLInputElement;
  pcCameraToggle?.addEventListener('change', async (e: any) => {
    if (!activePC) {
      alert("Connect to a PC first!");
      pcCameraToggle.checked = false;
      return;
    }
    try {
      const active = e.target.checked;
      const res = await fetch(`${location.origin}/api/hub/camera/toggle?target=${activePC.ip}`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ active })
      });
      const data = await res.json();
      if (!data.ok) throw new Error(data.error);
      if (remoteGestureStatus) remoteGestureStatus.textContent = active ? "CAMERA ON" : "CAMERA OFF";
    } catch (err) {
      alert(`Failed to toggle camera: ${err}`);
      pcCameraToggle.checked = !e.target.checked;
    }
  });

  // Vision Mode Buttons
  const modeBtns = document.querySelectorAll(".mode-btn");
  modeBtns.forEach(btn => {
    btn.addEventListener('click', async () => {
      const mode = parseInt((btn as HTMLElement).dataset.mode || "0");
      try {
        const res = await fetch(`${location.origin}/api/hub/mode`, {
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

  // Bug 4: Scan Network via discovered endpoint
  const scanBtn = document.getElementById("scanBtn");
  const scanRipple = document.getElementById("scanRipple");
  scanBtn?.addEventListener('click', async () => {
    scanBtn.setAttribute('disabled', 'true');
    scanRipple?.classList.add('active');
    try {
      const res = await fetch(`${location.origin}/api/discovered`);
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
        // Just re-render if we found things but they were already there
        renderDeviceList();
      }
    } finally {
      scanBtn.removeAttribute('disabled');
      scanRipple?.classList.remove('active');
    }
  });

  // Load shortcuts from server
  try {
    const r = await fetch(`${location.origin}/api/shortcuts`);
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
    deviceList.innerHTML = '<div style="padding: 20px; text-align: center; color: var(--text-secondary); font-size: 0.85rem;">Tap Scan to discover PCs</div>';
    return;
  }
  deviceList.innerHTML = devices.map((d, i) => {
    const isActive = activePC?.ip === d.ip;
    return `
    <div id="device-card-${i}" style="display: flex; justify-content: space-between; align-items: center; padding: 14px; background: var(--glass); border-radius: 14px; border: 1px solid ${isActive ? 'rgba(0,255,149,0.3)' : 'var(--border)'}; transition: all 0.2s;">
      <div style="flex:1; min-width:0;">
        <div style="font-weight: 600; font-size: 0.9rem; display:flex; align-items:center; gap:6px;">
          <span id="device-name-${i}" style="cursor:pointer; border-bottom: 1px dashed rgba(255,255,255,0.2);" onclick="globalThis.renameDevice(${i})" title="Tap to rename">${d.hostname}</span>
          <span style="font-size:0.6rem; color:var(--text-secondary); opacity:0.5;">✏️</span>
        </div>
        <div style="font-size: 0.7rem; color: var(--text-secondary);">${d.ip}</div>
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
    await fetch(`${location.origin}/api/devices/rename`, {
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

  // Bug 5: animate connecting state
  const connectBtn = document.getElementById(`connect-btn-${i}`);
  if (connectBtn) {
    connectBtn.textContent = 'Connecting';
    connectBtn.classList.add('connecting');
  }

  const proto = location.protocol === "https:" ? "wss:" : "ws:";
  const wsUrl = `${proto}//${location.host}/ws?token=${authToken}&target=${d.ip}`;

  try {
    if (d.ws) d.ws.close();
    const ws = new WebSocket(wsUrl);
    ws.binaryType = "arraybuffer";

    ws.onopen = () => {
      d.ws = ws;
      activatePC(d);
      initWebRTC();
      renderDeviceList();
    };

    ws.onerror = (err) => {
      console.error("WS Connection Error", err);
      alert(`⚠️ Could not connect to ${d.hostname}. Ensure the Agent is running and on the same network.`);
      renderDeviceList(); // Reset UI state
    };

    ws.onclose = (event) => {
      d.ws = null;
      if (activePC === d) {
        connBadge.textContent = "OFFLINE";
        connBadge.classList.remove('online');
        // Bug 2: if token rejected (4003), force re-pair
        if (event.code === 4003 || event.code === 1008) {
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
        // Handle relay error from Hub (agent unreachable)
        if (data.type === 'error') {
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
  } catch (_) {
    if (connectBtn) {
      connectBtn.textContent = 'Connect';
      connectBtn.classList.remove('connecting');
    }
    alert("Connection Failed");
  }
};

async function initWebRTC() {
  if (peerConn) peerConn.close();
  peerConn = new RTCPeerConnection({ iceServers: [{ urls: "stun:stun.l.google.com:19302" }] });

  peerConn.onicecandidate = (e) => {
    if (e.candidate) sendSignal({ type: "candidate", candidate: e.candidate });
  };

  const offer = await peerConn.createOffer();
  await peerConn.setLocalDescription(offer);
  sendSignal({ type: "offer", sdp: offer.sdp });

  // Signaling loop
  (async () => {
    while (peerConn) {
      try {
        const res = await fetch(`/api/webrtc/signal/${myPeerId}`);
        const data = await res.json();
        if (data.ok && data.signal) {
          if (data.signal.type === "answer") await peerConn.setRemoteDescription(new RTCSessionDescription(data.signal));
          else if (data.signal.type === "candidate") await peerConn.addIceCandidate(new RTCIceCandidate(data.signal.candidate));
        }
      } catch (e) { }
      await new Promise(r => setTimeout(r, 2000));
    }
  })();
}

async function sendSignal(data: any) {
  await fetch(`/api/webrtc/signal/hub_pc`, {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ from: myPeerId, ...data })
  });
}

async function activatePC(d: any) {
  activePC = d;
  activeDeviceName.textContent = d.hostname;
  activeDeviceIP.textContent = d.ip;
  connBadge.textContent = "ONLINE";
  connBadge.classList.add('online');
  // Bug 2: show disconnect button when connected
  document.getElementById('disconnectBtn')?.classList.add('visible');
  syncSettings();

  // Initial mode and camera sync
  try {
    const [modeRes, camRes] = await Promise.all([
      fetch(`${location.origin}/api/hub/mode`).then(r => r.json()),
      fetch(`${location.origin}/api/hub/camera/status?target=${d.ip}`).then(r => r.json()).catch(() => ({ active: false }))
    ]);

    const modeBtns = document.querySelectorAll(".mode-btn");
    modeBtns.forEach(b => {
      if (parseInt((b as HTMLElement).dataset.mode || "0") === modeRes.mode) b.classList.add('active');
      else b.classList.remove('active');
    });

    const pcCameraToggle = document.getElementById("pcCameraToggle") as HTMLInputElement;
    if (pcCameraToggle) pcCameraToggle.checked = camRes.active;
    const remoteGestureStatus = document.getElementById("remoteGestureStatus");
    if (remoteGestureStatus) remoteGestureStatus.textContent = camRes.active ? "CAMERA ON" : "CAMERA OFF";

  } catch (_) { }
}

async function syncSettings() {
  if (!activePC) return;
  try {
    const [shortcutsRes, setRes] = await Promise.all([
      fetch(`${location.origin}/api/shortcuts`).then(r => r.json()),
      fetch(`${location.origin}/api/settings`).then(r => r.json())
    ]);
    const sens = document.getElementById("sensRange") as HTMLInputElement;
    const scroll = document.getElementById("scrollRange") as HTMLInputElement;
    if (sens) sens.value = setRes.sensitivity || 50;
    if (scroll) scroll.value = setRes.scroll_speed || 12;
    renderShortcuts(shortcutsRes.shortcuts || {});
  } catch (_) { }
}

function renderShortcuts(shortcuts: Record<string, any>) {
  // Update the shortcut key display from server data
  document.querySelectorAll<HTMLElement>('[data-shortcut]').forEach(el => {
    const key = el.dataset.shortcut!;
    if (shortcuts[key]) {
      const binding = shortcuts[key];
      el.textContent = binding.target || "None";
    }
  });
}

// @ts-ignore
globalThis.editShortcut = async (slot: string) => {
  const modal = document.getElementById("appModal")!;
  const select = document.getElementById("appSelect") as HTMLSelectElement;
  const customInput = document.getElementById("customTarget") as HTMLInputElement;
  const saveBtn = document.getElementById("saveAppShortcut")!;
  const closeBtn = document.getElementById("closeAppModal")!;

  // Reset modal
  select.innerHTML = '<option value="">-- Choose from device --</option>';
  customInput.value = "";
  modal.style.display = 'flex';

  // Fetch apps from active device via Hub proxy
  try {
    const ip = activePC?.ip || location.hostname;
    const res = await fetch(`${location.origin}/api/apps?ip=${ip}`);
    const data = await res.json();
    (data.apps || []).forEach((app: any) => {
      const opt = document.createElement("option");
      opt.value = app.target;
      opt.textContent = app.name;
      select.appendChild(opt);
    });
  } catch (e) { console.error("Failed to fetch apps", e); }

  closeBtn.onclick = () => modal.style.display = 'none';

  saveBtn.onclick = async () => {
    const target = customInput.value || select.value;
    if (!target) return;

    try {
      const res = await fetch(`${location.origin}/api/shortcuts`);
      const data = await res.json();
      const shortcuts = data.shortcuts || {};

      shortcuts[slot] = { target, mode: target.startsWith('http') ? 'url' : 'app' };

      await fetch(`${location.origin}/api/shortcuts`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ shortcuts })
      });

      renderShortcuts(shortcuts);
      triggerHaptic(ImpactStyle.Medium);
      modal.style.display = 'none';
    } catch (e) { alert("Failed to update shortcut"); }
  };
};

async function saveSettings() {
  if (!activePC) return;
  try {
    const sens = (document.getElementById("sensRange") as HTMLInputElement).value;
    const scroll = (document.getElementById("scrollRange") as HTMLInputElement).value;
    await fetch(`${location.origin}/api/settings`, {
      method: "POST", headers: { "Content-Type": "application/json" },
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
  pinInputs.forEach((input, i) => {
    input.addEventListener('input', () => {
      if (input.value && i < 5) pinInputs[i + 1].focus();
    });
    input.addEventListener('keydown', (e) => {
      if (e.key === 'Backspace' && !input.value && i > 0) pinInputs[i - 1].focus();
    });
  });
  pairBtn.onclick = async () => {
    const pin = Array.from(pinInputs).map(i => i.value).join("");
    await autoPair(pin);
  };
}

async function autoPair(pin: string) {
  if (pin.length !== 6) return;
  pairStatusText.textContent = "Verifying PIN...";

  try {
    const res = await fetch(`${location.origin}/api/pair`, {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ pin, hostname: "Mobile Controller" })
    });
    const data = await res.json();

    if (data.status === "approved" && data.token) {
      finalizePairing(data.token);
    } else if (data.status === "pending") {
      pairStatusText.textContent = "Waiting for Hub approval...";
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
      const res = await fetch(`${location.origin}/api/pair/status/${reqId}`);
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

// Issue 6 fix: setupShortcuts only does initial load of labels.
// editShortcut is handled by the globalThis.editShortcut below (line ~326).
async function setupShortcuts() {
  try {
    const res = await fetch(`${location.origin}/api/shortcuts`);
    const data = await res.json();
    renderShortcuts(data.shortcuts || {});
  } catch (_) { /* use defaults */ }
}

let activeShortcutSlot = "";
(globalThis as any).editShortcut = async (slot: string) => {
  activeShortcutSlot = slot;
  appModal.style.display = 'flex';

  // Load apps from active PC
  try {
    const targetIp = activePC ? activePC.ip : "";
    const res = await fetch(`${location.origin}/api/apps?ip=${targetIp}`);
    const data = await res.json();
    appSelect.innerHTML = '<option value="">-- Choose from device --</option>';
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
    // Issue 7 fix: use the correct {slot, target} payload the server expects
    const res = await fetch(`${location.origin}/api/shortcuts`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ shortcuts: { [activeShortcutSlot]: { target, enabled: true } } })
    });
    if (!res.ok) throw new Error("Server error");

    // Update UI label
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
  addDeviceToList(location.hostname, "Hub (Primary)");
  // @ts-ignore
  globalThis.connectToPC(0);
}

// Issue 5 (client-side): Call /api/logout to revoke token server-side before clearing state.
async function logout() {
  const token = localStorage.getItem("gesturelink_token");
  if (token) {
    try {
      await fetch(`${location.origin}/api/logout`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ token })
      });
    } catch (_) { /* best effort */ }
  }
  localStorage.removeItem("gesturelink_token");
  localStorage.removeItem("gesturelink_ip");
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
  let twoFingerStarted = false;  // lock state: true when 2+ fingers detected
  let twoFingerMidY = 0;          // midpoint Y of two fingers for scroll

  touchZone.addEventListener('touchstart', (e: any) => {
    maxFingers = Math.max(maxFingers, e.touches.length);
    lastX = e.touches[0].clientX; lastY = e.touches[0].clientY;
    startTime = Date.now();
    isMoving = false;

    if (e.touches.length === 2) {
      twoFingerStarted = true;  // lock: no cursor moves allowed this gesture
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
      if (activePC?.ws?.readyState === 1) {
        activePC.ws.send(JSON.stringify({ type: 'click_down', button: 'left' }));
      }
    }
    e.preventDefault();
  }, { passive: false });

  touchZone.addEventListener('touchmove', (e: any) => {
    isMoving = true;
    maxFingers = Math.max(maxFingers, e.touches.length);

    // As soon as a second finger appears mid-gesture, lock into scroll mode
    if (e.touches.length >= 2 && !twoFingerStarted) {
      twoFingerStarted = true;
      twoFingerMidY = (e.touches[0].clientY + e.touches[1].clientY) / 2;
      lastPinchDist = Math.hypot(
        e.touches[0].clientX - e.touches[1].clientX,
        e.touches[0].clientY - e.touches[1].clientY
      );
    }

    if (twoFingerStarted && e.touches.length >= 2) {
      // --- Two-Finger Zone: scroll or pinch-zoom ---
      const currentDist = Math.hypot(
        e.touches[0].clientX - e.touches[1].clientX,
        e.touches[0].clientY - e.touches[1].clientY
      );
      const currentMidY = (e.touches[0].clientY + e.touches[1].clientY) / 2;
      const pinchDelta = currentDist - lastPinchDist;

      if (Math.abs(pinchDelta) > 8) {
        // Pinch-to-zoom when fingers spread/close significantly
        if (activePC?.ws?.readyState === 1) {
          activePC.ws.send(JSON.stringify({ type: 'zoom', delta: pinchDelta }));
        }
        lastPinchDist = currentDist;
      } else {
        // Two-finger scroll — always active when not pinching
        const scrollDy = currentMidY - twoFingerMidY;
        if (Math.abs(scrollDy) > 2 && activePC?.ws?.readyState === 1) {
          activePC.ws.send(JSON.stringify({ type: 'scroll', dy: scrollDy * -1.5 }));
        }
      }
      twoFingerMidY = currentMidY;
      lastPinchDist = currentDist;

    } else if (!twoFingerStarted && e.touches.length === 1 && !isDragging) {
      // --- Single finger: cursor move ---
      const now = Date.now();
      if (now - lastMoveTime < 16) return;

      const dx = e.touches[0].clientX - lastX;
      const dy = e.touches[0].clientY - lastY;
      lastX = e.touches[0].clientX; lastY = e.touches[0].clientY;

      if (activePC?.ws?.readyState === 1) {
        activePC.ws.send(JSON.stringify({ type: 'move', dx, dy }));
        lastMoveTime = now;
      }
    } else if (!twoFingerStarted && e.touches.length === 1 && isDragging) {
      // --- Single finger drag (left-mouse held) ---
      const dx = e.touches[0].clientX - lastX;
      const dy = e.touches[0].clientY - lastY;
      lastX = e.touches[0].clientX; lastY = e.touches[0].clientY;
      if (activePC?.ws?.readyState === 1) {
        activePC.ws.send(JSON.stringify({ type: 'move', dx, dy }));
      }
    } else if (e.touches.length === 2) {
      const currentDist = Math.hypot(
        e.touches[0].clientX - e.touches[1].clientX,
        e.touches[0].clientY - e.touches[1].clientY
      );

      const delta = currentDist - lastPinchDist;
      if (Math.abs(delta) > 5) {
        isPinching = true;
        if (activePC?.ws?.readyState === 1) {
          activePC.ws.send(JSON.stringify({ type: 'zoom', delta }));
        }
        lastPinchDist = currentDist;
      } else if (!isPinching) {
        // Two-Finger Scroll as fallback if not pinching
        const currentY = (e.touches[0].clientY + e.touches[1].clientY) / 2;
        const dy = currentY - lastY;
        lastY = currentY;
        if (activePC?.ws?.readyState === 1) {
          activePC.ws.send(JSON.stringify({ type: 'scroll', dy: dy * -1.5 }));
        }
      }
    }
    e.preventDefault();
  }, { passive: false });

  touchZone.addEventListener('touchend', (e: any) => {
    if (e.touches.length > 0) return; // Wait until the last finger is lifted to prevent double-taps

    const now = Date.now();
    const duration = now - startTime;

    if (isDragging) {
      isDragging = false;
      if (activePC?.ws?.readyState === 1) {
        activePC.ws.send(JSON.stringify({ type: 'click_up', button: 'left' }));
      }
    } else if (duration < 250 && !isMoving) {
      // Standard Tap (1 or 2 fingers)
      if (activePC?.ws?.readyState === 1 && maxFingers < 3) {
        const button = maxFingers === 2 ? 'right' : 'left';
        activePC.ws.send(JSON.stringify({ type: 'click', button }));
        triggerHaptic(maxFingers === 2 ? ImpactStyle.Medium : ImpactStyle.Light);
      }
      lastTapTime = now;
    } else if (duration >= 1000 && !isMoving) {
      // Long Hold (3 or 4 fingers)
      if (activePC?.ws?.readyState === 1 && (maxFingers === 3 || maxFingers === 4)) {
        activePC.ws.send(JSON.stringify({ type: 'shortcut', slot: `touch_${maxFingers}_finger` }));
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

init();
