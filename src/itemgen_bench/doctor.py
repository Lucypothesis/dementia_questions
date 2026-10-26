"""기술이전 환경 사전 점검.

기본 점검은 wheel에 명세·설정·compose 자산이 제대로 들어갔는지 본다.
--full은 실제 로컬 LLM 생성에 필요한 vLLM, NVIDIA GPU와 디스크도 확인한다.
"""

from __future__ import annotations

import argparse
import json
import os
import shlex
import shutil
import subprocess
import sys
from pathlib import Path

from . import paths


def _gpu_memory_mib() -> list[int]:
    try:
        result = subprocess.run(
            ["nvidia-smi", "--query-gpu=memory.total", "--format=csv,noheader,nounits"],
            check=True, capture_output=True, text=True, timeout=15,
        )
    except (OSError, subprocess.SubprocessError):
        return []
    values = []
    for line in result.stdout.splitlines():
        try:
            values.append(int(line.strip()))
        except ValueError:
            continue
    return values


def _vllm_python(vllm: str) -> str:
    """vLLM 콘솔 스크립트가 실제로 사용하는 Python을 찾는다."""
    try:
        first_line = Path(vllm).read_text(encoding="utf-8", errors="replace").splitlines()[0]
        command = shlex.split(first_line[2:]) if first_line.startswith("#!") else []
    except (OSError, IndexError, ValueError):
        command = []

    if command and Path(command[0]).name == "env":
        program = next((arg for arg in command[1:] if not arg.startswith("-")), "")
        return shutil.which(program) or sys.executable
    if command and Path(command[0]).is_file():
        return command[0]
    return sys.executable


def _probe_vllm_cuda(vllm: str) -> tuple[bool, str]:
    """vLLM 환경에서 PyTorch가 GPU를 실제 초기화할 수 있는지 확인한다."""
    code = """
import importlib.metadata
import torch

torch.cuda.init()
torch.empty(1, device="cuda")
print(
    f"vLLM {importlib.metadata.version('vllm')}, "
    f"PyTorch {torch.__version__}, CUDA {torch.version.cuda}, "
    f"{torch.cuda.get_device_name(0)}"
)
"""
    python = _vllm_python(vllm)
    try:
        result = subprocess.run(
            [python, "-c", code], capture_output=True, text=True, timeout=45,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return False, f"{python}: {exc}"

    output = [line.strip() for line in (result.stdout + "\n" + result.stderr).splitlines()
              if line.strip()]
    detail = output[-1] if output else f"종료 코드 {result.returncode}"
    return result.returncode == 0, detail


def main(argv=None) -> None:
    parser = argparse.ArgumentParser(description="itemgen 실행 환경 사전 점검")
    parser.add_argument(
        "--full", action="store_true",
        help="Qwen/Mistral 실제 생성용 vLLM·GPU·디스크까지 확인",
    )
    args = parser.parse_args(argv)
    passed = 0
    warnings: list[str] = []
    failures: list[str] = []

    def check(condition: bool, message: str, detail: str = "") -> None:
        nonlocal passed
        if condition:
            passed += 1
            print(f"[PASS] {message}{(' — ' + detail) if detail else ''}")
        else:
            failures.append(message)
            print(f"[FAIL] {message}{(' — ' + detail) if detail else ''}")

    check(sys.version_info >= (3, 10), "Python 3.10 이상", sys.version.split()[0])

    spec_dir = paths.spec_dir()
    config = paths.config_path()
    assets = paths.assets_dir()
    check((spec_dir / "task_spec.md").is_file(), "과제 명세서 발견", str(spec_dir))
    check((spec_dir / "item_schema.json").is_file(), "문항 스키마 발견")
    slots_path = spec_dir / "slots.json"
    try:
        slots_doc = json.loads(slots_path.read_text(encoding="utf-8"))
        slots = slots_doc["slots"] if isinstance(slots_doc, dict) else slots_doc
    except (OSError, ValueError, KeyError, TypeError):
        slots = []
    check(len(slots) == 50, "영역별 10개·총 50슬롯", f"실제 {len(slots)}개")

    check(config.is_file(), "기본 모델 설정 발견", str(config))
    try:
        import yaml
        cfg = yaml.safe_load(config.read_text(encoding="utf-8"))
    except Exception:
        cfg = {}
    llms = cfg.get("llms") or []
    remote_urls = [entry.get("base_url", "") for entry in llms
                   if entry.get("base_url") and "127.0.0.1" not in entry["base_url"]
                   and "localhost" not in entry["base_url"]]
    check(not remote_urls, "기본 LLM 설정은 외부 API를 사용하지 않음",
          ", ".join(remote_urls) if remote_urls else "local vLLM")
    check((cfg.get("judge") or {}).get("adapter") == "stub",
          "기본 자동 판정은 외부 API 없는 stub")

    asset_count = len(list(assets.glob("*.png"))) if assets.is_dir() else 0
    check(asset_count >= 90, "compose 자산 동봉", f"PNG {asset_count}개: {assets}")

    for module in ("PIL", "requests", "yaml"):
        try:
            __import__(module)
        except ImportError:
            check(False, f"필수 Python 모듈 import: {module}")
        else:
            check(True, f"필수 Python 모듈 import: {module}")

    if args.full:
        vllm_value = os.environ.get("VLLM") or "vllm"
        vllm = shutil.which(vllm_value)
        check(bool(vllm), "vLLM 실행 파일", str(vllm or "PATH에서 찾지 못함"))
        if vllm:
            cuda_ok, cuda_detail = _probe_vllm_cuda(vllm)
            check(cuda_ok, "vLLM 환경의 PyTorch CUDA 초기화", cuda_detail)
        check(bool(shutil.which("setsid")), "프로세스 그룹 명령: setsid")
        check(bool(shutil.which("flock")), "중복 실행 잠금 명령: flock")
        check(bool(shutil.which("nvidia-smi")), "NVIDIA 드라이버 명령")
        memories = _gpu_memory_mib()
        largest = max(memories, default=0)
        check(largest >= 70 * 1024, "70GB 이상 GPU 메모리",
              f"최대 {largest / 1024:.1f}GB")

        cache_root = Path(
            os.environ.get("ITEMGEN_CACHE_ROOT")
            or os.environ.get("HF_HOME")
            or (Path.cwd() / ".cache")
        ).expanduser()
        probe = cache_root if cache_root.exists() else cache_root.parent
        try:
            free_gb = shutil.disk_usage(probe).free / (1024 ** 3)
        except OSError:
            free_gb = 0
        if free_gb < 140:
            warnings.append(
                f"모델 캐시 디스크 여유가 {free_gb:.0f}GB다. "
                "두 LLM을 처음 받을 때는 140GB 이상을 권장한다."
            )
            print(f"[WARN] {warnings[-1]}")
        else:
            passed += 1
            print(f"[PASS] 모델 캐시 디스크 여유 — {free_gb:.0f}GB")

    print(f"\n결과: PASS {passed} / WARN {len(warnings)} / FAIL {len(failures)}")
    if failures:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
