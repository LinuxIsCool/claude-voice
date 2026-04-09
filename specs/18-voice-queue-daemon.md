---
title: "Spec 18: Voice Queue Daemon"
status: ready
phase: "3.5"
layer: 6
created: 2026-03-27
author: matt
effort: 3h
prior_art: LinuxIsCool/claude-plugins-public/plugins/voice/src/coordination/
tags: [queue, daemon, turn-taking, scheduling, multi-agent, phase-3.5]
---

# Spec 18: Voice Queue Daemon (Phase 3.5, Layer 6)

## Problem

When multiple agents complete tasks simultaneously, their TTS responses overlap.
The spatial mixer (Layer 0) controls HOW LOUD each agent speaks but not WHEN.
Two agents at 50% volume speaking simultaneously is still cacophony.

## Solution

A scheduling daemon that ensures only one agent speaks at a time. Agents enqueue
speech requests; the daemon signals each in turn. Priority determines order.
Speaker transitions get a 300ms pause for natural rhythm. Stale requests expire.

## Prior Art

The TypeScript POC designed this completely at:
`~/.claude/local/dock/repos/LinuxIsCool/claude-plugins-public/plugins/voice/src/coordination/`

6 files: types.ts (140 lines), queue-manager.ts (350 lines), ipc-server.ts (377 lines),
daemon.ts, ipc-client.ts, config.ts. Full priority heap, interruption policies,
speaker transitions, IPC protocol.

We port the design to Python, simplified. ~280 lines total (200 daemon + 80 client).

## Design

### Daemon: `scripts/voice_queue.py`

```python
import heapq, json, os, signal, socket, time, sys
from pathlib import Path

SOCKET_PATH = Path("~/.claude/local/voice/queue.sock").expanduser()
PID_PATH = Path("~/.claude/local/voice/queue.pid").expanduser()
LOG_PATH = Path("~/.claude/local/voice/queue.log").expanduser()

# Read config
CONFIG_PATH = Path("~/.claude/local/voice/config.yaml").expanduser()
MAX_ITEMS = 50
MAX_WAIT_SECONDS = 30
SPEAKER_TRANSITION_MS = 300
INTERRUPT_THRESHOLD = 2  # priority >= 2 can interrupt current speaker
IDLE_TIMEOUT = 30 * 60   # 30 min

class QueueItem:
    __slots__ = ('id', 'priority', 'timestamp', 'agent_id', 'wav_path', 'volume')
    def __init__(self, id, priority, timestamp, agent_id, wav_path, volume):
        self.id = id
        self.priority = priority
        self.timestamp = timestamp
        self.agent_id = agent_id
        self.wav_path = wav_path
        self.volume = volume
    def __lt__(self, other):
        # Higher priority first, then earlier timestamp
        if self.priority != other.priority:
            return self.priority > other.priority
        return self.timestamp < other.timestamp

class VoiceQueue:
    def __init__(self):
        self.heap = []           # heapq of QueueItem
        self.current = None      # Currently playing item
        self.last_speaker = None # Agent ID of last speaker
        self.stats = {'processed': 0, 'dropped': 0, 'expired': 0}

    def enqueue(self, item: QueueItem) -> int:
        # Expire old items
        self._expire()
        # Drop lowest if full
        if len(self.heap) >= MAX_ITEMS:
            dropped = heapq.nlargest(1, self.heap)[-1]  # lowest priority
            self.heap.remove(dropped)
            heapq.heapify(self.heap)
            self.stats['dropped'] += 1
        heapq.heappush(self.heap, item)
        return len(self.heap) - 1  # position (approximate)

    def get_next(self) -> QueueItem | None:
        self._expire()
        if not self.heap:
            return None
        item = heapq.heappop(self.heap)
        self.current = item
        return item

    def complete(self, item_id: str):
        if self.current and self.current.id == item_id:
            self.last_speaker = self.current.agent_id
            self.stats['processed'] += 1
            self.current = None

    def should_interrupt(self, new_priority: int) -> bool:
        if not self.current:
            return False
        return new_priority >= INTERRUPT_THRESHOLD and new_priority > self.current.priority

    def needs_speaker_transition(self, item: QueueItem) -> bool:
        return self.last_speaker is not None and self.last_speaker != item.agent_id

    def _expire(self):
        now = time.time()
        before = len(self.heap)
        self.heap = [i for i in self.heap if now - i.timestamp < MAX_WAIT_SECONDS]
        expired = before - len(self.heap)
        if expired:
            heapq.heapify(self.heap)
            self.stats['expired'] += expired
```

