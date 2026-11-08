# Speech recognition

`faster_whisper.py` and `whisper_engine.py` implement local transcription.
`nemotron.py` connects to an external streaming recognition service.
`base.py` defines the common provider contract.

Select the provider with `stt_provider`. Faster-Whisper transcribes the complete
recording after release; streaming providers can send partial transcripts.
The browser sends the actual PCM capture sample rate.

See [configuration](../../docs/configuration.md),
[runtime architecture](../../docs/architecture.md), and
[provider tests](../tests/README.md).
