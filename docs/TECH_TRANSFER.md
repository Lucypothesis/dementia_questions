# 인지검사 문항 생성 파이프라인 기술이전서

| 항목 | 내용 |
|---|---|
| 문서 버전 | 1.0.0 |
| 기준일 | 2026-10-16 |
| 대상 운영체제 | Ubuntu/Linux |
| Python | 3.10 이상 |
| 배포 형태 | GitHub 저장소, Python wheel, 기술이전 ZIP |
| 기본 생성 모델 | Qwen/Qwen3.8-27B, mistralai/Mistral-Small-24B-Instruct-2501 |
| 외부 OpenAI API | 사용하지 않음 |

## 1. 문서 목적

이 문서는 인지검사 문항 생성 벤치마크 및 제작 파이프라인을 새로운 운영자가 설치하고,
실제 로컬 LLM으로 문항을 생성하며, 이미지를 렌더하고, 사람 검수와 최종 50문제 조립까지
재현할 수 있도록 기술이전 절차를 정의한다.

납품 범위는 다음 세 가지다.

1. 버전 이력이 보존된 GitHub 저장소
2. 빈 Python 환경에 설치할 수 있는 실행 wheel과 기술이전 ZIP
3. 설치·운영·검증·장애 대응을 포함한 Word 기술이전 문서

본 납품본의 기본 설정은 OpenAI 서비스나 OpenAI API 키를 사용하지 않는다. 텍스트 생성은
로컬 vLLM 서버에서 수행하고, 기본 자동 이미지 judge는 외부 호출이 없는 stub이다. 최종
품질 판정은 생성된 HTML 검수 시트에서 사람이 수행한다.

## 2. 시스템 개요

파이프라인은 LLM의 문항 설계 능력과 이미지 렌더러의 명세 준수 능력을 분리해 측정한다.
최종 이미지만 평가하면 실패가 문항 JSON의 오류인지 이미지 생성기의 오류인지 구분하기
어렵기 때문이다.

```text
과제 명세 + 고정 슬롯 50개
          |
          v
로컬 LLM 문항 JSON 생성
          |
          v
스키마 검증 + 규칙 점수
          |
          v
compose 또는 diffusion 이미지 렌더
          |
          v
HTML 사람 검수(ok / minor / bad)
          |
          v
선택 정책 검증 + 최종 50문제 HTML
```

모든 중간 결과는 `out/<run>/`에 저장된다. 이미 존재하는 정상 산출물은 재사용하므로 GPU
작업이 중단되어도 같은 명령으로 이어서 실행할 수 있다.

## 3. 핵심 설계

### 3.1 고정 슬롯

문항은 기억력, 주의력, 언어능력, 시공간능력, 집행기능의 다섯 영역으로 구성한다. 각
영역은 10슬롯이며 총 50슬롯이다. 모든 LLM이 동일한 슬롯을 채우므로 모델이 자신에게 쉬운
유형만 선택하는 편향을 막는다.

### 3.2 구조화된 장면

이미지 요구사항은 자유 산문이 아니라 `scene` 객체로 저장한다. 사물, 좌표, 색, 방향,
크기와 장면 배치를 구조화한 뒤 파이프라인이 렌더 프롬프트를 생성한다. 서로 다른 LLM의
문체가 이미지 모델 입력에 직접 섞이지 않는다.

### 3.3 편집 기반 동일성

학습·검사 또는 기준·보기 장면처럼 대부분이 같아야 하는 패널은 두 번째 장면 전체를 다시
기술하지 않는다. `kind: edit`와 변경 목록만 선언하고 코드가 원본 장면을 복제해 지정된
변경만 적용한다.

### 3.4 정답 번호 균형

객관식 21슬롯에는 생성 전에 `target_answer`가 지정되어 있다. 목표 분포는 1번 5개,
2번 5개, 3번 5개, 4번 6개다. 생성 결과가 목표 번호와 다르면 저장 전에 보기, 정답과
해설 번호를 함께 교환하고 검증기가 다시 확인한다.

## 4. 구성 요소

