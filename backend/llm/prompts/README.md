# Model prompts

`system.txt` contains the conversation instructions, permitted gesture tags,
and speech-speed guidance. The backend loads the file selected by
`system_prompt_file`.

Keep prompt examples consistent with the inline parser and animation catalog.
The animation sync script also updates the prompt's allowed gesture list.
The backend validates model output before applying actions.

See [chat adapter](../README.md), [scripts](../../scripts/README.md), and
[speech timing](../../../docs/audio-gesture-synchronization.md).
