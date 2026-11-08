"""Small, explicit tool surface. Arguments are data, never executable code."""

import json
import re
import unicodedata

from websockets import WebSocketException

from world import cv_request


def tool(name, description, properties, required):
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": description,
            "parameters": {
                "type": "object",
                "properties": properties,
                "required": required,
                "additionalProperties": False,
            },
        },
    }


TOOLS = [
    tool(
        "rename_person",
        "Remember names only from explicit naming phrases such as 'my name is Ganush', 'call me Ganush', or a request to remember or change a name. Do not treat 'I'm ...' or 'I am ...' alone as naming intent; 'I'm good' describes a state. Use the current speaker's visible track_id; ask if the target is unclear.",
        {"track_id": {"type": "integer"}, "name": {"type": "string"}},
        ["track_id", "name"],
    ),
]
MODEL_TOOLS = [item for item in TOOLS if item["function"]["name"] == "rename_person"]


ACTION_PLAN_SCHEMA = {
    "type": "object", "additionalProperties": False,
    "properties": {
        "reply_kind": {"type": "string", "enum": ["conversation", "speaker_identity"]},
        "actions": {"type": "array", "maxItems": 3, "items": {"anyOf": [
            {"type": "object", "additionalProperties": False,
             "properties": {"name": {"const": item["function"]["name"], "type": "string"},
                            "arguments": item["function"]["parameters"]},
             "required": ["name", "arguments"]}
            for item in MODEL_TOOLS
        ]}},
        "speech": {"type": "string", "minLength": 1, "maxLength": 2000},
    }, "required": ["reply_kind", "speech", "actions"],
}


def validate_action_plan(value):
    """Validate the entire model plan before speaking or dispatching anything."""
    if (not isinstance(value, dict) or set(value) != {"reply_kind", "speech", "actions"}
            or value["reply_kind"] not in ("conversation", "speaker_identity")):
        raise ValueError("Invalid action plan fields.")
    speech, actions = value["speech"], value["actions"]
    if not isinstance(speech, str) or not speech.strip() or len(speech) > 2000:
        raise ValueError("Invalid action plan speech.")
    if re.search(r"\b(?:track[_ -]?id|track\s*#?\s*\d+|world state|tool_calls?|function calls?|"
                 r"push_to_talk|single_visible|lip_motion|tracking|gesture|json|rename_person|avatar_action)\b|<tool_call>", speech, re.I):
        raise ValueError("Spoken reply exposes implementation details.")
    if not isinstance(actions, list) or len(actions) > 3:
        raise ValueError("Invalid action plan actions.")
    if value["reply_kind"] == "speaker_identity" and actions:
        raise ValueError("Speaker identification needs no avatar action.")
    for action in actions:
        if not isinstance(action, dict) or set(action) != {"name", "arguments"}:
            raise ValueError("Invalid action fields.")
        name, args = action["name"], action["arguments"]
        if not isinstance(name, str) or not isinstance(args, dict):
            raise ValueError("Invalid action types.")
        if name == "rename_person":
            if (set(args) != {"track_id", "name"} or type(args["track_id"]) is not int
                    or not isinstance(args["name"], str) or not args["name"].strip()
                    or len(args["name"]) > 80
                    or any(unicodedata.category(char).startswith("C") for char in args["name"])):
                raise ValueError("Invalid rename action.")
        else:
            raise ValueError("Unknown action.")
    return value


# Brief demonstrations shared by native tools and structured plans.
ACTION_EXAMPLES = (
    ("Hi!", "conversation", "[greeting]Hello!", ()),
    ("I finally fixed that bug!", "conversation", "[clapping]You got it working!", ()),
    ("What is two plus two?", "conversation", "Four.", ()),
    ("Say hello slowly.", "conversation", "[speed:0.85][greeting]Hello there.", ()),
)


def action_example():
    """Fresh native-tool demonstrations; never dispatched as real actions."""
    messages = []
    for index, (request, _, speech, actions) in enumerate(ACTION_EXAMPLES):
        messages.append({"role": "user", "content": request})
        if actions:
            calls = [{"id": f"example_{index}_{action}", "type": "function",
                      "function": {"name": name, "arguments": json.dumps({"action": action})}}
                     for name, action in actions]
            messages.append({"role": "assistant", "content": None, "tool_calls": calls})
            messages.extend({"role": "tool", "tool_call_id": call["id"],
                             "content": json.dumps({"ok": True, "status": "dispatched"})}
                            for call in calls)
        messages.append({"role": "assistant", "content": speech})
    return messages


async def execute_tool(
    name, arguments, scene, on_action, *, speaker=None, user_text=""
):
    try:
        args = json.loads(arguments) if isinstance(arguments, str) else arguments
        if not isinstance(args, dict):
            raise ValueError("Tool arguments must be an object.")
        if name != "rename_person":
            raise ValueError("Unknown tool.")
        if set(args) != {"track_id", "name"} or type(args["track_id"]) is not int:
            raise ValueError("Rename requires an integer track_id and a name.")
        name_value = args["name"]
        if (
            not isinstance(name_value, str)
            or not name_value.strip()
            or len(name_value) > 80
            or any(unicodedata.category(char).startswith("C") for char in name_value)
        ):
            raise ValueError(
                "The preferred name must contain 1 to 80 printable characters."
            )
        person = next(
            (
                item
                for item in scene.get("people", [])
                if item["track_id"] == args["track_id"]
            ),
            None,
        )
        if person is None:
            raise ValueError("That track was not visible in this turn's world state.")
        if len(scene.get("people", [])) > 1:
            explicit_track = re.search(
                r"\btrack\s*#?\s*" + str(args["track_id"]) + r"\b", user_text, re.I
            )
            old_name = person.get("display_name")
            unique_name = (
                old_name
                and old_name != "Unknown"
                and sum(
                    item.get("display_name") == old_name for item in scene["people"]
                )
                == 1
                and re.search(
                    r"(?<!\w)" + re.escape(old_name) + r"(?!\w)", user_text, re.I
                )
            )
            if not (
                explicit_track
                or unique_name
                or (speaker and speaker.get("track_id") == args["track_id"])
            ):
                raise ValueError(
                    "Several people are visible. Ask which person or track to rename."
                )
        response = await cv_request(
            "rename_person",
            track_id=args["track_id"],
            name=name_value.strip(),
            source_id=scene.get("source_id"),
            generation=person.get("generation"),
        )
        return response.get("data", {"ok": False, "error": "No rename result."})
    except (ValueError, OSError, TimeoutError, WebSocketException) as exc:
        return {"ok": False, "error": str(exc)}
