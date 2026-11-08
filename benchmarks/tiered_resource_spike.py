"""Opt-in storage experiments, not a production tiered index or relevance benchmark.

Run with the project Python plus psutil. Writes fixtures and caches only in a
temporary directory. Vectors are synthetic except in embed mode, which uses
the cached default model. No model is downloaded.
"""

from __future__ import annotations

import argparse
import gc
import hashlib
import json
import os
import platform
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from importlib.metadata import version
from pathlib import Path
from types import SimpleNamespace
from typing import cast
from unittest.mock import patch

import numpy as np
import psutil
from huggingface_hub import snapshot_download
from model2vec import StaticModel
from vicinity.backends.basic import BasicArgs

from semble.index.create import create_index_from_path
from semble.index.dense import SelectableBasicBackend
from semble.index.files import detect_language
from semble.index.index import SembleIndex
from semble.utils import DEFAULT_MODEL_NAME

BLOCK = 65_536
DIM = 256


def encode(texts: list[str], **kwargs: object) -> np.ndarray:
    """Return deterministic unit vectors, intentionally without semantic meaning."""
    vectors = np.zeros((len(texts), DIM), dtype=np.float32)
    vectors[:, 0] = 1
    return vectors


def baseline(root: Path) -> dict:
    """Measure the existing builder, cache roundtrip and query."""
    model = cast(StaticModel, SimpleNamespace(dim=DIM, encode=encode))
    started = time.monotonic()
    try:
        bm25, dense, chunks, manifest = create_index_from_path(root, model, display_root=root)
    except ValueError as exc:
        if not str(exc).startswith("No supported files"):
            raise
        return {"excluded_by_baseline": True, "seconds": time.monotonic() - started}
    index = SembleIndex(model, bm25, dense, chunks, "synthetic", root=root, manifest=manifest)
    cache = root.parent / f"cache-{root.name}"
    index.save(cache)
    build_seconds = time.monotonic() - started
    started = time.monotonic()
    with patch("semble.index.index.load_model", return_value=(model, "synthetic")):
        loaded = SembleIndex.load_from_disk(cache)
    load_seconds = time.monotonic() - started
    started = time.monotonic()
    loaded._semantic_index.query(encode(["query"]), 10)
    query_seconds = time.monotonic() - started
    count = len(loaded.chunks)
    cache_bytes = {str(p.relative_to(cache)): p.stat().st_size for p in cache.rglob("*") if p.is_file()}
    del loaded, index, dense, bm25, chunks
    gc.collect()
    return {
        "chunks": count,
        "build_seconds": build_seconds,
        "load_seconds": load_seconds,
        "query_seconds": query_seconds,
        "cache_bytes": cache_bytes,
    }


def stream(root: Path) -> dict:
    """Measure fixed reads and reference writes without accumulating source text."""
    count = total = 0
    started = time.monotonic()
    with (root / "input.py").open("rb") as source, (root / "references.bin").open("wb") as refs:
        while block := source.read(BLOCK):
            refs.write(np.array([total, total + len(block), count, block.count(b"\n")], dtype="<u8").tobytes())
            count += 1
            total += len(block)
    return {
        "scanned_bytes": total,
        "windows": count,
        "seconds": time.monotonic() - started,
        "reference_bytes": (root / "references.bin").stat().st_size,
    }


def source_embed(root: Path) -> dict:
    """Measure bounded real model batches using only an already cached model."""
    snapshot = Path(snapshot_download(DEFAULT_MODEL_NAME, local_files_only=True))
    model = StaticModel.from_pretrained(str(snapshot))
    count = total = 0
    started = time.monotonic()
    with (root / "input.py").open("rb") as source, (root / "vectors.f32").open("wb") as output:
        while block := source.read(131_072):
            texts = [block[i : i + 4096].decode("utf-8", errors="replace") for i in range(0, len(block), 4096)]
            values = model.encode(texts, use_multiprocessing=False, max_length=512)
            output.write(np.asarray(values, dtype="<f4").tobytes())
            total += len(block)
            count += len(texts)
    return {
        "scanned_bytes": total,
        "vectors": count,
        "dimension": model.dim,
        "seconds": time.monotonic() - started,
        "vector_bytes": (root / "vectors.f32").stat().st_size,
        "model_snapshot": snapshot.name,
        "max_tokens": 512,
    }


