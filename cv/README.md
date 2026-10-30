# Computer Vision Subsystem (`cv-assistant`)

A modular, real-time computer vision subsystem designed for the `virtual-human` assistant. It provides real-time person detection, pose tracking, gesture recognition, facial re-identification, spoken self-introduction association, and structured event broadcasting over a WebSocket bridge.

---

## Key Features

1. **GPU-Accelerated Person Detection & Pose Estimation**:
   - Single-pass person detection and 17-point body keypoint estimation via Ultralytics YOLOv8-pose.
   - Fully accelerated via PyTorch with CUDA 12.4 (`torchvision` and `torch` running on NVIDIA RTX 4060).

2. **Temporal Multi-Object Tracking**:
   - ByteTrack tracking keeping short-term track continuity across frames with debounce/cooldown rules.
   - Clean separation between ephemeral tracker IDs (`track_id`) and persistent person identities (`person_id`).

3. **Gesture & Temporal Action Recognition**:
   - Rolling buffer of normalized keypoints (invariant to camera distance).
   - Real-time heuristic detection for waving (oscillating wrist above shoulder) and raised hand (sustained wrist above head), with customizable debounce cooldowns.

4. **Facial Recognition & Quality Assessment**:
   - InsightFace ArcFace 512-d embeddings accelerated via ONNX Runtime GPU (`CUDAExecutionProvider`, `TensorrtExecutionProvider`).
   - Strict quality gates (face crop size, detection score, normalized embedding validity) to prevent enrolling blurry or low-quality faces.
   - Configurable cosine similarity matching with ambiguity margin detection.

5. **Persistent Identity Memory**:
   - Local SQLite database (`data/identities.db`) with transactional safety.
   - Stores multiple quality-approved embeddings per person with automatic eviction of lowest-quality vectors.
   - Supports consent tracking and permanent identity deletion.

6. **Spoken Self-Introduction Association**:
   - Parses self-introductions ("My name is Bablu", "I am Alice", "Call me Bob") from STT transcripts.
   - Safely associates the speaker with the visible person and updates metadata without changing persistent IDs.
   - Clarification events emitted if speaker attribution is ambiguous.

7. **Virtual Human WebSocket Adapter**:
   - Lightweight WebSocket bridge (`ws://localhost:8765`) streaming typed, versioned JSON events (`person_entered`, `person_left`, `person_identified`, `person_renamed`, `action_detected`, `objects_detected`, etc.).
   - Asynchronous and bounded queues to ensure CV never stalls or blocks avatar animation, STT, or TTS audio playback.

---

## Directory Structure

```text
cv/
├── README.md                      # Documentation & instructions
├── IMPLEMENTATION_NOTES.md        # Technical findings & architecture rationale
├── requirements.txt               # Dependencies list
├── pyproject.toml                 # Package definition & configuration
├── .gitignore                     # Ignores database, models, logs, and venvs
├── config/
│   └── config.example.yaml        # Default configuration template
├── src/
│   └── cv_assistant/
│       ├── __init__.py
│       ├── config.py              # Configuration loader & defaults
│       ├── camera.py              # Threaded camera capture & drop-stale queue
│       ├── detection.py           # YOLO-pose person detector & tracker
│       ├── tracking.py            # Track lifecycle manager
│       ├── events.py              # Typed versioned event definitions
│       ├── state.py               # Thread-safe scene state manager
│       ├── pipeline.py            # Main pipeline coordinator
│       ├── cli.py                 # Command-line interface
│       ├── actions/               # Keypoint buffers & gesture heuristics
│       ├── faces/                 # Face detector, matcher, quality filter
│       ├── identity/              # SQLite store & identity service
│       ├── speaker/               # Name intent parser & speaker association
│       └── integration/           # VirtualHumanAdapter & WebSocket server
├── tests/                         # Unit and integration test suite
├── scripts/
│   ├── check_environment.py       # Hardware and dependency checker
│   └── benchmark_pipeline.py     # Performance benchmarking script
├── data/                          # Local databases (git-ignored)
├── models/                        # Cached weights (git-ignored)
└── logs/                          # Local logs (git-ignored)
```

---

## Setup & Environment

The subsystem is configured in a dedicated virtual environment with CUDA GPU acceleration.