| 경로 | 역할 |
|---|---|
| `src/itemgen_bench/` | Python 실행 코드와 `itemgen` CLI |
| `spec/task_spec.md` | LLM에 전달되는 과제 명세 |
| `spec/slots.json` | 영역별 10개, 총 50개 슬롯 |
| `spec/item_schema.json` | 문항 JSON 스키마 |
| `config/models.yaml` | 로컬 LLM, 렌더러, judge 설정 |
| `assets/` | compose 렌더러용 PNG 97개(실물 자산 96종과 보조 자산) |
| `run_all.sh` | 실제 로컬 모델을 순차 기동하는 무인 실행 스크립트 |
| `status.sh` | 실행 상태, GPU, 캐시, 산출물 요약 |
| `scripts/acceptance_test.sh` | wheel 단독 CPU 인수 테스트 |
| `scripts/gpu_acceptance.sh` | Qwen으로 문항 한 개를 실제 생성하는 GPU 인수 테스트 |
| `out/final50/` | 최종 50문제와 사람 판정 결과 |

wheel 설치본에는 기본 `spec/`, `config/`, `assets/`가 `share/itemgen-bench/` 아래에
포함된다. 현재 작업 디렉터리에 같은 이름의 디렉터리가 있으면 운영자가 수정한 파일을 먼저
사용한다.

## 5. 권장 실행 환경

### 5.1 CPU 기능 확인 환경

- Ubuntu/Linux
- Python 3.10 이상
- wheel 설치용 인터넷 또는 내부 Python 패키지 미러
- GPU 불필요

CPU만으로 `itemgen doctor`, `itemgen selftest`, `itemgen demo`, compose 렌더와 기존 최종
HTML 열람이 가능하다.

### 5.2 실제 LLM 생성 환경

- NVIDIA Linux 드라이버와 CUDA 호환 PyTorch
- NVIDIA GPU 한 장, 메모리 70GB 이상 권장
- 시스템 메모리 64GB 이상 권장
- 두 LLM 최초 다운로드 기준 캐시 여유 140GB 이상 권장
- vLLM 0.19 계열 또는 대상 모델을 지원하는 호환 버전
- Hugging Face 모델 다운로드가 가능한 네트워크 또는 사전 복사한 모델 캐시

Qwen과 Mistral은 동시에 올리지 않고 순차 기동한다. 기본 compose 렌더러는 CPU를 사용한다.
FLUX.2 klein 렌더러까지 실행하려면 추가로 약 20GB의 GPU 여유와 diffusers 계열 의존성이
필요하다.

### 5.3 실제 인수 테스트 기준 환경

2026-10-16에 다음 환경에서 Qwen 문항 1개를 실제 생성하고 검증·compose 렌더·HTML 생성까지
통과했다. 이 표는 모든 환경에 강제하는 설치 잠금이 아니라 문제 재현을 위한 기준점이다.

| 구성 | 검증 버전 |
|---|---|
| GPU | NVIDIA A100-SXM4-80GB, 81251 MiB |
| GPU Python | 3.12.3 |
| vLLM | 0.19.1 |
| PyTorch | 2.10.0+cu128 |
| transformers | 5.17.0 |
| diffusers | 0.37.1 |
| accelerate | 1.13.0 |
| safetensors | 0.7.0 |

CUDA·PyTorch·vLLM은 서로 호환되는 조합으로 설치해야 한다. 기관 표준 GPU 이미지가 위
버전과 다르면 먼저 `scripts/gpu_acceptance.sh` 한 슬롯 테스트를 통과시킨 뒤 전체 50슬롯을
실행한다.

## 6. wheel 설치와 CPU 인수 테스트

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install package/itemgen_bench-1.0.0-py3-none-any.whl

itemgen doctor
itemgen selftest
itemgen demo
itemgen serve --run demo
```

브라우저에서 `http://127.0.0.1:8012/sheet.html`을 연다. 다른 포트가 필요하면
`itemgen serve --run demo --port 8020`처럼 지정한다.

wheel 자체의 자동 인수 테스트는 저장소 또는 기술이전 ZIP 루트에서 수행한다.

```bash
bash scripts/acceptance_test.sh package/itemgen_bench-1.0.0-py3-none-any.whl
```

합격 조건은 wheel 단독 설치, 기본 리소스 발견, 68개 자기검증 통과, demo 실행과 HTML 생성이다.

## 7. 실제 문항 생성 준비

### 7.1 vLLM 설치

GPU 드라이버와 CUDA에 맞는 PyTorch를 먼저 설치한 뒤 vLLM을 설치한다.

