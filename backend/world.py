"""Bounded CV queries and the compact world state supplied to each model turn."""

import asyncio
import json
import uuid

import websockets

from config import settings


async def cv_request(kind, **fields):
    url = getattr(settings, "cv_adapter_url", "")
    if not url:
        raise ValueError("The CV service is not configured.")
    request_id = str(uuid.uuid4())
    async with asyncio.timeout(2):
        async with websockets.connect(url, open_timeout=1, close_timeout=.2) as socket:
            await socket.send(json.dumps({"type": kind, "request_id": request_id, **fields}))
            while True:
                message = json.loads(await socket.recv())
                if message.get("request_id") == request_id:
                    if message.get("type") == "error":
                        raise ValueError(message.get("message", "CV request failed."))
                    return message


async def current_world(speaker_hint=None):
    """Use a fresh server snapshot; never turn missing camera data into an empty scene."""
    try:
        response = await cv_request("get_scene")
        scene = response.get("data", {})
    except (OSError, ValueError, TimeoutError, websockets.WebSocketException):
        return {"status": "unavailable", "people": [], "objects": [], "speaker": None}, {}
    history = {"recent_visual_history": scene["recent_visual_history"]} if "recent_visual_history" in scene else {}
    if not scene.get("frame_available", bool(scene.get("people") or scene.get("objects"))):
        return {"status": "unavailable", "people": [], "objects": [], "speaker": None, **history}, scene
    people = [{"track_id": person["track_id"], "name": person.get("display_name") or "Unknown",
               "gestures": list(person.get("actions") or ())}
              for person in scene.get("people", [])]
    objects = [{"track_id": item["track_id"], "label": item["label"]}
               for item in scene.get("objects", [])]
    speaker = None
    if (isinstance(speaker_hint, dict) and type(speaker_hint.get("track_id")) is int
            and type(speaker_hint.get("generation")) is int
            and speaker_hint.get("method") in {"single_visible", "lip_motion"}):
        match = next((person for person in scene.get("people", [])
                      if person["track_id"] == speaker_hint.get("track_id")
                      and person.get("generation") == speaker_hint.get("generation")), None)
        if match and speaker_hint.get("source_id") == scene.get("source_id"):
            speaker = {"track_id": match["track_id"], "name": match.get("display_name") or "Unknown",
                       "evidence": "push_to_talk+" + speaker_hint["method"]}
    return {"status": "available", "people": people, "objects": objects, "speaker": speaker, **history,
            "gesture_window_seconds": scene.get("gesture_window_seconds", settings.cv_world_history_seconds),
            "counts": {"people": len(people), "objects": len(objects),
                       "recognized_faces": sum(bool(person.get("person_id"))
                                               for person in scene.get("people", []))}}, scene
