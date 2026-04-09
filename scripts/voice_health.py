#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""Voice system health check.

Verifies:
  1. TTS daemon socket exists and responds
  2. Queue daemon socket exists and responds
  3. No stale flag files
  4. PipeWire audio is available

Writes result to ~/.claude/local/health/voice-health.json
Sends desktop notification on failure.
"""
from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import time
from pathlib import Path

VOICE_DIR = Path("~/.claude/local/voice").expanduser()
HEALTH_DIR = Path("~/.claude/local/health").expanduser()
HEALTH_FILE = HEALTH_DIR / "voice-health.json"

DAEMON_SOCK = VOICE_DIR / "daemon.sock"
QUEUE_SOCK = VOICE_DIR / "queue.sock"
STT_ACTIVE = VOICE_DIR / "stt-active"
TTS_PLAYING = VOICE_DIR / "tts-playing"


def check_socket(sock_path: Path, name: str) -> dict:
    """Check if a daemon socket exists and responds to status request."""
    if not sock_path.exists():
        return {"name": name, "status": "down", "error": "socket missing"}
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as s:
            s.settimeout(2)
            s.connect(str(sock_path))
            s.sendall(b'{"type":"status"}\n')
            buf = b""
            while True:
                chunk = s.recv(4096)
                if not chunk:
                    break
                buf += chunk
                if b"\n" in buf:
                    break
            resp = json.loads(buf.split(b"\n")[0])
            return {"name": name, "status": "ok", "response": resp}
    except Exception as e:
        return {"name": name, "status": "error", "error": str(e)}


def check_stale_flags() -> list[dict]:
    """Check for stale flag files. Self-heals dead_pid flags automatically."""
    issues = []
    for flag_path in [STT_ACTIVE, TTS_PLAYING]:
        if flag_path.exists():
            try:
                content = flag_path.read_text().strip()
                if content:
                    parts = content.split()
                    if len(parts) >= 2:
                        pid, ts = int(parts[0]), float(parts[1])
                        age = time.time() - ts
                        try:
                            os.kill(pid, 0)
                        except ProcessLookupError:
                            # Self-heal: remove flag from dead process
                            flag_path.unlink(missing_ok=True)
                            print(f"Self-healed: removed {flag_path.name} (dead pid {pid})", file=sys.stderr)
                            continue  # Not an issue — we fixed it
                        if age > 120:
                            issues.append({
                                "flag": flag_path.name,
                                "issue": "stale",
                                "pid": pid,
                                "age_seconds": round(age),
                            })
                else:
                    stat = flag_path.stat()
                    age = time.time() - stat.st_mtime
                    if age > 120:
                        # Self-heal: legacy flag with no pid, just remove it
                        flag_path.unlink(missing_ok=True)
                        print(f"Self-healed: removed legacy {flag_path.name} (age {round(age)}s)", file=sys.stderr)
            except Exception:
                pass
    return issues


def check_pipewire() -> dict:
    """Check if PipeWire is running."""
    try:
        result = subprocess.run(
            ["pw-cli", "info", "0"],
            capture_output=True, text=True, timeout=2,
        )
        return {"status": "ok" if result.returncode == 0 else "down"}
    except Exception:
        return {"status": "unknown"}


def main():
    HEALTH_DIR.mkdir(parents=True, exist_ok=True)

    tts = check_socket(DAEMON_SOCK, "tts_daemon")
    queue = check_socket(QUEUE_SOCK, "queue_daemon")
    stale = check_stale_flags()
    pw = check_pipewire()

    healthy = (
        tts["status"] == "ok"
        and queue["status"] == "ok"
        and len(stale) == 0
        and pw["status"] == "ok"
    )

    report = {
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "healthy": healthy,
        "tts_daemon": tts,
        "queue_daemon": queue,
        "stale_flags": stale,
        "pipewire": pw,
    }

    HEALTH_FILE.write_text(json.dumps(report, indent=2) + "\n")

    if not healthy:
        lines = []
        if tts["status"] != "ok":
            err = tts.get("error", tts["status"])
            lines.append(f"TTS daemon: {err}")
            if "socket missing" in str(err):
                lines.append("  Fix: systemctl --user restart voice-tts.service")
            elif "error" in str(err):
                lines.append("  Fix: journalctl --user -u voice-tts.service --since '5min ago'")
        if queue["status"] != "ok":
            err = queue.get("error", queue["status"])
            lines.append(f"Queue daemon: {err}")
            if "socket missing" in str(err):
                lines.append("  Fix: systemctl --user restart voice-queue.service")
            elif "error" in str(err):
                lines.append("  Fix: journalctl --user -u voice-queue.service --since '5min ago'")
        for s in stale:
            lines.append(f"Stale flag: {s['flag']} (pid {s.get('pid','?')}, {s.get('age_seconds',0)}s old, {s['issue']})")
            lines.append(f"  Fix: rm ~/.claude/local/voice/{s['flag']}")
        if pw["status"] != "ok":
            lines.append(f"PipeWire: {pw['status']}")
            lines.append("  Fix: systemctl --user restart pipewire.service")

        issue_count = (
            (tts["status"] != "ok")
            + (queue["status"] != "ok")
            + len(stale)
            + (pw["status"] != "ok")
        )
        title = f"Voice: {issue_count} issue{'s' if issue_count != 1 else ''}"
        body = "\n".join(lines)
        try:
            subprocess.run(
                ["notify-send", "-u", "critical", "-a", "claude-voice", title, body],
                timeout=2,
            )
        except Exception:
            pass
        print(f"{title}\n{body}", file=sys.stderr)
        sys.exit(1)
    else:
        print("Voice healthy")


if __name__ == "__main__":
    main()