```bash
pip install 'vllm>=0.19'
itemgen doctor --full
```

기관 내부 표준 CUDA 이미지가 있으면 해당 이미지에서 vLLM을 설치하는 편이 안전하다.
`itemgen doctor --full`은 vLLM 명령, NVIDIA 드라이버, 최대 GPU 메모리와 캐시 디스크를
확인한다.

### 7.2 캐시와 GPU 지정

```bash
export ITEMGEN_CACHE_ROOT=/mnt/model-cache
export GPUS="0"
export VLLM=$(command -v vllm)
```

`VLLM_API_KEY`의 기본값은 로컬 통신용 `dummy`다. 이것은 외부 API 키가 아니며 vLLM
프로세스는 `127.0.0.1`에만 열린다.

## 8. GPU 실제 생성 인수 테스트

먼저 한 슬롯만 실행한다. 최초 실행은 Qwen 가중치 다운로드 시간 때문에 오래 걸릴 수 있다.

```bash
GPU=0 ITEMGEN_CACHE_ROOT=/mnt/model-cache \
  bash scripts/gpu_acceptance.sh
```

이 스크립트는 다음 작업을 실제 수행한다.

1. Qwen3.8-27B vLLM 서버 기동
2. `MEM_01` 문항 JSON 생성
3. 스키마와 규칙 검증
4. compose 이미지 렌더
5. 외부 API 없는 judge 자리표시 생성
6. 사람 검수용 HTML 생성

합격 산출물은 `out/gpu_acceptance/` 아래의 문항 JSON, `check.json`, `panels.json`, 이미지와
`sheet.html`이다.

## 9. 전체 실행

기본 전체 실행은 Qwen과 Mistral로 50슬롯을 생성하고 compose로 렌더한 뒤 검수 시트를 만든다.

```bash
ITEMGEN_CACHE_ROOT=/mnt/model-cache GPUS="0" bash run_all.sh
```

사용 범위는 환경변수로 조절한다.

```bash
# Qwen만 전체 50슬롯
LLMS=qwen38_27b bash run_all.sh

# 지정한 다섯 슬롯만
RUN=pilot LLMS=qwen38_27b \
SLOT_IDS=MEM_01,ATT_01,LAN_01,VIS_01,EXE_01 \
bash run_all.sh

# compose와 FLUX.2 klein을 모두 렌더
RENDERERS="compose flux2_klein_4b" bash run_all.sh

# 실행하지 않고 계획만 표시
bash run_all.sh --dry-run
```

`RUN` 기본값은 `main`, `LLMS` 기본값은 두 로컬 LLM, `RENDERERS` 기본값은 `compose`,
`GPUS` 기본값은 `0`이다.

## 10. 단계별 수동 실행

vLLM 서버를 운영자가 별도로 관리하는 경우에는 `itemgen run`을 직접 사용할 수 있다.

```bash
export VLLM_API_KEY=dummy

itemgen run --run main --stages gen,check --llms qwen38_27b
itemgen run --run main --stages render --llms qwen38_27b --renderers compose
itemgen run --run main --stages judge,sheet --llms qwen38_27b --renderers compose
```

단계는 `plan`, `gen`, `check`, `render`, `judge`, `sheet` 순서다. 같은 명령을 다시 실행하면
기존 결과를 재사용한다. 강제 재생성은 `--force gen` 또는 `--force render`처럼 지정한다.

## 11. 결과 디렉터리

```text
out/<run>/
├── run.json
├── items/<llm>/<slot>.json
├── check.json
├── images/<llm>/<renderer>/*.png
├── panels.json
├── judge.json
├── sheet.html
└── logs/
```

- `items`: LLM이 생성하고 정답 위치 보정을 마친 문항 JSON
- `check.json`: 파싱 여부, 스키마 오류와 규칙 점수
- `images`: 패널별 PNG
- `panels.json`: 이미지와 문항 셀을 연결하는 색인
- `judge.json`: 기본 납품 설정에서는 stub 결과
- `sheet.html`: 사람 검수 인터페이스
- `logs`: vLLM, 생성, 렌더와 전체 실행 로그

## 12. 사람 검수와 최종 50문제

검수자는 HTML에서 각 셀을 다음과 같이 판정한다.

