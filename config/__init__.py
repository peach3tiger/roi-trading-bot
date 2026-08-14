"""Đánh dấu `config/` là package — cùng lý do với `tests/__init__.py`.

Không có file này, `config/validate.py` được mypy thấy qua HAI tên module
(`validate` vì `config/` nằm trên đường import, và `config.validate` vì
`tests/test_config_validate.py` import như vậy), rồi mypy dừng với
"Source file found twice under different module names" sau khi kiểm 0
file.

Đây là lần thứ HAI cùng một lỗi xảy ra trong dự án (lần đầu:
`tests/__init__.py`, 2026-08-14). Lần này nó KHÔNG ẩn được: cổng
`ops/verify_scope.py` (CLAUDE.md #19) đỏ ngay ở lần chạy test đầu tiên sau
khi thêm `config/validate.py`, với thông điệp "mypy không in 'checked N
source files' — nó đã DỪNG SỚM". Đó chính là việc cổng đó tồn tại để làm.
"""
