import sys, time
from huggingface_hub import snapshot_download
name = sys.argv[1]
repo = {"large-v3-turbo": "mobiuslabsgmbh/faster-whisper-large-v3-turbo", "small": "Systran/faster-whisper-small"}[name]
t = time.time()
p = snapshot_download(repo)
import os
tot = sum(os.path.getsize(os.path.join(p, f)) for f in os.listdir(p) if os.path.isfile(os.path.join(p, f)) or os.path.islink(os.path.join(p, f)))
print(name, p, "bytes(follow)", sum(os.stat(os.path.join(p,f)).st_size for f in os.listdir(p)), "sec", round(time.time()-t,1))
