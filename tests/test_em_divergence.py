"""Phân kỳ EM — hợp đồng tường minh thay cho phụ phẩm.

## Vì sao file này tồn tại

Lớp bảo vệ chống model phân kỳ chưa từng được viết ra. Nó là hệ quả phụ
của vòng random restart: `scan_bic` giữ restart có log-likelihood cao
nhất, một fit đã phân kỳ thì log-likelihood tệ nên luôn thua. Đo trên
backtest kiểm định: 0/13 cửa sổ chọn phải model phân kỳ, dù 10/13 cửa sổ
CÓ chứa restart phân kỳ trong 50 lần thử.

Đó là một quan sát về dữ liệu ĐÃ THẤY, không phải một bảo đảm. Và đường
làm nó biến mất đã nằm sẵn trong repo: `prompts/` phase-12b §0.3 đề xuất
giảm `n_init` xuống 3 cho kịch bản "nhanh". Với tỷ lệ phân kỳ 10.5% mỗi
fit, ít restart hơn = lớp bảo vệ mỏng hơn — và trước file này, không phép
kiểm nào phản đối.
"""

from __future__ import annotations

import logging

import pytest

from core.hmm_engine import (
    _EM_DIVERGENCE_DELTA,
    MIN_N_INIT,
    BICCandidateResult,
    EMDivergenceError,
    HMMRegimeEngine,
    _theo_doi_phan_ky,
)


@pytest.fixture
def hmm_engine_rong() -> HMMRegimeEngine:
    """Engine chưa train. `_assert_chosen_model_converged` chỉ đọc
    `self.bic_results` và `self.model`, nên không cần train thật — và
    không nên: một lần train thật ở đây làm test phụ thuộc vào việc EM có
    tình cờ phân kỳ hay không, tức là làm chính test thành ngẫu nhiên."""
    e = HMMRegimeEngine(
        n_candidates=[4, 5],
        n_init=MIN_N_INIT,
        covariance_type="diag",
        min_train_bars=10,
        stability_bars=2,
        flicker_window=5,
        flicker_threshold=3,
    )
    e.model = object()
    return e


def _ket_qua(n_components: int, bic: float, phan_ky: float) -> BICCandidateResult:
    return BICCandidateResult(
        n_components=n_components,
        bic=bic,
        log_likelihood=-100.0,
        converged=True,
        n_iter=12,
        n_params=40,
        max_em_divergence=phan_ky,
    )


# ----------------------------------------------------------------------
# Bộ thu — `monitor_.converged` KHÔNG thay được nó
# ----------------------------------------------------------------------


def _phat(delta: float) -> None:
    logging.getLogger("hmmlearn").warning(
        "Model is not converging.  Current: -1.0 is not greater than -2.0. Delta is %s", delta
    )


def test_thu_doc_duoc_delta_tu_canh_bao() -> None:
    with _theo_doi_phan_ky() as thu:
        _phat(-128.8)

    assert thu.phan_ky_max == pytest.approx(128.8)


def test_thu_doc_duoc_ky_hieu_khoa_hoc() -> None:
    """`Delta is -1.94e-08` — bản đầu của biểu thức chính quy cắt ở chữ
    `e` và nổ với `ValueError`. Một bộ thu chết vì định dạng số là một bộ
    thu không đo được gì."""
    with _theo_doi_phan_ky() as thu:
        _phat(-1.9439780317043187e-08)

    assert thu.phan_ky_max < 1e-6


def test_hai_khoi_theo_doi_KHONG_ro_ri_sang_nhau() -> None:
    """Đây là điều làm khẳng định ở `select_and_train` có nghĩa.

    Cảnh báo phải quy về đúng restart phát ra nó. Nếu bọc cả cụm `n_init`
    lần fit trong một khối, restart THẮNG luôn "có vẻ phân kỳ" chỉ vì một
    restart khác đã phân kỳ — và cổng sẽ đỏ ở mọi cửa sổ, tức là bị tắt.
    """
    with _theo_doi_phan_ky() as a:
        _phat(-99.0)
    with _theo_doi_phan_ky() as b:
        pass

    assert a.phan_ky_max == pytest.approx(99.0)
    assert b.phan_ky_max == 0.0


def test_go_handler_ra_sau_khi_xong() -> None:
    """Rò handler qua 650 lần fit là 650 handler chồng nhau."""
    lg = logging.getLogger("hmmlearn")
    truoc = len(lg.handlers)

    with _theo_doi_phan_ky():
        pass

    assert len(lg.handlers) == truoc


