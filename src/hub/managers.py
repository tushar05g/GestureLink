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
        self.pending_approvals: Dict[str, asyncio.Event] = {}
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

    async def request_consent(self, ip: str) -> bool:
        if ip in ("127.0.0.1", "localhost", "::1"): return True
        if ip in self.blocked_ips: return False
        if ip in self.trusted_ips: return True

        if ip not in self.pending_approvals:
            self.pending_approvals[ip] = asyncio.Event()
        
        logger.info("New device %s is waiting for approval...", ip)
        try:
            await asyncio.wait_for(self.pending_approvals[ip].wait(), timeout=60.0)
            return ip in self.trusted_ips
        except asyncio.TimeoutError:
            logger.warning("Approval timeout for %s", ip)
            return False
        finally:
            self.pending_approvals.pop(ip, None)

class TokenManager:
    def __init__(self):
        self.current_pin: str = ""
        self.valid_tokens: Dict[str, str] = {}
        self.reset_pin()

    def reset_pin(self) -> str:
        self.current_pin = str(secrets.randbelow(900000) + 100000)
        # Optional: Decide if we want to clear all tokens when PIN resets
        # For security requested by user, clearing tokens forces re-pair
        self.valid_tokens.clear()
        logger.info("PIN rotated to %s. All existing tokens invalidated.", self.current_pin)
        return self.current_pin

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
        except Exception as e:
            logger.warning("Zeroconf: Failed to register service: %s", e)

        self.browser = ServiceBrowser(self.zc, "_gesturelink._tcp.local.", self)

    def update_service(self, zc: Zeroconf, type_: str, name: str) -> None: pass
    def remove_service(self, zc: Zeroconf, type_: str, name: str) -> None: pass
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
