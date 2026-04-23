import './style.css'
import { ImpactStyle } from '@capacitor/haptics';

// --- State ---
let activePC: any = null;
let devices: any[] = [];
let authToken = localStorage.getItem("gesturelink_token");
let hapticsEnabled = localStorage.getItem("gesturelink_haptics") !== "false";
let stream: MediaStream | null = null;
let sendingFrame = false;
let cameraEnabled = true;

// WebRTC State
let peerConn: RTCPeerConnection | null = null;
let myPeerId = Math.random().toString(36).substring(7);

// DOM Elements
const pairingOverlay = document.getElementById("pairingOverlay")!;
const pinInputs = document.querySelectorAll<HTMLInputElement>(".pin-box");
const pairBtn = document.getElementById("pairBtn")!;
const pairError = document.getElementById("pairError")!;
const connBadge = document.getElementById("connBadge")!;
const activeDeviceName = document.getElementById("activeDeviceName")!;
const activeDeviceIP = document.getElementById("activeDeviceIP")!;
const gestureText = document.getElementById("gestureText")!;
const video = document.getElementById("video") as HTMLVideoElement;
const touchZone = document.getElementById("touchZone")!;
const deviceList = document.getElementById("deviceList")!;
const navItems = document.querySelectorAll(".nav-item");
const tabContents = document.querySelectorAll(".tab-content");

