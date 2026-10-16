"""모델 어댑터. LLM · diffusion · judge 세 종류를 같은 모양으로 감싼다.

전부 stub 구현을 들고 있다. stub으로 파이프라인 전체를 먼저 돌려 배선을 확인하고,
그 다음에 실제 모델을 꽂는다. API 비용과 GPU 시간을 쓰기 전에 전 구간이 도는지
확인하는 쪽이 싸다.

config/models.yaml에서 어떤 어댑터를 쓸지 고른다.
"""

from __future__ import annotations

import json
import os
import random
import re
from pathlib import Path

from . import paths


# =========================================================== LLM

class LLM:
    name = "base"

    def generate(self, prompt: str) -> str:
        raise NotImplementedError


class StubLLM(LLM):
    """슬롯 정의를 읽어 스키마에 맞는 문항을 기계적으로 만든다.

    실제 모델이 아니다. 파이프라인 배선 확인과 회귀 테스트용이다. 슬롯이 edit을
    요구하면 edit을 쓰고 보기를 요구하면 보기를 만들므로, 스키마 검증·렌더·채점·시트가
    전부 도는지 공짜로 확인할 수 있다.
    """

    name = "stub"
    POOL = [("머그컵", "손잡이 달린 흰색 낱개", "흰색"), ("열쇠", "열쇠고리 없는 금속 낱개 하나", "은색"),
            ("사과", "둥근 낱개 하나", "빨강"), ("안경", "검은 뿔테", "검정"),
            ("가위", "낱개 하나", "회색"), ("숟가락", "낱개 하나", "은색"),
            ("우산", "접힌 낱개 하나", "파랑"), ("시계", "둥근 벽시계 하나", "흰색"),
            ("연필", "낱개 하나", "노랑"), ("책", "닫힌 낱개 하나", "초록")]

    def __init__(self, seed: int = 0):
        self.seed = seed

    def generate(self, prompt: str) -> str:
        slot = json.loads(re.findall(r"```json\n(\{.*?\})\n```", prompt, re.S)[-1])
        rng = random.Random(f"{self.seed}:{slot['id']}")
        return json.dumps(self._item(slot, rng), ensure_ascii=False)

    def _scene(self, rng, n=4, rows=2, cols=3):
        # 칸을 사물보다 넉넉히 둔다. 꽉 채우면 move 변경을 넣을 빈 칸이 없다.
        picks = rng.sample(self.POOL, n)
        cells = [[r, c] for r in range(rows) for c in range(cols)][:n]
        return {"layout": "grid", "rows": rows, "cols": cols, "background": "흰 바닥",
                "objects": [{"ref": f"o{i+1}", "name": nm, "appearance": ap, "color": co,
                             "cell": cells[i], "is_target": i == 0}
                            for i, (nm, ap, co) in enumerate(picks)]}

    @staticmethod
    def _variant(op: str, target: str, k: int, base: dict):
        """보기 k번에 쓸 변형 하나. 보기마다 달라야 정답이 유일해진다."""
        if op == "rotate":
            return {"op": "rotate", "target": target, "to_rotation": 90 * (k - 1)}
        if op == "mirror":
            return ({"op": "mirror", "target": target} if k == 2
                    else {"op": "rotate", "target": target, "to_rotation": 90 * (k - 1)})
        if op == "rescale":
            return {"op": "rescale", "target": target, "to_scale": round(0.6 + 0.2 * k, 2)}
        if op == "recolor":
            return {"op": "recolor", "target": target,
                    "to_color": ["초록", "파랑", "노랑", "보라", "주황"][(k - 2) % 5]}
        if op == "move":
            taken = {tuple(o["cell"]) for o in base["objects"] if "cell" in o}
            free = [[r, c] for r in range(base["rows"]) for c in range(base["cols"])
                    if (r, c) not in taken]
            if free:
                return {"op": "move", "target": target, "to_cell": free[(k - 2) % len(free)]}
            return {"op": "rotate", "target": target, "to_rotation": 90 * (k - 1)}
        if op == "remove":
            return {"op": "remove", "target": base["objects"][(k - 2) % len(base["objects"])]["ref"]}
        return {"op": "rotate", "target": target, "to_rotation": 90 * (k - 1)}

    @staticmethod
    def _free_cell(scene: dict):
        taken = {tuple(o["cell"]) for o in scene["objects"] if "cell" in o}
        for r in range(scene["rows"]):
            for c in range(scene["cols"]):
                if (r, c) not in taken:
                    return [r, c]
        return None

    def _item(self, slot: dict, rng) -> dict:
        panels, base = [], None
        for p in slot["panels"]:
            if p.get("kind") == "option":
                n = p.get("n", 4)
                target_answer = int(slot.get("target_answer", 1))
                if p.get("derived"):
                    # 보기를 기준에서 파생시킨다. target_answer는 기준 그대로인 정답,
                    # 나머지는 서로 다른 변형이다.
                    op = (p.get("ops") or ["rotate"])[0]
                    target = base["objects"][0]["ref"]
                    variants = iter(range(2, n + 1))
                    for k in range(1, n + 1):
                        panel = {"stage": p["stage"], "kind": "option",
                                 "option_index": k, "edit_of": p["edit_of"]}
                        if k != target_answer:
                            variant = next(variants)
                            panel["changes"] = [self._variant(op, target, variant, base)]
                        panels.append(panel)
                else:
                    for k in range(1, n + 1):
                        sc = self._scene(rng, n=2, rows=1, cols=2)
                        sc["objects"][0]["rotation"] = 90 * (k - 1)
                        panels.append({"stage": p["stage"], "kind": "option",
                                       "option_index": k, "scene": sc})
            elif p.get("kind") == "edit":
                ops = p.get("ops", ["move"])
                refs = [o["ref"] for o in base["objects"]]
                changes, busy = [], {tuple(o["cell"]) for o in base["objects"] if "cell" in o}

                def claim():
                    """아직 아무도 안 쓴 칸을 하나 집어 예약한다."""
                    for r in range(base["rows"]):
                        for c in range(base["cols"]):
                            if (r, c) not in busy:
                                busy.add((r, c))
                                return [r, c]
                    return None

                for j in range(p.get("n_changes", 1)):
                    op = ops[j % len(ops)]
                    target = refs[j % len(refs)]
                    ch = {"op": op, "target": target}
                    if op == "move":
                        cell = claim()
                        if cell is None:        # 빈 칸이 없으면 다른 변경으로 바꾼다
                            ch = {"op": "recolor", "target": target, "to_color": "초록"}
                        else:
                            ch["to_cell"] = cell
                            busy.discard(tuple(base["objects"][refs.index(target)]["cell"]))
                    elif op == "recolor":
                        ch["to_color"] = "초록"
                    elif op == "add":
                        cell = claim()
                        if cell is None:
                            ch = {"op": "recolor", "target": target, "to_color": "초록"}
                        else:
                            ch["target"] = f"extra{j}"
                            ch["object"] = {"ref": f"extra{j}", "name": "가위",
                                            "appearance": "낱개 하나", "cell": cell}
                    elif op == "remove":
                        busy.discard(tuple(base["objects"][refs.index(target)]["cell"]))
                    changes.append(ch)
                panels.append({"stage": p["stage"], "kind": "edit",
                               "edit_of": p["edit_of"], "changes": changes})
            else:
                sc = self._scene(rng)
                base = sc
                panels.append({"stage": p["stage"], "kind": "scene", "scene": sc})

        answer = (str(slot.get("target_answer", 1))
                  if slot.get("answer_kind") == "보기번호" else "머그컵")
        if slot.get("answer_kind") == "개수":
            answer = "4"
        elif slot.get("answer_kind") == "좌우":
            answer = "왼쪽"
        return {"slot_id": slot["id"],
                "question": f"[stub] {slot['subtype']} 문항입니다. 정답을 말해주세요.",
                "answer": answer,
                "answer_rationale": "stub 생성물이므로 유일성 근거는 비어 있다.",
                "panels": panels}


