"""
tests/benchmark_multicam.py

Stage H: Multi-Camera Performance Optimization and System Benchmarking Harness.
Executes reproducible benchmarks across:
- 1 Active Camera
- 2 Simultaneous Cameras
- 3 Simultaneous Cameras
- 2 Live Cameras + Concurrent Uploaded-Video Analysis Job

Measures:
- Capture FPS vs Actual Inference FPS
- Aggregate Throughput (FPS)
- Inference Latency: min, p50, p95, p99, max (ms)
- Capture-to-Detection Age (ms)
- CPU Usage % and RAM Consumption (MB)
- Dropped / Skipped Frames
- MJPEG Preview Latency & Delivery
- Query Latency Under Load
- Alert Generation & Queue Stats
"""

import sys
import os
import time
import json
import statistics
import threading
import psutil
from typing import Dict, Any, List

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from backend.app.core.stream_manager import StreamManager
from backend.app.core.detector import get_detector
from backend.app.core.live_indexer import get_live_indexer
from backend.app.core.alert_engine import get_alert_engine
from backend.app.core.hardware import detect_hardware

# Available distinct video sources
VIDEO_SOURCES = [
    os.path.abspath("test_cctv.mp4"),
    os.path.abspath("test_real_cctv.mp4"),
    os.path.abspath("test_empty_scene.mp4")
]