### Client: `lib/queue_client.py`

```python
import json, socket, os, time
from pathlib import Path
from constants import VOICE_DATA_DIR

QUEUE_SOCKET = VOICE_DATA_DIR / "queue.sock"

def enqueue_speech(wav_path: str, priority: int = 1,
                   agent_id: str = "", volume: float = 0.8) -> dict | None:
    """Enqueue a WAV for scheduled playback. Returns queue position or None."""
    if not QUEUE_SOCKET.exists():
        return None  # Queue daemon not running — caller should play directly
    try:
        request = json.dumps({
            "type": "enqueue",
            "wav_path": wav_path,
            "priority": priority,
            "agent_id": agent_id or os.environ.get("PERSONA_SLUG", ""),
            "volume": volume,
        })
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as s:
            s.settimeout(2)
            s.connect(str(QUEUE_SOCKET))
            s.sendall((request + "\n").encode())
            buf = b""
            while True:
                chunk = s.recv(4096)
                if not chunk:
                    break
                buf += chunk
                if b"\n" in buf:
                    break
            return json.loads(buf.split(b"\n")[0])
    except Exception:
        return None  # Fail open — play directly
```

### Integration in `router.py`

```python
# After TTS synthesis returns wav_path:
from queue_client import enqueue_speech

result = enqueue_speech(
    wav_path=str(wav_path),
    priority=priority,
    agent_id=os.environ.get("PERSONA_SLUG", ""),
    volume=mixed_vol,
)
if result is None:
    # Queue daemon not running — play directly (current behavior)
    play_sound(wav_path, volume=mixed_vol)
```

Earcons bypass the queue (they're <300ms, low risk of overlap). Only TTS goes through the queue. This is the key simplification from the POC — the POC queued everything, we only queue speech.

## Config

```yaml
queue:
  enabled: true
  max_items: 50
  max_wait_seconds: 30
  speaker_transition_ms: 300
  interrupt_threshold: 2
```

## Graceful Degradation

If the queue daemon is not running, `enqueue_speech()` returns `None` and the caller plays directly — exactly the current behavior. The queue is additive. Nothing breaks without it.

## What the POC Got Right (and We Keep)

1. **Priority heap** — higher priority speaks first
2. **Speaker transition pauses** — 300ms between different agents
3. **Expiration** — stale items dropped (we use 30s, POC used configurable timeout)
4. **Interruption** — CRITICAL preempts current speaker
5. **Separation of synthesis from scheduling** — TTS daemon synthesizes, queue daemon schedules

## What the POC Overcomplicated (and We Simplify)

1. **Persistent connections** → one-shot per event (hook processes are short-lived)
2. **5 priority levels** → 3 (maps directly from theme.json 0/1/2)
3. **VoiceConfig in queue items** → just wav_path + volume (synthesis already done)
4. **EventEmitter pattern** → simple log calls
5. **Full client library with reconnection** → 30-line function with fail-open

## Tests

1. Two Stop events at once → sequential, not overlapping
2. Error during speech → CRITICAL interrupts, new speech plays
3. 10 subagents complete → first few speak, rest expire at 30s
4. Different agents → 300ms pause audible between speakers
5. Same agent back-to-back → no pause
6. Queue daemon not running → falls back to direct playback
7. Queue daemon stops mid-speech → current playback completes normally