class OpenAICompatLLM(LLM):
    """OpenAI 호환 /chat/completions 엔드포인트. 상용 API와 vLLM·Ollama가 모두 이 모양이다."""

    def __init__(self, name: str, model: str, base_url: str, api_key_env: str,
                 temperature: float = 0.2, max_tokens: int = 16384,
                 extra_body: dict | None = None, repair: bool = True):
        self.name, self.model, self.base_url = name, model, base_url.rstrip("/")
        self.api_key_env, self.temperature, self.max_tokens = api_key_env, temperature, max_tokens
        # extra_body: 공급자별 추가 인자 (vLLM의 chat_template_kwargs 등)
        self.extra_body = extra_body or {}
        self.repair = repair
        self._drop: set[str] = set()      # 이 공급자가 거부한 파라미터
        self._rename: dict[str, str] = {}  # 이름만 바꾸면 되는 파라미터

    # 공급자마다 받는 파라미터가 다르다. 최신 OpenAI 모델은 max_tokens를 거부하고
    # temperature도 기본값만 받는다. vLLM은 둘 다 받는다. 거부당한 파라미터를 보고
    # 한 번 고쳐서 다시 보내고, 고친 내용을 기억해 이후 호출에 바로 적용한다.
    RENAME = {"max_tokens": "max_completion_tokens"}

    def _body(self, messages: list[dict]) -> dict:
        body = {"model": self.model, "messages": messages}
        if "temperature" not in self._drop:
            body["temperature"] = self.temperature
        key = self._rename.get("max_tokens", "max_tokens")
        if "max_tokens" not in self._drop:
            body[key] = self.max_tokens
        body.update(self.extra_body)
        return body

    def _call(self, messages: list[dict]) -> dict:
        """한 번 호출한다. 거부당한 파라미터는 고쳐서 재시도하고 그 조정을 기억한다."""
        import requests
        key = os.environ.get(self.api_key_env, "")
        if not key:
            raise RuntimeError(f"환경변수 {self.api_key_env}가 비어 있다")
        headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}
        for _ in range(4):
            r = requests.post(f"{self.base_url}/chat/completions", headers=headers,
                              json=self._body(messages), timeout=900)
            if r.status_code == 200:
                return r.json()
            if r.status_code != 400:
                r.raise_for_status()
            err = (r.json().get("error") or {})
            bad, code = err.get("param"), err.get("code")
            if not bad:
                r.raise_for_status()
            if code == "unsupported_parameter" and bad in self.RENAME:
                self._rename[bad] = self.RENAME[bad]
            else:
                self._drop.add(bad)              # 값이 안 맞으면 그냥 빼고 기본값에 맡긴다
        raise RuntimeError(f"{self.name}: 파라미터를 맞추지 못했다 "
                           f"(drop={sorted(self._drop)}, rename={self._rename})")

    @staticmethod
    def _content(resp: dict) -> tuple[str, str]:
        """본문과 종료 사유를 꺼낸다. reasoning parser가 붙어 있으면 사고 과정은
        reasoning_content로 분리되어 content에는 최종 답만 남는다."""
        ch = resp["choices"][0]
        return (ch["message"].get("content") or "", ch.get("finish_reason") or "")

    def generate(self, prompt: str) -> str:
        messages = [{"role": "user", "content": prompt}]
        text, finish = self._content(self._call(messages))
        if not self.repair or extract_json(text) is not None:
            return text
        # 1차에서 JSON을 못 얻었다. 모델이 사고 과정을 쏟았거나 길이 한도에 잘린 경우다.
        # 그 출력을 되돌려 주고 "JSON만" 다시 내게 한다. 복구 성공은 따로 집계해야
        # 1차 준수율과 섞이지 않는다.
        self.repaired = getattr(self, "repaired", 0) + 1
        messages += [
            {"role": "assistant", "content": text[-4000:] or "(빈 응답)"},
            {"role": "user", "content":
             "위 내용에서 최종 JSON 객체 하나만 출력하라. 설명·사고 과정·코드펜스를 "
             "붙이지 않는다. 아직 JSON을 만들지 않았다면 지금 만들어 그것만 출력하라."},
        ]
        text2, _ = self._content(self._call(messages))
        return text2 if extract_json(text2) is not None else text


