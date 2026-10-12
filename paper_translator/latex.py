"""Split a LaTeX body into prose to translate and markup to keep verbatim.

Prose is "masked" before translation: math, citations, references and any other
command become placeholders like [[3]], so the translator only sees words. After
translation the placeholders are put back. A sentence whose placeholders do not
survive translation is kept in English, so the output always compiles.
"""

import re
from collections import Counter

# Environments copied verbatim (math, tables, code, figures drawn in TeX, references).
PROTECTED_ENVS = {
    "equation", "equation*", "align", "align*", "alignat", "alignat*", "gather", "gather*",
    "multline", "multline*", "flalign", "flalign*", "eqnarray", "eqnarray*", "math",
    "displaymath", "tabular", "tabular*", "tabularx", "tabulary", "longtable", "tikzpicture",
    "pgfpicture", "verbatim", "Verbatim", "lstlisting", "minted", "algorithmic", "thebibliography",
    "filecontents", "filecontents*", "comment",
}
VERBATIM_ENVS = {"verbatim", "Verbatim", "lstlisting", "minted", "comment", "filecontents", "filecontents*"}
# Commands that end a paragraph; they are kept verbatim between translated pieces.
BOUNDARY_CMDS = {
    "begin", "end", "item", "par", "\\", "newline", "linebreak", "centering", "raggedright",
    "raggedleft", "maketitle", "newpage", "clearpage", "cleardoublepage", "noindent", "vspace",
    "vspace*", "smallskip", "medskip", "bigskip", "appendix", "tableofcontents", "bibliography",
    "bibliographystyle", "printbibliography", "includegraphics", "input", "include", "label",
}
# Headings stand alone; their argument is translated as its own unit.
HEADING_CMDS = {"part", "chapter", "section", "subsection", "subsubsection", "paragraph", "subparagraph"}
HEADING_CMDS |= {c + "*" for c in HEADING_CMDS}
# Commands whose main argument is prose, translated as its own unit.
SEGMENT_CMDS = {
    "part", "chapter", "section", "subsection", "subsubsection", "paragraph", "subparagraph",
    "caption", "subcaption", "footnote", "title",
}
SEGMENT_CMDS |= {c + "*" for c in SEGMENT_CMDS}
# Inline formatting: the argument is translated, unless it looks like a name (V-JEPA, DINOv2).
FORMAT_CMDS = {"textbf", "textit", "emph", "underline", "textsl"}
DEFINITION_CMDS = {"def", "gdef", "edef", "xdef"}

PH_RE = re.compile(r"\[\[\s*(\d+)\s*\]\]")
_NAME_LIKE = re.compile(r"[A-Za-z0-9./\-]+[A-Z0-9][A-Za-z0-9./\-]*|[A-Z0-9./\-]+")  # V-JEPA, DINOv2, ViT-L/16
_ABBREV = {"e.g", "i.e", "al", "fig", "figs", "eq", "eqs", "sec", "secs", "tab", "vs", "cf", "resp",
           "approx", "no", "dr", "ref", "refs", "app", "alg"}


# ---------------------------------------------------------------- low-level scanning
def skip_group(text, i, open_ch="{", close_ch="}"):
    """`text[i]` is `open_ch`; return the index just after its matching `close_ch`."""
    depth, j, n = 0, i, len(text)
    while j < n:
        c = text[j]
        if c == "\\":
            j += 2
            continue
        if c == open_ch:
            depth += 1
        elif c == close_ch:
            depth -= 1
            if depth == 0:
                return j + 1
        j += 1
    return n


def read_command(text, i):
    """`text[i]` is a backslash. Return (name, index after the name)."""
    j = i + 1
    if j < len(text) and text[j].isalpha():
        while j < len(text) and text[j].isalpha():
            j += 1
        name = text[i + 1 : j]
        if j < len(text) and text[j] == "*":
            name += "*"
            j += 1
        return name, j
    return text[i + 1 : i + 2], min(i + 2, len(text))


