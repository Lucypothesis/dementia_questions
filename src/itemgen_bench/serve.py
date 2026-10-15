"""검수 시트용 정적 서버. 캐시를 끈다.

python -m http.server를 쓰면 브라우저(특히 VSCode Simple Browser)가 HTML을 쥐고 있어서
시트를 다시 만들어도 옛 화면이 보인다. 매번 강력 새로고침하는 대신 서버가 막는다.

  python serve.py            # 127.0.0.1:8012
  python serve.py --port 9000 --bind 0.0.0.0
"""

import argparse
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from . import paths


class NoCacheHandler(SimpleHTTPRequestHandler):
    def end_headers(self):
        self.send_header("Cache-Control", "no-store, no-cache, must-revalidate, max-age=0")
        self.send_header("Pragma", "no-cache")
        self.send_header("Expires", "0")
        super().end_headers()

    def send_head(self):
        # 조건부 요청(If-Modified-Since)으로 304가 나가면 캐시 금지가 무의미해진다.
        self.headers.replace_header("If-Modified-Since", "") \
            if "If-Modified-Since" in self.headers else None
        if "If-None-Match" in self.headers:
            del self.headers["If-None-Match"]
        return super().send_head()

    def log_message(self, fmt, *args):
        pass   # 요청 로그로 터미널을 채우지 않는다


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8012)
    parser.add_argument("--bind", default="127.0.0.1")
    parser.add_argument("--run", default="main", help="out/ 아래 실행 이름")
    parser.add_argument("--dir", default=None)
    args = parser.parse_args()

    root = Path(args.dir) if args.dir else (paths.out_dir() / args.run)
    if not root.exists():
        raise SystemExit(f"결과 폴더가 없다: {root}")
    handler = partial(NoCacheHandler, directory=str(root))
    server = ThreadingHTTPServer((args.bind, args.port), handler)
    print(f"serving {root} at http://{args.bind}:{args.port}/sheet.html  (캐시 금지)")
    server.serve_forever()


