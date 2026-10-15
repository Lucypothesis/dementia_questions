"""`itemgen` 명령. 긴 python 호출을 짧은 하위명령으로 감싼다.

  itemgen demo            비용 0·GPU 0으로 전 구간을 1초 안에 돌려본다
  itemgen run --stages gen,check --llms gpt6_sol
  itemgen selftest        외부 모델 없이 48항목 자기검증
  itemgen watch           실시간 진행
  itemgen thumbs          검수용 축소본 생성 (저장소에 넣을 용도)
  itemgen serve           결과 시트를 브라우저로 (캐시 금지)
"""

from __future__ import annotations

import sys

HELP = __doc__


def main(argv=None) -> None:
    argv = list(sys.argv[1:] if argv is None else argv)
    cmd = argv[0] if argv else ""
    rest = argv[1:]

    if cmd in ("", "-h", "--help", "help"):
        print(HELP)
        return

    if cmd == "demo":
        # 받는 사람이 가장 먼저 쳐보는 명령. 실제 모델을 쓰지 않으므로
        # GPU도 API 키도 필요 없고 1초 안에 끝난다. 구조를 눈으로 확인하는 용도다.
        from . import paths, pipeline
        cfg = paths.REPO_ROOT / "config" / "models.stub.yaml"
        args = ["--run", "demo", "--slots", "10", "--config", str(cfg)] + rest
        sys.argv = ["itemgen demo"] + args
        pipeline.main()
        out = paths.out_dir() / "demo" / "sheet.html"
        print(f"\n시트: {out}\n보려면: itemgen serve")
        return

    if cmd == "run":
        from . import pipeline
        sys.argv = ["itemgen run"] + rest
        pipeline.main()
        return

    if cmd == "selftest":
        from . import selftest
        selftest.main()
        return

    if cmd == "watch":
        from . import watch
        sys.argv = ["itemgen watch"] + rest
        watch.main()
        return

    if cmd == "thumbs":
        from . import thumbs
        thumbs.main(rest)
        return

    if cmd == "serve":
        from . import serve
        sys.argv = ["itemgen serve"] + rest
        serve.main()
        return

    raise SystemExit(f"모르는 명령: {cmd!r}\n\n{HELP}")
