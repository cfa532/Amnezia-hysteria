#!/usr/bin/env python3
import socket, threading, os, subprocess, time, random

LOCAL_HOST = '127.0.0.1'
LOCAL_PORT = int(os.environ.get('HYSTERIA_PROXY_PORT', '9443'))
SERVERS_CONF = os.path.expanduser('~/Library/Application Support/hysteria/servers.conf')
STATE_FILE = '/tmp/hysteria-server-index'
BIND_IFACE = 'en1'
BUFFER = 65535

def get_iface_ip(iface):
    try:
        r = subprocess.run(['ipconfig', 'getifaddr', iface],
                           capture_output=True, text=True, timeout=2)
        ip = r.stdout.strip()
        if ip:
            return ip
    except Exception:
        pass
    return None

def get_current_server():
    servers = []
    try:
        with open(SERVERS_CONF) as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith('#'):
                    continue
                parts = line.split()
                if parts:
                    port = int(parts[2]) if len(parts) > 2 else 80
                    servers.append((parts[0], port))
    except Exception:
        pass
    if not servers:
        return ('8.222.164.32', 80)
    try:
        idx = int(open(STATE_FILE).read().strip()) % len(servers)
    except Exception:
        idx = random.randrange(len(servers))
        try:
            open(STATE_FILE, 'w').write(str(idx))
        except Exception:
            pass
    return servers[idx]

def remote_reader(remote_sock, local_sock, client_addr, sessions):
    try:
        while True:
            data = remote_sock.recv(BUFFER)
            if not data:
                break
            local_sock.sendto(data, client_addr)
    except Exception:
        pass
    finally:
        try:
            remote_sock.close()
        except Exception:
            pass
        sessions.pop(client_addr, None)

def make_remote_socket(server_ip, server_port, bind_ip):
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.settimeout(120)
    if bind_ip:
        try:
            s.bind((bind_ip, 0))
        except Exception as e:
            print(f'warn: bind to {bind_ip} failed: {e}', flush=True)
    s.connect((server_ip, server_port))
    return s

def main():
    bind_ip = None
    for _ in range(30):
        bind_ip = get_iface_ip(BIND_IFACE)
        if bind_ip:
            break
        print(f'waiting for {BIND_IFACE} IP...', flush=True)
        time.sleep(2)

    if bind_ip:
        print(f'binding remote sockets to {BIND_IFACE} ({bind_ip})', flush=True)
    else:
        print(f'WARNING: could not get {BIND_IFACE} IP', flush=True)

    local_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    local_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    local_sock.bind((LOCAL_HOST, LOCAL_PORT))
    print(f'proxy up on {LOCAL_HOST}:{LOCAL_PORT}', flush=True)

    sessions = {}
    while True:
        try:
            data, client_addr = local_sock.recvfrom(BUFFER)
        except Exception as e:
            print(f'recv error: {e}', flush=True)
            continue

        if client_addr not in sessions:
            server_ip, server_port = get_current_server()
            cur_ip = get_iface_ip(BIND_IFACE) or bind_ip
            try:
                rs = make_remote_socket(server_ip, server_port, cur_ip)
                sessions[client_addr] = rs
                print(f'session {client_addr} -> {server_ip}:{server_port} via {cur_ip}', flush=True)
                t = threading.Thread(target=remote_reader,
                    args=(rs, local_sock, client_addr, sessions), daemon=True)
                t.start()
            except Exception as e:
                print(f'connect error: {e}', flush=True)
                continue

        rs = sessions.get(client_addr)
        if rs:
            try:
                rs.send(data)
            except Exception as e:
                print(f'send error: {e}', flush=True)
                sessions.pop(client_addr, None)

if __name__ == '__main__':
    main()
