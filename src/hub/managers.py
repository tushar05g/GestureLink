import asyncio
import json
import logging
import secrets
import socket
import uuid
import random
from pathlib import Path
from typing import Dict, Set, Optional

logger = logging.getLogger("gesture_control.remote")

def detect_lan_ip() -> str:
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("8.8.8.8", 80))
            return str(s.getsockname()[0])
    except OSError:
        return "127.0.0.1"

class SecurityManager:
    def __init__(self, security_file: Path):
        self.security_file = security_file
        self.trusted_ips: Set[str] = set()
        self.blocked_ips: Set[str] = set()
        # request_id -> {ip, hostname, timestamp}
        self.pending_requests: Dict[str, dict] = {}
        # ip -> token (stored here until phone polls)
        self.approved_tokens: Dict[str, str] = {}
        self.load()

    def save(self) -> None:
        try:
            with open(self.security_file, "w") as f:
                json.dump({
                    "trusted": list(self.trusted_ips),
                    "blocked": list(self.blocked_ips)
                }, f)
        except Exception as e:
            logger.error("Failed to save security settings: %s", e)

    def load(self) -> None:
        if self.security_file.exists():
            try:
                with open(self.security_file, "r") as f:
                    data = json.load(f)
                    self.trusted_ips.update(data.get("trusted", []))
                    self.blocked_ips.update(data.get("blocked", []))
                    logger.info("Loaded security: %d trusted, %d blocked", len(self.trusted_ips), len(self.blocked_ips))
            except Exception as e:
                logger.error("Failed to load security settings: %s", e)

    def add_pending_request(self, ip: str, hostname: str) -> str:
        req_id = str(uuid.uuid4())[:8]
        self.pending_requests[req_id] = {
            "ip": ip,
            "hostname": hostname,
            "id": req_id
        }
        return req_id

    def approve_request(self, req_id: str, token: str) -> bool:
        req = self.pending_requests.pop(req_id, None)
        if req:
            ip = req["ip"]
            self.trusted_ips.add(ip)
            self.approved_tokens[ip] = token
            self.save()
            return True
        return False

    def reject_request(self, req_id: str) -> None:
        req = self.pending_requests.pop(req_id, None)
        if req:
            self.blocked_ips.add(req["ip"])
            self.save()

    def get_token_for_ip(self, ip: str) -> Optional[str]:
        return self.approved_tokens.pop(ip, None)

    async def request_consent(self, ip: str) -> bool:
        # Legacy/Handshake check
        if ip in ("127.0.0.1", "localhost", "::1"): return True
        if ip in self.blocked_ips: return False
        return ip in self.trusted_ips

class TokenManager:
    def __init__(self):
        self.current_pin: str = ""
        self.valid_tokens: Dict[str, str] = {}
        self.reset_pin()

    def reset_pin(self) -> str:
        self.current_pin = str(secrets.randbelow(900000) + 100000)
        logger.info("PIN rotated to %s. Active session tokens remain valid.", self.current_pin)
        return self.current_pin

    def validate_pin(self, pin: str) -> bool:
        return pin == self.current_pin

    def generate_token(self, client_ip: str) -> str:
        token = str(uuid.uuid4())
        self.valid_tokens[token] = client_ip
        return token

    def validate_token(self, token: Optional[str]) -> bool:
        if not token: return False
        if token == "hub_internal": return True
        return token in self.valid_tokens

from zeroconf import IPVersion, ServiceInfo, Zeroconf, ServiceBrowser, ServiceListener

class DeviceDiscovery(ServiceListener):
    def __init__(self, port: int):
        self.port = port
        self.zc = Zeroconf(ip_version=IPVersion.V4Only)
        self.discovered_devices: Dict[str, str] = {} # ip -> hostname
        self.info: Optional[ServiceInfo] = None
        self.browser: Optional[ServiceBrowser] = None

    def start(self) -> None:
        local_ip = detect_lan_ip()
        hostname = socket.gethostname().replace(".local", "")
        service_type = "_gesturelink._tcp.local."
        service_name = f"GestureLink-Hub-{hostname}.{service_type}"

        self.info = ServiceInfo(
            service_type,
            service_name,
            addresses=[socket.inet_aton(local_ip)],
            port=self.port,
            properties={"type": "hub", "version": "1.0.0"},
            server=f"{hostname}.local.",
        )
        try:
            self.zc.register_service(self.info)
            logger.info("Zeroconf: Broadcasting Hub as %s", service_name)
        except Exception as exc:
            # Issue 9: log full exception so the cause is visible in terminal
            logger.warning("Zeroconf: Failed to register service (%s: %s). mDNS discovery disabled.",
                           type(exc).__name__, exc)
            self.info = None  # prevent unregister attempt on shutdown

        self.browser = ServiceBrowser(self.zc, "_gesturelink._tcp.local.", self)

    def update_service(self, zc: Zeroconf, type_: str, name: str) -> None: pass
    def remove_service(self, zc: Zeroconf, type_: str, name: str) -> None:
        # Issue 9: remove agent from discovered list when it goes offline
        hostname = name.split(".")[0]
        to_remove = [ip for ip, hn in self.discovered_devices.items() if hn == hostname]
        for ip in to_remove:
            del self.discovered_devices[ip]
            logger.info("Zeroconf: Agent offline: %s (%s)", hostname, ip)
    def add_service(self, zc: Zeroconf, type_: str, name: str) -> None:
        info = zc.get_service_info(type_, name)
        if info and info.addresses:  # Bug #4: guard against empty addresses
            ip = socket.inet_ntoa(info.addresses[0])
            hostname = name.split(".")[0]
            if ip != detect_lan_ip():
                self.discovered_devices[ip] = hostname
                logger.info("Zeroconf: Discovered Agent: %s at %s", hostname, ip)

    def stop(self) -> None:
        if self.info:
            self.zc.unregister_service(self.info)
        self.zc.close()
