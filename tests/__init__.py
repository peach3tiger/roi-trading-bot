"""Đánh dấu `tests/` là package.

KHÔNG phải để import lẫn nhau — điều đó đã chạy được nhờ `pythonpath = ["."]`
trong `pyproject.toml`. Lý do là `mypy .`:

Ba file test dùng `from tests.test_main_loop import ...` (fixture dùng
chung, không dựng bản thứ hai). Không có file này, mypy thấy
`tests/test_main_loop.py` qua HAI tên module — `test_main_loop` (vì
`tests/` nằm trên đường import) và `tests.test_main_loop` — rồi dừng với
"Source file found twice under different module names", chặn luôn việc
kiểm phần còn lại của repo.

Lỗi này có TỪ TRƯỚC Phase 12b (xác minh ở commit 5c49fa5): `mypy .` chưa
bao giờ chạy hết trong dự án này, nên "mypy sạch" trước đây nghĩa là "mypy
dừng ngay ở file đầu tiên". Sửa ở đây để mục nghiệm thu `ruff check . &&
mypy .` đo được thứ nó định đo.
"""
