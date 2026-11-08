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


def test_sync_updates_prompt_catalog_without_requesting_automatic_talking(tmp_path):
    source = tmp_path / "TalkingHead/animations"
    source.mkdir(parents=True)
    for name in ("Greeting.fbx", "Talking2.fbx"):
        (source / name).write_bytes(b"animation fixture")
    prompt = tmp_path / "backend/llm/prompts/system.txt"
    prompt.parent.mkdir(parents=True)
    prompt.write_text("Allowed gesture tags: [old].\nTalking is automatic.")

    catalog = sync(tmp_path)

    assert {item["tag"] for item in catalog} == {"greeting", "talking"}
    assert prompt.read_text() == "Allowed gesture tags: [greeting].\nTalking is automatic."
