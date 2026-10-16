# v1.0.0 기술이전 릴리스

## 납품 범위

- 영역별 10문제, 총 50문제의 최종 HTML 및 생성 근거
- Qwen3.8-27B·Mistral-Small-24B 로컬 vLLM 기반 문항 생성 파이프라인
- compose 및 선택형 diffusion 렌더러
- 스키마·규칙 검증, 사람 검수 시트, 최종 정책 조립 기능
- 저장소 없이 설치 가능한 Python wheel과 기술이전 ZIP
- 기술이전 Word 문서 및 CPU/GPU 인수 테스트 스크립트

## 최종 검수 결과

- 최종 50셀 전수 사람 검수
- `ok` 48개, 허용 `minor` 2개(`LAN_10`, `EXE_02`), `bad` 0개
- 영역별 10문제 고정
- 객관식 21문제 정답 위치: 1번 5개, 2번 5개, 3번 5개, 4번 6개

## 실행 정책

- 기본 실행은 외부 OpenAI API를 사용하지 않는다.
- Qwen과 Mistral을 로컬 vLLM으로 순차 기동한다.
- 기본 렌더러는 동봉 자산을 사용하는 `compose`다.
- 기본 자동 judge는 `stub`이며 최종 품질 판정은 HTML 사람 검수로 수행한다.
- `RENDERERS="compose flux2_klein_4b"`로 diffusion 렌더를 추가할 수 있다.

## 호환 환경

- Ubuntu/Linux
- Python 3.10 이상
- 실제 24~27B LLM 생성: NVIDIA GPU 메모리 70GB 이상 권장
- 최초 모델 다운로드: 캐시 디스크 여유 140GB 이상 권장
