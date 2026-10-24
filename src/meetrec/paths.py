import json
from datetime import datetime
from pathlib import Path

DEFAULT_OUT = Path.home() / "Recordings" / "meetrec"


def list_recordings(root: Path = DEFAULT_OUT) -> list[tuple[Path, str]]:
    """Finished recordings (meta.json is written on stop), newest first, with a label for a picker.
    A folder still being recorded has no meta.json yet, so it is not listed."""
    out: list[tuple[Path, str]] = []
    if not root.exists():
        return out
    for d in sorted((p for p in root.iterdir() if p.is_dir()), reverse=True):
        if not ((d / "video.mp4").exists() and (d / "meta.json").exists()):
            continue
        try:
            sec = int(json.loads((d / "meta.json").read_text(encoding="utf-8")).get("duration_sec") or 0)
            when = datetime.strptime(d.name, "%Y-%m-%d_%H-%M-%S").strftime("%Y-%m-%d %H:%M:%S")
        except Exception:
            sec, when = 0, d.name
        out.append((d, f"{when}  ·  {sec // 60}:{sec % 60:02d}"))
    return out
