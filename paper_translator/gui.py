"""Desktop app: paste an arXiv id, get the Vietnamese PDF.

The translation runs `python -m paper_translator ... --compile` in a child process, so the
window stays responsive, "Dừng" can stop it, and the cache lets it resume later.
The heavy model libraries (torch, transformers) are not bundled: the app installs them
once into USER_LIBS (CPU build, or CUDA build when an NVIDIA GPU is present).
"""

import importlib.util
import os
import re
import shutil
import sys

from PySide6.QtCore import QProcess, QProcessEnvironment, Qt, QUrl
from PySide6.QtGui import QDesktopServices, QFont, QGuiApplication, QIcon
from PySide6.QtWidgets import (
    QApplication, QButtonGroup, QFileDialog, QFrame, QGridLayout, QHBoxLayout, QLabel, QLineEdit,
    QMessageBox, QPlainTextEdit, QProgressBar, QPushButton, QRadioButton, QSpinBox, QVBoxLayout, QWidget,
)

from .fetch import arxiv_id

APP_NAME = "Paper Translator VI"
DATA_DIR = os.path.join(os.environ.get("XDG_DATA_HOME") or os.path.expanduser("~/.local/share"), "paper-translator-vi")
USER_LIBS = os.path.join(DATA_DIR, f"pylib-{sys.version_info.major}.{sys.version_info.minor}")
DEFAULT_OUT = os.path.join(os.path.expanduser("~/Documents"), "PaperTranslator")
TEX_INSTALL = "sudo pacman -S --needed texlive texlive-langother"
ICON = os.path.join(os.path.dirname(os.path.abspath(__file__)), "icon.svg")

if os.path.isdir(USER_LIBS) and USER_LIBS not in sys.path:
    sys.path.insert(0, USER_LIBS)


def model_libs_installed():
    importlib.invalidate_caches()
    return all(importlib.util.find_spec(m) is not None for m in ("torch", "transformers", "sentencepiece"))


def has_nvidia_gpu():
    return shutil.which("nvidia-smi") is not None or os.path.exists("/proc/driver/nvidia/version")


def latex_available():
    return shutil.which("latexmk") is not None or shutil.which("pdflatex") is not None


def install_commands(gpu):
    pip = [sys.executable, "-m", "pip", "install", "--disable-pip-version-check", "--upgrade", "--target", USER_LIBS]
    torch = pip + ["torch"] + ([] if gpu else ["--index-url", "https://download.pytorch.org/whl/cpu"])
    return [torch, pip + ["transformers>=4.40", "sentencepiece", "protobuf"]]


