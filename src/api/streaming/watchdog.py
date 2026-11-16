"""Independent fail-stop guard around its own media child, not an arbitrary PID."""
import ctypes
import os
import select
import signal
import struct
import subprocess
import time
WATCHDOG_SECONDS = 1.0  # Standalone guard must not import the application package.


def supervise(command, heartbeat_fd, *, clock=time.monotonic):
    """Run in a separate process with a private inherited pipe from the controller.

    A heartbeat carries the controller's monotonic completion time. Backlogged
    bytes cannot extend authority; EOF, stale timestamps and malformed frames
    kill the owned process group. Nothing here renews authority automatically.
    """
    # Linux parent-death signal prevents a crashed guard from orphaning media.
    parent = os.getpid()
    libc = ctypes.CDLL(None, use_errno=True)
    def child_guard():
        if libc.prctl(1, signal.SIGKILL, 0, 0, 0) != 0 or os.getppid() != parent:
            os._exit(1)
    process = subprocess.Popen(command, start_new_session=True, stdin=subprocess.DEVNULL,
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                               close_fds=True, preexec_fn=child_guard)
    deadline = clock() + WATCHDOG_SECONDS
    pending = b''
    try:
        while process.poll() is None:
            remaining = deadline - clock()
            if remaining <= 0: break
            ready, _, _ = select.select([heartbeat_fd], [], [], min(remaining, 0.02))
            if not ready: continue
            block = os.read(heartbeat_fd, 4096)
            if not block or len(pending) + len(block) > 4096: break
            pending += block
            while len(pending) >= 8:
                sent = struct.unpack('!d', pending[:8])[0]
                pending = pending[8:]
                now = clock()
                if not 0 <= now - sent < WATCHDOG_SECONDS: return 1
                deadline = sent + WATCHDOG_SECONDS
    finally:
        # Kill the entire owned group even if its leader exited first.
        try: os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError: pass
        process.wait(timeout=0.5)
        os.close(heartbeat_fd)
    return 1


def heartbeat(fd):
    data = struct.pack('!d', time.monotonic())
    # Call only after one complete successful controller pass; never on a timer.
    if os.write(fd, data) != len(data): raise RuntimeError('Watchdog pipe unavailable')


if __name__ == '__main__':
    import sys
    if len(sys.argv)!=3 or not sys.argv[1].isdigit():
        raise SystemExit('Private watchdog arguments required')
    raise SystemExit(supervise(['/usr/local/bin/mediamtx',sys.argv[2]],int(sys.argv[1])))