// --- Initialization ---
async function init() {
  setupNav();
  setupTouchpad();
  setupPinInputs();
  
  // Auto-pairing from QR
  const urlParams = new URLSearchParams(globalThis.location.search);
  const autoPin = urlParams.get('pin');
  if (autoPin?.length === 6 && !authToken) {
    await autoPair(autoPin);
    authToken = localStorage.getItem("gesturelink_token");
  }

  // Overlay management
  if (authToken && authToken !== "undefined") {
    pairingOverlay.classList.add('hidden');
    setTimeout(() => pairingOverlay.style.display = 'none', 500);
    startApp();
  } else {
    pairingOverlay.style.display = 'flex';
  }
  
  // Toggles
  document.getElementById("hapticToggle")?.addEventListener('change', (e: any) => {
    hapticsEnabled = e.target.checked;
    localStorage.setItem("gesturelink_haptics", hapticsEnabled.toString());
    if (hapticsEnabled) triggerHaptic();
  });

  const cameraToggle = document.getElementById("cameraToggle") as HTMLInputElement;
  const cameraOverlay = document.getElementById("cameraOffOverlay");
  cameraToggle?.addEventListener('change', async (e: any) => {
    cameraEnabled = e.target.checked;
    if (cameraEnabled) {
      if (cameraOverlay) cameraOverlay.style.display = 'none';
      await initCamera();
    } else {
      if (cameraOverlay) cameraOverlay.style.display = 'flex';
      stopCamera();
    }
  });

  document.getElementById("saveBtn")?.addEventListener('click', saveSettings);

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
      Object.entries(discovered).forEach(([ip, hostname]) => addDeviceToList(ip, hostname as string));
      if (Object.keys(discovered).length === 0) {
        deviceList.innerHTML = '<div style="padding: 20px; text-align: center; color: var(--text-secondary); font-size: 0.85rem;">No agents found on network</div>';
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
      <div>
        <div style="font-weight: 600; font-size: 0.9rem;">${d.hostname}</div>
        <div style="font-size: 0.7rem; color: var(--text-secondary);">${d.ip}</div>
      </div>
      <button id="connect-btn-${i}" onclick="globalThis.connectToPC(${i})" class="device-connect-btn ${isActive ? 'connected' : ''}">${isActive ? '✓ Active' : 'Connect'}</button>
    </div>`;
  }).join("");
}

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
  const wsUrl = `${proto}//${location.host}/ws?token=${authToken}`;

  try {
    if (d.ws) d.ws.close();
    const ws = new WebSocket(wsUrl);
    ws.binaryType = "arraybuffer";
    
    ws.onopen = () => {
      d.ws = ws;
      activatePC(d);
      startVisionLoop();
      initWebRTC();
      renderDeviceList(); // re-render to show 'Active' state
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
        if (data.gesture) {
          const old = gestureText.textContent;
          gestureText.textContent = data.gesture;
          if (data.gesture !== old && data.gesture !== 'IDLE') triggerHaptic(ImpactStyle.Light);
        }
      } catch (_) {}
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

function activatePC(d: any) {
  activePC = d;
  activeDeviceName.textContent = d.hostname;
  activeDeviceIP.textContent = d.ip;
  connBadge.textContent = "ONLINE";
  connBadge.classList.add('online');
  // Bug 2: show disconnect button when connected
  document.getElementById('disconnectBtn')?.classList.add('visible');
  syncSettings();
}

async function syncSettings() {
  if (!activePC) return;
  try {
    const [shortcutsRes, setRes] = await Promise.all([
      fetch(`${location.origin}/api/shortcuts`).then(r => r.json()),
      fetch(`${location.origin}/api/settings`).then(r => r.json())
    ]);
    const sens = document.getElementById("sensRange") as HTMLInputElement;
    if (sens) sens.value = setRes.sensitivity || 50;
    renderShortcuts(shortcutsRes.shortcuts || {});
  } catch (_) { }
}

function renderShortcuts(shortcuts: Record<string, string>) {
  // Update the shortcut key display from server data
  document.querySelectorAll<HTMLElement>('[data-shortcut]').forEach(el => {
    const key = el.dataset.shortcut!;
    if (shortcuts[key]) el.textContent = shortcuts[key];
  });
}

async function saveSettings() {
  if (!activePC) return;
  try {
    const sens = (document.getElementById("sensRange") as HTMLInputElement).value;
    await fetch(`${location.origin}/api/settings`, {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ sensitivity: Number.parseInt(sens) })
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
  try {
    const res = await fetch(`${location.origin}/api/pair`, {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ pin })
    });
    const data = await res.json();
    if (data.token) {
      localStorage.setItem("gesturelink_token", data.token);
      authToken = data.token;
      pairingOverlay.classList.add('hidden');
      setTimeout(() => pairingOverlay.style.display = 'none', 500);
      triggerHaptic(ImpactStyle.Heavy);
      startApp();
    } else {
      pairError.style.opacity = '1';
      triggerHaptic(ImpactStyle.Medium);
      setTimeout(() => pairError.style.opacity = '0', 3000);
    }
  } catch (e) { pairError.style.opacity = '1'; }
}

async function startApp() {
  await initCamera();
  addDeviceToList(location.hostname, "Hub (Primary)");
  // @ts-ignore
  globalThis.connectToPC(0);
}

async function triggerHaptic(style: ImpactStyle = ImpactStyle.Light) {
  if (!hapticsEnabled) return;
  if (navigator.vibrate) navigator.vibrate(style === ImpactStyle.Heavy ? 40 : 15);
}

async function initCamera() {
  if (!cameraEnabled) return;
  try {
    stream = await navigator.mediaDevices.getUserMedia({ video: { facingMode: "user", width: 640, height: 480 } });
    video.srcObject = stream;
  } catch (e) { console.error("Camera error", e); }
}

function stopCamera() {
  if (stream) {
    stream.getTracks().forEach(track => track.stop());
    stream = null;
    video.srcObject = null;
  }
}

function startVisionLoop() {
  const canvas = document.createElement("canvas");
  const ctx = canvas.getContext("2d")!;
  setInterval(() => {
    if (!cameraEnabled || !activePC?.ws || activePC.ws.readyState !== 1 || sendingFrame || !stream) return;
    canvas.width = 640; canvas.height = 480;
    ctx.drawImage(video, 0, 0, 640, 480);
    sendingFrame = true;
    canvas.toBlob(b => {
      sendingFrame = false;
      if (b) b.arrayBuffer().then(buf => activePC.ws.send(buf));
    }, "image/jpeg", 0.5);
  }, 40);
}

function setupTouchpad() {
  let lastX = 0, lastY = 0, startTime = 0;
  touchZone.addEventListener('touchstart', (e: any) => {
    lastX = e.touches[0].clientX; lastY = e.touches[0].clientY;
    startTime = Date.now();
    e.preventDefault();
  }, { passive: false });

  touchZone.addEventListener('touchmove', (e: any) => {
    const dx = e.touches[0].clientX - lastX;
    const dy = e.touches[0].clientY - lastY;
    lastX = e.touches[0].clientX; lastY = e.touches[0].clientY;
    if (activePC?.ws?.readyState === 1) activePC.ws.send(JSON.stringify({ type: 'move', dx, dy }));
    e.preventDefault();
  }, { passive: false });

  touchZone.addEventListener('touchend', (e: any) => {
    if (Date.now() - startTime < 200) {
      const fingers = e.changedTouches.length;
      if (activePC?.ws?.readyState === 1) {
        activePC.ws.send(JSON.stringify({ type: 'click', button: fingers === 1 ? 'left' : 'right' }));
        triggerHaptic(fingers === 1 ? ImpactStyle.Light : ImpactStyle.Medium);
      }
    }
  });
}

init();
