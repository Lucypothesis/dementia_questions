#!/usr/bin/env python3
"""wheel·문서·최종 예시·최소 재조립 근거를 기술이전 ZIP으로 묶는다."""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import stat
import zipfile
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent


def copy_tree(source: Path, destination: Path) -> None:
    if not source.exists():
        raise SystemExit(f"필수 경로가 없다: {source}")
    shutil.copytree(
        source, destination, dirs_exist_ok=True,
        ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
    )


def copy_file(source: Path, destination: Path) -> None:
    if not source.is_file():
        raise SystemExit(f"필수 파일이 없다: {source}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, destination)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def copy_minimal_evidence(stage: Path, policy: dict) -> None:
    """finalize를 다시 돌릴 수 있는 선택 셀의 원본만 복사한다."""
    by_run: dict[str, list[dict]] = {}
    for cell in policy["selected_cells"]:
        by_run.setdefault(cell["source_run"], []).append(cell)

    for run_name, cells in by_run.items():
        source_run = ROOT / "out" / run_name
        target_run = stage / "out" / run_name
        panels = json.loads((source_run / "panels.json").read_text(encoding="utf-8"))
        keys = {(cell["slot_id"], cell["llm"], cell["renderer"]) for cell in cells}
        selected = [row for row in panels if (
            row.get("slot_id"), row.get("llm"), row.get("renderer")) in keys]
        if not selected:
            raise SystemExit(f"{run_name}에서 선택 패널을 찾지 못했다")
        target_run.mkdir(parents=True, exist_ok=True)
        (target_run / "panels.json").write_text(
            json.dumps(selected, ensure_ascii=False, indent=2), encoding="utf-8")

        for cell in cells:
            item = Path("items") / cell["llm"] / f"{cell['slot_id']}.json"
            copy_file(source_run / item, target_run / item)
        for row in selected:
            image = Path(row["image"])
            copy_file(source_run / image, target_run / image)
            thumb = Path(str(image).replace("images/", "thumbs/", 1)).with_suffix(".jpg")
            if (source_run / thumb).is_file():
                copy_file(source_run / thumb, target_run / thumb)

    evidence = {
        cell.get("human_evidence") for cell in policy["selected_cells"]
        if cell.get("human_evidence")
    }
    final_review = policy.get("final_human_review") or {}
    if final_review.get("source"):
        evidence.add(final_review["source"])
    for relative in sorted(evidence):
        copy_file(ROOT / relative, stage / relative)


def build(version: str, output_dir: Path) -> tuple[Path, Path]:
    wheel = next(iter(sorted((ROOT / "dist").glob("itemgen_bench-*.whl"))), None)
    sdist = next(iter(sorted((ROOT / "dist").glob("itemgen_bench-*.tar.gz"))), None)
    docx = ROOT / "docs" / "인지검사_문항생성_파이프라인_기술이전서_v1.0.docx"
    if not wheel or not sdist:
        raise SystemExit("dist/의 wheel과 source distribution을 먼저 빌드한다")
    if not docx.is_file():
        raise SystemExit("Word 문서를 먼저 생성한다: python scripts/build_word_doc.py")

    output_dir.mkdir(parents=True, exist_ok=True)
    stage = output_dir / f".itemgen-transfer-v{version}.staging"
    if stage.exists():
        shutil.rmtree(stage)
    stage.mkdir()

    for name in ("README.md", "LICENSE", "RELEASE_NOTES.md", "run_all.sh", "status.sh"):
        copy_file(ROOT / name, stage / name)
    for name in ("config", "spec", "assets", "scripts"):
        copy_tree(ROOT / name, stage / name)
    copy_file(docx, stage / "docs" / docx.name)
    copy_file(ROOT / "docs" / "TECH_TRANSFER.md", stage / "docs" / "TECH_TRANSFER.md")
    copy_file(wheel, stage / "package" / wheel.name)
    copy_file(sdist, stage / "package" / sdist.name)
    copy_tree(ROOT / "out" / "final50", stage / "out" / "final50")

    policy = json.loads(
        (ROOT / "spec" / "renderer_policy_final_50.json").read_text(encoding="utf-8"))
    copy_minimal_evidence(stage, policy)

    verification = stage / "verification"
    verification.mkdir(parents=True, exist_ok=True)
    for source_log in sorted(output_dir.glob("*acceptance*.log")):
        copy_file(source_log, verification / source_log.name)

    rows = []
    for path in sorted(p for p in stage.rglob("*") if p.is_file()):
        rows.append(f"{sha256(path)}  {path.relative_to(stage).as_posix()}")
    (verification / "SHA256SUMS").write_text("\n".join(rows) + "\n", encoding="utf-8")

    archive = output_dir / f"itemgen-transfer-v{version}.zip"
    if archive.exists():
        archive.unlink()
    with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED,
                         compresslevel=9) as bundle:
        for path in sorted(p for p in stage.rglob("*") if p.is_file()):
            relative = Path(f"itemgen-transfer-v{version}") / path.relative_to(stage)
            info = zipfile.ZipInfo.from_file(path, relative.as_posix())
            mode = path.stat().st_mode
            if path.suffix == ".sh" or path.parent.name == "scripts":
                mode |= stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH
            info.external_attr = mode << 16
            info.compress_type = zipfile.ZIP_DEFLATED
            with path.open("rb") as handle:
                bundle.writestr(info, handle.read(), compresslevel=9)

    shutil.rmtree(stage)

    published = []
    for source in (wheel, sdist, docx):
        destination = output_dir / source.name
        copy_file(source, destination)
        published.append(destination)
    release_files = [*published, archive]
    external = output_dir / "SHA256SUMS"
    external.write_text("\n".join(
        f"{sha256(path)}  {path.name}" for path in release_files
    ) + "\n", encoding="utf-8")
    return archive, external


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--version", default="1.0.0")
    parser.add_argument("--output-dir", type=Path, default=ROOT / "release")
    args = parser.parse_args()
    archive, checksums = build(args.version, args.output_dir.resolve())
    print(archive)
    print(checksums)


if __name__ == "__main__":
    main()