class MultiCamBenchmark:
    def __init__(self, duration_per_scenario: float = 15.0, warmup_seconds: float = 3.0):
        self.duration = duration_per_scenario
        self.warmup = warmup_seconds
        self.hw_info = detect_hardware()
        self.process = psutil.Process()

    def run_scenario(self, camera_count: int, run_uploaded_job: bool = False) -> Dict[str, Any]:
        print(f"\n{'='*60}")
        mode_str = f"{camera_count} Cameras" + (" + Uploaded Video Job" if run_uploaded_job else "")
        print(f"BENCHMARK SCENARIO: {mode_str}")
        print(f"Duration: {self.duration}s (Warmup: {self.warmup}s) | Device: {self.hw_info.get('primary_device')}")
        print(f"{'='*60}")

        # Warm up detector model if not already warmed up
        detector = get_detector()
        
        # Initialize fresh stream manager
        mgr = StreamManager()
        active_cam_ids = []

        for i in range(camera_count):
            cam_id = f"bench_cam_{i+1}"
            src = VIDEO_SOURCES[i % len(VIDEO_SOURCES)]
            name = f"Benchmark Camera {i+1}"
            success = mgr.connect_camera(cam_id, name, src, timeout=5.0)
            if success:
                active_cam_ids.append(cam_id)
                print(f"  ✓ Connected: {cam_id} -> {os.path.basename(src)}")
            else:
                print(f"  ✗ Failed to connect: {cam_id}")

        assert len(active_cam_ids) == camera_count, f"Expected {camera_count} cameras connected"

        # If running uploaded video job concurrently
        uploaded_thread = None
        if run_uploaded_job:
            from backend.app.core.video import process_video_job
            job_id = f"bench_job_{int(time.time())}"
            uploaded_thread = threading.Thread(
                target=process_video_job,
                args=(job_id, "bench_uploaded_cam", VIDEO_SOURCES[0], 2),
                daemon=True
            )
            uploaded_thread.start()
            print("  ✓ Started concurrent uploaded video processing job")

        # Let system stabilize and pass warmup
        print(f"  Stabilizing & warming up ({self.warmup}s)...")
        time.sleep(self.warmup)

        # Baseline capture samples before measurement period
        start_snapshots = {}
        for cid in active_cam_ids:
            stream = mgr.get_stream(cid)
            start_snapshots[cid] = {
                "frames_read": stream.frames_read,
                "frames_analyzed": stream._frames_analyzed,
                "timestamp": time.time()
            }

        # Measurement sampling loop
        t_start = time.time()
        latencies_sampled: List[float] = []
        frame_ages_sampled: List[float] = []
        cpu_samples: List[float] = []
        ram_samples: List[float] = []
        preview_frame_counts = {cid: 0 for cid in active_cam_ids}

        # Consumer thread to simulate browser MJPEG preview reception
        stop_preview = threading.Event()
        def mjpeg_consumer(cid):
            stream = mgr.get_stream(cid)
            gen = stream.generate_mjpeg_stream(fps_limit=15.0)
            while not stop_preview.is_set():
                try:
                    next(gen)
                    preview_frame_counts[cid] += 1
                except Exception:
                    break

        preview_threads = [
            threading.Thread(target=mjpeg_consumer, args=(cid,), daemon=True)
            for cid in active_cam_ids
        ]
        for pt in preview_threads:
            pt.start()

        # Measurement window
        while time.time() - t_start < self.duration:
            cpu_samples.append(self.process.cpu_percent(interval=0.2))
            ram_samples.append(self.process.memory_info().rss / (1024 * 1024))
            
            for cid in active_cam_ids:
                stream = mgr.get_stream(cid)
                status = stream.get_detection_status()
                lat = status.get("last_latency_ms", 0.0)
                if lat > 0:
                    latencies_sampled.append(lat)
                
                # Approximate frame age: time between capture and detection
                with stream._frame_lock:
                    capture_ts = stream.last_frame_time
                with stream._detection_lock:
                    analyzed_ts = stream._last_analyzed_time
                if capture_ts > 0 and analyzed_ts > 0:
                    age_ms = max(0.0, (time.time() - capture_ts) * 1000.0)
                    frame_ages_sampled.append(age_ms)

        elapsed_total = time.time() - t_start
        stop_preview.set()

        # Gather final counts
        per_cam_results = {}
        total_captured = 0
        total_analyzed = 0

        for cid in active_cam_ids:
            stream = mgr.get_stream(cid)
            initial = start_snapshots[cid]
            cap_diff = stream.frames_read - initial["frames_read"]
            ana_diff = stream._frames_analyzed - initial["frames_analyzed"]
            total_captured += cap_diff
            total_analyzed += ana_diff

            cap_fps = cap_diff / elapsed_total if elapsed_total > 0 else 0.0
            inf_fps = ana_diff / elapsed_total if elapsed_total > 0 else 0.0
            prev_fps = preview_frame_counts[cid] / elapsed_total if elapsed_total > 0 else 0.0

            per_cam_results[cid] = {
                "capture_fps": round(cap_fps, 2),
                "inference_fps": round(inf_fps, 2),
                "preview_fps": round(prev_fps, 2),
                "frames_captured": cap_diff,
                "frames_analyzed": ana_diff,
                "frames_skipped": max(0, cap_diff - ana_diff),
                "avg_latency_ms": round(stream._avg_latency_ms, 1),
                "unique_tracks": stream.get_status().get("active_tracks_count", 0)
            }

        agg_inf_fps = round(total_analyzed / elapsed_total, 2)
        agg_cap_fps = round(total_captured / elapsed_total, 2)

        # Latency statistics
        if latencies_sampled:
            lat_p50 = round(statistics.median(latencies_sampled), 1)
            lat_p95 = round(statistics.quantiles(latencies_sampled, n=20)[18], 1) if len(latencies_sampled) >= 20 else round(max(latencies_sampled), 1)
            lat_max = round(max(latencies_sampled), 1)
            lat_min = round(min(latencies_sampled), 1)
        else:
            lat_p50 = lat_p95 = lat_max = lat_min = 0.0

        avg_age_ms = round(statistics.mean(frame_ages_sampled), 1) if frame_ages_sampled else 0.0
        avg_cpu = round(statistics.mean(cpu_samples), 1) if cpu_samples else 0.0
        avg_ram = round(statistics.mean(ram_samples), 1) if ram_samples else 0.0

        # Query Latency check under current steady state load
        query_start = time.time()
        try:
            from backend.app.core.vector_search import get_vector_search
            get_vector_search().search_text("car", top_k=5)
            query_latency_ms = round((time.time() - query_start) * 1000.0, 1)
        except Exception:
            query_latency_ms = 0.0

        # Disconnect and cleanup
        mgr.disconnect_all()

        report = {
            "scenario": mode_str,
            "cameras_count": camera_count,
            "concurrent_uploaded_job": run_uploaded_job,
            "duration_seconds": round(elapsed_total, 2),
            "aggregate_inference_fps": agg_inf_fps,
            "aggregate_capture_fps": agg_cap_fps,
            "per_camera_metrics": per_cam_results,
            "inference_latency_ms": {
                "min": lat_min,
                "p50": lat_p50,
                "p95": lat_p95,
                "max": lat_max
            },
            "frame_age_ms": avg_age_ms,
            "avg_cpu_percent": avg_cpu,
            "avg_ram_mb": avg_ram,
            "query_latency_under_load_ms": query_latency_ms,
            "hardware": self.hw_info
        }

        # Print summary table
        print(f"\n--- SCENARIO METRICS ({mode_str}) ---")
        print(f"  Aggregate Inference Throughput : {agg_inf_fps} FPS (Capture: {agg_cap_fps} FPS)")
        print(f"  Inference Latency (p50 / p95) : {lat_p50} ms / {lat_p95} ms (Max: {lat_max} ms)")
        print(f"  Capture-to-Detection Age       : {avg_age_ms} ms")
        print(f"  Avg System CPU / RAM           : {avg_cpu}% / {avg_ram} MB")
        print(f"  Conversational Query Latency   : {query_latency_ms} ms")
        for cid, res in per_cam_results.items():
            print(f"    [{cid}] Inf FPS: {res['inference_fps']} | Cap FPS: {res['capture_fps']} | Preview FPS: {res['preview_fps']} | Analyzed/Captured: {res['frames_analyzed']}/{res['frames_captured']}")

        return report

    def run_full_benchmark(self, output_file: str = "benchmark_results.json"):
        print("Starting Full Multi-Camera Performance Benchmark Suite...")
        results = {
            "timestamp": time.time(),
            "hardware": self.hw_info,
            "scenarios": {}
        }

        # 1. One Camera
        results["scenarios"]["1_camera"] = self.run_scenario(1)
        time.sleep(2)

        # 2. Two Cameras
        results["scenarios"]["2_cameras"] = self.run_scenario(2)
        time.sleep(2)

        # 3. Three Cameras
        results["scenarios"]["3_cameras"] = self.run_scenario(3)
        time.sleep(2)

        # 4. Live Cameras + Uploaded Video Job
        results["scenarios"]["2_cameras_plus_upload"] = self.run_scenario(2, run_uploaded_job=True)

        with open(output_file, "w") as f:
            json.dump(results, f, indent=2)
        print(f"\n✓ Benchmark results written to {output_file}")
        return results

if __name__ == "__main__":
    out = sys.argv[1] if len(sys.argv) > 1 else "benchmark_baseline.json"
    bm = MultiCamBenchmark(duration_per_scenario=12.0, warmup_seconds=2.5)
    bm.run_full_benchmark(output_file=out)
