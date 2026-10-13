# paper-translator-vi: dịch paper arXiv sang tiếng Việt, chạy trên máy bạn

Đưa vào mã arXiv (ví dụ `2506.09985` của V-JEPA 2), nhận lại **source LaTeX tiếng Việt**, file zip để mở trên
Overleaf, và PDF (nếu máy có LaTeX). Việc dịch chạy bằng model trên máy bạn: **không gọi API trả phí, không tốn token**.

## Cách hoạt động

```
mã arXiv ──► tải source LaTeX (.tar.gz) từ arxiv.org/e-print
         ──► tách văn bản: công thức, bảng, code, \cite, \ref... được giữ nguyên
             (đổi thành placeholder [[1]], [[2]]...), chỉ phần chữ được đưa vào model
         ──► áp glossary: thuật ngữ được dịch thống nhất hoặc giữ tiếng Anh
         ──► model dịch từng câu (VinAI en2vi chạy local, hoặc LLM qua Ollama)
         ──► trả placeholder về chỗ cũ, thêm \usepackage[T5]{fontenc} cho tiếng Việt
         ──► translations/<id>/vi/ (LaTeX), vi-overleaf.zip, main.pdf
```

- Câu nào model làm mất placeholder sẽ được **dịch lại từng đoạn** nằm giữa các placeholder rồi ghép lại
  (kém mượt hơn một chút). Nếu vẫn lỗi thì giữ tiếng Anh, để file LaTeX luôn compile được.
  Các câu này được liệt kê trong `translations/<id>/problems.txt`.
- Bản dịch được lưu vào `cache.<backend>.json`, nên dừng giữa chừng rồi chạy lại sẽ dịch tiếp, không dịch lại từ đầu.
- Không dịch: công thức, bảng (`tabular`), code, tài liệu tham khảo, tiêu đề paper (nằm trong preamble).

## Cài đặt

```bash
git clone https://github.com/1Tyson/paper-translator-vi
cd paper-translator-vi
python -m venv .venv                 # môi trường riêng, không đụng vào Python của hệ thống
source .venv/bin/activate            # fish: source .venv/bin/activate.fish ; Windows: .venv\Scripts\activate

# Máy KHÔNG có GPU NVIDIA: cài bản torch cho CPU trước (~200 MB thay vì vài GB)
pip install torch --index-url https://download.pytorch.org/whl/cpu

pip install -r requirements.txt
```

Mỗi lần mở terminal mới, chạy lại `source .venv/bin/activate` trước khi dùng.

Lần chạy đầu sẽ tự tải model VinAI (khoảng 1,5 GB) về `~/.cache/huggingface`. Từ lần sau chạy offline được.

## Chạy

```bash
# Thử nhanh 30 câu đầu, xem chất lượng trước khi dịch cả bài
python -m paper_translator 2506.09985 --limit 30

# Dịch cả bài (chạy lại lệnh này sau khi --limit cũng được: phần đã dịch nằm sẵn trong cache)
python -m paper_translator 2506.09985

# Dịch xong thì build luôn PDF (cần LaTeX trên máy, xem bên dưới)
python -m paper_translator 2506.09985 --compile
```

Đầu vào có thể là mã arXiv, link arXiv, hoặc thư mục / `.tar.gz` / `.tex` trên máy.
Cuối mỗi lần chạy có thống kê, ví dụ: `881 sentences: 820 translated, 61 translated piece by piece ..., 0 kept in English, ...`.

### Chọn model

| `--backend` | Model | Cần gì | Ghi chú |
|---|---|---|---|
| `vinai` (mặc định) | [vinai/vinai-translate-en2vi-v2](https://huggingface.co/vinai/vinai-translate-en2vi-v2) | ~4 GB RAM, chạy được bằng CPU | Model chuyên dịch Anh→Việt. Có GPU thì nhanh hơn nhiều |
| `ollama` | Qwen, Gemma... qua [Ollama](https://ollama.com) | 8 GB RAM trở lên (model 7–8B) | Hiểu ngữ cảnh tốt hơn, dịch cả đoạn một lần |
| `dummy` | Không có model | Không cần gì | Chỉ để thử phần xử lý LaTeX |

```bash
# Dùng Ollama: cài Ollama, rồi
ollama pull qwen2.5:7b
python -m paper_translator 2506.09985 --backend ollama --model qwen2.5:7b
```

Trên CPU, model VinAI dịch chậm (ước tính vài chục phút cho một paper). Muốn nhanh hơn thì giảm `--beams 2`,
hoặc chạy trên GPU miễn phí của Kaggle/Colab:

```python
# Kaggle notebook: bật Internet + GPU T4
!git clone https://github.com/1Tyson/paper-translator-vi
%cd paper-translator-vi
!pip install -q -r requirements.txt
!python -m paper_translator 2506.09985
# tải translations/2506.09985/vi-overleaf.zip về, rồi upload lên Overleaf
```

## Ra PDF

- **Dễ nhất:** upload `vi-overleaf.zip` lên [Overleaf](https://www.overleaf.com) (New Project → Upload Project), không cần cài gì.
- **Trên máy:** Ubuntu: `sudo apt install texlive-latex-extra texlive-lang-other latexmk`, rồi chạy kèm `--compile`.
  Windows: cài MiKTeX (tự tải package còn thiếu khi compile).

Tiếng Việt dùng font encoding T5 (gói `vntex`, có trong `texlive-lang-other`). Paper dùng XeLaTeX (`fontspec`) thì không cần T5.

## Glossary

Sửa `paper_translator/glossary.yaml`:

```yaml
translate:          # lần đầu: "học tự giám sát (self-supervised learning)", các lần sau: "học tự giám sát"
  self-supervised learning: học tự giám sát
keep:               # giữ nguyên tiếng Anh
  - encoder
```

## Giới hạn

- Chỉ dịch được paper có source LaTeX trên arXiv. Đa số paper ML đều có; paper chỉ có PDF sẽ báo lỗi.
- Paper dùng nhiều macro tự định nghĩa có thể có vài chỗ dịch chưa đẹp. Câu lỗi thì vẫn giữ tiếng Anh nên PDF không vỡ.
- Model VinAI có license riêng (xem trang model trên Hugging Face), dùng cá nhân thì thoải mái.
  Muốn đăng bản dịch công khai thì kiểm tra license của paper (đa số arXiv là CC-BY, cần ghi nguồn).

## Test

```bash
python -m pytest tests -q
```