def read_args(text, j):
    """Skip the [..] / {..} groups directly after a command."""
    while j < len(text) and text[j] in "[{":
        j = skip_group(text, j, text[j], "]" if text[j] == "[" else "}")
    return j


def _find_unescaped(text, token, start):
    j = start
    while True:
        j = text.find(token, j)
        if j < 0:
            return -1
        k, slashes = j - 1, 0
        while k >= 0 and text[k] == "\\":
            slashes, k = slashes + 1, k - 1
        if slashes % 2 == 0:
            return j
        j += 1


def math_end(text, i):
    """If a math span starts at `i`, return its end index, else None."""
    if text.startswith("$$", i):
        end = text.find("$$", i + 2)
        return len(text) if end < 0 else end + 2
    if text[i] == "$":
        end = _find_unescaped(text, "$", i + 1)
        return len(text) if end < 0 else end + 1
    for open_, close in (("\\(", "\\)"), ("\\[", "\\]")):
        if text.startswith(open_, i):
            end = text.find(close, i + 2)
            return len(text) if end < 0 else end + 2
    return None


# ---------------------------------------------------------------- comments and structure
def strip_comments(text):
    """Drop comment text but keep the `%` (it still joins lines); verbatim envs are untouched."""
    out, verbatim = [], None
    for line in text.split("\n"):
        if verbatim:
            out.append(line)
            if re.search(r"\\end\{" + re.escape(verbatim) + r"\}", line):
                verbatim = None
            continue
        idx = _find_unescaped(line, "%", 0)
        out.append(line if idx < 0 else line[: idx + 1])
        m = re.search(r"\\begin\{([^}]+)\}", out[-1])
        if m and m.group(1) in VERBATIM_ENVS and not re.search(r"\\end\{" + re.escape(m.group(1)) + r"\}", out[-1]):
            verbatim = m.group(1)
    return "\n".join(out)


_ENV_OR_IFFALSE = re.compile(r"\\begin\{([^}]+)\}|\\iffalse\b")


def split_protected(text):
    """Yield (is_protected, chunk) for protected environments and \\iffalse blocks."""
    pos = 0
    for m in _ENV_OR_IFFALSE.finditer(text):
        if m.start() < pos:
            continue
        if m.group(0).startswith("\\iffalse"):
            end_m = re.compile(r"\\fi\b").search(text, m.end())
            end = len(text) if end_m is None else end_m.end()
        elif m.group(1) in PROTECTED_ENVS:
            end = _env_end(text, m.group(1), m.end())
        else:
            continue
        if m.start() > pos:
            yield False, text[pos : m.start()]
        yield True, text[m.start() : end]
        pos = end
    if pos < len(text):
        yield False, text[pos:]


def _env_end(text, name, start):
    tok = re.compile(r"\\(begin|end)\{" + re.escape(name) + r"\}")
    depth = 1
    for m in tok.finditer(text, start):
        depth += 1 if m.group(1) == "begin" else -1
        if depth == 0:
            return m.end()
    return len(text)


_BLANK = re.compile(r"\n[ \t]*\n\s*")


def split_flow(text):
    """Split non-protected text into (is_prose, piece) at blank lines and boundary commands."""
    pieces, start, i, n = [], 0, 0, len(text)

    def flush(upto):
        if upto > start:
            pieces.append((True, text[start:upto]))

    while i < n:
        end = math_end(text, i) if text[i] in "$\\" else None
        if end is not None:
            i = end
            continue
        if text[i] == "\\":
            name, j = read_command(text, i)
            j = read_args(text, j)
            if name in BOUNDARY_CMDS or name in HEADING_CMDS:
                flush(i)
                pieces.append((name in HEADING_CMDS, text[i:j]))
                start = i = j
            else:
                i = j
            continue
        if text[i] == "\n":
            m = _BLANK.match(text, i)
            if m:
                flush(i)
                pieces.append((False, m.group(0)))
                start = i = m.end()
                continue
        i += 1
    flush(n)
    return pieces