def scan(root: Path, folded: bool = False) -> dict:
    """Measure literal search with bounded overlap, including a single-line input."""
    needle = b"needle_at_eof"
    total = matches = 0
    overlap = b""
    started = time.monotonic()
    with (root / "input.py").open("rb") as source:
        while block := source.read(BLOCK):
            data = overlap + block
            if folded:
                data = data.lower()
            matches += data.count(needle)
            overlap = data[-(len(needle) - 1) :]
            total += len(block)
    return {"scanned_bytes": total, "matches": matches, "seconds": time.monotonic() - started}


def scan_rg(root: Path) -> dict:
    """Compare the literal prototype with installed ripgrep, without printing lines."""
    started = time.monotonic()
    result = subprocess.run(
        ["rg", "--no-config", "-a", "-i", "-F", "-c", "needle_at_eof", str(root / "input.py")],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode not in (0, 1):
        raise RuntimeError(result.stderr)
    return {"seconds": time.monotonic() - started, "matching_lines": int(result.stdout or "0")}


def vectors(root: Path, bounded: bool) -> dict:
    """Compare vicinity's actual load with a bounded mapped-vector scan."""
    path = root / "vectors.npy"
    started = time.monotonic()
    if bounded:
        matrix = np.load(path, mmap_mode="r")
        load_seconds = time.monotonic() - started
        started = time.monotonic()
        best: list[tuple[float, int]] = []
        for offset in range(0, len(matrix), 4096):
            scores = matrix[offset : offset + 4096] @ encode(["query"])[0]
            ids = np.argsort(scores)[-10:]
            best = sorted(best + [(float(scores[i]), offset + int(i)) for i in ids], reverse=True)[:10]
        shared = True
    else:
        backend = SelectableBasicBackend.load(root)
        load_seconds = time.monotonic() - started
        started = time.monotonic()
        query_result = backend.query(encode(["query"]), 10)
        best = [
            (float(distance), int(row)) for rows, distances in query_result for row, distance in zip(rows, distances)
        ]
        shared = isinstance(backend.vectors, np.memmap)
    return {
        "vector_bytes": path.stat().st_size,
        "load_seconds": load_seconds,
        "query_seconds": time.monotonic() - started,
        "mapped": shared,
        "result_groups": len(best),
        "live_worker_memory": Path("/proc/self/status").read_text().split("VmPeak:", 1)[-1].split("Threads:", 1)[0],
    }


def memory_pages(members: list[psutil.Process]) -> tuple[int, int, int]:
    """Read Linux anonymous/file RSS counters when available; tolerate exited workers."""
    counts = {"RssAnon:": 0, "RssFile:": 0, "RssShmem:": 0}
    for member in members:
        try:
            lines = Path(f"/proc/{member.pid}/status").read_text().splitlines()
        except OSError:
            continue
        for line in lines:
            parts = line.split()
            if parts and parts[0] in counts:
                counts[parts[0]] += int(parts[1]) * 1024
    return counts["RssAnon:"], counts["RssFile:"], counts["RssShmem:"]


def measure(mode: str, root: Path) -> dict:
    """Sample child plus descendants RSS; keep launcher memory out of the metric."""
    env = dict(os.environ, OPENBLAS_NUM_THREADS="1", OMP_NUM_THREADS="1", SEMBLE_INDEX_WORKERS="2")
    command = [sys.executable, __file__, "--worker", mode, "--root", str(root)]
    with tempfile.TemporaryFile(mode="w+") as output:
        child = subprocess.Popen(command, stdout=output, env=env, start_new_session=True)
        process = psutil.Process(child.pid)
        peak = descendants_peak = anon_peak = file_peak = shmem_peak = 0
        started = time.monotonic()
        try:
            while child.poll() is None:
                rss = descendant_rss = 0
                try:
                    descendants = process.children(recursive=True)
                    for member in descendants:
                        try:
                            descendant_rss += member.memory_info().rss
                        except psutil.NoSuchProcess:
                            pass
                    rss = process.memory_info().rss + descendant_rss
                    anon, file_rss, shmem = memory_pages([process, *descendants])
                    anon_peak = max(anon_peak, anon)
                    file_peak = max(file_peak, file_rss)
                    shmem_peak = max(shmem_peak, shmem)
                except psutil.NoSuchProcess:
                    pass
                peak = max(peak, rss)
                descendants_peak = max(descendants_peak, descendant_rss)
                if time.monotonic() - started > 120:
                    raise TimeoutError(command)
                time.sleep(0.005)
        finally:
            if child.poll() is None:
                os.killpg(child.pid, signal.SIGKILL)
                child.wait()
        if child.returncode:
            raise RuntimeError(f"worker {mode} exited {child.returncode}")
        output.seek(0)
        result = json.load(output)
    return dict(
        result,
        mode=mode,
        peak_aggregate_rss=peak,
        peak_descendants_rss=descendants_peak,
        peak_aggregate_anon=anon_peak,
        peak_aggregate_file=file_peak,
        peak_aggregate_shmem=shmem_peak,
        elapsed_seconds=time.monotonic() - started,
    )


def write_fixture(path: Path, size: int) -> None:
    """Write physical bytes without holding the generated input in memory."""
    block = (b"def identity(value):\n    return value\n\n" * 1800)[:BLOCK]
    with path.open("wb") as output:
        remaining = size
        while remaining:
            piece = block[: min(remaining, len(block))]
            output.write(piece)
            remaining -= len(piece)


def advise_cold_pages(root: Path) -> None:
    """Flush fixture files then request per-file page eviction without privileges."""
    for path in root.iterdir():
        with path.open("rb") as handle:
            os.fsync(handle.fileno())
            os.posix_fadvise(handle.fileno(), 0, 0, os.POSIX_FADV_DONTNEED)


def run(tmpdir: Path | None) -> dict:
    """Run opt-in Linux experiments (requires findmnt and rg)."""
    results = []
    if shutil.disk_usage(tmpdir or tempfile.gettempdir()).free < 4_000_000_000:
        raise OSError("Resource spike requires at least 4 GB free temporary space")
    with tempfile.TemporaryDirectory(prefix="semble-spike-", dir=tmpdir) as temp:
        root = Path(temp)
        filesystem = subprocess.check_output(["findmnt", "-T", temp, "-n", "-o", "FSTYPE,SOURCE"], text=True).strip()
        for size in (100_000, 4_000_000, 32_000_000, 128_000_000, 1_000_000_001):
            fixture = root / str(size)
            fixture.mkdir()
            write_fixture(fixture / "input.py", size)
            modes = ["baseline", "stream", "scan"]
            if 1_000_000 < size <= 128_000_000:
                modes.append("embed")
            for mode in modes:
                for repeat in range(2):
                    if repeat == 0:
                        advise_cold_pages(fixture)
                    results.append(
                        dict(
                            measure(mode, fixture),
                            size=size,
                            repeat=repeat,
                            cache_hint="fsync-fadvise-DONTNEED" if repeat == 0 else "warm-repeat",
                        )
                    )
        results.extend(additional_fixtures(root))
    return {
        "platform": platform.platform(),
        "python": platform.python_version(),
        "packages": {name: version(name) for name in ("numpy", "vicinity", "model2vec", "psutil")},
        "baseline": "3ff4086",
        "script_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "git_describe": subprocess.check_output(["git", "describe", "--always", "--dirty"], text=True).strip(),
        "sampling_min_seconds": 0.005,
        "filesystem": filesystem,
        "results": results,
    }


def additional_fixtures(root: Path) -> list[dict]:
    """Measure workers, one giant line and a mapped vector matrix."""
    results = []
    parallel = root / "parallel"
    parallel.mkdir()
    for number in range(200):
        (parallel / f"input{number}.py").write_bytes(b"def identity(value):\n    return value\n" * 100)
    results.append(dict(measure("baseline", parallel), fixture="200-files-2-workers"))
    long_line = root / "long-line"
    long_line.mkdir()
    with (long_line / "input.py").open("wb") as output:
        for _ in range(1024):
            output.write(b"x" * 1_048_576)
        output.write(b"needle_at_eof")
    for mode in ("scan", "folded", "rg"):
        results.append(dict(measure(mode, long_line), fixture="1-GiB-single-line"))
    vector_root = root / "vector"
    vector_root.mkdir()
    matrix = np.lib.format.open_memmap(vector_root / "vectors.npy", mode="w+", dtype="float32", shape=(131_072, DIM))
    for start in range(0, len(matrix), 4096):
        matrix[start : start + 4096] = encode([""] * min(4096, len(matrix) - start))
    matrix.flush()
    del matrix
    BasicArgs().dump(vector_root / "arguments.json")
    for mode in ("vicinity", "mapped"):
        for repeat in range(2):
            results.append(dict(measure(mode, vector_root), repeat=repeat))
    return results


def diagnostics(repos: list[Path]) -> dict:
    """Count tokenizer density and tracked supported-extension files, without paths."""
    snapshot = snapshot_download(DEFAULT_MODEL_NAME, local_files_only=True)
    model = StaticModel.from_pretrained(snapshot)
    model.tokenizer.no_truncation()
    samples = {
        "repeated-python": b"def identity(value):\n    return value\n\n",
        "minified-js": b"function f(x){return x.map((v,i)=>({id:i,value:v*2}));}",
        "hex": bytes(range(256)).hex().encode(),
        "json": b'{"id":12345,"items":[true,false,null,12.34],"name":"generated"}',
        "repo-python": (Path(__file__).resolve().parents[1] / "src/semble/index/index.py").read_bytes()[:4096],
    }
    density = {}
    for name, sample in samples.items():
        window = (sample * (4096 // len(sample) + 1))[:4096].decode("utf-8", errors="replace")
        count = len(model.tokenizer.encode(window, add_special_tokens=False).ids)
        density[name] = {
            "bytes": len(window.encode()),
            "tokens": count,
            "tokens_per_KiB": count / 4,
            "split_windows_at_512_min": (count + 511) // 512,
        }
    inventories = []
    for repo in repos:
        names = subprocess.check_output(["git", "-C", str(repo), "ls-files", "-z"])
        sizes = [
            (repo / os.fsdecode(name)).lstat().st_size
            for name in names.split(b"\0")
            if name
            and detect_language(Path(os.fsdecode(name)))
            and (repo / os.fsdecode(name)).is_file()
            and not (repo / os.fsdecode(name)).is_symlink()
        ]
        inventories.append(
            {
                "supported_extension_tracked_files": len(sizes),
                "above_1MB": sum(size > 1_000_000 for size in sizes),
                "above_100MB": sum(size > 100_000_000 for size in sizes),
                "above_1GB": sum(size > 1_000_000_000 for size in sizes),
            }
        )
    return {
        "model": DEFAULT_MODEL_NAME,
        "dimension": model.dim,
        "token_density": density,
        "inventory_scope": "tracked supported extensions; upper bound before content/binary filters; no paths",
        "repositories": inventories,
    }


def table(path: Path) -> str:
    """Render every experiment with explicitly named operation timing columns."""
    rows = [
        "| Mode / fixture | Input bytes | Repeat | Cache hint | Build s | Load s | Query s | Operation s | Excluded | "
        "RSS MiB | Anon MiB | File MiB | Shmem MiB |",
        "| --- | ---: | ---: | --- | ---: | ---: | ---: | ---: | --- | ---: | ---: | ---: | ---: |",
    ]
    for result in json.loads(path.read_text())["results"]:
        times = [
            f"{result[key]:.4f}" if key in result else "—"
            for key in ("build_seconds", "load_seconds", "query_seconds", "seconds")
        ]
        memory = [
            f"{result.get(key, 0) / 2**20:.1f}"
            for key in ("peak_aggregate_rss", "peak_aggregate_anon", "peak_aggregate_file", "peak_aggregate_shmem")
        ]
        cells = [
            result["mode"] + " " + result.get("fixture", ""),
            str(result.get("size", result.get("vector_bytes", ""))),
            str(result.get("repeat", "—")),
            result.get("cache_hint", "—"),
            *times,
            str(result.get("excluded_by_baseline", False)),
            *memory,
        ]
        rows.append("| " + " | ".join(cells) + " |")
    return "\n".join(rows)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--worker", choices=("baseline", "stream", "scan", "folded", "rg", "embed", "vicinity", "mapped")
    )
    parser.add_argument("--root", type=Path)
    parser.add_argument("--tmpdir", type=Path)
    parser.add_argument("--table", type=Path)
    parser.add_argument("--inventory", type=Path, nargs="+")
    args = parser.parse_args()
    answer: dict | list[dict]
    if args.worker == "baseline":
        answer = baseline(args.root)
    elif args.worker == "stream":
        answer = stream(args.root)
    elif args.worker in ("scan", "folded"):
        answer = scan(args.root, args.worker == "folded")
    elif args.worker == "rg":
        answer = scan_rg(args.root)
    elif args.worker == "embed":
        answer = source_embed(args.root)
    elif args.worker:
        answer = vectors(args.root, args.worker == "mapped")
    elif args.table:
        print(table(args.table))
        sys.exit(0)
    elif args.inventory:
        answer = diagnostics(args.inventory)
    else:
        answer = run(args.tmpdir)
    print(json.dumps(answer, indent=2))