class Window(QWidget):
    def __init__(self):
        super().__init__()
        self.setWindowTitle(APP_NAME)
        if os.path.exists(ICON):
            self.setWindowIcon(QIcon(ICON))
        self.resize(760, 640)
        self.proc = None
        self.queue = []  # pending (program, args) for the dependency installer
        self.pdf = self.out_dir = None

        title = QLabel("Dịch paper khoa học sang tiếng Việt")
        f = QFont()
        f.setPointSize(f.pointSize() + 5)
        f.setBold(True)
        title.setFont(f)
        subtitle = QLabel("Nhập mã arXiv hoặc chọn file source LaTeX. Kết quả là file PDF tiếng Việt giữ nguyên bố cục.")
        subtitle.setWordWrap(True)

        self.source = QLineEdit()
        self.source.setPlaceholderText("Ví dụ: 2506.09985  hoặc  https://arxiv.org/abs/2506.09985")
        self.source.returnPressed.connect(self.start)
        pick = QPushButton("Chọn file…")
        pick.clicked.connect(self.pick_source)

        self.vinai = QRadioButton("VinAI (dịch Anh→Việt, chạy trên máy)")
        self.ollama = QRadioButton("Ollama:")
        self.vinai.setChecked(True)
        group = QButtonGroup(self)
        group.addButton(self.vinai)
        group.addButton(self.ollama)
        self.ollama_model = QLineEdit("qwen2.5:7b")
        self.ollama_model.setMaximumWidth(180)
        for w in (self.vinai, self.ollama):
            w.toggled.connect(self.refresh_status)

        self.out = QLineEdit(DEFAULT_OUT)
        change_out = QPushButton("Đổi…")
        change_out.clicked.connect(self.pick_out)

        self.limit = QSpinBox()
        self.limit.setRange(0, 100000)
        self.limit.setSpecialValueText("cả bài")
        self.limit.setSuffix(" câu")
        self.limit.setToolTip("Dịch thử N câu đầu để xem chất lượng. Phần đã dịch được lưu lại cho lần sau.")

        grid = QGridLayout()
        grid.addWidget(QLabel("Paper:"), 0, 0)
        grid.addWidget(self.source, 0, 1, 1, 3)
        grid.addWidget(pick, 0, 4)
        grid.addWidget(QLabel("Model:"), 1, 0)
        grid.addWidget(self.vinai, 1, 1)
        models = QHBoxLayout()
        models.addWidget(self.ollama)
        models.addWidget(self.ollama_model)
        models.addStretch()
        grid.addLayout(models, 1, 2, 1, 3)
        grid.addWidget(QLabel("Lưu vào:"), 2, 0)
        grid.addWidget(self.out, 2, 1, 1, 3)
        grid.addWidget(change_out, 2, 4)
        grid.addWidget(QLabel("Dịch:"), 3, 0)
        grid.addWidget(self.limit, 3, 1)
        grid.setColumnStretch(1, 1)

        # Component status: model libraries and LaTeX.
        self.model_status = QLabel()
        self.install_btn = QPushButton("Cài đặt")
        self.install_btn.clicked.connect(self.install_model_libs)
        self.tex_status = QLabel()
        self.tex_status.setWordWrap(True)
        self.copy_tex = QPushButton("Sao chép lệnh")
        self.copy_tex.clicked.connect(lambda: QGuiApplication.clipboard().setText(TEX_INSTALL))
        status = QGridLayout()
        status.addWidget(self.model_status, 0, 0)
        status.addWidget(self.install_btn, 0, 1)
        status.addWidget(self.tex_status, 1, 0)
        status.addWidget(self.copy_tex, 1, 1)
        status.setColumnStretch(0, 1)
        status_box = QFrame()
        status_box.setFrameShape(QFrame.StyledPanel)
        status_box.setLayout(status)

        self.go = QPushButton("Dịch → PDF")
        self.go.setDefault(True)
        self.go.setMinimumHeight(38)
        self.go.clicked.connect(self.start)
        self.stop_btn = QPushButton("Dừng")
        self.stop_btn.setEnabled(False)
        self.stop_btn.clicked.connect(self.stop)
        actions = QHBoxLayout()
        actions.addWidget(self.go, 3)
        actions.addWidget(self.stop_btn, 1)

        self.progress = QProgressBar()
        self.progress.setTextVisible(False)
        self.stage = QLabel("Sẵn sàng.")
        self.log = QPlainTextEdit()
        self.log.setReadOnly(True)
        self.log.setMaximumBlockCount(5000)
        mono = QFont("monospace")
        mono.setStyleHint(QFont.Monospace)
        self.log.setFont(mono)

        self.open_pdf = QPushButton("Mở PDF")
        self.open_pdf.clicked.connect(lambda: self.pdf and QDesktopServices.openUrl(QUrl.fromLocalFile(self.pdf)))
        self.open_dir = QPushButton("Mở thư mục kết quả")
        self.open_dir.clicked.connect(
            lambda: self.out_dir and QDesktopServices.openUrl(QUrl.fromLocalFile(self.out_dir)))
        self.open_pdf.setEnabled(False)
        self.open_dir.setEnabled(False)
        results = QHBoxLayout()
        results.addWidget(self.open_pdf)
        results.addWidget(self.open_dir)
        results.addStretch()

        layout = QVBoxLayout(self)
        layout.addWidget(title)
        layout.addWidget(subtitle)
        layout.addSpacing(6)
        layout.addLayout(grid)
        layout.addWidget(status_box)
        layout.addLayout(actions)
        layout.addWidget(self.stage)
        layout.addWidget(self.progress)
        layout.addWidget(self.log, 1)
        layout.addLayout(results)
        self.refresh_status()

    # ------------------------------------------------------------ status
    def refresh_status(self):
        busy = self.proc is not None
        if self.ollama.isChecked():
            self.model_status.setText("Ollama: cần cài Ollama và tải model trước (ollama pull " +
                                      self.ollama_model.text() + ").")
            self.install_btn.setVisible(False)
        elif model_libs_installed():
            self.model_status.setText("Thành phần dịch VinAI: đã cài ✓")
            self.install_btn.setVisible(False)
        else:
            size = "khoảng 3 GB (có GPU NVIDIA)" if has_nvidia_gpu() else "khoảng 1 GB (bản CPU)"
            self.model_status.setText(f"Thành phần dịch VinAI: chưa cài, cần tải {size}, chỉ một lần.")
            self.install_btn.setVisible(True)
        if latex_available():
            self.tex_status.setText("LaTeX để xuất PDF: đã có ✓")
            self.copy_tex.setVisible(False)
        else:
            self.tex_status.setText("Chưa có LaTeX nên chỉ xuất file zip cho Overleaf. Để xuất thẳng PDF, cài bằng: "
                                    + TEX_INSTALL)
            self.copy_tex.setVisible(True)
        self.go.setEnabled(not busy)
        self.install_btn.setEnabled(not busy)
        self.stop_btn.setEnabled(busy)

    # ------------------------------------------------------------ pickers
    def pick_source(self):
        path, _ = QFileDialog.getOpenFileName(self, "Chọn source LaTeX", os.path.expanduser("~"),
                                              "LaTeX source (*.tar.gz *.tgz *.tar *.gz *.tex);;Tất cả (*)")
        if path:
            self.source.setText(path)

    def pick_out(self):
        path = QFileDialog.getExistingDirectory(self, "Lưu kết quả vào", self.out.text())
        if path:
            self.out.setText(path)

    # ------------------------------------------------------------ processes
    def run(self, program, args, env=None, on_done=None):
        self.proc = QProcess(self)
        self.proc.setProcessChannelMode(QProcess.MergedChannels)
        e = QProcessEnvironment.systemEnvironment()
        e.insert("PYTHONUNBUFFERED", "1")
        for k, v in (env or {}).items():
            e.insert(k, v)
        self.proc.setProcessEnvironment(e)
        self.proc.readyReadStandardOutput.connect(self.read_output)
        self.proc.finished.connect(lambda code, _status: self.finished(code, on_done))
        self.proc.errorOccurred.connect(lambda err: err == QProcess.FailedToStart and self.finished(-1, on_done))
        self.log.appendPlainText("$ " + " ".join([os.path.basename(program)] + args))
        self.proc.start(program, args)
        self.refresh_status()

    def read_output(self):
        text = bytes(self.proc.readAllStandardOutput()).decode("utf-8", "replace")
        for line in re.split(r"[\r\n]+", text):
            if not line.strip():
                continue
            self.log.appendPlainText(line)
            self.parse(line)

    def parse(self, line):
        m = re.match(r"\s*(\d+)/(\d+)\s+\(~([\d.]+) min left\)", line)
        if m:
            done, total = int(m.group(1)), int(m.group(2))
            self.progress.setRange(0, total)
            self.progress.setValue(done)
            self.stage.setText(f"Đang dịch {done}/{total}, còn khoảng {float(m.group(3)):.0f} phút…")
        elif re.match(r"\d+ sentences, \d+ texts to translate", line):
            self.stage.setText("Đang dịch… (" + line.strip() + ")")
        elif line.startswith("Downloading"):
            self.stage.setText("Đang tải source LaTeX từ arXiv…")
        elif line.startswith("Loading"):
            self.stage.setText("Đang nạp model (lần đầu sẽ tải model về, mất vài phút)…")
            self.progress.setRange(0, 0)
        elif line.startswith("Translated LaTeX:"):
            self.stage.setText("Đang build PDF…")
            self.progress.setRange(0, 0)
        elif line.startswith("PDF:"):
            self.pdf = line.split(":", 1)[1].strip()
        elif line.startswith("Collecting") or line.startswith("Downloading torch"):
            self.stage.setText("Đang tải thành phần dịch… " + line.strip()[:80])

    def finished(self, code, on_done):
        if self.proc is None:
            return
        self.proc = None
        self.progress.setRange(0, 1)
        self.progress.setValue(1 if code == 0 else 0)
        if on_done:
            on_done(code)
        self.refresh_status()

    def stop(self):
        if self.proc:
            self.proc.kill()
            self.stage.setText("Đã dừng. Bấm Dịch lần nữa sẽ dịch tiếp từ chỗ đã dừng.")

    # ------------------------------------------------------------ install model libraries
    def install_model_libs(self):
        gpu = has_nvidia_gpu()
        msg = ("Sẽ tải torch (bản CUDA cho GPU NVIDIA, khoảng 3 GB)" if gpu else "Sẽ tải torch bản CPU (khoảng 1 GB)")
        msg += f" và transformers vào:\n{USER_LIBS}\n\nChỉ cần làm một lần. Tiếp tục?"
        if QMessageBox.question(self, "Cài thành phần dịch", msg) != QMessageBox.Yes:
            return
        os.makedirs(USER_LIBS, exist_ok=True)
        self.queue = [(c[0], c[1:]) for c in install_commands(gpu)]
        self.stage.setText("Đang tải thành phần dịch…")
        self.progress.setRange(0, 0)
        self.next_install(0)

    def next_install(self, code):
        if code != 0:
            self.queue = []
            self.stage.setText("Cài đặt lỗi, xem log bên dưới (kiểm tra kết nối mạng rồi thử lại).")
            return
        if not self.queue:
            if USER_LIBS not in sys.path:
                sys.path.insert(0, USER_LIBS)
            self.stage.setText("Đã cài xong thành phần dịch.")
            return
        program, args = self.queue.pop(0)
        self.run(program, args, on_done=self.next_install)

    # ------------------------------------------------------------ translate
    def start(self):
        if self.proc:
            return
        source = self.source.text().strip()
        if not source:
            QMessageBox.information(self, APP_NAME, "Nhập mã arXiv (ví dụ 2506.09985) hoặc chọn file source.")
            return
        if not arxiv_id(source) and not os.path.exists(source):
            QMessageBox.warning(self, APP_NAME, "Không nhận ra mã arXiv, và cũng không có file nào ở đường dẫn này.")
            return
        if self.vinai.isChecked() and not model_libs_installed():
            QMessageBox.information(self, APP_NAME, "Cần cài thành phần dịch VinAI trước (nút Cài đặt).")
            return
        name = arxiv_id(source) or os.path.splitext(os.path.basename(os.path.normpath(source)))[0]
        self.out_dir = os.path.join(self.out.text().strip() or DEFAULT_OUT, name)
        os.makedirs(self.out_dir, exist_ok=True)
        args = ["-m", "paper_translator", source, "--out", self.out_dir]
        if self.ollama.isChecked():
            args += ["--backend", "ollama", "--model", self.ollama_model.text().strip() or "qwen2.5:7b"]
        if self.limit.value():
            args += ["--limit", str(self.limit.value())]
        if latex_available():
            args.append("--compile")
        self.pdf = None
        self.open_pdf.setEnabled(False)
        self.open_dir.setEnabled(False)
        self.progress.setRange(0, 0)
        self.stage.setText("Đang chuẩn bị…")
        path = os.pathsep.join(p for p in (USER_LIBS, os.environ.get("PYTHONPATH")) if p)
        self.run(sys.executable, args, env={"PYTHONPATH": path}, on_done=self.translated)

    def translated(self, code):
        self.open_dir.setEnabled(True)
        if code != 0:
            self.stage.setText("Có lỗi, xem log bên dưới. Phần đã dịch vẫn được lưu, chạy lại sẽ dịch tiếp.")
            return
        if self.pdf and os.path.exists(self.pdf):
            self.open_pdf.setEnabled(True)
            self.stage.setText("Xong! Đã tạo PDF tiếng Việt.")
            QDesktopServices.openUrl(QUrl.fromLocalFile(self.pdf))
        elif latex_available():
            self.stage.setText("Đã dịch xong nhưng build PDF lỗi. Xem file .log trong thư mục vi/, "
                               "hoặc upload vi-overleaf.zip lên Overleaf.")
        else:
            self.stage.setText("Đã dịch xong. Upload vi-overleaf.zip lên overleaf.com để xem PDF (hoặc cài LaTeX).")

    def closeEvent(self, event):
        if self.proc and QMessageBox.question(self, APP_NAME, "Đang chạy. Dừng và thoát?") != QMessageBox.Yes:
            event.ignore()
            return
        if self.proc:
            self.proc.kill()
            self.proc.waitForFinished(3000)
        event.accept()


def main():
    app = QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    app.setDesktopFileName("paper-translator-vi")
    w = Window()
    if len(sys.argv) > 1:
        w.source.setText(sys.argv[1])
    w.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
