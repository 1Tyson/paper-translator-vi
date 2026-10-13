import os
import re
import shutil
import subprocess

import pytest

from paper_translator import latex
from paper_translator.glossary import Glossary
from paper_translator.pipeline import Context, compile_pdf, translate_project, translate_tex

SAMPLE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "sample_paper")


def run(tex, translate=lambda s: "VI " + s, glossary=None, sentence_level=True):
    seen = []

    def lookup(s):
        seen.append(s)
        return translate(s)

    ctx = Context(lookup, glossary or Glossary(), sentence_level)
    return translate_tex(tex, ctx), seen, ctx.stats


def test_math_citations_and_refs_are_hidden_from_the_translator():
    out, seen, _ = run(r"We predict $s_y = f(y)$ as in~\cite{a}, see Fig.~\ref{f} and 5\% more.")
    assert seen == ["We predict [[1]] as in [[2]], see Fig. [[3]] and 5[[4]] more."]
    assert out == r"VI We predict $s_y = f(y)$ as in~\cite{a}, see Fig.~\ref{f} and 5\% more."


def test_protected_environments_and_comments_are_kept():
    tex = ("Intro text.\n\\begin{equation}\nE = mc^2 % energy\n\\end{equation}\n"
           "% a comment\n\\begin{tabular}{l}Method \\\\\n\\end{tabular}\n\\iffalse\nOld text.\n\\fi\n")
    out, seen, _ = run(tex)
    assert seen == ["Intro text."]
    assert "E = mc^2 %\n" in out and "Method \\\\" in out and "Old text." in out
    assert "a comment" not in out


def test_headings_captions_and_footnotes_are_separate_units():
    tex = ("\\section{Related Work}\\label{s}\nPrior work exists.\\footnote{See the appendix.}\n"
           "\\begin{figure}\\centering\\caption{An overview.}\\end{figure}\n")
    out, seen, _ = run(tex)
    assert seen == ["Related Work", "See the appendix.", "Prior work exists.[[1]]", "An overview."]
    assert "\\section{VI Related Work}" in out
    assert "\\footnote{VI See the appendix.}" in out and "\\caption{VI An overview.}" in out


def test_emph_translates_words_but_keeps_names():
    _, seen, _ = run(r"Our \emph{main idea} beats \textbf{DINOv2}.")
    assert seen == ["main idea", "Our [[1]] beats [[2]]."]


def test_sentence_split_keeps_abbreviations():
    assert latex.split_sentences("We use e.g. ViT [[1]]. Results by Smith et al. hold. Next one!") == [
        "We use e.g. ViT [[1]].", "Results by Smith et al. hold.", "Next one!"]


def test_lost_placeholder_translates_piece_by_piece():
    vi = {"We train it as in [[1]] with [[2]].": "Chúng tôi huấn luyện nó.",  # placeholders lost
          "We train it as in": "Chúng tôi huấn luyện nó như trong", "with": "với", "Fine here.": "Tốt."}
    out, _, stats = run(r"We train it as in~\cite{x} with $T$. Fine here.", translate=vi.get)
    assert out == r"Chúng tôi huấn luyện nó như trong~\cite{x} với $T$. Tốt."
    assert (stats.translated, stats.piecewise, stats.kept_english) == (1, 1, 0)


def test_english_kept_when_pieces_fail_too():
    out, _, stats = run(r"See~\cite{x}. Fine here.", translate=lambda s: "Xem [[7]]." if "See" in s else "Tốt.")
    assert out == r"See~\cite{x}. Tốt."
    assert (stats.translated, stats.kept_english) == (1, 1)


class LosePlaceholders:
    name, sentence_level = "lose", True

    def translate_batch(self, texts):
        return ["VI " + re.sub(r"\[\[\d+\]\]", "", t) for t in texts]


def test_project_retranslates_failed_sentences_piecewise(tmp_path):
    main, stats = translate_project(SAMPLE, str(tmp_path), LosePlaceholders(), Glossary.load())
    assert stats.kept_english == 0 and stats.not_translated == 0 and stats.piecewise > 0
    assert "\\cite{lecun2022}" in open(main, encoding="utf-8").read()
    assert "EN: " in open(os.path.join(tmp_path, "problems.txt"), encoding="utf-8").read()


def test_translator_output_is_escaped_for_latex():
    out, _, _ = run("Accuracy grows.", translate=lambda s: "Độ chính xác tăng 5% & {x}_1.")
    assert out == r"Độ chính xác tăng 5\% \& x\_1."


def test_glossary_first_occurrence_shows_english():
    g = Glossary({"self-supervised learning": "học tự giám sát"}, ["encoder"])
    out, seen, _ = run("Self-supervised learning trains encoders. Self-supervised learning works.", glossary=g)
    assert seen[0] == "[[1]] trains [[2]]."
    assert out == ("VI học tự giám sát (Self-supervised learning) trains encoders. "
                   "VI học tự giám sát works.")


def test_preamble_gets_vietnamese_support():
    out, _, _ = run("\\documentclass{article}\n\\usepackage[latin1]{inputenc}\n\\begin{document}\nHi there.\n\\end{document}")
    assert "\\usepackage[utf8]{inputenc}" in out
    assert out.index("\\usepackage[T5]{fontenc}") < out.index("\\begin{document}")


class MarkBackend:
    name, sentence_level = "mark", True

    def translate_batch(self, texts):
        return ["Bản dịch tiếng Việt: " + t for t in texts]


def test_whole_project(tmp_path):
    main, stats = translate_project(SAMPLE, str(tmp_path), MarkBackend(), Glossary.load())
    assert stats.translated == stats.sentences > 15
    vi = open(main, encoding="utf-8").read()
    method = open(os.path.join(tmp_path, "vi", "sections", "method.tex"), encoding="utf-8").read()
    assert "\\section{Bản dịch tiếng Việt: Method}" in method
    assert "This paragraph is disabled" in vi and "\\bibitem{lecun2022} Y. LeCun." in vi
    assert os.path.exists(os.path.join(tmp_path, "vi-overleaf.zip"))
    # every \cite/\ref/$...$ of the original survives
    orig = open(os.path.join(SAMPLE, "main.tex"), encoding="utf-8").read()
    for pat in (r"\\cite\{\w+\}", r"\\ref\{[\w:]+\}", r"\$[^$]+\$"):
        assert re.findall(pat, orig) == re.findall(pat, vi)

    # a second run reuses the cache instead of translating again
    class Fail(MarkBackend):
        def translate_batch(self, texts):
            assert not texts
            return []

    translate_project(SAMPLE, str(tmp_path), Fail(), Glossary.load())


@pytest.mark.skipif(shutil.which("pdflatex") is None or shutil.which("pdftotext") is None,
                    reason="needs pdflatex (with vntex) and pdftotext")
def test_pdf_shows_vietnamese(tmp_path):
    main, _ = translate_project(SAMPLE, str(tmp_path), MarkBackend(), Glossary.load())
    pdf = compile_pdf(main)
    assert pdf
    text = subprocess.run(["pdftotext", "-enc", "UTF-8", pdf, "-"], capture_output=True, text=True).stdout
    assert "Bản dịch tiếng Việt" in text and "học tự giám sát" in text
