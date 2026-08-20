"""Ba nguồn khai báo bộ feature phải TRÙNG NHAU.

## Khoảng trống này đã tồn tại nhiều tuần mà không phép kiểm nào thấy

| nguồn | trước 2026-08-16 |
|---|---|
| `tests/regression_harness.py::_FEATURE_SUBSET` — bộ đã kiểm định | 8 |
| `forward/logger.py::FEATURE_SUBSET` — thí nghiệm 12 tháng ĐANG CHẠY | 8 |
| `config/settings.yaml` — `main.py --backtest` | **14** |

Cấu hình 14 feature chưa từng được kiểm định, và là cấu hình DUY NHẤT mà
BIC được chứng minh là chọn phải model phân kỳ (`n_components=7`,
`|delta|` 271.5). `covariance_type: full` làm số tham số tăng bậc hai theo
số feature, nên 8 → 14 không phải một khác biệt nhỏ.

## Đọc theo ĐÚNG đường mã mỗi nơi thật sự dùng

Đây là điểm quan trọng nhất của file này, và nó đến từ một sai lầm cụ thể.

2026-08-16, khi đi tìm khoảng trống trên, tôi đo cấu hình forward bằng
`main.build_feature_config(config_frozen)` — một hàm tiện tay — và kết
luận forward chạy 14 feature. **SAI.** `forward/logger.py` KHÔNG dùng
`build_feature_config`; nó dựng `FeatureConfig` trực tiếp và truyền
`FEATURE_SUBSET`, một hằng số hardcode trong chính file đóng băng. Kết
luận sai đó suýt làm thí nghiệm 12 tháng bị khởi động lại.

Nên file này đọc **hằng số**, không gọi hàm dựng config. Một hàm tiện tay
cho ra con số hợp lý, và một con số hợp lý không tự tố cáo mình đến từ
nhầm chỗ.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest
import yaml

_ROOT = Path(__file__).resolve().parent.parent


def _hang_so_tuple(duong_dan: Path, ten: str) -> tuple[str, ...]:
    """Đọc một hằng số tuple[str, ...] ở tầng module bằng AST.

    AST chứ không `import`: `forward/logger.py` là file ĐÓNG BĂNG và
    import nó kéo theo cả module trong tiến trình test. Đọc tĩnh cho đúng
    thứ nằm trên đĩa — cũng chính là thứ SHA256 đang ghim.
    """
    cay = ast.parse(duong_dan.read_text(encoding="utf-8"))
    for node in cay.body:
        if isinstance(node, ast.Assign):
            muc_tieu = node.targets[0] if node.targets else None
        elif isinstance(node, ast.AnnAssign):
            muc_tieu = node.target
        else:
            continue
        if not isinstance(muc_tieu, ast.Name) or muc_tieu.id != ten:
            continue
        gia_tri = node.value
        assert isinstance(gia_tri, (ast.Tuple, ast.List)), f"{ten} không phải tuple/list"
        ra = [
            e.value for e in gia_tri.elts
            if isinstance(e, ast.Constant) and isinstance(e.value, str)
        ]
        assert len(ra) == len(gia_tri.elts), f"{ten} có phần tử không phải hằng chuỗi"
        return tuple(ra)
    raise AssertionError(f"không tìm thấy hằng số {ten} trong {duong_dan}")


def bo_forward() -> tuple[str, ...]:
    """`FEATURE_SUBSET` trong `forward/logger.py` — ĐÓNG BĂNG, ghim SHA256."""
    return _hang_so_tuple(_ROOT / "forward" / "logger.py", "FEATURE_SUBSET")


def bo_kiem_dinh() -> tuple[str, ...]:
    """`_FEATURE_SUBSET` trong harness — bộ sinh ra snapshot Phase 7."""
    return _hang_so_tuple(_ROOT / "tests" / "regression_harness.py", "_FEATURE_SUBSET")


def bo_san_xuat() -> tuple[str, ...]:
    """`features.subset` trong `settings.yaml` — `main.py --backtest`."""
    s = yaml.safe_load((_ROOT / "config" / "settings.yaml").read_text(encoding="utf-8"))
    con = (s.get("features") or {}).get("subset")
    assert con, (
        "`features.subset` KHÔNG có trong settings.yaml — thiếu nó nghĩa là "
        "`main.py --backtest` chạy TOÀN BỘ feature, một cấu hình chưa kiểm định."
    )
    return tuple(con)


# ----------------------------------------------------------------------


def test_ba_nguon_khai_bao_TRUNG_NHAU() -> None:
    """Khẳng định trung tâm. Ba file, ba định dạng, một tập hợp.

    So bằng TẬP HỢP chứ không theo thứ tự: `FeatureConfig.feature_subset`
    lọc cột, và thứ tự cột do `compute_all_features` quyết định, không do
    thứ tự khai báo. Bắt lỗi thứ tự ở đây là bắt một thứ không tồn tại.
    """
    fw, kd, sx = set(bo_forward()), set(bo_kiem_dinh()), set(bo_san_xuat())

    assert fw == kd, (
        "forward/logger.py và regression_harness LỆCH NHAU — thí nghiệm forward "
        f"đang đo một cấu hình khác cấu hình đã kiểm định.\n"
        f"  chỉ có ở forward : {sorted(fw - kd)}\n  chỉ có ở harness : {sorted(kd - fw)}"
    )
    assert sx == kd, (
        "settings.yaml và regression_harness LỆCH NHAU — `main.py --backtest` "
        f"chạy một cấu hình khác cấu hình đã kiểm định.\n"
        f"  chỉ có ở settings: {sorted(sx - kd)}\n  chỉ có ở harness : {sorted(kd - sx)}"
    )


def test_dung_tam_feature_dung_ten_pruned8() -> None:
    """Ghim chính bộ tên, không chỉ "ba nguồn giống nhau".

    Ba nguồn cùng trôi sang một bộ khác vẫn thoả test trên. Bộ này gắn với
    ablation đã chạy và với snapshot Phase 7; đổi nó là đổi thứ mọi con số
    trong `VALIDATION_REPORT` nói về (CLAUDE.md #13).
    """
    assert set(bo_kiem_dinh()) == {
        "log_return_1",
        "log_return_5",
        "realized_vol_20",
        "vol_ratio_5_20",
        "adx_14",
        "sma50_slope",
        "trade_count_zscore_50",
        "trade_count_sma10_slope",
    }


def test_moi_ten_deu_la_feature_tier1_hop_le() -> None:
    """Một tên gõ sai lọc ra một cột không tồn tại — im lặng, và HMM train
    trên ít feature hơn nó nghĩ."""
    import main as main_mod

    hop_le = set(main_mod._VALID_TIER1_FEATURES)  # noqa: SLF001

    for ten, bo in (("forward", bo_forward()), ("harness", bo_kiem_dinh()), ("settings", bo_san_xuat())):
        sai = set(bo) - hop_le
        assert not sai, f"{ten} khai báo feature không hợp lệ: {sorted(sai)}"


def test_settings_that_su_cho_ra_dung_tam_cot() -> None:
    """Khai báo đúng CHƯA đủ — phải kiểm nó ĐI TỚI `compute_all_features`.

    Trước 2026-08-16 `build_feature_config` bỏ qua config hoàn toàn: khai
    báo có thể đúng mà đường mã vẫn dùng 14 cột. Một cổng không được nối
    vào đường thật là một cổng không tồn tại.
    """
    import pandas as pd

    import main as main_mod
    from data.feature_engineering import compute_all_features

    fx = pd.read_parquet(_ROOT / "tests" / "fixtures" / "btcusdt_1d_2018_2026.parquet")
    cot = compute_all_features(fx, main_mod.build_feature_config(main_mod.load_settings())).columns

    assert set(cot) == set(bo_san_xuat())


def test_tham_so_tuong_minh_van_thang_config() -> None:
    """`--feature-subset` là ý định trước mắt; config là mặc định dự án.
    Đảo thứ tự ưu tiên sẽ làm ablation không chạy được nữa."""
    import pandas as pd

    import main as main_mod
    from data.feature_engineering import compute_all_features

    fx = pd.read_parquet(_ROOT / "tests" / "fixtures" / "btcusdt_1d_2018_2026.parquet")
    fc = main_mod.build_feature_config(
        main_mod.load_settings(), feature_subset=("log_return_1", "adx_14")
    )

    assert set(compute_all_features(fx, fc).columns) == {"log_return_1", "adx_14"}


@pytest.mark.parametrize("ten_bo", ["forward", "harness", "settings"])
def test_khong_bo_nao_rong_hay_trung_lap(ten_bo: str) -> None:
    bo = {"forward": bo_forward, "harness": bo_kiem_dinh, "settings": bo_san_xuat}[ten_bo]()

    assert bo, f"{ten_bo} khai báo bộ RỖNG"
    assert len(bo) == len(set(bo)), f"{ten_bo} có tên lặp: {sorted(bo)}"
