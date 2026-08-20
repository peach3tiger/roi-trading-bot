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

    #: `(n_components, random_state)` -> mức |delta| sẽ phát ra.
    phan_ky_o: dict[tuple[int, int], float] = {}
    #: `(n_components, random_state)` -> log-likelihood, quyết định restart
    #: nào THẮNG trong cùng một `n_components`.
    diem: dict[tuple[int, int], float] = {}

    def __init__(self, *, n_components: int, random_state: int, **_: object) -> None:
        import numpy as np

        self.n_components = n_components
        self.random_state = random_state
        self.monitor_ = type("M", (), {"converged": True, "iter": 5, "n_iter": 100})()
        # `_build_regime_infos()` xếp hạng regime theo `means_[:, return_idx]`
        # và đọc phương sai — stub phải có, nếu không `select_and_train`
        # chết ở bước gán nhãn thay vì ở bước đang được kiểm.
        self.means_ = np.linspace(-1.0, 1.0, n_components).reshape(n_components, 1)
        self.covars_ = np.ones((n_components, 1, 1))
        self.startprob_ = np.full(n_components, 1.0 / n_components)
        self.transmat_ = np.full((n_components, n_components), 1.0 / n_components)

    def fit(self, X: object) -> None:
        d = self.phan_ky_o.get((self.n_components, self.random_state))
        if d is not None:
            _phat(-d)

    def score(self, X: object) -> float:
        return self.diem.get((self.n_components, self.random_state), -1000.0)

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


def test_restart_phan_ky_bi_LOAI_khong_bao_gio_thang(
    hmm_gia: type[_HmmGia], hmm_engine_rong: HMMRegimeEngine, caplog: pytest.LogCaptureFixture
) -> None:
    """Restart phân kỳ bị loại NGAY, kể cả khi log-likelihood của nó cao
    nhất. Trước bản này nó vẫn thắng rồi mới bị `raise` sau — đúng ý định,
    sai cơ chế."""
    import pandas as pd

    hmm_gia.phan_ky_o = {(4, 0): 128.8}
    hmm_gia.diem = {(4, 0): -10.0, (4, 1): -50.0}  # restart 0 phân kỳ NHƯNG điểm cao nhất
    e = hmm_engine_rong
    e.n_candidates = [4]

    with caplog.at_level(logging.WARNING, logger="core.hmm_engine"):
        _, kq = e.scan_bic(pd.DataFrame({"log_return_1": [0.1] * 20}))

    assert kq[0].max_em_divergence == 0.0, "restart phân kỳ vẫn thắng"
    assert kq[0].so_restart_loai == 1
    assert kq[0].loai_bo is False

    # Loại IM LẶNG là chế độ hỏng riêng: hệ thống vẫn chạy, kết quả vẫn ra,
    # và không ai biết một phần không gian tìm kiếm đã bị vứt đi.
    ban_ghi = " ".join(r.getMessage() for r in caplog.records)
    assert "restart bị LOẠI" in ban_ghi, "loại restart mà KHÔNG cảnh báo"
    assert "random_state=0" in ban_ghi, "cảnh báo không nói restart nào"


def test_restart_THUA_phan_ky_khong_lam_ung_vien_bi_loai(
    hmm_gia: type[_HmmGia], hmm_engine_rong: HMMRegimeEngine
) -> None:
    """10/13 cửa sổ CÓ restart phân kỳ. Nếu một restart hỏng làm cả ứng
    viên bị loại thì cổng đỏ ở mọi cửa sổ, và một cổng luôn đỏ sẽ bị tắt."""
    import pandas as pd

    hmm_gia.phan_ky_o = {(4, 1): 128.8}
    hmm_gia.diem = {(4, 0): -10.0, (4, 1): -999.0}
    e = hmm_engine_rong
    e.n_candidates = [4]

    _, kq = e.scan_bic(pd.DataFrame({"log_return_1": [0.1] * 20}))

    assert kq[0].loai_bo is False
    assert kq[0].max_em_divergence == 0.0


# ----------------------------------------------------------------------
# HAI CA DỰNG TAY — đường "loại rồi chọn cái kế" KHÔNG BAO GIỜ chạy tự nhiên
# ----------------------------------------------------------------------
#
# Với pruned-8, ứng viên BIC tốt nhất chưa bao giờ phân kỳ (đo: 0/13 cửa
# sổ). Nên nhánh quan trọng nhất của cơ chế mới sẽ xanh một cách RỖNG nếu
# chỉ dựa vào dữ liệu thật — đúng mẫu hỏng đã gặp sáu lần trong dự án này.
# Hai ca dưới đây tồn tại để nhánh đó thật sự được chạy.


