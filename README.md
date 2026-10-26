# 인지검사 문항 생성 벤치마크 파이프라인

같은 과제명세서를 여러 LLM에 주고 문항을 만들게 한 뒤, 그 문항을 여러 이미지 생성
모델로 그려서 **LLM의 능력과 렌더러의 능력을 따로** 재는 파이프라인.

```
과제명세서 ──→ LLM ──→ 문항 JSON ──→ [측정 1 · check]   LLM만의 능력
                        │              스키마 합격률 · 룰 10종
                        ▼
                     렌더러 ──→ 이미지 ──→ [측정 2 · judge]  렌더러 능력
                                            명세-이미지 대조
                                              ▼
                                         [사람 검수 · sheet]
```

측정 지점이 둘인 이유: 최종 이미지만 보면 실패가 LLM 탓인지 렌더러 탓인지 분리되지
않는다. LLM N개 × 렌더러 D개를 섞으면 더 심해진다.

## 바로 확인하기

- 웹 데모: [https://dementia-questions.netlify.app/](https://dementia-questions.netlify.app/)
- 실행 패키지·기술이전서: [GitHub Release v1.1.0](https://github.com/Lucypothesis/dementia_questions/releases/tag/v1.1.0)

웹 데모와 `itemgen demo`가 생성하는 로컬 결과는 같은 카드형 UI를 사용한다.

## 핵심 설계 세 가지

**1. 장면을 산문이 아니라 구조로 받는다.** `image_spec`을 문장으로 쓰게 하면 문체가
교란변수가 된다. `scene` 객체에 사물과 좌표를 열거하게 하고, 디퓨전에 넣을 산문은
파이프라인이 템플릿으로 생성한다. 모든 arm에 같은 템플릿이 적용되므로 남는 차이는
장면 설계의 차이뿐이다.

**2. 같은 장면을 두 번 적지 않는다.** `학습`/`검사`처럼 거의 같은 장면이 반복되는
문항에서 두 번째 패널을 다시 기술하면 두 이미지가 미묘하게 달라져 문항이 깨진다.
두 번째 패널은 `kind: "edit"`으로 **변경만** 선언하고, 파이프라인이 첫 패널을 복제해
그 변경만 적용한다. 보기 4장도 기준에서 파생시킨다. 동일성이 모델의 선의가 아니라
코드로 보장된다. 50슬롯 중 19개가 이 경로를 쓴다.

**3. 슬롯을 고정한다.** 모든 LLM이 같은 50칸(영역 5 × 10)을 채운다. 자유롭게 만들게
하면 모델마다 쉬운 유형을 골라 비교가 불가능해진다.

## 1분 만에 돌려보기

GPU도 API 키도 필요 없다. stub 어댑터가 실제 모델 자리에 들어가 배선만 돌린다.

```bash
python3 -m venv .venv && source .venv/bin/activate   # 권장
pip install -e .
itemgen demo        # 1초 안에 전 구간: 문항 생성 -> 검증 -> 렌더 -> 채점 -> 시트
itemgen serve --run demo
```

브라우저에서 `http://127.0.0.1:8012/sheet.html`을 열면 새 카드형 UI로 생성 결과를
확인할 수 있다. 문항카드는 2열이며, 각 카드 안에는 문제 → 이미지 → 정답 순서로 표시된다.
`검수 정보 보기`를 누르면 모델·렌더러 정보와 사람 판정 기능이 나타난다.

`itemgen: command not found`가 나오면 설치한 가상환경이 활성화되지 않은 것이다.
`source <venv>/bin/activate` 하거나 `<venv>/bin/itemgen` 전체 경로로 부른다.

구조를 먼저 눈으로 확인한 뒤 실제 모델을 꽂는 순서를 권한다.

wheel 납품본을 저장소 없이 설치할 때도 같은 명령이 동작한다. 기본 명세 50개,
로컬 모델 설정과 compose PNG 97개(실물 자산 96종과 보조 자산)가 함께 들어 있다.

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install package/itemgen_bench-1.0.0-py3-none-any.whl
itemgen doctor       # 동봉 리소스·필수 모듈 점검
itemgen selftest     # 68항목
itemgen demo
```

## 디렉터리

```
pyproject.toml            의존성·버전·`itemgen` 명령 선언
spec/task_spec.md         LLM에 그대로 들어가는 과제명세서 본문 (9절)
spec/slots.json           문항 슬롯 50개 (영역 5 x 10)
spec/item_schema.json     출력 JSON 스키마
config/models.yaml        실험 설정 (arm 정의)
config/models.stub.yaml   비용 0 배선 확인용
src/itemgen_bench/
  spec.py                 명세서 조립 · 스키마 검증 · 룰 채점
  render.py               scene -> 산문 / 이미지, edit 적용
  shapes.py               원·다각형·회전·반전·면별 정육면체 코드 합성
  assets_build.py         compose용 단일 사물 자산 생성·배경 제거
  human_compare.py        동일 셀 사람 판정 비교·제작 정책 산출
  finalize.py             여러 run의 선택 셀을 최종 50문제 HTML로 조립
  models.py               LLM · 렌더러 · judge 어댑터
  pipeline.py             6단계 오케스트레이터
  sheet.py                사람 검수용 N-way 시트
  slots_rank.py           사람 판정 기반 슬롯 품질 순위·스냅샷
  selftest.py             외부 모델 없이 도는 자기검증 68항목
  watch.py                실시간 진행 감시기
  serve.py                캐시 없는 정적 서버
  paths.py                spec/·config/·out/ 위치 결정
  doctor.py               기본 리소스·실제 생성 환경 사전 점검
  cli.py                  `itemgen` 하위명령
run_all.sh                무인 실행 스크립트
status.sh                 한 화면 상태 요약
scripts/acceptance_test.sh wheel 단독 CPU 인수 테스트
scripts/gpu_acceptance.sh  로컬 Qwen으로 문항 1개 실제 생성 인수 테스트
out/<run>/                실행 결과
```

`spec/`와 `config/`는 일부러 저장소 루트에 두었다. 받는 쪽이 바꿀 것은 코드가 아니라
슬롯·명세서·arm 목록이기 때문이다. 패키지 안에 숨기지 않는다.

## 실행

```bash
itemgen selftest                              # 자기검증 68항목 (비용 0)
itemgen run --run main --stages plan          # 규모·비용 계산만
itemgen doctor --full                         # vLLM·GPU·디스크까지 점검
bash run_all.sh                               # Qwen+Mistral 생성, compose 렌더, 시트
GPUS="3 0 1" bash run_all.sh                  # 비는 카드를 골라 순차 실행
```

기본 제작 실행은 **외부 API와 OpenAI API 키를 전혀 사용하지 않는다.** Qwen3.8-27B와
Mistral-Small-24B를 로컬 vLLM으로 한 번에 하나씩 띄운다. 기본 렌더러는 CPU 기반
`compose`, 자동 judge는 외부 호출이 없는 자리표시이며 최종 판정은 HTML에서 사람이 한다.
실제 생성 환경은 NVIDIA GPU 메모리 70GB 이상과 모델 캐시 여유 140GB 이상을 권장한다.
`itemgen doctor --full`은 vLLM이 사용하는 PyTorch 환경에서 CUDA를 직접 초기화해
드라이버와 PyTorch CUDA 빌드의 호환성도 검사한다.

먼저 문항 하나만 실제 생성해 인수 테스트하려면:

```bash
pip install '.[serve]'
GPU=0 ITEMGEN_CACHE_ROOT=/큰/디스크/cache bash scripts/gpu_acceptance.sh
```

`serve` 추가 의존성은 검증된 `vLLM 0.19.1`로 고정되어 있다. 상한 없는 최신 vLLM을
별도로 설치하면 서버 드라이버보다 새로운 CUDA용 PyTorch가 설치될 수 있으므로,
운영 환경에서 임의로 업그레이드하지 않는다.

GPU 인수 테스트가 대화형 터미널에서 성공하면 검수 HTML 서버가 자동으로 시작된다.
브라우저에서 `http://127.0.0.1:8012/sheet.html`을 열고, 종료할 때는 `Ctrl+C`를 누른다.
서빙 없이 테스트만 마치려면 `SERVE=0`, 다른 포트는 `SERVE_PORT=8013`처럼 지정한다.

전체 50문제를 생성하거나 렌더러를 추가할 때는 환경변수로 범위를 바꾼다.

```bash
# 기본: Qwen + Mistral, 50슬롯, compose
bash run_all.sh

# Qwen만 5개 슬롯으로 확인
RUN=pilot LLMS=qwen38_27b SLOT_IDS=MEM_01,ATT_01,LAN_01,VIS_01,EXE_01 \
  bash run_all.sh

# compose와 FLUX.2 klein을 모두 렌더
RENDERERS="compose flux2_klein_4b" bash run_all.sh
```

`VLLM=/경로/vllm`, `ITEMGEN_CACHE_ROOT=/모델/캐시`, `GPUS="0 1"`, `RUN=실행명`도
환경에 맞게 지정할 수 있다. 모델 서버는 `127.0.0.1`에만 열리고 실행 종료 시 해당
스크립트가 만든 프로세스 그룹만 정리한다.

compose 렌더러의 자산과 사람 판정 기반 슬롯 선택:

```bash
itemgen assets --run main --list
CUDA_VISIBLE_DEVICES=2 itemgen assets --run main --build
itemgen slots --run main --human out/main/human_verdicts.json \
  --renderers flux2_klein_4b --accept ok,minor --cut 1.0 \
  --write spec/slots_selected_13.json
```

두 렌더러의 전수 사람 판정을 짝비교하고 제작용 정책·슬롯을 재생성하려면:

```bash
itemgen compare --run main \
  --compose-human out/main/human_verdicts_v02.json \
  --baseline-human out/main/human_verdicts.json \
  --write-prefix out/main/human_comparison_v02 \
  --write-policy spec/renderer_policy_final.json \
  --write-spec spec/slots_selected_final.json
```

사람 검수를 통과한 셀을 영역별 10개씩 최종 50문제로 조립하려면:

```bash
itemgen finalize --policy spec/renderer_policy_final_50.json --run final50 --embed
itemgen serve --run final50
# http://127.0.0.1:8012/sheet_final50.html
```

`spec/slots.json`의 객관식 21개 슬롯에는 `target_answer`가 미리 배정되어 있다.
분포는 1번 5개, 2번 5개, 3번 5개, 4번 6개다. 생성 모델이 다른 번호로 쓰더라도
`gen` 저장 직전에 정답 보기·`answer`·해설 번호를 함께 교환하고, 검증기가 목표 번호를
다시 확인하므로 번호가 치우친 문항은 렌더 단계로 넘어가지 않는다.

현재 최종 정책은 `out/final50/human_verdicts_v04.json`의 50개 판정을 근거로 한다.
48개 `ok`, 2개 `minor`(`LAN_10`, `EXE_02`), 0개 `bad`이며 두 `minor`는 제작용으로
명시적으로 허용했다. 보완 생성한 9개 셀은 모두 `ok`다.

단계별로 돌리려면:

```bash
itemgen run --run main --stages gen,check --llms gpt6_sol
itemgen run --run main --stages render --renderers flux2_klein_4b
itemgen run --run main --stages judge,sheet
itemgen run --run repair --stages gen,check --llms qwen38_27b --slot-ids EXE_01
itemgen slots --ids MEM_01,ATT_01,VIS_01 --write spec/slots_custom.json
```

모든 단계가 재개 가능하다. 이미 만든 결과는 건너뛰므로 중간에 끊겨도 다시 치면 된다.
강제로 다시 하려면 `--force gen`.

경로는 바꿀 수 있다. 자기 슬롯·설정으로 돌리려면:

```bash
itemgen run --spec-dir ./my_spec --config ./my_models.yaml --out ./my_results
```

생략하면 현재 디렉터리의 `spec/`·`config/`를 먼저 보고, 없으면 저장소 기본값을 쓴다.
결과는 저장소 밖에서 실행하면 **그 폴더에** 쌓인다. 설치된 저장소를 덮어쓰지 않는다.

## 결과 보기

```bash
itemgen serve               # 127.0.0.1:8012, 캐시 금지
itemgen serve --run demo    # 다른 실행 결과를 보려면
```

생성 원본은 **768×768 PNG**다. 저장소에 든 이미지는 검수용 축소본(256px)이고,
원본이 필요하면 `--renderers`로 다시 그리면 된다.

시트는 슬롯 하나가 한 카드이고, 카드 안에 (LLM × 렌더러) 조합이 가로로 놓인다.
칸마다 자동 판정 배지, 질문·정답, 패널 이미지, 사람 판정 버튼이 들어간다.
상단 칩으로 보고 싶은 arm과 렌더러를 고를 수 있다 (기본값: 렌더러는 klein-4B 하나).

## 실행 결과 (2026-10)

### 측정 1 · LLM이 문항을 쓰는 능력

| arm | 스키마 합격 | 룰 총점 |
|---|---|---|
| mistral_small_24b | 44/50 | **0.976** |
| qwen38_27b | **50/50** | 0.961 |
| gpt6_sol | **50/50** | 0.960 |
| qwen38_27b_noparser | 10/50 | 0.938 |

**같은 모델이 하네스 설정만으로 20% → 100%가 됐다.** `qwen38_27b_noparser`는
reasoning parser 없이 `max_tokens=4096`으로 돌린 1차 결과다. 사고 과정이 본문으로
쏟아져 길이 한도에 잘렸다(실패 출력 중앙값 14,640자, 성공은 408자). 조건을 맞추기
전의 숫자로 "이 모델은 지시를 못 따른다"고 결론내면 틀린다. 이 대조군은 `readonly`로
설정에 남겨 두었다.

### 측정 2 · 렌더러가 명세대로 그리는 능력

패널 단위 사용가능률 (VLM judge 자동 채점):

| arm | klein-4B | z-image | qwen-image | Lightning |
|---|---|---|---|---|
| mistral_small_24b | **50%** | 40% | 24% | 24% |
| gpt6_sol | 32% | 9% | 11% | 11% |
| qwen38_27b | 29% | 16% | 1% | 1% |
| **렌더러 평균** | **38%** | 21% | 11% | 11% |

- **4B가 20B를 3배 이상 이긴다.** 크기가 자극 품질을 보장하지 않는다.
- **증류가 품질을 떨어뜨리지 않는다.** 30스텝과 4스텝(Lightning)이 똑같이 11%다.
  7배 빠른 쪽을 써도 된다.
- 최고 조합이 패널 50% / 문항 23%다. 문항 단위는 그 문항의 패널이 **전부** 통과해야
  1로 세므로 실제 수율에 가깝다.

**이 수치는 전부 자동 채점이다.** 사람 검수 표본과의 상관을 확인하기 전에는
확정된 결과로 쓰지 말 것.

### 측정 3 · compose 하이브리드 렌더러

도형은 코드로, 실물은 GPU 2에서 생성한 단일 사물 자산 96종으로 합성했다. 비교에는
오픈모델 `qwen38_27b`, `mistral_small_24b`만 넣었고, `gpt6_sol`과
`qwen38_27b_noparser`는 완전히 제외했다.

자동 VLM judge 결과(구현 점검용):

| 렌더러 | 패널 사용가능 | 문항 사용가능 |
|---|---:|---:|
| **compose** | **131/267 (49.1%)** | **38/94 (40.4%)** |
| flux2_klein_4b | 104/267 (39.0%) | 19/94 (20.2%) |

분모에는 judge 미판정도 실패로 넣었다(compose 패널 37건, klein-4B 39건).

동일한 Qwen/Mistral 94칸을 사람이 전수 검수한 결과다. compose 판정 파일에는
`ok`/`bad`만 있고, klein-4B는 `ok`와 `minor`를 사용 가능으로 셌다.

| 범위 | compose | klein-4B |
|---|---:|---:|
| **전체** | **53/94 (56.4%)** | 40/94 (42.6%) |
| qwen38_27b | **32/50 (64.0%)** | 21/50 (42.0%) |
| mistral_small_24b | **21/44 (47.7%)** | 19/44 (43.2%) |

| 영역 | compose | klein-4B |
|---|---:|---:|
| 기억력 | **17/18 (94.4%)** | 12/18 (66.7%) |
| 주의력 | **13/19 (68.4%)** | 9/19 (47.4%) |
| 시공간능력 | **9/18 (50.0%)** | 4/18 (22.2%) |
| 집행기능 | **7/19 (36.8%)** | 3/19 (15.8%) |
| 언어능력 | 7/20 (35.0%) | **12/20 (60.0%)** |

두 렌더러 모두 성공 32칸, compose만 성공 21칸, klein-4B만 성공 8칸, 둘 다 실패
33칸이다. 짝지은 정확 McNemar 검정은 `p=0.024`로, 이 표본에서는 compose의 13.8%p
개선이 유의하다. 다만 언어능력에서는 klein-4B가 더 높아 렌더러를 한 종류로만 고정하는
것이 최선인지는 별도 판단이 필요하다.

94칸은 원본 셀과 정확히 일치하며 중복·누락이 없다. 자동 judge를 compose의 같은 칸
문항 판정과 비교하면 TP/FP/FN/TN = 26/12/27/29, 정확도 58.5%, 재현율 49.1%,
Cohen kappa 0.190이다. 즉 자동 judge가 사람이 쓸 수 있다고 본 문항의 절반가량을 놓쳐
compose 수율을 40.4%로 과소평가했다.

```bash
itemgen serve --run main
# http://127.0.0.1:8012/sheet_compose.html
```

이 시트에는 Qwen/Mistral × compose만 들어 있다. 현재 판정 파일은
`out/main/human_verdicts_v02.json`이며 다음처럼 재집계한다.

```bash
itemgen slots --run main --human out/main/human_verdicts_v02.json \
  --renderers compose --accept ok
```

기존 klein-4B 사람 판정에서 두 오픈모델 모두 `ok` 또는 `minor`였던 13개 슬롯은
`spec/slots_selected_13.json`에 별도 스냅샷으로 보존했다. 이 13개는 compose 전수 검수의
관측 셀에서도 22/24 (91.7%)가 `ok`였다. `LAN_01`의 Mistral과 `VIS_06`의 Qwen이
실패했고, `VIS_06`·`VIS_10`의 Mistral 셀은 생성 결과가 없어 시트에 포함되지 않았다.
compose에서 두 모델의 결과가 모두 있고 둘 다 `ok`인 슬롯은 16개다.

실제 제작에는 영역별 사람 판정 우세 렌더러를 쓰는 정책을 별도로 고정했다. 기억력·주의력·
시공간능력·집행기능은 compose, 언어능력은 klein-4B를 쓴다. 관측 수율은 58/94 (61.7%)이고,
선택된 렌더러에서 Qwen/Mistral이 모두 통과한 제작용 슬롯은 17개다. 이는 평가 결과를 보고
만든 **제작용 정책**이며, 편향 없는 벤치마크 수치로 사용하면 안 된다.

- `out/main/human_comparison_v02.json`: 기준·해시·짝비교·judge 일치도·제작 정책
- `out/main/human_comparison_v02.csv`: 모델·영역별 요약표
- `out/main/human_comparison_v02_cells.csv`: 94개 셀의 렌더러별 판정
- `spec/renderer_policy_final.json`: 영역별 제작 렌더러 정책
- `spec/slots_selected_final.json`: 두 오픈모델이 모두 통과한 제작용 17슬롯

17슬롯은 수율을 우선해 고른 결과라 기억력 7개, 시공간능력 2개, 집행기능 1개로
영역이 치우친다. 실제 제작의 기본안은 이를 보정한 **균형형 18슬롯**이다.

| 기억력 | 주의력 | 언어능력 | 시공간능력 | 집행기능 |
|---:|---:|---:|---:|---:|
| 5 | 4 | 3 | 3 | 3 |

`MEM_07`, `MEM_08`을 빼고 `VIS_04`, `EXE_02`, `EXE_03`을 넣었다. 추가한 세 슬롯은
모두 `qwen38_27b × compose`의 사람 판정 `ok` 셀을 고정해 사용한다. 재생성한
`EXE_01`은 문제 의도가 불명확하다는 사람 판정 `bad`를 받아 채택하지 않았다.

- `spec/slots_selected_balanced_18.json`: 균형형 제작용 18슬롯
- `spec/renderer_policy_balanced_18.json`: 슬롯별 LLM·렌더러 고정과 판정 근거

## 설정 바꾸기

`config/models.yaml`의 `llms`/`renderers`에 항목을 추가하면 arm이 늘어난다.

- LLM: `openai_compat`(OpenAI 호환 **프로토콜**의 로컬 vLLM·Ollama) 또는
  `hf_local`(transformers). 납품 기본 설정은 외부 OpenAI 서비스를 쓰지 않는다.
- 렌더러: `diffusion`(diffusers 파이프라인 아무거나), `compose`(코드 합성), `stub`
- judge: `vlm`(OpenAI 호환 프로토콜의 로컬 vision 서버), `stub`

어댑터가 모르는 키는 조립 단계에서 거부된다. API 키는 `api_key_env`에 적은
환경변수에서 읽고 코드·설정에 넣지 않는다.

`readonly: true`를 주면 그 arm은 `gen`이 호출하지 않는다. 다른 조건으로 받아둔
결과를 비교용으로 남길 때 쓴다.

## 알아두면 시간을 아끼는 것들

실행하며 실제로 막혔던 지점들. 전부 코드에 반영되어 있지만 환경이 다르면 다시
만날 수 있다.

- **vLLM 버전마다 받는 인자가 다르다.** `vllm serve --help`는 그룹 목록만 보여준다.
  실제 플래그는 `--help=all`로 봐야 한다.
- **`VLLM_API_KEY`는 서버가 요구하는 키다.** 설정하면 모든 요청에 그 키가 필요하다.
  헬스체크에 헤더를 안 넣으면 서버가 다 떠 있어도 401만 돌아온다.
- **vLLM은 `VLLM::EngineCore`라는 자식을 남긴다.** 부모만 죽이면 GPU 메모리 50GB를
  계속 물고 있어 다음 모델이 못 뜬다.
- **`--gpu-memory-utilization`은 전체 메모리 대비 비율이다.** 여유 대비가 아니다.
  게다가 vLLM이 실제로 메모리를 확인하는 건 기동 수십 초 뒤다.
- **`HF_HUB_CACHE`가 `HF_HOME`보다 우선한다.** 둘 다 덮어쓰지 않으면 엉뚱한
  디스크로 받다가 공간 부족으로 터진다.
- **Mistral-Small-3.1 / Ministral-3은 전부 멀티모달이다.** vLLM + transformers 조합에
  따라 `ImportError(PixtralRotaryEmbedding)`로 기동이 안 된다. 크기를 줄여도 같다.
  텍스트 전용인 3.0(=2501)을 쓴다.
- **아키텍처가 레지스트리에 등록되어 있어도 import가 안 될 수 있다.** 등록 여부만
  확인하면 속는다. 모듈을 실제로 import해 봐야 한다.
- **Lightning LoRA에는 `peft`가 필요하다.** 없으면 전량 실패한다.

## 자기검증

`itemgen selftest`가 68개 항목을 본다. 외부 모델 없이 돌고 비용이 0이다.

- 나쁜 출력 11종(ref 중복, 격자 범위 초과, 칸 겹침, 없는 ref를 edit, 모르는 op,
  edit 대신 장면 재기술 …)을 검증기가 실제로 잡는지
- `edit` 적용 후 **달라진 사물이 정확히 1개**이고 나머지는 완전히 동일한지
- 파생 보기 4장의 사물 구성이 같고 변환만 다른지, 전부 같은 변형이면 거부하는지
- 같은 scene은 같은 픽셀, edit된 scene은 다른 픽셀이 나오는지
- 거북선/직사각형/반원/사분원/정육면체 도형 분류와 안전한 자산 파일명
- judge 재사용 키가 LLM arm별 결과를 서로 덮어쓰지 않는지
- 객관식 목표 정답 분포와 불일치 거부, 보기·정답·해설의 동시 번호 이동
- 시트 JS에 날 줄바꿈 리터럴이 없는지 (있으면 스크립트 전체가 죽어 버튼이 먹통이 된다)