| 판정 | 의미 |
|---|---|
| `ok` | 수정 없이 사용 가능 |
| `minor` | 경미한 문제가 있으나 정책상 허용 가능 |
| `bad` | 문항 의도 또는 이미지가 사용 불가 |

현재 최종 정책은 `spec/renderer_policy_final_50.json`이며 판정 근거는
`out/final50/human_verdicts_v04.json`이다. 최종 50셀은 `ok` 48개, 명시적으로 허용한
`minor` 2개(`LAN_10`, `EXE_02`), `bad` 0개다. 보완 생성한 9개 셀은 모두 `ok`다.

최종 HTML을 근거와 함께 다시 조립하려면 저장소 또는 기술이전 ZIP 루트에서 실행한다.

```bash
itemgen finalize --policy spec/renderer_policy_final_50.json --run final50_rebuilt --embed
itemgen serve --run final50_rebuilt
```

정책은 영역별 10문제, 선택 셀 50개, 사람 판정 수와 허용 판정, 증거 파일, 객관식 정답
분포를 모두 검증한다. 근거 파일이나 이미지가 누락되면 조립을 중단한다.

## 13. 운영과 재개

### 13.1 상태 확인

```bash
bash status.sh main
tail -f out/main/logs/run_all.log
```

### 13.2 중단 후 재개

같은 `RUN`, `LLMS`, `SLOT_IDS`, `RENDERERS`로 `run_all.sh`를 다시 실행한다. 이미 완성된
문항과 이미지는 건너뛴다. 실행 잠금 파일은 동일 run을 동시에 두 번 실행하는 것을 막는다.

### 13.3 결과를 다시 만들 때

재생성은 기존 결과 비교와 추적이 가능하도록 새 `RUN` 이름을 권장한다. 기존 결과를
덮어쓸 때만 단계별 `--force`를 사용한다.

## 14. 장애 대응

### 14.1 `itemgen: command not found`

가상환경을 활성화하거나 `.venv/bin/itemgen` 전체 경로를 사용한다.

### 14.2 vLLM 실행 파일 없음

`VLLM=$(command -v vllm)`을 설정한다. 별도 가상환경에 있다면
`VLLM=/opt/vllm/bin/vllm`처럼 전체 경로를 지정한다.

### 14.3 GPU 메모리 부족

다른 프로세스를 종료하고 `GPUS`에 여유 카드만 지정한다. 두 LLM은 반드시 순차 실행한다.
diffusion 렌더가 필요하지 않으면 기본 `RENDERERS=compose`를 유지한다.

### 14.4 모델 다운로드 중 디스크 부족

`ITEMGEN_CACHE_ROOT`를 큰 디스크로 바꾼다. `HF_HOME`, `HF_HUB_CACHE`를 기관에서 이미
관리한다면 기존 값이 우선한다.

### 14.5 Qwen 출력 파싱 실패

Qwen 서버에 `--reasoning-parser qwen3`가 있어야 한다. `run_all.sh`는 이를 자동으로
지정한다. 수동 기동 시 빠뜨리면 사고 과정이 본문으로 출력되어 JSON이 잘릴 수 있다.

### 14.6 Mistral import 오류

기본 모델은 텍스트 전용 `Mistral-Small-24B-Instruct-2501`이다. 다른 멀티모달 변형은
vLLM과 transformers 버전 조합에 따라 비전 모듈 import 오류가 날 수 있다.

### 14.7 compose에서 사물이 글자로 표시됨

해당 사물 자산이 없다는 뜻이다. `ITEMGEN_ASSETS_DIR`가 동봉 `assets/`를 가리키는지
`itemgen doctor`로 확인한다. 새 자산은 `itemgen assets --list`와 `--build`로 만든다.

## 15. 보안과 네트워크

- 기본 설정에는 외부 OpenAI API 주소와 키가 없다.
- vLLM 서버는 `127.0.0.1`에만 바인딩한다.
- 실제 비밀값을 YAML이나 저장소에 기록하지 않는다.
- 모델 다운로드 시 Hugging Face 네트워크 접근이 발생할 수 있다.
- 폐쇄망은 승인된 모델 파일과 Python wheelhouse를 별도로 반입해야 한다.
- HTML 검수 결과에는 의료 대상자의 개인정보를 넣지 않는다.

## 16. 라이선스와 제약

