"""Translators that run locally. None of them calls a paid API."""

import json
import re
import sys
import urllib.request


class DummyBackend:
    """Marks text instead of translating it: checks the LaTeX pipeline without any model."""

    name = "dummy"
    sentence_level = True

    def translate_batch(self, texts):
        return ["[VI] " + t for t in texts]


class VinAIBackend:
    """vinai/vinai-translate-en2vi-v2 (mBART, ~1.5 GB) via Hugging Face transformers."""

    sentence_level = True

    def __init__(self, model="vinai/vinai-translate-en2vi-v2", device=None, beams=4, batch_size=8):
        import torch
        from transformers import AutoModelForSeq2SeqLM, AutoTokenizer

        self.name = "vinai-" + model.rsplit("/", 1)[-1]
        self.torch = torch
        self.device = device or ("cuda" if torch.cuda.is_available() else "cpu")
        print(f"Loading {model} on {self.device} (first run downloads it once)...", file=sys.stderr)
        self.tokenizer = AutoTokenizer.from_pretrained(model, src_lang="en_XX")
        self.model = AutoModelForSeq2SeqLM.from_pretrained(model).to(self.device).eval()
        if self.device.startswith("cuda"):
            self.model.half()
        self.vi_id = self.tokenizer.convert_tokens_to_ids("vi_VN")
        self.beams, self.batch_size = beams, batch_size

    def translate_batch(self, texts):
        out = [None] * len(texts)
        order = sorted(range(len(texts)), key=lambda k: len(texts[k]))  # less padding
        for s in range(0, len(order), self.batch_size):
            idx = order[s : s + self.batch_size]
            enc = self.tokenizer([texts[k] for k in idx], padding=True, truncation=True, max_length=512,
                                 return_tensors="pt").to(self.device)
            with self.torch.inference_mode():
                ids = self.model.generate(**enc, decoder_start_token_id=self.vi_id, num_beams=self.beams,
                                          max_new_tokens=768, early_stopping=True)
            for k, t in zip(idx, self.tokenizer.batch_decode(ids, skip_special_tokens=True)):
                out[k] = t
        return out


class OllamaBackend:
    """Any chat model served by a local Ollama (https://ollama.com), e.g. qwen2.5:7b."""

    sentence_level = False
    PROMPT = (
        "Translate the following excerpt of a computer-science research paper from English to Vietnamese.\n"
        "Rules:\n"
        "- Keep every placeholder such as [[3]] exactly as written, the same number of times, in a natural position.\n"
        "- Keep model names, dataset names and acronyms in English.\n"
        "- Use a formal, academic Vietnamese style.\n"
        "- Output only the Vietnamese translation, nothing else.\n\n"
        "Text:\n{text}"
    )

    def __init__(self, model="qwen2.5:7b", host="http://localhost:11434"):
        self.name = "ollama-" + model.replace(":", "-").replace("/", "-")
        self.model, self.host = model, host.rstrip("/")

    def _one(self, text):
        body = json.dumps({"model": self.model, "prompt": self.PROMPT.format(text=text), "stream": False,
                           "options": {"temperature": 0.1}}).encode()
        req = urllib.request.Request(self.host + "/api/generate", body, {"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=600) as r:
            reply = json.loads(r.read())["response"]
        reply = re.sub(r"<think>.*?</think>", "", reply, flags=re.S).strip()  # reasoning models
        return reply.strip('"').strip()

    def translate_batch(self, texts):
        return [self._one(t) for t in texts]


def make_backend(name, model=None, **kw):
    if name == "dummy":
        return DummyBackend()
    if name == "vinai":
        return VinAIBackend(model or "vinai/vinai-translate-en2vi-v2", device=kw.get("device"),
                            beams=kw.get("beams", 4), batch_size=kw.get("batch_size", 8))
    if name == "ollama":
        return OllamaBackend(model or "qwen2.5:7b", host=kw.get("ollama_host") or "http://localhost:11434")
    raise ValueError(f"unknown backend {name!r}")