### 1. Activating the Virtual Environment

From PowerShell in `c:\Users\bhuva\Desktop\7semproj\cv`:

```powershell
.\.venv\Scripts\activate
```

### 2. Verify GPU Acceleration & Environment

Run the environment check script:

```powershell
.\.venv\Scripts\python scripts\check_environment.py
```

Expected output:
* **Python**: 3.12.x
* **PyTorch**: 2.6.0+cu124 (CUDA Available: True, NVIDIA GeForce RTX 4060 Laptop GPU)
* **ONNX Runtime**: 1.31.0 with providers `['TensorrtExecutionProvider', 'CUDAExecutionProvider', 'CPUExecutionProvider']`
* **Ultralytics**: >= 8.3.0
* **InsightFace**: 2.1
* **OpenCV**: Camera 0 accessible

---

## CLI Usage

All commands can be run via the module CLI from the `cv/` directory:

```powershell
# Set PYTHONPATH to include src
$env:PYTHONPATH="src"

# 1. Environment and hardware check
.\.venv\Scripts\python -m cv_assistant.cli check-env

# 2. Run live camera pipeline
.\.venv\Scripts\python -m cv_assistant.cli run

# 3. Run with a video file (for testing without a webcam)
.\.venv\Scripts\python -m cv_assistant.cli run-video path\to\video.mp4

# 4. Run mock pipeline (for headless automated testing)
.\.venv\Scripts\python -m cv_assistant.cli run-mock

# 5. List enrolled identities
.\.venv\Scripts\python -m cv_assistant.cli list-people

# 6. Rename an enrolled identity
.\.venv\Scripts\python -m cv_assistant.cli rename <person_id> "New Name"

# 7. Permanently delete a person and their biometric embeddings
.\.venv\Scripts\python -m cv_assistant.cli delete <person_id>

# 8. Benchmark pipeline performance
.\.venv\Scripts\python scripts\benchmark_pipeline.py
```

---

## Running Tests

Run the full pytest suite:

```powershell
$env:PYTHONPATH="src"
.\.venv\Scripts\pytest -v tests
```

The test suite covers:
* `test_identity_store.py`: SQLite CRUD, embedding persistence, dimension checks, cascade deletes, quality replacement.
* `test_face_matching.py`: Cosine similarity, exact matches, low similarity rejection, ambiguous close matches, quality filters.
* `test_name_binding.py`: Name intent parsing, single speaker resolution, multi-speaker ambiguity safeguards.
* `test_action_heuristics.py`: Synthetic waving gesture detection, hand raised gestures, wrist-height checks, cooldown suppression.
* `test_event_schema.py`: Event serialization/deserialization, ISO timestamps, schema versioning.
* `test_state.py`: Scene snapshot state, cooldown mechanics, track lifecycle.
* `test_integration_adapter.py`: Async WebSocket server, transcript message flow, privacy/logging safeguards.

---

## Configuration

Default settings reside in `cv_assistant.config.DEFAULTS`. To customize, copy `config/config.example.yaml` to `config/config.yaml` or set the `CV_CONFIG_PATH` environment variable:

```yaml
detection:
  model: "yolov8n-pose.pt"
  device: "cuda"            # Force CUDA or use "auto"
  target_fps: 12
  confidence: 0.5

face:
  enabled: true
  model_name: "buffalo_l"
  matching:
    threshold: 0.45         # Calibrated cosine similarity threshold
    ambiguity_margin: 0.05

identity:
  database_path: "data/identities.db"
  auto_enroll: false        # Explicit consent required before storing faces
  consent_required: true

integration:
  websocket_host: "0.0.0.0"
  websocket_port: 8765
```

---

## Privacy & Biometric Data Handling

* **Local Processing Only**: Face detection, embeddings, and tracking are computed 100% locally on the host GPU; no biometric data leaves the system.
* **Consent Enforcement**: The `consent_required` flag ensures face embeddings are only saved when explicit consent has been granted.
* **Right to Erasure**: Deleting a person via CLI permanently deletes their record and cascades removal of all raw embedding vectors.
* **Zero Biometrics in Logs**: Log formatters and event schemas strictly prohibit dumping raw vector arrays or face crop images into log streams.