# ---------------------------------------------------------------- masking
class Masked:
    """Prose with placeholders; `values[k - 1]` is what [[k]] stands for."""

    def __init__(self):
        self.parts = []
        self.values = []

    def ph(self, value):
        self.values.append(value)
        self.parts.append(f"[[{len(self.values)}]]")

    @property
    def text(self):
        return "".join(self.parts)


def mask(text, translate_unit):
    """Mask `text`. `translate_unit(prose)` translates the arguments of SEGMENT/FORMAT commands."""
    m, i, n, tilde = Masked(), 0, len(text), ""

    def ph(value):
        nonlocal tilde
        m.ph(tilde + value)
        tilde = ""

    while i < n:
        c = text[i]
        end = math_end(text, i) if c in "$\\" else None
        if end is not None:
            ph(text[i:end])
            i = end
        elif c == "\\":
            name, j = read_command(text, i)
            if name in SEGMENT_CMDS or name in FORMAT_CMDS:
                k = j
                while k < n and text[k] == "[":
                    k = skip_group(text, k, "[", "]")
                if k < n and text[k] == "{":
                    arg_end = skip_group(text, k)
                    arg = text[k + 1 : arg_end - 1]
                    if name in FORMAT_CMDS and _NAME_LIKE.fullmatch(arg.strip()):
                        ph(text[i:arg_end])
                    else:
                        ph(text[i : k + 1] + translate_unit(arg) + "}")
                    i = arg_end
                    continue
            if name in DEFINITION_CMDS:
                j = text.find("{", j)
                j = n if j < 0 else skip_group(text, j)
            else:
                j = read_args(text, j)
            ph(text[i:j])
            i = j
        elif c == "{":
            j = skip_group(text, i)
            ph(text[i:j])
            i = j
        elif c == "~":
            if text.startswith("\\", i + 1):
                tilde = "~"  # kept with the next placeholder: Fig.~\ref{x}
            m.parts.append(" ")
            i += 1
        elif text.startswith("``", i):
            m.parts.append("\u201c")
            i += 2
        elif text.startswith("''", i):
            m.parts.append("\u201d")
            i += 2
        else:
            m.parts.append(c)
            i += 1
    return m


_PH_WITH_SPACE = re.compile(r"(\s*)\[\[\s*(\d+)\s*\]\]")


def unmask(text, values):
    def sub(mt):
        value = values[int(mt.group(2)) - 1]
        return value if value.startswith("~") else mt.group(1) + value

    return _PH_WITH_SPACE.sub(sub, text)


def placeholders(text):
    return Counter(int(x) for x in PH_RE.findall(text))


def has_words(masked_text):
    return re.search(r"[A-Za-z]{2,}", PH_RE.sub(" ", masked_text)) is not None


_SPECIALS = {"%": r"\%", "&": r"\&", "#": r"\#", "_": r"\_", "$": r"\$", "^": r"\^{}", "~": " ",
             "\\": "", "{": "", "}": ""}


def clean_translation(text):
    """Make translator output safe for LaTeX (all original markup is inside placeholders)."""
    out, pos = [], 0
    for mt in PH_RE.finditer(text):
        out.append("".join(_SPECIALS.get(c, c) for c in text[pos : mt.start()]))
        out.append(f"[[{mt.group(1)}]]")
        pos = mt.end()
    out.append("".join(_SPECIALS.get(c, c) for c in text[pos:]))
    return re.sub(r"[ \t]+", " ", "".join(out)).strip()


def split_sentences(text):
    """Split masked prose into sentences, keeping abbreviations like "e.g." and "et al." intact."""
    sentences, start = [], 0
    for mt in re.finditer(r"[.!?]\s+(?=[A-Z0-9\[\u201c(])", text):
        word = re.search(r"([A-Za-z.]+)$", text[start : mt.start()])
        if word and word.group(1).lower().rstrip(".") in _ABBREV:
            continue
        if word and len(word.group(1)) == 1 and word.group(1).isupper():  # initials: "J. Smith"
            continue
        sentences.append(text[start : mt.start() + 1].strip())
        start = mt.end()
    tail = text[start:].strip()
    if tail:
        sentences.append(tail)
    return sentences