def test_CA_A_ung_vien_BIC_tot_nhat_phan_ky_thi_chon_cai_NHI(
    hmm_gia: type[_HmmGia], hmm_engine_rong: HMMRegimeEngine, caplog: pytest.LogCaptureFixture
) -> None:
    """Ca (a). `_HmmGia.bic = 100 * n_components`, nên n=4 luôn thắng BIC.
    Cho TOÀN BỘ restart của n=4 phân kỳ -> phải chọn n=5, và phải CẢNH BÁO.

    Suy giảm có kiểm soát: hệ thống vẫn cho ra một model dùng được thay vì
    dừng hẳn, nhưng việc đó không được im lặng.
    """
    import pandas as pd

    hmm_gia.phan_ky_o = {(4, r): 200.0 for r in range(hmm_engine_rong.n_init)}
    hmm_gia.diem = {(5, 0): -10.0}
    e = hmm_engine_rong
    e.n_candidates = [4, 5]

    with caplog.at_level(logging.WARNING, logger="core.hmm_engine"):
        model, kq = e.scan_bic(pd.DataFrame({"log_return_1": [0.1] * 20}))

    theo_n = {r.n_components: r for r in kq}
    assert theo_n[4].loai_bo is True, "ứng viên phân kỳ hoàn toàn vẫn dự phép so BIC"
    assert theo_n[4].bic == float("inf")
    assert theo_n[5].loai_bo is False
    assert model.n_components == 5, "không lùi sang ứng viên kế"

    ban_ghi = " ".join(r.getMessage() for r in caplog.records)
    assert "LOẠI HOÀN TOÀN" in ban_ghi, "loại ứng viên mà KHÔNG cảnh báo"
    assert "n_components=4" in ban_ghi


def test_CA_B_toan_bo_ung_vien_phan_ky_thi_RAISE(
    hmm_gia: type[_HmmGia], hmm_engine_rong: HMMRegimeEngine
) -> None:
    """Ca (b). Suy giảm có kiểm soát có ĐÁY: khi không còn ứng viên nào
    dùng được thì dừng là đúng. Trả về một model bất kỳ ở đây nghĩa là
    giao dịch bằng tham số vô nghĩa."""
    import pandas as pd

    hmm_gia.phan_ky_o = {
        (n, r): 200.0 for n in (4, 5) for r in range(hmm_engine_rong.n_init)
    }
    e = hmm_engine_rong
    e.n_candidates = [4, 5]

    with pytest.raises(EMDivergenceError) as ex:
        e.scan_bic(pd.DataFrame({"log_return_1": [0.1] * 20}))

    assert "MỌI ứng viên" in str(ex.value)
    assert "n=4:200.0" in str(ex.value), "thông điệp không nêu ứng viên nào hỏng bao nhiêu"


def test_select_and_train_van_giu_khang_dinh_hau_dieu_kien(
    hmm_gia: type[_HmmGia], hmm_engine_rong: HMMRegimeEngine
) -> None:
    """`_assert_chosen_model_converged()` giờ là HẬU ĐIỀU KIỆN, không phải
    cổng chính — theo cấu tạo `scan_bic` không thể trả về model phân kỳ
    nữa. Giữ lại vì nếu logic loại bỏ trôi đi, đây là thứ bắt được.
    """
    import pandas as pd

    hmm_gia.phan_ky_o = {(4, r): 200.0 for r in range(hmm_engine_rong.n_init)}
    hmm_gia.diem = {(5, 0): -10.0}
    e = hmm_engine_rong
    e.n_candidates = [4, 5]

    e.select_and_train(pd.DataFrame({"log_return_1": [0.1] * 20}))

    assert min(e.bic_results, key=lambda r: r.bic).n_components == 5


def test_select_and_train_VAN_GOI_hau_dieu_kien(
    hmm_engine_rong: HMMRegimeEngine, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Đột biến "bỏ hậu điều kiện trong `select_and_train`" SỐNG SÓT ở vòng
    đầu, và lý do đáng ghi: theo cấu tạo mới `scan_bic` KHÔNG THỂ trả về
    model phân kỳ, nên hậu điều kiện không bao giờ kích hoạt trên đường
    thật — gỡ nó đi không đổi hành vi nào quan sát được.

    Nhưng nó vẫn đáng giữ: nếu logic loại bỏ trôi đi, đây là thứ bắt được.
    Muốn kiểm một phòng thủ chiều sâu thì phải ép nó vào tình huống mà lớp
    trước đã hỏng — ở đây là thay `scan_bic` bằng một bản trả về đúng thứ
    nó lẽ ra không được trả.
    """
    import pandas as pd

    class _ModelGia:
        n_components = 4

    def _scan_hong(features: object) -> tuple[object, list[BICCandidateResult]]:
        return _ModelGia(), [_ket_qua(4, 100.0, phan_ky=128.8)]

    monkeypatch.setattr(hmm_engine_rong, "scan_bic", _scan_hong)

    with pytest.raises(EMDivergenceError):
        hmm_engine_rong.select_and_train(pd.DataFrame({"log_return_1": [0.1] * 20}))