class HFLocalLLM(LLM):
    """로컬 transformers 모델. 오픈모델 arm용."""

    def __init__(self, name: str, model: str, max_new_tokens: int = 2048):
        self.name, self.model_id, self.max_new_tokens = name, model, max_new_tokens
        self._pipe = None

    def _load(self):
        if self._pipe is None:
            import torch
            from transformers import AutoModelForCausalLM, AutoTokenizer
            tok = AutoTokenizer.from_pretrained(self.model_id)
            mdl = AutoModelForCausalLM.from_pretrained(
                self.model_id, dtype=torch.bfloat16, device_map="auto")
            mdl.eval()
            self._pipe = (tok, mdl)
        return self._pipe

    def generate(self, prompt: str) -> str:
        import torch
        tok, mdl = self._load()
        text = tok.apply_chat_template([{"role": "user", "content": prompt}],
                                       tokenize=False, add_generation_prompt=True)
        ids = tok(text, return_tensors="pt").to(mdl.device)
        with torch.no_grad():
            out = mdl.generate(**ids, max_new_tokens=self.max_new_tokens, do_sample=False)
        return tok.decode(out[0][ids["input_ids"].shape[1]:], skip_special_tokens=True)


def extract_json(raw: str):
    """모델 출력에서 JSON 객체 하나를 꺼낸다. 코드펜스·잡설을 견딘다."""
    fenced = re.findall(r"```(?:json)?\s*(\{.*?\})\s*```", raw, re.S)
    for cand in (fenced + [raw]):
        start = cand.find("{")
        if start < 0:
            continue
        depth, in_str, esc = 0, False, False
        for i, ch in enumerate(cand[start:], start):
            if in_str:
                if esc:
                    esc = False
                elif ch == "\\":
                    esc = True
                elif ch == '"':
                    in_str = False
                continue
            if ch == '"':
                in_str = True
            elif ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    try:
                        return json.loads(cand[start:i + 1])
                    except json.JSONDecodeError:
                        break
    return None


