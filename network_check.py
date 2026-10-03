#!/usr/bin/env python3
"""
Network connectivity check script.
Tests Internet, RU zone, and Provider connectivity.
Uses both ICMP ping and HTTPS requests to bypass DPI.
"""

import subprocess
import json
import sys
import socket
import ssl
import time

def ping(host, timeout=3, count=2):
    """Ping a host using ICMP"""
    try:
        result = subprocess.run(
            ["ping", "-c", str(count), "-W", str(timeout), host],
            capture_output=True,
            text=True,
            timeout=timeout + 2
        )
        return result.returncode == 0
    except Exception:
        return False

def https_check(host, port=443, timeout=3):
    """Check HTTPS connectivity"""
    try:
        context = ssl.create_default_context()
        context.check_hostname = False
        context.verify_mode = ssl.CERT_NONE
        
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(timeout)
        sock.connect((host, port))
        
        wrapped = context.wrap_socket(sock, server_hostname=host)
        wrapped.close()
        return True
    except Exception:
        return False

def http_check(url, timeout=3):
    """Check HTTP connectivity using curl"""
    try:
        result = subprocess.run(
            ["curl", "-s", "-o", "/dev/null", "-w", "%{http_code}",
             "--max-time", str(timeout), "--connect-timeout", str(timeout),
             "-k", url],
            capture_output=True,
            text=True,
            timeout=timeout + 2
        )
        return result.stdout.strip() in ("200", "301", "302", "303", "307", "308")
    except Exception:
        return False

def check_internet():
    """Check general internet connectivity"""
    # Try multiple targets
    targets = [
        ("https", "google.com"),
        ("https", "cloudflare.com"),
        ("https", "ya.ru"),
    ]
    
    for scheme, host in targets:
        if https_check(host):
            return True
    
    # Fallback to ping
    for host in ["8.8.8.8", "1.1.1.1", "208.67.222.222"]:
        if ping(host):
            return True
    
    return False

def check_ru_zone():
    """Check Russian zone connectivity"""
    targets = [
        ("https", "ya.ru"),
        ("https", "vk.com"),
        ("https", "mail.ru"),
        ("https", "yandex.ru"),
    ]
    
    for scheme, host in targets:
        if https_check(host):
            return True
    
    # Fallback to ping
    for host in ["77.88.8.8", "87.240.129.13"]:
        if ping(host):
            return True
    
    return False

def check_provider(host):
    """Check custom provider/host"""
    # Try HTTPS first
    if https_check(host):
        return True
    
    # Try HTTP
    if http_check(f"https://{host}"):
        return True
    
    # Try ping
    if ping(host):
        return True
    
    return False


if __name__ == "__main__":
    action = sys.argv[1] if len(sys.argv) > 1 else "all"
    
    if action == "internet":
        result = check_internet()
        print(json.dumps({"host": "internet", "online": result}))
    
    elif action == "ru":
        result = check_ru_zone()
        print(json.dumps({"host": "ru_zone", "online": result}))
    
    elif action == "provider":
        host = sys.argv[2] if len(sys.argv) > 2 else "192.168.3.1"
        result = check_provider(host)
        print(json.dumps({"host": host, "online": result}))
    
    elif action == "all":
        results = {
            "internet": check_internet(),
            "ru_zone": check_ru_zone(),
        }
        print(json.dumps(results))
    
    else:
        print(json.dumps({"error": "unknown action"}))
