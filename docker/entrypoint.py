"""Container entrypoint: run as the owner of the mounted project dir.

On Linux hosts the bind-mounted /app belongs to the host user; running as root
would leave root-owned PDFs and state files the user can't edit. If /app is owned
by a non-root uid, drop to that uid/gid before starting Python. On Docker Desktop
(macOS/Windows) /app usually reports root and nothing changes.
"""

import os
import sys

st = os.stat("/app")
if os.getuid() == 0 and st.st_uid != 0:
    os.environ["HOME"] = "/tmp"  # xelatex/fontconfig caches need a writable home
    os.setgroups([])
    os.setgid(st.st_gid)
    os.setuid(st.st_uid)

os.execvp("python", ["python", *sys.argv[1:]])
