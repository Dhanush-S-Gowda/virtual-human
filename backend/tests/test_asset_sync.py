"""Animation deployment follows the frontend repository layout."""

import json

from backend.scripts.sync_animation_assets import sync


def test_sync_copies_animations_to_frontend(tmp_path):
    source = tmp_path / "TalkingHead/animations"
    source.mkdir(parents=True)
    (source / "Greeting.fbx").write_bytes(b"animation fixture")
    prompt = tmp_path / "backend/llm/prompts/system.txt"
    prompt.parent.mkdir(parents=True)
    prompt.write_text("Put a tag immediately before its associated spoken phrase: [old].")

    catalog = sync(tmp_path)

    assert (tmp_path / "frontend/public/animations/Greeting.fbx").read_bytes() == b"animation fixture"
    assert not (tmp_path / "talking-avatar").exists()
    assert json.loads((tmp_path / "TalkingHead/animation_catalog.json").read_text()) == catalog
    assert catalog[0]["tag"] == "greeting"
    assert "[greeting]" in prompt.read_text()
