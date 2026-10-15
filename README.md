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

`itemgen: command not found`가 나오면 설치한 가상환경이 활성화되지 않은 것이다.
`source <venv>/bin/activate` 하거나 `<venv>/bin/itemgen` 전체 경로로 부른다.

구조를 먼저 눈으로 확인한 뒤 실제 모델을 꽂는 순서를 권한다.

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
  models.py               LLM · 렌더러 · judge 어댑터
  pipeline.py             6단계 오케스트레이터
  sheet.py                사람 검수용 N-way 시트
  selftest.py             외부 모델 없이 도는 자기검증 48항목
  watch.py                실시간 진행 감시기
  serve.py                캐시 없는 정적 서버
  paths.py                spec/·config/·out/ 위치 결정
  cli.py                  `itemgen` 하위명령
run_all.sh                무인 실행 스크립트
status.sh                 한 화면 상태 요약
out/<run>/                실행 결과
```

`spec/`와 `config/`는 일부러 저장소 루트에 두었다. 받는 쪽이 바꿀 것은 코드가 아니라
슬롯·명세서·arm 목록이기 때문이다. 패키지 안에 숨기지 않는다.

## 실행

```bash
itemgen selftest                              # 자기검증 48항목 (비용 0)
itemgen run --run main --stages plan          # 규모·비용 계산만
bash run_all.sh                               # 전 구간 무인 실행
GPUS="3 0 1" bash run_all.sh                  # 비는 카드를 골라 쓴다
```

단계별로 돌리려면:

```bash
itemgen run --run main --stages gen,check --llms gpt6_sol
itemgen run --run main --stages render --renderers flux2_klein_4b
itemgen run --run main --stages judge,sheet
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

## 설정 바꾸기

`config/models.yaml`의 `llms`/`renderers`에 항목을 추가하면 arm이 늘어난다.

- LLM: `openai_compat`(상용 API·vLLM·Ollama 공통) 또는 `hf_local`(transformers)
- 렌더러: `diffusion`(diffusers 파이프라인 아무거나), `compose`(코드 합성), `stub`
- judge: `vlm`(OpenAI 호환 vision), `stub`

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
- **최신 OpenAI 모델은 `max_tokens`와 `temperature`를 거부한다.**
  `max_completion_tokens`를 쓰고 temperature는 기본값만 받는다. 어댑터가 거부당한
  파라미터를 보고 자동으로 고쳐 재시도한다.
- **Mistral-Small-3.1 / Ministral-3은 전부 멀티모달이다.** vLLM + transformers 조합에
  따라 `ImportError(PixtralRotaryEmbedding)`로 기동이 안 된다. 크기를 줄여도 같다.
  텍스트 전용인 3.0(=2501)을 쓴다.
- **아키텍처가 레지스트리에 등록되어 있어도 import가 안 될 수 있다.** 등록 여부만
  확인하면 속는다. 모듈을 실제로 import해 봐야 한다.
- **Lightning LoRA에는 `peft`가 필요하다.** 없으면 전량 실패한다.

## 자기검증

`itemgen selftest`가 48개 항목을 본다. 외부 모델 없이 돌고 비용이 0이다.

- 나쁜 출력 11종(ref 중복, 격자 범위 초과, 칸 겹침, 없는 ref를 edit, 모르는 op,
  edit 대신 장면 재기술 …)을 검증기가 실제로 잡는지
- `edit` 적용 후 **달라진 사물이 정확히 1개**이고 나머지는 완전히 동일한지
- 파생 보기 4장의 사물 구성이 같고 변환만 다른지, 전부 같은 변형이면 거부하는지
- 같은 scene은 같은 픽셀, edit된 scene은 다른 픽셀이 나오는지
- 시트 JS에 날 줄바꿈 리터럴이 없는지 (있으면 스크립트 전체가 죽어 버튼이 먹통이 된다)
