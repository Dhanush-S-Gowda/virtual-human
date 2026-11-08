"""GPU probes are optional and never turn unavailable measurements into zero."""

from types import SimpleNamespace

from backend import resource_metrics as module


def collector(monkeypatch):
    monkeypatch.setattr(module.shutil, "which", lambda name: None)
    monkeypatch.setattr(module.importlib, "import_module", lambda name: (_ for _ in ()).throw(ImportError()))
    return module.GPUCollector()


def test_gpu_absence_is_reported_without_numeric_utilization(monkeypatch):
    gpu = collector(monkeypatch)
    monkeypatch.setattr(gpu, "_drm_samples", lambda roles: [])
    sample = gpu.sample({1: "llm"})[0]
    assert sample["gpu_status"] == "unavailable"
    assert "gpu_busy_percent" not in sample
    assert "vram_used_mib" not in sample
    assert gpu.sample({1: "cv.objects"}, devices=False) == []


def test_nvml_device_and_process_vram_are_separate_and_deduplicated(monkeypatch):
    gpu = collector(monkeypatch)
    process = SimpleNamespace(pid=7, usedGpuMemory=200 * module.MIB)
    gpu.nvml = SimpleNamespace(
        nvmlDeviceGetCount=lambda: 1, nvmlDeviceGetHandleByIndex=lambda index: index,
        nvmlDeviceGetName=lambda device: b"RTX 4060 Laptop GPU",
        nvmlDeviceGetUUID=lambda device: b"GPU-test",
        nvmlDeviceGetUtilizationRates=lambda device: SimpleNamespace(gpu=42),
        nvmlDeviceGetMemoryInfo=lambda device: SimpleNamespace(used=2 * 1024 * module.MIB, total=8 * 1024 * module.MIB),
        nvmlDeviceGetComputeRunningProcesses=lambda device: [process],
        nvmlDeviceGetGraphicsRunningProcesses=lambda device: [process],
    )
    device, task = gpu.sample({7: "tts.kokoro"})
    assert device["gpu_busy_percent"] == 42
    assert device["vram_used_percent"] == 25
    assert device["vram_total_mib"] == 8192
    assert task["process_vram_mib"] == 200
    assert task["task"] == "tts.kokoro"
    assert len(gpu.sample({7: "tts.kokoro"}, devices=False)) == 1
    process.usedGpuMemory = 2 ** 64 - 1  # NVML unavailable sentinel, e.g. WDDM.
    task = gpu.sample({7: "tts.kokoro"}, devices=False)[0]
    assert task["process_vram_mib"] is None
    assert task["gpu_status"] == "unsupported_process_memory"


def test_nvml_failed_probe_does_not_interrupt_collection(monkeypatch):
    gpu = collector(monkeypatch)
    gpu.nvml = SimpleNamespace(nvmlDeviceGetCount=lambda: (_ for _ in ()).throw(RuntimeError("lost device")))
    assert gpu.sample({})[0]["gpu_status"] == "unavailable"


def test_cli_fallback_preserves_unknown_vram(monkeypatch):
    gpu = collector(monkeypatch)
    gpu.cli = "nvidia-smi"
    monkeypatch.setattr(gpu, "_query_cli", lambda query:
                        [["GPU-1", "RTX 4060", "30", "2048", "8192"]] if query.startswith("--query-gpu=")
                        else [["GPU-1", "7", "[N/A]"]])
    device, process = gpu.sample({7: "llm.server"})
    assert device["vram_used_percent"] == 25
    assert process["process_vram_mib"] is None


def test_drm_engine_deltas_and_shared_memory_are_not_dedicated_vram(monkeypatch, tmp_path):
    gpu = collector(monkeypatch)
    info = tmp_path / "fdinfo"
    info.mkdir()
    counter = [100_000_000]

    def write():
        data = ("drm-driver: i915\ndrm-pdev: 0000:00:02.0\ndrm-client-id: 3\n"
                f"drm-engine-render: {counter[0]} ns\ndrm-resident-system: 2048 KiB\n")
        (info / "1").write_text(data)
        (info / "2").write_text(data)  # Duplicate descriptor for the same DRM client.

    real_path = module.Path
    monkeypatch.setattr(module, "Path", lambda value: info if str(value) == "/proc/7/fdinfo" else real_path(value))
    clock = [10.]
    monkeypatch.setattr(module.time, "monotonic", lambda: clock[0])
    write()
    first = gpu.sample({7: "cv.objects"}, devices=False)
    assert len(first) == 1 and first[0]["gpu_resident_mib"] == 2
    assert "process_vram_mib" not in first[0]
    clock[0] = 12.
    counter[0] += 1_000_000_000
    write()
    second = gpu.sample({7: "cv.objects"}, devices=False)
    assert len(second) == 2
    assert next(row["gpu_engine_percent"] for row in second if "gpu_engine_percent" in row) == 50.


def test_process_roles_use_only_known_services_and_descendants():
    roles = module.process_roles(SimpleNamespace(pid=1), "conversation_api", {
        2: ("llm.server", SimpleNamespace(children=lambda recursive: [SimpleNamespace(pid=3)]))})
    assert roles == {1: "conversation_api", 2: "llm.server", 3: "llm.server"}
