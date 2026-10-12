"""python -m paper_translator 2506.09985 [--backend vinai|ollama|dummy] [--compile]"""

import argparse
import os

from .backends import make_backend
from .fetch import arxiv_id
from .glossary import DEFAULT_PATH, Glossary
from .pipeline import compile_pdf, translate_project


def main(argv=None):
    p = argparse.ArgumentParser(prog="python -m paper_translator",
                                description="Translate an arXiv paper's LaTeX source from English to Vietnamese, locally.")
    p.add_argument("source", help="arXiv id or URL (2506.09985), or a local folder / .tar.gz / .tex")
    p.add_argument("--out", help="output folder (default: translations/<id or name>)")
    p.add_argument("--backend", default="vinai", choices=["vinai", "ollama", "dummy"],
                   help="vinai: VinAI en2vi model via transformers (default); ollama: local LLM; dummy: no model")
    p.add_argument("--model", help="model name (vinai: HF id; ollama: e.g. qwen2.5:7b, gemma3:12b)")
    p.add_argument("--device", help="cpu / cuda (vinai; default: cuda if available)")
    p.add_argument("--beams", type=int, default=4, help="beam search width (vinai; lower = faster)")
    p.add_argument("--batch-size", type=int, default=8, help="sentences per forward pass (vinai)")
    p.add_argument("--ollama-host", default="http://localhost:11434")
    p.add_argument("--glossary", default=DEFAULT_PATH, help="YAML glossary ('' to disable)")
    p.add_argument("--limit", type=int, help="translate at most N new sentences (quick test)")
    p.add_argument("--compile", action="store_true", help="build the PDF with a local LaTeX installation")
    a = p.parse_args(argv)

    name = arxiv_id(a.source) or os.path.splitext(os.path.basename(os.path.normpath(a.source)))[0]
    out = a.out or os.path.join("translations", name)
    backend = make_backend(a.backend, a.model, device=a.device, beams=a.beams, batch_size=a.batch_size,
                           ollama_host=a.ollama_host)
    main_tex, stats = translate_project(a.source, out, backend, Glossary.load(a.glossary), limit=a.limit)
    print(stats)
    print(f"Translated LaTeX: {os.path.dirname(main_tex)}")
    print(f"Overleaf zip:     {os.path.join(out, 'vi-overleaf.zip')}")
    if a.compile:
        pdf = compile_pdf(main_tex)
        if pdf:
            print(f"PDF:              {pdf}")


if __name__ == "__main__":
    main()