def test_van_de_lai_canh_bao_cho_nguoi_van_hanh() -> None:
    """Bộ thu QUAN SÁT, không nuốt. Một cổng làm mất log của thứ nó đang
    gác khiến việc chẩn đoán sau đó bất khả."""
    lg = logging.getLogger("hmmlearn")
    ghi: list[logging.LogRecord] = []

    class _Ghi(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            ghi.append(record)

    khac = _Ghi()
    lg.addHandler(khac)
    try:
        with _theo_doi_phan_ky():
            _phat(-50.0)
    finally:
        lg.removeHandler(khac)

    assert ghi, "cảnh báo bị nuốt mất"


# ----------------------------------------------------------------------
# Khẳng định ở `select_and_train` — RAISE, không log
# ----------------------------------------------------------------------


def test_model_duoc_chon_phan_ky_thi_RAISE(hmm_engine_rong: HMMRegimeEngine) -> None:
    """RAISE chứ không log: một model phân kỳ vẫn cho
    `predict_regime_filtered()` chạy được và trả về số. Nó không hỏng ở
    chỗ nhìn thấy được — nó hỏng ở chỗ những con số đó vô nghĩa."""
    e = hmm_engine_rong
    e.bic_results = [_ket_qua(4, 100.0, phan_ky=128.8), _ket_qua(5, 200.0, phan_ky=0.0)]

    with pytest.raises(EMDivergenceError) as ex:
        e._assert_chosen_model_converged()

    assert "n_components=4" in str(ex.value)
    assert "128.8" in str(ex.value)


def test_chi_MODEL_DUOC_CHON_moi_tinh(hmm_engine_rong: HMMRegimeEngine) -> None:
    """Ứng viên THUA phân kỳ là chuyện bình thường — 10/13 cửa sổ có. Đỏ
    vì nó nghĩa là cổng đỏ ở mọi cửa sổ, và một cổng luôn đỏ sẽ bị tắt."""
    e = hmm_engine_rong
    e.bic_results = [_ket_qua(4, 100.0, phan_ky=0.0), _ket_qua(5, 200.0, phan_ky=128.8)]

    e._assert_chosen_model_converged()


def test_nhieu_lam_tron_duoi_nguong_thi_bo_qua(hmm_engine_rong: HMMRegimeEngine) -> None:
    """Ngưỡng 1.0 loại nhiễu số học. Đo được: |delta| trong nhóm phân kỳ
    thật có trung vị 78.9, không giá trị nào rơi vào khoảng 1..10."""
    e = hmm_engine_rong
    e.bic_results = [_ket_qua(4, 100.0, phan_ky=1e-8)]

    e._assert_chosen_model_converged()


def test_nguong_phan_ky_dung_nhu_da_do() -> None:
    assert _EM_DIVERGENCE_DELTA == 1.0


# ----------------------------------------------------------------------
# Sàn `n_init` — tham số AN TOÀN, không phải tham số tốc độ
# ----------------------------------------------------------------------


def test_san_n_init_dung_nhu_suy_ra_tu_so_do() -> None:
    """6 = ceil(ln(0.001)/ln(0.3)), với 0.3 là tỷ lệ restart "bẩn" của ô
    (cửa sổ, n_components) TỆ NHẤT đo được (7/10 dùng được).

    Không phải số tròn (CLAUDE.md #18). Nếu ai đổi nó, test này buộc họ
    đổi cả phép suy — hoặc thừa nhận đang đặt số tròn.
    """
    import math

    assert MIN_N_INIT == math.ceil(math.log(0.001) / math.log(0.3))
    assert MIN_N_INIT == 6


@pytest.mark.parametrize("n", [1, 2, 3, 5])
def test_cong_cau_hinh_TU_CHOI_n_init_duoi_san(n: int) -> None:
    """3 là con số `prompts/` phase-12b §0.3 đề xuất cho kịch bản "nhanh".
    Đó là đường làm lớp bảo vệ biến mất, và nó phải bị chặn ở cổng."""
    from config.validate import check_n_init_floor

    van_de = check_n_init_floor({"hmm": {"n_init": n}})

    assert van_de
    assert van_de[0].check == "hmm.n_init"
    # Thông điệp phải nói RA lý do. Một cổng chặn mà không giải thích sẽ bị
    # người bị chặn tìm cách đi vòng, không tìm cách hiểu.
    assert "AN TOÀN" in van_de[0].detail
    assert str(MIN_N_INIT) in van_de[0].detail


@pytest.mark.parametrize("n", [6, 10, 20])
def test_cong_cau_hinh_CHAP_NHAN_tu_san_tro_len(n: int) -> None:
    from config.validate import check_n_init_floor

    assert check_n_init_floor({"hmm": {"n_init": n}}) == []


def test_thieu_n_init_la_van_de_khong_phai_mac_dinh_im_lang() -> None:
    from config.validate import check_n_init_floor

    assert check_n_init_floor({"hmm": {}})


def test_settings_that_dat_san() -> None:
    """Cổng chỉ có nghĩa nếu nó gác cấu hình ĐANG DÙNG."""
    import main as main_mod
    from config.validate import check_n_init_floor

    assert check_n_init_floor(main_mod.load_settings()) == []


# ----------------------------------------------------------------------
# ĐƯỜNG ĐẦY ĐỦ — hai lỗ hổng do đột biến phát hiện
# ----------------------------------------------------------------------


class _HmmGia:
    """GaussianHMM giả, đủ để `scan_bic` chạy.

    Cần một stub vì không có cách nào ÉP EM thật phân kỳ một cách tất
    định: phân kỳ phụ thuộc dữ liệu và seed, và một test dựa vào việc nó
    "thường xảy ra" là một test ngẫu nhiên đội lốt.
    """

    #: `random_state` sẽ phát cảnh báo phân kỳ, và mức delta.
    phan_ky_o: dict[int, float] = {}
    #: `random_state` -> log-likelihood, quyết định restart nào THẮNG.
    diem: dict[int, float] = {}

    def __init__(self, *, n_components: int, random_state: int, **_: object) -> None:
        self.n_components = n_components
        self.random_state = random_state
        self.monitor_ = type("M", (), {"converged": True, "iter": 5, "n_iter": 100})()

    def fit(self, X: object) -> None:
        d = self.phan_ky_o.get(self.random_state)
        if d is not None:
            _phat(-d)

    def score(self, X: object) -> float:
        return self.diem.get(self.random_state, -1000.0)

    def bic(self, X: object) -> float:
        return 100.0 * self.n_components

    def _get_n_fit_scalars_per_param(self) -> dict[str, int]:
        return {"s": 1}


@pytest.fixture
def hmm_gia(monkeypatch: pytest.MonkeyPatch) -> type[_HmmGia]:
    import core.hmm_engine as he

    _HmmGia.phan_ky_o = {}
    _HmmGia.diem = {}
    monkeypatch.setattr(he, "GaussianHMM", _HmmGia)
    return _HmmGia


def test_scan_bic_DIEN_muc_phan_ky_cua_restart_THANG(
    hmm_gia: type[_HmmGia], hmm_engine_rong: HMMRegimeEngine
) -> None:
    """Đột biến "phân kỳ của restart thắng luôn = 0" SỐNG SÓT ở vòng đầu:
    không test nào kiểm trường này được ĐIỀN từ fit thật. Cả cổng đứng
    trên nó, nên một hằng số 0 làm mọi khẳng định phía sau thành vô nghĩa
    mà vẫn xanh."""
    import pandas as pd

    hmm_gia.phan_ky_o = {0: 128.8}
    hmm_gia.diem = {0: -10.0}  # restart 0 phân kỳ NHƯNG thắng điểm
    e = hmm_engine_rong
    e.n_candidates = [4]

    _, kq = e.scan_bic(pd.DataFrame({"log_return_1": [0.1] * 20}))

    assert kq[0].max_em_divergence == pytest.approx(128.8)


def test_restart_THUA_phan_ky_KHONG_duoc_quy_cho_restart_thang(
    hmm_gia: type[_HmmGia], hmm_engine_rong: HMMRegimeEngine
) -> None:
    """Mặt kia của cùng phép nối dây. Bọc cả cụm thay vì từng restart làm
    cổng đỏ ở mọi cửa sổ — 10/13 cửa sổ có restart phân kỳ — và một cổng
    luôn đỏ sẽ bị tắt trong tuần đầu."""
    import pandas as pd

    hmm_gia.phan_ky_o = {1: 128.8}
    hmm_gia.diem = {0: -10.0, 1: -999.0}  # restart 1 phân kỳ VÀ thua
    e = hmm_engine_rong
    e.n_candidates = [4]

    _, kq = e.scan_bic(pd.DataFrame({"log_return_1": [0.1] * 20}))

    assert kq[0].max_em_divergence == 0.0


def test_select_and_train_THAT_SU_goi_khang_dinh(
    hmm_gia: type[_HmmGia], hmm_engine_rong: HMMRegimeEngine
) -> None:
    """Đột biến "bỏ khẳng định trong `select_and_train`" SỐNG SÓT ở vòng
    đầu: mọi test gọi THẲNG `_assert_chosen_model_converged()`, nên không
    ai kiểm nó ĐƯỢC GỌI. Một cổng không được nối vào đường thật là một
    cổng không tồn tại — đúng mẫu hỏng của cổng §E."""
    import pandas as pd

    hmm_gia.phan_ky_o = {0: 128.8}
    hmm_gia.diem = {0: -10.0}
    e = hmm_engine_rong
    e.n_candidates = [4]

    with pytest.raises(EMDivergenceError):
        e.select_and_train(pd.DataFrame({"log_return_1": [0.1] * 20}))
