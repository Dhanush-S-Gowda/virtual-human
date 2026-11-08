# Speech synthesis

`kokoro.py` calls the Kokoro service and uses available word timestamps.
`supertonic.py` runs local ONNX synthesis. `normalization.py` prepares speech
text, `voices.py` lists voice choices, and `base.py` defines the common contract.

Select the provider with `tts_provider`. Shared configuration controls speed,
segment size, and concurrent synthesis requests. The pipeline handles inline
gesture and speed tags before synthesis.

See [speech timing](../../docs/audio-gesture-synchronization.md),
[configuration](../../docs/configuration.md), and [tests](../tests/README.md).
