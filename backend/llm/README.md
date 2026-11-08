# Chat and model actions

`openai.py` implements the OpenAI-compatible chat adapter. `base.py` defines the
provider contract, and `tools.py` defines permitted tools and action handling.
[Prompts](prompts/README.md) contains the assistant instructions.

The Docker demo uses Ollama with validated structured plans. Host settings can
select another compatible endpoint or native tool mode. Provider URL, model,
and tool mode come from the active backend configuration.

See [configuration](../../docs/configuration.md) and
[backend tests](../tests/README.md) for action-plan and streaming checks.
