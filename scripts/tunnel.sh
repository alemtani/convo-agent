#!/usr/bin/env bash
#
# Public HTTPS tunnel to a local dev server, for testing on a real phone.
#
# The mic path needs a secure context: browsers accept https:// and localhost,
# but not a LAN IP. So a phone on the same wifi cannot reach http://<mac-ip>:8000
# with a working microphone. A Cloudflare quick tunnel gives the local server a
# real HTTPS origin that a phone can open.
#
# Usage: tunnel.sh start [PORT] | stop | url | port | status
#
# State is keyed per agent session, and each session picks its own free port,
# so concurrent agents in this repo never see or kill each other's tunnel.
# Claude sets CLAUDE_CODE_SESSION_ID. OpenCode's plugin injects
# OPENCODE_SESSION_ID into every shell. `stop` only ever touches the
# tunnel this session started.

set -euo pipefail

ROOT="${CLAUDE_PROJECT_DIR:-$(cd "$(dirname "$0")/.." && pwd)}"
KEY="${CLAUDE_CODE_SESSION_ID:-${OPENCODE_SESSION_ID:-manual-$$}}"
RUN="$ROOT/.tunnel/$KEY"
PIDFILE="$RUN/pid"
URLFILE="$RUN/url"
PORTFILE="$RUN/port"
LOGFILE="$RUN/log"

# A pidfile alone can point at a recycled pid. Confirm the process is ours.
alive() {
  [ -f "$PIDFILE" ] || return 1
  local pid
  pid=$(cat "$PIDFILE" 2>/dev/null) || return 1
  [ -n "$pid" ] || return 1
  kill -0 "$pid" 2>/dev/null || return 1
  ps -p "$pid" -o command= 2>/dev/null | grep -q cloudflared
}

# Let the OS hand us a free port rather than guessing one another agent holds.
pick_port() {
  python3 -c 'import socket; s=socket.socket(); s.bind(("127.0.0.1",0)); print(s.getsockname()[1]); s.close()'
}

start() {
  if alive; then
    cat "$URLFILE" 2>/dev/null || echo "tunnel running (url unknown)"
    return 0
  fi
  command -v cloudflared >/dev/null 2>&1 || {
    echo "cloudflared not found — brew install cloudflared" >&2
    return 1
  }
  local port="${1:-${PORT:-$(pick_port)}}"
  mkdir -p "$RUN"
  rm -f "$PIDFILE" "$URLFILE" "$PORTFILE" "$LOGFILE"
  echo "$port" >"$PORTFILE"

  cloudflared tunnel --url "http://localhost:$port" >"$LOGFILE" 2>&1 &
  echo $! >"$PIDFILE"

  # cloudflared prints the assigned hostname to its log a second or two in.
  local url=""
  for _ in $(seq 1 40); do
    url=$(grep -om1 'https://[a-z0-9-]*\.trycloudflare\.com' "$LOGFILE" 2>/dev/null || true)
    [ -n "$url" ] && break
    sleep 0.5
  done
  if [ -z "$url" ]; then
    echo "tunnel did not come up within 20s — see $LOGFILE" >&2
    stop
    return 1
  fi
  echo "$url" >"$URLFILE"
  echo "$url"
  echo "serve on it with: uvicorn backend.main:app --reload --port $port" >&2
}

stop() {
  if [ -f "$PIDFILE" ]; then
    local pid
    pid=$(cat "$PIDFILE" 2>/dev/null || true)
    if [ -n "$pid" ]; then
      kill "$pid" 2>/dev/null || true
      for _ in 1 2 3 4 5 6 7 8 9 10; do
        kill -0 "$pid" 2>/dev/null || break
        sleep 0.2
      done
      kill -9 "$pid" 2>/dev/null || true
    fi
  fi
  rm -rf "$RUN"
}

case "${1:-}" in
  start)  start "${2:-}" ;;
  stop)   stop ;;
  url)    cat "$URLFILE" 2>/dev/null || { echo "no tunnel running" >&2; exit 1; } ;;
  port)   cat "$PORTFILE" 2>/dev/null || { echo "no tunnel running" >&2; exit 1; } ;;
  status) if alive; then echo "up: $(cat "$URLFILE" 2>/dev/null) -> :$(cat "$PORTFILE" 2>/dev/null)"; else echo "down"; fi ;;
  *)      echo "usage: $0 {start [PORT]|stop|url|port|status}" >&2; exit 2 ;;
esac
