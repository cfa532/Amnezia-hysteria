// hysteria-proxy — high-performance UDP forwarder replacing hysteria-udp-proxy.py.
//
// Listens on 127.0.0.1:<port>, binds outgoing sockets to a named interface,
// and forwards to whichever Hysteria2 server is selected in servers.conf.
// One goroutine per session direction; no GIL, no interpreter overhead, 4 MB buffers.
//
// Flags:
//   -iface  string   outgoing interface to bind (default: en1)
//   -conf   string   path to servers.conf
//   -port   int      local listen port (default: 9443)
//   -state  string   server index state file (default: /tmp/hysteria-server-index)
package main

import (
	"bufio"
	"flag"
	"fmt"
	"log"
	"math/rand"
	"net"
	"os"
	"path/filepath"
	"strconv"
	"strings"
	"sync"
	"syscall"
	"time"
)

const (
	localHost  = "127.0.0.1"
	sessionTTL = 120 * time.Second
	bufSize    = 65535
	sockBuf    = 4 << 20 // 4 MB
	ifaceWait  = 60 * time.Second
)

var (
	flagIface = flag.String("iface", "en1", "outgoing interface to bind")
	flagPort  = flag.Int("port", 9443, "local UDP listen port")
	flagConf  = flag.String("conf", "", "path to servers.conf (default: ~/Library/Application Support/hysteria/servers.conf)")
	flagState = flag.String("state", "/tmp/hysteria-server-index", "server index state file")
)

// ── server config ──────────────────────────────────────────────────────────────

type server struct {
	ip   string
	port int
}

func serversConfPath() string {
	if *flagConf != "" {
		return *flagConf
	}
	home, _ := os.UserHomeDir()
	return filepath.Join(home, "Library", "Application Support", "hysteria", "servers.conf")
}

func loadServers() ([]server, error) {
	f, err := os.Open(serversConfPath())
	if err != nil {
		return nil, err
	}
	defer f.Close()

	var out []server
	sc := bufio.NewScanner(f)
	for sc.Scan() {
		line := strings.TrimSpace(sc.Text())
		if line == "" || strings.HasPrefix(line, "#") {
			continue
		}
		parts := strings.Fields(line)
		port := 80
		if len(parts) >= 3 {
			if p, err := strconv.Atoi(parts[2]); err == nil {
				port = p
			}
		}
		out = append(out, server{ip: parts[0], port: port})
	}
	return out, sc.Err()
}

func pickServer() (server, error) {
	servers, err := loadServers()
	if err != nil || len(servers) == 0 {
		return server{}, fmt.Errorf("no servers in %s: %w", serversConfPath(), err)
	}
	idx := rand.Intn(len(servers))
	if raw, err := os.ReadFile(*flagState); err == nil {
		if n, err := strconv.Atoi(strings.TrimSpace(string(raw))); err == nil {
			idx = n % len(servers)
		}
	} else {
		_ = os.WriteFile(*flagState, []byte(strconv.Itoa(idx)), 0644)
	}
	return servers[idx], nil
}

// ── network helpers ────────────────────────────────────────────────────────────

func ifaceIP(name string) string {
	iface, err := net.InterfaceByName(name)
	if err != nil {
		return ""
	}
	addrs, _ := iface.Addrs()
	for _, a := range addrs {
		if ipNet, ok := a.(*net.IPNet); ok {
			if v4 := ipNet.IP.To4(); v4 != nil {
				return v4.String()
			}
		}
	}
	return ""
}

func waitIfaceIP(name string, timeout time.Duration) string {
	deadline := time.Now().Add(timeout)
	for time.Now().Before(deadline) {
		if ip := ifaceIP(name); ip != "" {
			return ip
		}
		log.Printf("waiting for %s IP...", name)
		time.Sleep(2 * time.Second)
	}
	return ""
}

func setSockBufs(conn *net.UDPConn, size int) {
	raw, err := conn.SyscallConn()
	if err != nil {
		return
	}
	_ = raw.Control(func(fd uintptr) {
		_ = syscall.SetsockoptInt(int(fd), syscall.SOL_SOCKET, syscall.SO_RCVBUF, size)
		_ = syscall.SetsockoptInt(int(fd), syscall.SOL_SOCKET, syscall.SO_SNDBUF, size)
	})
}

// ── session ────────────────────────────────────────────────────────────────────

