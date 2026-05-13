#!/usr/bin/env python3
"""
Quick diagnostic script for GestureLink connectivity issues
Run this to test all connection paths: local LAN, tunnel, WebRTC
"""
import subprocess
import socket
import sys
import json
import asyncio
from urllib.request import urlopen, Request
from urllib.error import URLError, HTTPError
import ssl

# Suppress SSL warnings
ssl._create_default_https_context = ssl._create_unverified_context

def test_localhost():
    """Test local hub on localhost:8000"""
    print("\n🔍 TEST 1: Local Hub (localhost:8000)")
    try:
        req = Request("https://localhost:8000/api/ping", headers={'Accept': 'application/json'})
        with urlopen(req, timeout=3) as response:
            data = json.loads(response.read())
            print(f"  ✅ Localhost reachable: {data}")
            return True
    except Exception as e:
        print(f"  ❌ Failed: {e}")
        return False

def test_lan_ip():
    """Test LAN IP detection and connectivity"""
    print("\n🔍 TEST 2: LAN IP Detection")
    try:
        # Get LAN IP
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("8.8.8.8", 80))
            lan_ip = s.getsockname()[0]
        
        print(f"  Detected LAN IP: {lan_ip}")
        
        # Test hub/info endpoint
        req = Request("https://localhost:8000/api/hub/info", headers={'Accept': 'application/json'})
        with urlopen(req, timeout=3) as response:
            info = json.loads(response.read())
            print(f"  📋 Hub Info:")
            print(f"     - Port: {info.get('port', 'MISSING')}")
            print(f"     - SSL Active: {info.get('ssl_active', 'MISSING')}")
            print(f"     - Local IP: {info.get('local_ip', 'MISSING')}")
            print(f"     - Cloudflare URL: {info.get('cloudflare_url', 'MISSING')}")
            
            # Test LAN probe
            local_ip = info.get('local_ip', '')
            port = info.get('port', 8000)
            ssl_active = info.get('ssl_active', False)
            proto = "https" if ssl_active else "http"
            
            print(f"\n  Testing LAN probe: {proto}://{local_ip}:{port}/api/ping")
            try:
                req = Request(f"{proto}://{local_ip}:{port}/api/ping", headers={'Accept': 'application/json'})
                with urlopen(req, timeout=1) as r:
                    data = json.loads(r.read())
                    print(f"  ✅ LAN reachable: {data}")
            except Exception as e:
                print(f"  ⚠️  LAN not reachable: {e}")
            
            return True
    except Exception as e:
        print(f"  ❌ Failed: {e}")
        return False

def test_cloudflare():
    """Test Cloudflare tunnel connectivity"""
    print("\n🔍 TEST 3: Cloudflare Tunnel")
    try:
        req = Request("https://localhost:8000/api/hub/info", headers={'Accept': 'application/json'})
        with urlopen(req, timeout=3) as response:
            info = json.loads(response.read())
            cf_url = info.get('cloudflare_url')
            
            if not cf_url:
                print(f"  ❌ Cloudflare URL not found in response")
                return False
            
            print(f"  Tunnel URL: {cf_url}")
            
            # Try to reach through tunnel
            try:
                req = Request(f"{cf_url}/api/ping", headers={'Accept': 'application/json'})
                with urlopen(req, timeout=5) as r:
                    data = json.loads(r.read())
                    print(f"  ✅ Cloudflare tunnel reachable: {data}")
                    return True
            except Exception as e:
                print(f"  ⚠️  Tunnel might be slow: {e}")
                print(f"     (This is OK - tunnel may take 5-10s to stabilize)")
                return False
    except Exception as e:
        print(f"  ❌ Failed: {e}")
        return False

def test_webrtc():
    """Test WebRTC signaling endpoint"""
    print("\n🔍 TEST 4: WebRTC Signaling")
    try:
        from aiortc import RTCPeerConnection

        async def _run():
            pc = RTCPeerConnection()
            try:
                pc.createDataChannel("test")
                offer = await pc.createOffer()
                await pc.setLocalDescription(offer)

                payload = {
                    "sdp": pc.localDescription.sdp,
                    "type": pc.localDescription.type,
                }
                data = json.dumps(payload).encode()
                req = Request(
                    "https://localhost:8000/api/webrtc/offer",
                    data=data,
                    headers={
                        'Content-Type': 'application/json',
                        'Accept': 'application/json'
                    },
                    method='POST'
                )

                with urlopen(req, timeout=5) as response:
                    result = json.loads(response.read())
                    if "sdp" in result and "type" in result:
                        print(f"  ✅ WebRTC signaling works")
                        print(f"     - Response type: {result['type']}")
                        return True
                    else:
                        print(f"  ⚠️  Unexpected response: {result}")
                        return False
            finally:
                await pc.close()

        return asyncio.run(_run())
    except Exception as e:
        print(f"  ❌ Failed: {e}")
        return False

def test_turn_server():
    """Test TURN server reachability"""
    print("\n🔍 TEST 5: TURN Server")
    try:
        # Test if TURN server is reachable
        result = subprocess.run(
            ["ping", "-n", "1", "numb.viagenie.ca"],
            capture_output=True,
            timeout=5,
            text=True
        )
        if result.returncode == 0:
            print(f"  ✅ TURN server (numb.viagenie.ca) reachable")
            return True
        else:
            print(f"  ⚠️  TURN server unreachable (might be blocked by ISP/firewall)")
            return False
    except Exception as e:
        print(f"  ⚠️  Could not test: {e}")
        return False

def main():
    print("=" * 60)
    print("GestureLink Connectivity Diagnostic")
    print("=" * 60)
    
    results = {
        "Localhost": test_localhost(),
        "LAN IP": test_lan_ip(),
        "Cloudflare": test_cloudflare(),
        "WebRTC": test_webrtc(),
        "TURN Server": test_turn_server(),
    }
    
    print("\n" + "=" * 60)
    print("Summary:")
    print("=" * 60)
    for test, passed in results.items():
        status = "✅ PASS" if passed else "❌ FAIL"
        print(f"{test:20} {status}")
    
    all_pass = all(results.values())
    print("\n" + ("✅ All tests passed!" if all_pass else "⚠️  Some tests failed - see above"))
    print("=" * 60)

if __name__ == "__main__":
    main()
