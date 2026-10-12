"""Translate a whole LaTeX project.

Runs the deterministic LaTeX pass twice: the first only records which sentences need
translating, those are translated in batches (and cached on disk, so an interrupted run
resumes), the second writes the files using the cached translations.
"""

import json
import os
import re
import shutil
import subprocess
import sys
import time

from . import latex
from .fetch import find_main_tex, prepare_source, read_tex, reading_order


class Stats:
    def __init__(self):
        self.sentences = self.translated = self.kept_english = self.not_translated = 0

    def __str__(self):
        return (f"{self.sentences} sentences: {self.translated} translated, "
                f"{self.kept_english} kept in English (placeholders lost), "
                f"{self.not_translated} not translated yet")


class Context:
    """`lookup(masked_sentence)` returns the translation, or None if there is none."""

    def __init__(self, lookup, glossary, sentence_level=True):
        self.lookup = lookup
        self.glossary = glossary
        self.sentence_level = sentence_level
        self.stats = Stats()
        glossary.reset()

    def translate_prose(self, text):
        """Translate one paragraph (or command argument) of LaTeX prose."""
        lead = text[: len(text) - len(text.lstrip())]
        trail = text[len(text.rstrip()) :]
        core = re.sub(r"%[ \t]*(\n[ \t]*|$)", "", text.strip())  # "%" at line end joins lines
        if not core:
            return text
        m = latex.mask(core, self.translate_prose)
        if not latex.has_words(m.text):
            return lead + latex.unmask(m.text, m.values) + trail
        self.glossary.apply(m)
        masked = re.sub(r"\s+", " ", m.text).strip()
        units = latex.split_sentences(masked) if self.sentence_level else [masked]
        out = [self._translate_unit(u) if latex.has_words(u) else u for u in units]
        return lead + latex.unmask(" ".join(out), m.values) + trail

    def _translate_unit(self, src):
        self.stats.sentences += 1
        vi = self.lookup(src)
        if vi is None:
            self.stats.not_translated += 1
            return src
        vi = latex.clean_translation(vi)
        if latex.placeholders(vi) != latex.placeholders(src) or not vi:
            self.stats.kept_english += 1
            return src
        self.stats.translated += 1
        return vi


def vietnamese_preamble(preamble):
    """Make the preamble able to typeset Vietnamese."""
    if re.search(r"\\usepackage(\[[^\]]*\])?\{[^}]*\bfontspec\b", preamble):
        return preamble  # XeLaTeX/LuaLaTeX: Unicode already works if the font has Vietnamese glyphs
    preamble = re.sub(r"\\usepackage\[[^\]]*\]\{inputenc\}", r"\\usepackage[utf8]{inputenc}", preamble)
    return preamble.rstrip("\n") + "\n% --- paper_translator: Vietnamese support (pdfLaTeX) ---\n\\usepackage[T5]{fontenc}\n"


def translate_tex(text, ctx):
    begin = text.find("\\begin{document}")
    if begin >= 0:
        head, body = vietnamese_preamble(text[:begin]), text[begin:]
    else:
        head, body = "", text
    out = [head]
    for protected, chunk in latex.split_protected(latex.strip_comments(body)):
        if protected:
            out.append(chunk)
            continue
        for prose, piece in latex.split_flow(chunk):
            out.append(ctx.translate_prose(piece) if prose else piece)
    return "".join(out)


class Cache:
    def __init__(self, path):
        self.path = path
        self.data = {}
        if os.path.exists(path):
            with open(path, encoding="utf-8") as f:
                self.data = json.load(f)

    def save(self):
        tmp = self.path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(self.data, f, ensure_ascii=False, indent=0)
        os.replace(tmp, self.path)


def translate_project(source, out_dir, backend, glossary, limit=None, chunk=32):
    """Translate `source` into `out_dir/vi`. Returns (main .tex path, Stats)."""
    os.makedirs(out_dir, exist_ok=True)
    src = os.path.join(out_dir, "original")
    dst = os.path.join(out_dir, "vi")
    prepare_source(source, src)
    files = reading_order(src, find_main_tex(src))
    texts = {p: read_tex(p) for p in files}

    # Pass 1: which sentences need translating.
    wanted = []
    ctx = Context(lambda s: wanted.append(s) or s, glossary, backend.sentence_level)
    for p in files:
        translate_tex(texts[p], ctx)

    cache = Cache(os.path.join(out_dir, f"cache.{backend.name}.json"))
    todo = list(dict.fromkeys(s for s in wanted if s not in cache.data))
    if limit is not None:
        todo = todo[:limit]
    print(f"{len(wanted)} sentences, {len(todo)} to translate with {backend.name}")
    t0 = time.time()
    for s in range(0, len(todo), chunk):
        batch = todo[s : s + chunk]
        for src_text, vi in zip(batch, backend.translate_batch(batch)):
            cache.data[src_text] = vi
        cache.save()
        done = s + len(batch)
        eta = (time.time() - t0) / done * (len(todo) - done)
        print(f"  {done}/{len(todo)}  (~{eta / 60:.1f} min left)", file=sys.stderr)

    # Pass 2: write the translated project.
    if os.path.exists(dst):
        shutil.rmtree(dst)
    shutil.copytree(src, dst)
    ctx = Context(cache.data.get, glossary, backend.sentence_level)
    for p in files:
        with open(os.path.join(dst, os.path.relpath(p, src)), "w", encoding="utf-8") as f:
            f.write(translate_tex(texts[p], ctx))
    shutil.make_archive(os.path.join(out_dir, "vi-overleaf"), "zip", dst)
    return os.path.join(dst, os.path.relpath(files[0], src)), ctx.stats


def compile_pdf(main_tex):
    """Build the PDF with latexmk (or pdflatex/xelatex twice). Returns the PDF path or None."""
    folder, name = os.path.split(main_tex)
    engine = "xelatex" if "fontspec" in read_tex(main_tex) else "pdflatex"
    if shutil.which("latexmk"):
        cmds = [["latexmk", "-xelatex" if engine == "xelatex" else "-pdf", "-interaction=nonstopmode", "-f", name]]
    elif shutil.which(engine):
        cmds = [[engine, "-interaction=nonstopmode", name]] * 2
    else:
        print("No LaTeX installed: upload vi-overleaf.zip to https://www.overleaf.com instead.")
        return None
    for cmd in cmds:
        try:
            subprocess.run(cmd, cwd=folder, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=600)
        except subprocess.TimeoutExpired:
            print(f"{cmd[0]} timed out")
    pdf = os.path.splitext(main_tex)[0] + ".pdf"
    if os.path.exists(pdf):
        return pdf
    print(f"Compilation failed, see {os.path.splitext(main_tex)[0]}.log")
    return None