# =========================================================== Diffusion

class Renderer:
    name = "base"

    def render(self, panel: dict, out_path: Path, seed: int) -> None:
        raise NotImplementedError


class ComposeRenderer(Renderer):
    """코드 합성 렌더러. 대조군이자 상한선."""

    name = "compose"

    def __init__(self, size: int = 768):
        self.size = size

    def render(self, panel: dict, out_path: Path, seed: int) -> None:
        from .render import render_compose
        render_compose(panel["scene"], self.size).save(out_path, "PNG")


class StubRenderer(Renderer):
    """배선 확인용. 패널 정보를 적은 회색 이미지를 만든다. GPU를 쓰지 않는다."""

    name = "stub"

    def __init__(self, size: int = 512):
        self.size = size

    def render(self, panel: dict, out_path: Path, seed: int) -> None:
        from PIL import Image, ImageDraw
        img = Image.new("RGB", (self.size, self.size), "#eceff2")
        d = ImageDraw.Draw(img)
        names = ", ".join(o["name"] for o in panel["scene"]["objects"])
        d.text((16, 16), f"[stub] {panel.get('stage')}", fill="#333")
        d.text((16, 40), names[:60], fill="#555")
        d.text((16, 64), f"seed={seed}", fill="#888")
        img.save(out_path, "PNG")


