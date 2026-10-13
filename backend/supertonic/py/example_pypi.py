from pathlib import Path

from supertonic import TTS

# Download the archived model to assets/ first; see the main README.
model_dir = Path(__file__).resolve().parents[1] / "assets"
tts = TTS(model="supertonic-3", model_dir=model_dir, auto_download=False)

# Get a voice style
style = tts.get_voice_style(voice_name="M4")

# Generate speech
text = "This morning, I took a walk in the park, and the sound of the birds and the breeze was so pleasant that I stopped for a long time just to listen."
wav, duration = tts.synthesize(text, lang="en", voice_style=style)
# wav: np.ndarray, shape = (1, num_samples)
# duration: np.ndarray, shape = (1,)

# Save to file
output_dir = Path(__file__).resolve().parent / "results"
output_dir.mkdir(exist_ok=True)
tts.save_audio(wav, str(output_dir / "example_pypi.wav"))
print(f"Generated {duration[0]:.2f}s of audio")
