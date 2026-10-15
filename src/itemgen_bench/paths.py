"""spec/ · config/ · out/ 을 어디서 찾을지 한곳에서 정한다.

이 코드는 세 가지 방식으로 돌아간다. 어느 쪽이든 같은 파일을 가리켜야 한다.

  1. 저장소를 clone 해서 그 안에서 실행       -> 저장소 루트의 spec/ config/
  2. pip install -e . 로 깔고 저장소 안에서    -> 같음
  3. 다른 디렉터리에서 실행                    -> 현재 작업 디렉터리에 있으면 그것

규칙은 단순하다. 명시적으로 준 경로 > 현재 작업 디렉터리 > 저장소 루트.
현재 디렉터리를 저장소보다 먼저 보는 이유는, 받는 쪽이 자기 슬롯·명세서로
바꿔 돌릴 때 원본을 건드리지 않고 자기 폴더에 두면 되게 하기 위해서다.
"""

from __future__ import annotations

import os
from pathlib import Path

# src/itemgen_bench/paths.py -> src/itemgen_bench -> src -> 저장소 루트
REPO_ROOT = Path(__file__).resolve().parent.parent.parent


def _pick(name: str, override: str | Path | None, env: str) -> Path:
    if override:
        p = Path(override).expanduser().resolve()
        if not p.exists():
            raise SystemExit(f"{name} 경로가 없다: {p}")
        return p
    if os.environ.get(env):
        return Path(os.environ[env]).expanduser().resolve()
    here = Path.cwd() / name
    if here.exists():
        return here.resolve()
    return REPO_ROOT / name


def spec_dir(override=None) -> Path:
    """task_spec.md · slots.json · item_schema.json 이 있는 곳."""
    return _pick("spec", override, "ITEMGEN_SPEC_DIR")


def config_path(override=None) -> Path:
    """models.yaml. 디렉터리를 주면 그 안의 models.yaml을 쓴다."""
    if override:
        p = Path(override).expanduser().resolve()
        if p.is_dir():
            p = p / "models.yaml"
        if not p.exists():
            raise SystemExit(f"설정 파일이 없다: {p}")
        return p
    if os.environ.get("ITEMGEN_CONFIG"):
        return Path(os.environ["ITEMGEN_CONFIG"]).expanduser().resolve()
    here = Path.cwd() / "config" / "models.yaml"
    if here.exists():
        return here.resolve()
    return REPO_ROOT / "config" / "models.yaml"


def out_dir(override=None) -> Path:
    """결과가 쌓이는 곳.

    spec/·config/ 와 달리 출력은 저장소로 떨어지면 안 된다. 다른 폴더에서 돌렸는데
    설치된 저장소 안에 수백 MB가 쌓이면 남의 작업물을 덮어쓴다. 그래서 규칙이 다르다.
    저장소 안에서 돌릴 때만 저장소 out/, 그 밖에서는 언제나 현재 디렉터리.
    """
    if override:
        return Path(override).expanduser().resolve()
    if os.environ.get("ITEMGEN_OUT"):
        return Path(os.environ["ITEMGEN_OUT"]).expanduser().resolve()
    cwd = Path.cwd().resolve()
    inside_repo = cwd == REPO_ROOT or REPO_ROOT in cwd.parents
    return (REPO_ROOT / "out") if inside_repo else (cwd / "out")