class DiffusersRenderer(Renderer):
    """diffusers 파이프라인 하나를 감싸는 범용 렌더러.

    모델마다 클래스를 따로 두지 않는다. 모델별로 다른 것은 두 가지뿐이다.

      pipeline  : 쓸 파이프라인 클래스 이름. "auto"면 DiffusionPipeline이 알아서 고른다.
      cfg_param : CFG 인자 이름. FLUX/SD는 guidance_scale, Qwen-Image는 true_cfg_scale이다.
                  이름이 틀리면 조용히 무시되어 CFG가 안 걸린 그림이 나온다.
    """

    def __init__(self, name: str, model: str, steps: int = 30, guidance: float = 4.0,
                 size: int = 768, pipeline: str = "auto", cfg_param: str = "guidance_scale",
                 dtype: str = "bfloat16", negative_prompt: str = "", offload: bool = False,
                 lora: str = "", lora_weight_name: str = "", lora_scale: float = 1.0):
        self.name, self.model_id = name, model
        self.steps, self.guidance, self.size = steps, guidance, size
        self.pipeline, self.cfg_param = pipeline, cfg_param
        self.dtype, self.negative_prompt, self.offload = dtype, negative_prompt, offload
        self.lora, self.lora_weight_name, self.lora_scale = lora, lora_weight_name, lora_scale
        self._pipe = None

    def _load(self):
        if self._pipe is not None:
            return self._pipe
        import torch
        import diffusers
        cls = (diffusers.DiffusionPipeline if self.pipeline == "auto"
               else getattr(diffusers, self.pipeline))
        pipe = cls.from_pretrained(self.model_id, torch_dtype=getattr(torch, self.dtype))
        if self.lora:
            # 증류 LoRA. 베이스 모델은 같고 스텝 수만 줄어든다.
            kw = {"weight_name": self.lora_weight_name} if self.lora_weight_name else {}
            pipe.load_lora_weights(self.lora, **kw)
            pipe.fuse_lora(lora_scale=self.lora_scale)
        if self.offload:
            pipe.enable_model_cpu_offload()      # 80GB에 안 들어갈 때만
        else:
            pipe = pipe.to("cuda")
        self._pipe = pipe
        return pipe

    def render(self, panel: dict, out_path: Path, seed: int) -> None:
        import torch
        from .render import build_diffusion_prompt
        pipe = self._load()
        kwargs = {
            "prompt": build_diffusion_prompt(panel),
            "num_inference_steps": self.steps,
            "height": self.size, "width": self.size,
            "generator": torch.Generator("cuda").manual_seed(seed),
            self.cfg_param: self.guidance,
        }
        if self.negative_prompt:
            kwargs["negative_prompt"] = self.negative_prompt
        pipe(**kwargs).images[0].save(out_path, "PNG")


# =========================================================== Judge

JUDGE_PROMPT = """너는 인지검사 자극 이미지를 검수한다. 아래 명세와 이미지를 대조해라.

명세:
{prose}

다음 JSON만 출력한다. 설명을 붙이지 않는다.
{{"objects_ok": true/false, "count_ok": true/false, "layout_ok": true/false,
  "no_text": true/false, "usable": true/false, "why": "한 문장"}}

objects_ok: 명세의 사물이 모두 있고 엉뚱한 것이 섞이지 않았나
count_ok  : 사물 개수가 명세와 같나
layout_ok : 배치(행·열·좌우·위아래)가 명세와 같나
no_text   : 이미지에 글자·숫자가 없나
usable    : 이 이미지를 문항 자극으로 쓸 수 있나"""


class Judge:
    name = "base"

    def judge(self, panel: dict, image_path: Path) -> dict:
        raise NotImplementedError


class StubJudge(Judge):
    """배선 확인용. 사물 수로 기계적인 판정을 낸다."""

    name = "stub"

    def judge(self, panel: dict, image_path: Path) -> dict:
        n = len(panel["scene"]["objects"])
        ok = n <= 4
        return {"objects_ok": ok, "count_ok": ok, "layout_ok": ok,
                "no_text": True, "usable": ok,
                "why": f"[stub] 사물 {n}개 — 4개 이하만 통과 처리"}


