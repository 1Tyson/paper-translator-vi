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
        self.sentences = self.translated = self.piecewise = self.kept_english = self.not_translated = 0

    def __str__(self):
        return (f"{self.sentences} sentences: {self.translated} translated, "
                f"{self.piecewise} translated piece by piece (placeholders lost in the whole sentence), "
                f"{self.kept_english} kept in English, {self.not_translated} not translated yet")


class Context:
    """`lookup(masked_text)` returns the translation, or None if there is none (recorded in `misses`)."""

    def __init__(self, lookup, glossary, sentence_level=True):
        self.lookup = lookup
        self.glossary = glossary
        self.sentence_level = sentence_level
        self.stats = Stats()
        self.misses = []
        self.problems = []  # (source, model output) for sentences that lost placeholders
        glossary.reset()

    def _lookup(self, src):
        vi = self.lookup(src)
        if vi is None:
            self.misses.append(src)
        return vi

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
        vi = self._lookup(src)
        if vi is None:
            self.stats.not_translated += 1
            return src
        vi = latex.clean_translation(vi)
        if vi and latex.placeholders(vi) == latex.placeholders(src):
            self.stats.translated += 1
            return vi
        self.problems.append((src, vi))
        pieces = self._piecewise(src)
        if pieces is None:
            self.stats.kept_english += 1
            return src
        self.stats.piecewise += 1
        return pieces

    def _piecewise(self, src):
        """Translate the text between placeholders separately, so none can be lost."""
        out, ok = [], True
        for part in re.split(r"(\[\[\d+\]\])", src):
            if latex.PH_RE.fullmatch(part) or not latex.has_words(part):
                out.append(part)
                continue
            vi = self._lookup(part.strip())
            vi = None if vi is None else latex.clean_translation(vi)
            if not vi or latex.placeholders(vi):
                ok = False
                out.append(part)
                continue
            lead = " " if part[:1].isspace() else ""
            trail = " " if part[-1:].isspace() else ""
            out.append(lead + vi + trail)
        return "".join(out) if ok else None


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

    # Run the LaTeX pass, translate what it was missing, repeat: the second round
    # translates the pieces of sentences that lost placeholders as a whole.
    cache = Cache(os.path.join(out_dir, f"cache.{backend.name}.json"))
    budget = limit
    for _ in range(3):
        ctx = Context(cache.data.get, glossary, backend.sentence_level)
        for p in files:
            translate_tex(texts[p], ctx)
        todo = list(dict.fromkeys(s for s in ctx.misses if s not in cache.data))
        if budget is not None:
            todo, budget = todo[:budget], budget - min(budget, len(todo))
        if not todo:
            break
        print(f"{ctx.stats.sentences} sentences, {len(todo)} texts to translate with {backend.name}")
        _translate_all(backend, todo, cache, chunk)

    # Write the translated project.
    if os.path.exists(dst):
        shutil.rmtree(dst)
    shutil.copytree(src, dst)
    ctx = Context(cache.data.get, glossary, backend.sentence_level)
    for p in files:
        with open(os.path.join(dst, os.path.relpath(p, src)), "w", encoding="utf-8") as f:
            f.write(translate_tex(texts[p], ctx))
    with open(os.path.join(out_dir, "problems.txt"), "w", encoding="utf-8") as f:
        f.write("Sentences whose placeholders [[n]] did not survive translation as a whole sentence.\n"
                "They were translated piece by piece instead (or kept in English if that failed too).\n\n")
        for src_text, vi in ctx.problems:
            f.write(f"EN: {src_text}\nVI: {vi}\n\n")
    shutil.make_archive(os.path.join(out_dir, "vi-overleaf"), "zip", dst)
    return os.path.join(dst, os.path.relpath(files[0], src)), ctx.stats


def _translate_all(backend, todo, cache, chunk):
    t0 = time.time()
    for s in range(0, len(todo), chunk):
        batch = todo[s : s + chunk]
        for src_text, vi in zip(batch, backend.translate_batch(batch)):
            cache.data[src_text] = vi
        cache.save()
        done = s + len(batch)
        eta = (time.time() - t0) / done * (len(todo) - done)
        print(f"  {done}/{len(todo)}  (~{eta / 60:.1f} min left)", file=sys.stderr)


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
