"""Offline Linux process contracts using a sleeping child, never a media server."""
import os
from pathlib import Path
import struct
import subprocess
import sys
import time
import pytest


@pytest.mark.parametrize('message',[None,b'',struct.pack('!d',float('nan')),struct.pack('!d',time.monotonic()-100)],ids=['absent','closed','non-finite','stale'])
def test_missing_invalid_or_closed_controller_heartbeat_stops_child_under_two_seconds(tmp_path,message):
    read,write=os.pipe()
    from api.streaming import watchdog
    code='import runpy,sys;supervise=runpy.run_path(sys.argv[2])["supervise"];raise SystemExit(supervise([sys.executable,"-c","import time;time.sleep(60)"],int(sys.argv[1])))'
    guard=subprocess.Popen([sys.executable,'-c',code,str(read),watchdog.__file__],pass_fds=(read,))
    os.close(read)
    try:
        start=time.monotonic()
        if message is not None:
            if message:os.write(write,message)
            os.close(write);write=None
        assert guard.wait(timeout=2)==1
        assert time.monotonic()-start<2
    finally:
        if write is not None:os.close(write)
        if guard.poll() is None:guard.kill();guard.wait()