class VLMJudge(Judge):
    """OpenAI 호환 vision 엔드포인트. 사람 검수를 대체하는 자동 채점기.

    LLM 쪽과 같은 파라미터 자동 조정이 필요하다. GPT-6 계열은 temperature를
    기본값만 받고 max_tokens 대신 max_completion_tokens를 쓴다. 그냥 보내면
    400으로 전수 실패한다.
    """

    RENAME = {"max_tokens": "max_completion_tokens"}

    def __init__(self, name: str, model: str, base_url: str, api_key_env: str,
                 temperature: float = 0, max_tokens: int = 400, detail: str = "auto"):
        self.name, self.model, self.base_url = name, model, base_url.rstrip("/")
        self.api_key_env, self.temperature = api_key_env, temperature
        self.max_tokens, self.detail = max_tokens, detail
        self._drop: set[str] = set()
        self._rename: dict[str, str] = {}

    def judge(self, panel: dict, image_path: Path) -> dict:
        import base64
        import requests
        from .render import scene_to_prose
        key = os.environ.get(self.api_key_env, "")
        if not key:
            raise RuntimeError(f"환경변수 {self.api_key_env}가 비어 있다")
        b64 = base64.b64encode(image_path.read_bytes()).decode("ascii")
        prompt = JUDGE_PROMPT.format(prose=scene_to_prose(panel["scene"]))
        headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}
        content = [{"type": "text", "text": prompt},
                   {"type": "image_url",
                    "image_url": {"url": f"data:image/png;base64,{b64}", "detail": self.detail}}]
        for _ in range(4):
            body = {"model": self.model, "messages": [{"role": "user", "content": content}]}
            if "temperature" not in self._drop:
                body["temperature"] = self.temperature
            if "max_tokens" not in self._drop:
                body[self._rename.get("max_tokens", "max_tokens")] = self.max_tokens
            r = requests.post(f"{self.base_url}/chat/completions",
                              headers=headers, json=body, timeout=300)
            if r.status_code == 200:
                out = extract_json(r.json()["choices"][0]["message"].get("content") or "")
                return out or {"usable": None, "why": "판정 파싱 실패"}
            if r.status_code != 400:
                r.raise_for_status()
            err = (r.json().get("error") or {})
            bad, code = err.get("param"), err.get("code")
            if not bad:
                r.raise_for_status()
            if code == "unsupported_parameter" and bad in self.RENAME:
                self._rename[bad] = self.RENAME[bad]
            else:
                self._drop.add(bad)
        return {"usable": None, "why": f"파라미터를 맞추지 못했다 (drop={sorted(self._drop)})"}


# =========================================================== 설정 로딩

def build(kind: str, cfg: dict):
    """config/models.yaml의 항목 하나를 어댑터 객체로 만든다."""
    import inspect
    cfg = dict(cfg)
    adapter = cfg.pop("adapter")
    label = cfg.get("name")
    table = {
        "llm": {"stub": StubLLM, "openai_compat": OpenAICompatLLM, "hf_local": HFLocalLLM},
        "renderer": {"stub": StubRenderer, "compose": ComposeRenderer,
                     "diffusion": DiffusersRenderer},
        "judge": {"stub": StubJudge, "vlm": VLMJudge},
    }[kind]
    if adapter not in table:
        raise SystemExit(f"{kind}에 모르는 adapter: {adapter!r} (가능: {sorted(table)})")
    cls = table[adapter]
    # 어댑터마다 받는 인자가 다르다. 생성자가 모르는 키는 떨어뜨리고, name은 끝에 붙인다.
    takes = inspect.signature(cls.__init__).parameters
    kwargs = {k: v for k, v in cfg.items() if k in takes}
    # 파이프라인이 쓰는 설정이지 어댑터 인자가 아닌 것들. 생성자에 넘기지 않는다.
    PIPELINE_ONLY = {"name", "workers", "readonly"}
    unknown = set(cfg) - set(kwargs) - PIPELINE_ONLY
    if unknown:
        raise SystemExit(f"{kind}/{adapter}가 모르는 설정 키: {sorted(unknown)}")
    obj = cls(**kwargs)
    if label:
        obj.name = label
    return obj


def load_config(path: Path | None = None) -> dict:
    import yaml
    path = paths.config_path(path)
    return yaml.safe_load(path.read_text(encoding="utf-8"))