type session struct {
	remote *net.UDPConn
	once   sync.Once
}

func (s *session) close() { s.once.Do(func() { s.remote.Close() }) }

// ── proxy ──────────────────────────────────────────────────────────────────────

type proxy struct {
	local   *net.UDPConn
	srv     server
	startIP string // en1 IP captured at startup

	mu   sync.RWMutex
	sess map[string]*session
}

// getOrCreate returns the session for clientAddr, creating one if needed.
func (p *proxy) getOrCreate(clientAddr *net.UDPAddr) *session {
	key := clientAddr.String()

	p.mu.RLock()
	s := p.sess[key]
	p.mu.RUnlock()
	if s != nil {
		return s
	}

	// Re-check under write lock to avoid double-creation.
	p.mu.Lock()
	defer p.mu.Unlock()
	if s = p.sess[key]; s != nil {
		return s
	}

	bindIP := ifaceIP(*flagIface)
	if bindIP == "" {
		bindIP = p.startIP
	}

	var localAddr *net.UDPAddr
	if bindIP != "" {
		localAddr = &net.UDPAddr{IP: net.ParseIP(bindIP)}
	}
	remoteAddr := &net.UDPAddr{IP: net.ParseIP(p.srv.ip), Port: p.srv.port}

	conn, err := net.DialUDP("udp", localAddr, remoteAddr)
	if err != nil {
		log.Printf("dial %s:%d error: %v", p.srv.ip, p.srv.port, err)
		return nil
	}
	setSockBufs(conn, sockBuf)

	s = &session{remote: conn}
	p.sess[key] = s
	log.Printf("session %s -> %s:%d via %s", key, p.srv.ip, p.srv.port, bindIP)

	go p.readRemote(s, clientAddr)
	return s
}

// readRemote forwards remote → local for one session. Cleans up on exit.
func (p *proxy) readRemote(s *session, clientAddr *net.UDPAddr) {
	defer func() {
		s.close()
		p.mu.Lock()
		if p.sess[clientAddr.String()] == s {
			delete(p.sess, clientAddr.String())
		}
		p.mu.Unlock()
		log.Printf("session %s closed", clientAddr)
	}()

	buf := make([]byte, bufSize)
	for {
		// Reset deadline on each read so the TTL is idle-based.
		_ = s.remote.SetReadDeadline(time.Now().Add(sessionTTL))
		n, err := s.remote.Read(buf)
		if err != nil {
			return
		}
		if _, err := p.local.WriteToUDP(buf[:n], clientAddr); err != nil {
			return
		}
	}
}

// run is the main packet dispatch loop (local → remote).
func (p *proxy) run() {
	buf := make([]byte, bufSize)
	for {
		n, clientAddr, err := p.local.ReadFromUDP(buf)
		if err != nil {
			log.Printf("local read error: %v", err)
			continue
		}

		s := p.getOrCreate(clientAddr)
		if s == nil {
			continue
		}

		if _, err := s.remote.Write(buf[:n]); err != nil {
			log.Printf("remote write error: %v", err)
			s.close()
			p.mu.Lock()
			if p.sess[clientAddr.String()] == s {
				delete(p.sess, clientAddr.String())
			}
			p.mu.Unlock()
		}
	}
}

// ── main ───────────────────────────────────────────────────────────────────────

func main() {
	flag.Parse()
	log.SetFlags(log.LstdFlags)

	startIP := waitIfaceIP(*flagIface, ifaceWait)
	if startIP != "" {
		log.Printf("binding remote sockets to %s (%s)", *flagIface, startIP)
	} else {
		log.Printf("WARNING: no IP on %s, continuing unbound", *flagIface)
	}

	srv, err := pickServer()
	if err != nil {
		log.Fatalf("server config error: %v", err)
	}
	log.Printf("using server %s:%d", srv.ip, srv.port)

	laddr := &net.UDPAddr{IP: net.ParseIP(localHost), Port: *flagPort}
	local, err := net.ListenUDP("udp", laddr)
	if err != nil {
		log.Fatalf("listen %s:%d: %v", localHost, *flagPort, err)
	}
	setSockBufs(local, sockBuf)
	log.Printf("proxy up on %s:%d", localHost, *flagPort)

	p := &proxy{
		local:   local,
		srv:     srv,
		startIP: startIP,
		sess:    make(map[string]*session),
	}
	p.run()
}
