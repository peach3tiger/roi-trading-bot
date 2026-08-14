"""Đánh dấu `scripts/` là package — cùng lý do với `tests/__init__.py` và
`config/__init__.py`: không có file này, mypy thấy mỗi script qua hai tên
module rồi dừng sớm. Xem `ops/verify_scope.py` (CLAUDE.md #19)."""
