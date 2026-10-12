"""Get a paper's LaTeX source: from arXiv, or from a local folder / archive / .tex file."""

import gzip
import io
import os
import re
import shutil
import tarfile
import urllib.request

ARXIV_ID = re.compile(r"(\d{4}\.\d{4,5}(?:v\d+)?)")


def arxiv_id(source):
    """'2506.09985', 'https://arxiv.org/abs/2506.09985v1', '.../pdf/2506.09985' -> id; else None."""
    if os.path.exists(source):
        return None
    m = ARXIV_ID.search(source)
    return m.group(1) if m else None


def download_arxiv(aid):
    url = f"https://arxiv.org/e-print/{aid}"
    print(f"Downloading {url}")
    req = urllib.request.Request(url, headers={"User-Agent": "paper-translator/0.1 (personal use)"})
    with urllib.request.urlopen(req, timeout=120) as r:
        return r.read()


def unpack(data, dest):
    """Unpack an arXiv e-print (tar.gz, or a single gzipped .tex) into `dest`."""
    os.makedirs(dest, exist_ok=True)
    if data[:4] == b"%PDF":
        raise SystemExit("arXiv only has a PDF for this paper (no LaTeX source), so it cannot be translated this way.")
    try:
        with tarfile.open(fileobj=io.BytesIO(data), mode="r:*") as tar:
            _safe_extract(tar, dest)
        return
    except tarfile.ReadError:
        pass
    try:
        data = gzip.decompress(data)
    except OSError:
        pass
    with open(os.path.join(dest, "main.tex"), "wb") as f:
        f.write(data)


def _safe_extract(tar, dest):
    try:
        tar.extractall(dest, filter="data")
    except TypeError:  # Python without extraction filters
        root = os.path.realpath(dest)
        for member in tar.getmembers():
            target = os.path.realpath(os.path.join(dest, member.name))
            if not target.startswith(root + os.sep) or member.issym() or member.islnk() or member.isdev():
                raise SystemExit(f"Refusing unsafe archive member: {member.name}")
        tar.extractall(dest)


def prepare_source(source, dest):
    """Copy or download the source into `dest` (a fresh folder)."""
    if os.path.exists(dest):
        shutil.rmtree(dest)
    aid = arxiv_id(source)
    if aid:
        unpack(download_arxiv(aid), dest)
    elif os.path.isdir(source):
        shutil.copytree(source, dest)
    elif source.endswith(".tex"):
        os.makedirs(dest)
        shutil.copy(source, os.path.join(dest, os.path.basename(source)))
    elif os.path.isfile(source):
        with open(source, "rb") as f:
            unpack(f.read(), dest)
    else:
        raise SystemExit(f"Not an arXiv id or an existing path: {source}")


def read_tex(path):
    with open(path, "rb") as f:
        raw = f.read()
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        return raw.decode("latin-1")


def find_main_tex(root):
    """The .tex with \\documentclass and \\begin{document}; the largest one if several."""
    candidates = []
    for path in tex_files(root):
        text = read_tex(path)
        if re.search(r"^[^%\n]*\\documentclass", text, re.M) and "\\begin{document}" in text:
            standalone = re.search(r"\\documentclass(\[[^\]]*\])?\{standalone\}", text) is not None
            candidates.append((not standalone, len(text), path))
    if not candidates:
        raise SystemExit(f"No main .tex file (with \\documentclass and \\begin{{document}}) found in {root}")
    return max(candidates)[2]


def tex_files(root):
    found = []
    for d, _, files in os.walk(root):
        found += [os.path.join(d, f) for f in files if f.endswith(".tex")]
    return sorted(found)


def reading_order(root, main):
    """.tex files in the order they are \\input from the main file, then any others."""
    order, seen = [], set()

    def visit(path):
        path = os.path.normpath(path)
        if path in seen or not os.path.isfile(path):
            return
        seen.add(path)
        order.append(path)
        for m in re.finditer(r"^[^%\n]*?\\(?:input|include|subfile)\{([^}]+)\}", read_tex(path), re.M):
            name = m.group(1).strip()
            sub = os.path.join(root, name)
            visit(sub if sub.endswith(".tex") else sub + ".tex")

    visit(main)
    return order + [p for p in tex_files(root) if os.path.normpath(p) not in seen]
