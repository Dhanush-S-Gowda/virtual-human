"""Sync the supplied FBX folder to the shared catalog and deployable assets."""

import json
from pathlib import Path
import re
import shutil


def sync(root):
    source = root / "TalkingHead/animations"
    destination = root / "frontend/public/animations"
    destination.mkdir(parents=True, exist_ok=True)
    grouped = {}
    for path in sorted(source.glob("*.fbx")):
        tag = path.stem.lower()
        if re.fullmatch(r"talking\d*", tag):
            tag = "talking"
        if not re.fullmatch(r"[a-z][a-z0-9_]*", tag):
            raise ValueError(f"Animation filename needs a simple tag-compatible name: {path.name}")
        grouped.setdefault(tag, []).append(path.name)
        shutil.copy2(path, destination / path.name)
    filenames = {filename for files in grouped.values() for filename in files}
    for path in destination.glob("*.fbx"):
        if path.name not in filenames:
            path.unlink()  # User requested only animations present in the source folder.
    catalog = [{"tag": tag, "label": tag.replace("_", " ").capitalize(), "files": files}
               for tag, files in grouped.items()]
    (root / "TalkingHead/animation_catalog.json").write_text(json.dumps(catalog, indent=2) + "\n")
    prompt_path = root / "backend/llm/prompts/system.txt"
    prompt = prompt_path.read_text()
    tags = ", ".join(f"[{item['tag']}]" for item in catalog)
    prompt = re.sub(r"Put a tag immediately before its associated spoken phrase: .*?\.",
                    "Put a tag immediately before its associated spoken phrase: " + tags + ".", prompt)
    prompt = prompt.replace(" [greet] means wave.", "")
    prompt_path.write_text(prompt)
    return catalog


if __name__ == "__main__":
    catalog = sync(Path(__file__).resolve().parents[2])
    print("Synced tags: " + ", ".join(item["tag"] for item in catalog))
