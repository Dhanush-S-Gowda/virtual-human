"""Command-line interface for the CV subsystem.

Usage::

    python -m cv_assistant.cli <command> [options]

Commands:
    run           Run the live camera pipeline
    run-video     Run with a video file
    run-mock      Run without a camera (for integration testing)
    check-env     Check environment and dependencies
    list-people   List enrolled identities
    rename        Rename a person by ID
    delete        Delete a person and their embeddings
    benchmark     Benchmark pipeline performance
    scene         Query current scene state (requires running pipeline)
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
from pathlib import Path


def _setup_logging(level: str = "INFO") -> None:
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )


def cmd_check_env(args: argparse.Namespace) -> None:
    """Check environment and dependencies."""
    print("=" * 60)
    print("CV Assistant — Environment Check")
    print("=" * 60)

    # Python
    print(f"\nPython: {sys.version}")
    print(f"Executable: {sys.executable}")

    # CUDA / GPU
    try:
        import torch
        print(f"\nPyTorch: {torch.__version__}")
        print(f"CUDA available: {torch.cuda.is_available()}")
        if torch.cuda.is_available():
            print(f"CUDA version: {torch.version.cuda}")
            print(f"GPU: {torch.cuda.get_device_name(0)}")
            print(f"GPU memory: {torch.cuda.get_device_properties(0).total_memory / 1024**3:.1f} GB")
    except ImportError:
        print("\nPyTorch: NOT INSTALLED")

    # OpenCV
    try:
        import cv2
        print(f"\nOpenCV: {cv2.__version__}")
        # Test camera access
        cap = cv2.VideoCapture(0)
        if cap.isOpened():
            print("Camera 0: accessible")
            cap.release()
        else:
            print("Camera 0: NOT accessible")
    except ImportError:
        print("\nOpenCV: NOT INSTALLED")

    # Ultralytics
    try:
        import ultralytics
        print(f"\nUltralytics: {ultralytics.__version__}")
    except ImportError:
        print("\nUltralytics: NOT INSTALLED")

    # InsightFace
    try:
        import insightface
        print(f"\nInsightFace: {insightface.__version__}")
    except ImportError:
        print("\nInsightFace: NOT INSTALLED")

    # ONNX Runtime
    try:
        import onnxruntime
        print(f"\nONNX Runtime: {onnxruntime.__version__}")
        print(f"Providers: {onnxruntime.get_available_providers()}")
    except ImportError:
        print("\nONNX Runtime: NOT INSTALLED")

    # NumPy
    try:
        import numpy
        print(f"\nNumPy: {numpy.__version__}")
    except ImportError:
        print("\nNumPy: NOT INSTALLED")

    print("\n" + "=" * 60)


def cmd_run(args: argparse.Namespace) -> None:
    """Run the live camera pipeline."""
    _setup_logging(args.log_level)
    from cv_assistant.pipeline import CVPipeline
    pipeline = CVPipeline()
    pipeline.initialize()
    try:
        asyncio.run(pipeline.run_async())
    except KeyboardInterrupt:
        pass


def cmd_run_video(args: argparse.Namespace) -> None:
    """Run with a video file."""
    _setup_logging(args.log_level)
    from cv_assistant.pipeline import CVPipeline
    pipeline = CVPipeline(video_file=args.video)
    pipeline.initialize()
    try:
        asyncio.run(pipeline.run_async())
    except KeyboardInterrupt:
        pass


def cmd_run_mock(args: argparse.Namespace) -> None:
    """Run without a camera for integration testing."""
    _setup_logging(args.log_level)
    from cv_assistant.pipeline import CVPipeline
    pipeline = CVPipeline(no_camera=True)
    pipeline.initialize()
    try:
        asyncio.run(pipeline.run_async())
    except KeyboardInterrupt:
        pass


def cmd_list_people(args: argparse.Namespace) -> None:
    """List enrolled identities."""
    from cv_assistant.config import settings
    from cv_assistant.identity.store import IdentityStore

    store = IdentityStore(db_path=settings.identity.database_path)
    store.open()
    try:
        people = store.list_people()
        if not people:
            print("No enrolled identities.")
            return
        print(f"\n{'ID':<38} {'Name':<20} {'Consent':<10} {'Last Seen':<24}")
        print("-" * 92)
        for p in people:
            emb_count = store.get_embedding_count(p["person_id"])
            print(
                f"{p['person_id']:<38} "
                f"{p['display_name']:<20} "
                f"{p['consent_status']:<10} "
                f"{p['last_seen_at'] or 'never':<24} "
                f"({emb_count} embeddings)"
            )
    finally:
        store.close()


def cmd_rename(args: argparse.Namespace) -> None:
    """Rename a person by persistent ID."""
    from cv_assistant.config import settings
    from cv_assistant.identity.store import IdentityStore

    store = IdentityStore(db_path=settings.identity.database_path)
    store.open()
    try:
        success = store.rename_person(args.person_id, args.name)
        if success:
            print(f"Renamed {args.person_id} to '{args.name}'.")
        else:
            print(f"Person ID not found: {args.person_id}")
            sys.exit(1)
    finally:
        store.close()


def cmd_delete(args: argparse.Namespace) -> None:
    """Delete a person and their embeddings."""
    from cv_assistant.config import settings
    from cv_assistant.identity.store import IdentityStore

    store = IdentityStore(db_path=settings.identity.database_path)
    store.open()
    try:
        person = store.get_person(args.person_id)
        if person is None:
            print(f"Person ID not found: {args.person_id}")
            sys.exit(1)

        if not args.force:
            confirm = input(
                f"Delete '{person['display_name']}' ({args.person_id}) "
                f"and all embeddings? [y/N]: "
            )
            if confirm.lower() != "y":
                print("Cancelled.")
                return

        success = store.delete_person(args.person_id)
        if success:
            print(f"Deleted {args.person_id} and all associated embeddings.")
        else:
            print(f"Delete failed for {args.person_id}.")
            sys.exit(1)
    finally:
        store.close()


def cmd_benchmark(args: argparse.Namespace) -> None:
    """Benchmark pipeline performance."""
    _setup_logging(args.log_level)
    import time
    from cv_assistant.pipeline import CVPipeline

    source = args.video if args.video else None
    pipeline = CVPipeline(video_file=source)
    pipeline.initialize()

    if source:
        print(f"Benchmarking with video: {source}")
    else:
        print("Benchmarking with live camera (press Ctrl+C to stop)")

    start = time.monotonic()
    try:
        pipeline.run()
    except KeyboardInterrupt:
        pass
    elapsed = time.monotonic() - start

    stats = pipeline.get_stats()
    print("\n" + "=" * 50)
    print("Benchmark Results")
    print("=" * 50)
    print(json.dumps(stats, indent=2))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="cv-assistant",
        description="CV subsystem for the virtual human assistant.",
    )
    parser.add_argument(
        "--log-level", default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        help="Logging level",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    # check-env
    subparsers.add_parser("check-env", help="Check environment and dependencies")

    # run
    subparsers.add_parser("run", help="Run live camera pipeline")

    # run-video
    p_video = subparsers.add_parser("run-video", help="Run with a video file")
    p_video.add_argument("video", help="Path to video file")

    # run-mock
    subparsers.add_parser("run-mock", help="Run without camera (mock mode)")

    # list-people
    subparsers.add_parser("list-people", help="List enrolled identities")

    # rename
    p_rename = subparsers.add_parser("rename", help="Rename a person by ID")
    p_rename.add_argument("person_id", help="Person UUID")
    p_rename.add_argument("name", help="New display name")

    # delete
    p_delete = subparsers.add_parser("delete", help="Delete a person and embeddings")
    p_delete.add_argument("person_id", help="Person UUID")
    p_delete.add_argument("--force", "-f", action="store_true", help="Skip confirmation")

    # benchmark
    p_bench = subparsers.add_parser("benchmark", help="Benchmark pipeline performance")
    p_bench.add_argument("--video", help="Optional video file for benchmark")

    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()

    commands = {
        "check-env": cmd_check_env,
        "run": cmd_run,
        "run-video": cmd_run_video,
        "run-mock": cmd_run_mock,
        "list-people": cmd_list_people,
        "rename": cmd_rename,
        "delete": cmd_delete,
        "benchmark": cmd_benchmark,
    }

    cmd_func = commands.get(args.command)
    if cmd_func:
        cmd_func(args)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