코드의 배포 라이선스는 저장소 `LICENSE`에 기록된 MIT License다. 모델 가중치와 외부
Python 패키지는 각각의 라이선스를 따른다. 현재 모델 ID는 설정에 고정되어 있지만 실제
배포 전에는 기관 담당자가 해당 모델 카드의 라이선스와 이용 조건을 다시 확인해야 한다.

기본 stub judge는 이미지 품질을 자동 판정하지 않는다. 이는 외부 API 제거를 위한 의도된
동작이며 최종 품질 보증에는 사람 검수가 필요하다. 생성 모델 출력은 실행 환경과 모델
버전에 따라 달라질 수 있으므로 최종 제작본은 정책 파일과 사람 판정 증거를 함께 보존한다.

본 파이프라인과 생성 문항은 의료기기 성능 또는 임상적 유효성을 자동으로 보증하지 않는다.
실제 임상 사용 전에는 별도의 전문가 검토, 연구 설계와 기관 승인 절차가 필요하다.

## 17. 인수 기준

| 구분 | 합격 기준 |
|---|---|
| GitHub | 릴리스 태그의 commit과 납품 문서 기준 commit이 일치 |
| wheel | 빈 가상환경 설치 성공 |
| 리소스 | 50슬롯, 스키마, 설정, compose 자산 90개 이상 발견 |
| 자기검증 | `itemgen selftest` 68개 전부 통과 |
| CPU demo | 검수 HTML 생성 성공 |
| GPU 실제 생성 | Qwen으로 1문항 생성·검증·compose 렌더·HTML 생성 성공 |
| 최종 결과 | 영역별 10문제, 총 50문제 HTML 열람 가능 |
| 무결성 | 배포 파일 SHA-256 검증 성공 |
| 문서 | Word 기술이전서 열람 가능 |

## 18. 기술이전 체크리스트

- [ ] GitHub 저장소 접근 권한 전달
- [ ] `v1.0.0` 태그와 기준 commit 확인
- [ ] wheel과 source distribution 전달
- [ ] 기술이전 ZIP 압축 해제 확인
- [ ] SHA-256 체크섬 검증
- [ ] CPU 인수 테스트 통과
- [ ] 운영 GPU에서 `itemgen doctor --full` 통과
- [ ] Qwen 한 슬롯 실제 생성 테스트 통과
- [ ] 최종 50문제 HTML 확인
- [ ] 모델 저장 위치와 디스크 운영 정책 합의
- [ ] 사람 검수 담당자와 판정 파일 보관 위치 합의
- [ ] 장애 연락 및 유지보수 범위 합의

## 부록 A. 주요 명령 요약

```bash
itemgen doctor
itemgen doctor --full
itemgen selftest
itemgen demo
itemgen run --run main --stages plan
itemgen serve --run main
bash run_all.sh --dry-run
bash run_all.sh
bash status.sh main
bash scripts/acceptance_test.sh dist/itemgen_bench-1.0.0-py3-none-any.whl
bash scripts/gpu_acceptance.sh
```

## 부록 B. 환경변수

| 변수 | 기본값 | 설명 |
|---|---|---|
| `RUN` | `main` | `out/` 아래 실행 이름 |
| `LLMS` | Qwen, Mistral | 실행할 로컬 LLM 이름, 공백 구분 |
| `SLOT_IDS` | 전체 50개 | 쉼표로 지정한 슬롯만 실행 |
| `RENDERERS` | `compose` | 공백으로 지정한 렌더러 |
| `GPUS` | `0` | 사용할 GPU 후보, 공백 또는 쉼표 구분 |
| `GPU_WAIT` | `7200` | GPU 대기 최대 초 |
| `VLLM` | PATH의 `vllm` | vLLM 실행 파일 경로 |
| `ITEMGEN_CACHE_ROOT` | `<납품루트>/.cache` | 모델 캐시 상위 경로 |
| `ITEMGEN_SPEC_DIR` | 동봉 `spec/` | 사용자 명세 디렉터리 |
| `ITEMGEN_CONFIG` | 동봉 `config/models.yaml` | 사용자 모델 설정 파일 |
| `ITEMGEN_ASSETS_DIR` | 동봉 `assets/` | compose 자산 디렉터리 |
| `ITEMGEN_OUT` | 현재 폴더의 `out/` | 결과 루트 |
