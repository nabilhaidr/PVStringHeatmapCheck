"""Orchestrator M2f: rakit ledger per (string, hari), klaim berurutan
prioritas, lalu emit waterfall, Pareto, dan audit closure.
"""
from __future__ import annotations

from datetime import datetime
from typing import Dict, List, Optional

import pandas as pd

# _classify_status dipakai apa adanya (bukan disalin) supaya M2f dan
# M2eAvailability tidak pernah berbeda pendapat soal status inverter mana yang
# berarti DOWN. Ia empat arah: DOWN / ON / TRANSITIONAL / UNKNOWN, dengan
# prioritas down > on > transitional.
from pv_pipeline.availability import _classify_status
from pv_pipeline.cell_temp import CellTempProvider
from pv_pipeline.core import (
    M2Finding,
    Severity,
    SubModule,
    load_empty_pv_map,
)
from pv_pipeline.m2f.baseline import (
    DEFAULT_FREQ_HOURS,
    compute_actual_energy_kwh,
    compute_expected_energy_kwh,
)
from pv_pipeline.m2f.deficit import reduce_deficit_frames
from pv_pipeline.m2f.estimators import (
    claim_availability_outage,
    claim_dc_cable_fault,
    claim_soiling,
)
from pv_pipeline.m2f.ledger import (
    CLAIMABLE_CATEGORIES,
    LOCKED_CATEGORIES,
    LossLedger,
)
from pv_pipeline.m2f.pareto import build_pareto_table
from pv_pipeline.m2f.plots import build_waterfall_table
from pv_pipeline.panel_spec import PanelSpec
from pv_pipeline.poa.provider import POAProvider
from pv_pipeline.transformations import (
    PV_POWER_RE,
    add_inverter_id,
    add_pv_power_columns,
)


PER_STRING_COLUMNS: List[str] = ["string_id", "day", "category", "loss_kwh"]
CLOSURE_COLUMNS: List[str] = [
    "string_id", "day", "l_total_kwh", "claimed_kwh",
    "residual_kwh", "residual_pct", "poa_coverage_pct", "tcell_coverage_pct",
    "poa_source", "tcell_source", "skipped_reason",
]
BIFACIAL_COLUMNS: List[str] = ["wb_id", "g_bifacial", "n_strings", "n_days"]

_NAN: float = float("nan")


def _index_deficit_frames(
    frames: Optional[List[pd.DataFrame]],
    poa_source: str,
) -> Dict[str, List[pd.DataFrame]]:
    """Kelompokkan frame defisit per ``string_id``, disaring ke ``poa_source``.

    ``reduce_deficit_frames`` HANYA menyaring kolom ``poa_source`` lalu
    mengambil maksimum elemen-per-elemen lintas frame. Tiap frame dari
    ``build_deficit_frame`` milik satu (inverter, PV string), jadi menyerahkan
    seluruh daftar site apa adanya akan membuat defisit string LAIN pada
    timestamp yang sama terklaim ke string ini. Penyaringan per-string harus
    terjadi di sini, sebelum reduce.

    Daftar kosong/``None`` -- atau ``poa_source`` yang tidak cocok dengan frame
    manapun -- menghasilkan peta kosong, sehingga ``claim_dc_cable_fault``
    tidak pernah dipanggil dan kategorinya tetap ``None`` ("tidak pernah
    diukur"), bukan ``0.0`` ("diukur, aman").
    """
    out: Dict[str, List[pd.DataFrame]] = {}
    for frame in frames or []:
        subset = frame[frame["poa_source"] == poa_source]
        if subset.empty:
            continue
        for key, part in subset.groupby(["inverter_id", "pv_string"], sort=False):
            inverter_id, pv_string = key
            out.setdefault(f"{inverter_id}-{pv_string}", []).append(part)
    return out


def _down_mask(status: pd.Series, status_map: dict) -> pd.Series:
    """Timestamp yang statusnya diklasifikasikan DOWN -- HANYA itu.

    Status kosong/NaN (UNKNOWN) dan status peralihan (TRANSITIONAL, termasuk
    ``"no sunlight"`` yang muncul di fajar/senja saat E_expected masih > 0)
    TIDAK dihitung DOWN. ``~on_grid`` akan menyapu keduanya menjadi outage,
    dan karena availability berprioritas pertama dan mengklaim seluruh sisa,
    itu akan melaparkan dc_cable_fault dan soiling serta menggeser seluruh
    waterfall. Ketiadaan bukan kematian: lihat pv_pipeline/availability.py
    baris 75-79, aturan yang sama berlaku di sini.
    """
    return status.map(lambda value: _classify_status(value, status_map) == "DOWN")


def _skipped_closure_row(
    string_id: str,
    day: pd.Timestamp,
    *,
    reason: str,
    poa_source: str,
    tcell_source: str,
    poa_coverage_pct: float = _NAN,
    tcell_coverage_pct: float = _NAN,
) -> dict:
    """Baris closure untuk string-hari yang tidak dievaluasi.

    Energinya NaN, bukan 0.0: hari tanpa POA bukan hari tanpa rugi, dan
    mengisinya 0.0 akan menurunkan angka rugi site secara palsu.
    """
    return {
        "string_id": string_id,
        "day": day,
        "l_total_kwh": _NAN,
        "claimed_kwh": _NAN,
        "residual_kwh": _NAN,
        "residual_pct": _NAN,
        "poa_coverage_pct": poa_coverage_pct,
        "tcell_coverage_pct": tcell_coverage_pct,
        "poa_source": poa_source,
        "tcell_source": tcell_source,
        "skipped_reason": reason,
    }


def _build_bifacial_table(wb_rows: List[dict]) -> pd.DataFrame:
    """Gain bifacial yang DIPAKAI per WB, plus cakupan string-hari di baliknya.

    Bukan hasil kalibrasi ulang -- ini jejak audit atas nilai yang dipakai
    menghitung E_expected, supaya gain 1.0 default (yang meng-under-estimate
    baseline) terlihat, bukan tersembunyi.
    """
    if not wb_rows:
        return pd.DataFrame(columns=BIFACIAL_COLUMNS)
    frame = pd.DataFrame(wb_rows)
    table = (
        frame.groupby(["wb_id", "g_bifacial"], sort=True)
        .agg(n_strings=("string_id", "nunique"), n_days=("day", "nunique"))
        .reset_index()
    )
    return table[BIFACIAL_COLUMNS]


class M2fLossAttribution(SubModule):
    """Atribusi rugi energi DC ke kategori penyebab, per (string, hari)."""

    name = "M2f_loss_attribution"

    def run(self, combined_df: pd.DataFrame, config: dict) -> List[M2Finding]:
        cfg = config.get("m2f") or {}
        if not cfg.get("enabled", False):
            return []

        order: List[str] = list(cfg.get("attribution_order") or [])
        gains: Dict[str, float] = dict(cfg.get("bifacial_gain_per_wb") or {})
        # .get(key) TANPA default 0.0 -- bulan/detektor yang absen dari dict
        # ini harus tetap None setelah lookup, bukan diam-diam jadi 0.0
        # sebelum sempat dicek.
        p_loss_by_month: Dict[str, float] = dict(cfg.get("p_loss_by_month") or {})
        deficit_frames: Optional[List[pd.DataFrame]] = cfg.get("deficit_frames")
        poa_source: str = str(cfg.get("poa_source", "pyranometer_per_ws"))
        # Default "measured_per_ws", BUKAN "auto": config yang lupa mengisi
        # kunci ini tidak boleh diam-diam jatuh ke SAPM (Tcell MODEL).
        tcell_source: str = str(cfg.get("tcell_source", "measured_per_ws"))
        coverage_min = float(cfg.get("poa_coverage_min_pct", 80.0)) / 100.0
        warn_pct = float(cfg.get("residual_warn_pct", 30.0))
        status_map: dict = (
            (config.get("m2e") or {}).get("inverter_status_map") or {}
        )
        # Tanpa down_keywords, "mati" tidak bisa dibedakan dari "hidup" --
        # kategorinya dilewati sepenuhnya, bukan diklaim 0.0.
        can_classify_status = bool(status_map.get("down_keywords"))

        df = combined_df
        if "Inverter_ID" not in df.columns:
            df = add_inverter_id(df)
        if not any(PV_POWER_RE.search(str(c)) for c in df.columns):
            df, _ = add_pv_power_columns(df)

        providers, provider_error = self._load_providers(config)
        frames_by_string = _index_deficit_frames(deficit_frames, poa_source)
        # Slot PV kosong by design, sama sumbernya dengan ketiga detektor m2b.
        empty_pv_map = load_empty_pv_map(config)

        per_string_rows: List[dict] = []
        closure_rows: List[dict] = []
        wb_rows: List[dict] = []
        # Kategori yang tidak pernah muncul di peta ini tetap absen: `.get(cat)`
        # mengembalikan None, artinya "tidak pernah diukur oleh string-hari
        # manapun". Satu klaim saja sudah cukup membuat kategori itu terukur di
        # level site, dan string-hari lain yang melaporkan None untuk kategori
        # yang sama menyumbang 0, bukan menggugurkan seluruh site jadi None.
        site_claimed: Dict[str, float] = {}
        site_e_expected_kwh: float = 0.0
        site_l_total_kwh: float = 0.0

        for string_id, wb_id, day, group, power_col in self._iter_string_days(
            df, empty_pv_map,
        ):
            if providers is None:
                closure_rows.append(_skipped_closure_row(
                    string_id, day,
                    reason=provider_error, poa_source=poa_source,
                    tcell_source=tcell_source,
                ))
                continue

            idx = pd.DatetimeIndex(group.index)
            # source=poa_source EKSPLISIT. Default get_poa adalah "auto", yang
            # mengisi tiap NaN dari rantai fallback sampai ke pvlib clear-sky
            # -- cakupan lalu terbaca ~100% walau tidak ada satu pun pembacaan
            # pyranometer, sehingga gate cakupan di bawah tidak pernah menyala.
            # Beda dari detektor m2b yang membandingkan string dengan sibling
            # (bias POA saling meniadakan), M2f membandingkan dengan baseline
            # fisika ABSOLUT: POA clear-sky di hari mendung menggelembungkan
            # E_expected, menggelembungkan L_total, dan selisihnya jatuh ke
            # unexplained. Closure tetap lolos; angkanya saja yang salah.
            poa = providers["poa"].get_poa(idx, wb_id, source=poa_source)
            # source=tcell_source EKSPLISIT, simetris dengan poa_source di atas
            # -- default get_tcell adalah "auto", yang rantai fallbacknya
            # berakhir di SAPM (Tcell MODEL, bukan terukur).
            tcell = providers["tcell"].get_tcell(idx, wb_id, source=tcell_source)
            # Ambang cakupan, BUKAN isna().all(): cakupan sebagian lolos gate
            # "semua NaN", lalu compute_expected_energy_kwh mem-fillna(0.0)
            # tiap timestamp NaN -- E_expected menyusut diam-diam untuk jam
            # yang tak tercakup dan L_total ikut menyusut tanpa jejak.
            poa_coverage = float(poa.notna().mean())
            tcell_coverage = float(tcell.notna().mean())
            if poa_coverage < coverage_min or tcell_coverage < coverage_min:
                closure_rows.append(_skipped_closure_row(
                    string_id, day,
                    reason="poa_or_tcell_missing",
                    poa_source=poa_source,
                    tcell_source=tcell_source,
                    poa_coverage_pct=poa_coverage * 100.0,
                    tcell_coverage_pct=tcell_coverage * 100.0,
                ))
                continue

            g = float(gains.get(wb_id, 1.0))
            e_exp = compute_expected_energy_kwh(
                poa, tcell, providers["spec"], wb_id, bifacial_gain=g,
            )
            e_act = compute_actual_energy_kwh(group[power_col])
            # HANYA string-hari yang benar-benar diproses; yang di-skip di atas
            # tidak boleh menyumbang E_expected ke waterfall site.
            site_e_expected_kwh += float(e_exp.sum())

            # index=idx WAJIB: tanpanya LossLedger.claim() tidak punya acuan
            # untuk memvalidasi pd.Series yang di-passing di bawah.
            ledger = LossLedger(
                string_id, day, e_exp.to_numpy(), e_act.to_numpy(), index=idx,
            )

            for category in order:
                if category == "unexplained":
                    continue
                if category == "availability_outage":
                    if not can_classify_status or "Inverter status" not in group.columns:
                        continue
                    down = _down_mask(group["Inverter status"], status_map)
                    claim_availability_outage(ledger, down_mask=down.to_numpy())
                elif category == "dc_cable_fault":
                    string_frames = frames_by_string.get(string_id)
                    if not string_frames:
                        continue
                    # reduce_deficit_frames sudah me-reindex ke `index=idx`
                    # (index-aware, bukan posisional), jadi hasilnya sejajar
                    # dengan ledger. Timestamp yang tak bisa dievaluasi tetap
                    # NaN dan gagal keras di pemeriksaan NaN LossLedger.claim().
                    reduced = reduce_deficit_frames(
                        string_frames,
                        poa_source=poa_source,
                        index=idx,
                        freq_hours=DEFAULT_FREQ_HOURS,
                    )
                    claim_dc_cable_fault(ledger, deficit_kwh=reduced)
                elif category == "soiling":
                    month_key = day.strftime("%Y-%m")
                    if month_key not in p_loss_by_month:
                        continue
                    claim_soiling(
                        ledger,
                        p_loss=float(p_loss_by_month[month_key]),
                        e_expected_kwh_per_ts=e_exp,
                    )

            ledger.assert_closure()
            totals = ledger.totals()
            l_total = ledger.l_total()
            claimed = float(sum(
                totals[cat] for cat in CLAIMABLE_CATEGORIES
                if totals[cat] is not None
            ))
            residual = float(totals["unexplained"])
            site_l_total_kwh += l_total

            closure_rows.append({
                "string_id": string_id,
                "day": day,
                "l_total_kwh": l_total,
                "claimed_kwh": claimed,
                "residual_kwh": residual,
                # Tanpa rugi positif, "berapa persen rugi yang tak terjelaskan"
                # tidak terdefinisi -- NaN, bukan 0.0.
                "residual_pct": (residual / l_total * 100.0) if l_total > 0 else _NAN,
                "poa_coverage_pct": poa_coverage * 100.0,
                "tcell_coverage_pct": tcell_coverage * 100.0,
                # Dicatat supaya baseline yang berdiri di atas irradiance
                # MODEL tidak tersaji seolah-olah hasil pengukuran.
                "poa_source": poa_source,
                "tcell_source": tcell_source,
                "skipped_reason": None,
            })

            for cat, val in totals.items():
                if val is None:
                    continue
                per_string_rows.append({
                    "string_id": string_id,
                    "day": day,
                    "category": cat,
                    "loss_kwh": float(val),
                })
                site_claimed[cat] = site_claimed.get(cat, 0.0) + float(val)

            wb_rows.append({
                "wb_id": wb_id, "g_bifacial": g,
                "string_id": string_id, "day": day,
            })

        site_totals: Dict[str, Optional[float]] = {
            cat: site_claimed.get(cat) for cat in CLAIMABLE_CATEGORIES
        }
        site_totals.update({cat: None for cat in LOCKED_CATEGORIES})
        site_totals["unexplained"] = site_claimed.get("unexplained")

        self.artifacts["M2f_Waterfall"] = build_waterfall_table(
            site_totals, order, e_expected_kwh=site_e_expected_kwh,
        )
        # cum_pct di sini kumulatif atas porsi ACTIONABLE saja: ia tidak
        # mencapai 100% selama `unexplained` bukan nol, dan di v1 unexplained
        # menyerap shading, low-irradiance, microcrack, bifacial dan
        # ground-fault sekaligus, jadi residual besar adalah yang diharapkan.
        self.artifacts["M2f_Pareto"] = build_pareto_table(site_totals)
        self.artifacts["M2f_PerString"] = pd.DataFrame(
            per_string_rows, columns=PER_STRING_COLUMNS,
        )
        self.artifacts["M2f_Closure"] = pd.DataFrame(
            closure_rows, columns=CLOSURE_COLUMNS,
        )
        self.artifacts["M2f_BifacialCalib"] = _build_bifacial_table(wb_rows)

        residual_kwh = site_claimed.get("unexplained", 0.0)
        residual_pct = (
            residual_kwh / site_l_total_kwh * 100.0 if site_l_total_kwh > 0 else 0.0
        )
        if residual_pct <= warn_pct:
            return []
        days = [row["day"] for row in wb_rows]
        return [M2Finding(
            timestamp=(
                pd.Timestamp(max(days)).to_pydatetime() if days
                else datetime.utcnow()
            ),
            inverter_id=None,
            pv_string=None,
            sub_module=self.name,
            severity=Severity.INFO,
            value=round(residual_pct, 4),
            threshold=warn_pct,
            message=(
                f"unexplained {residual_pct:.1f}% dari total rugi "
                f"(> {warn_pct:.1f}%): atribusi lemah, angka waterfall belum "
                f"layak dipakai untuk keputusan biaya"
            ),
            extra={
                "unexplained_kwh": round(float(residual_kwh), 3),
                "l_total_kwh": round(site_l_total_kwh, 3),
                "e_expected_kwh": round(site_e_expected_kwh, 3),
                "poa_source": poa_source,
                "n_string_days_scored": len(wb_rows),
                "n_string_days_skipped": len(closure_rows) - len(wb_rows),
            },
            fault_type="weak_attribution",
        )]

    @staticmethod
    def _load_providers(config: dict):
        """Muat POA/Tcell/PanelSpec sekali. Kembalikan (providers, error).

        Kegagalan tidak melempar: seluruh string dicatat sebagai skipped
        supaya kelima artifact tetap ter-emit dengan skema yang benar.
        """
        try:
            geometry = config["poa"]["site_geometry_path"]
            return (
                {
                    "poa": POAProvider.from_yaml(geometry),
                    "tcell": CellTempProvider.from_geometry_yaml(geometry),
                    "spec": PanelSpec.from_yaml(config["panel"]["spec_path"]),
                },
                None,
            )
        except Exception as err:  # noqa: BLE001 - dicatat, bukan ditelan
            return None, f"provider_unavailable: {err}"

    @staticmethod
    def _iter_string_days(df: pd.DataFrame, empty_pv_map: Dict[str, List[int]]):
        """Yield (string_id, wb_id, day, group, power_col) per string per hari.

        ``group`` ber-index DatetimeIndex terurut, siap dipakai provider POA.
        ``power_col`` adalah nama kolom daya yang sebenarnya di ``df``;
        ``string_id`` memakai label PV yang sudah dinormalisasi (``PV5``)
        supaya cocok dengan ``pv_string`` yang ditulis detektor m2b, walau
        kolom aslinya beda konvensi huruf.

        Slot PV yang terdaftar di ``empty_pv_map`` dilewati. Huawei melaporkan
        0 V / 0 A -- BUKAN NaN -- untuk input MPPT yang tidak terpasang, jadi
        penjaga all-NaN di bawah tidak pernah menangkapnya. Tanpa filter ini
        tiap slot hantu mendapat E_expected satu string penuh melawan aktual
        ~0: rugi 100% palsu yang menggelembungkan E_expected site, waterfall,
        residual Pareto, dan n_strings di M2f_BifacialCalib. Ketiga detektor
        m2b sudah menyaring hal yang sama lewat core.load_empty_pv_map.
        """
        frame = df.copy()
        frame["_ts"] = pd.to_datetime(frame["Start Time"], errors="coerce")
        frame = frame.dropna(subset=["_ts", "Inverter_ID"])
        power_cols: List[tuple] = []
        for col in frame.columns:
            match = PV_POWER_RE.search(str(col))
            if match:
                power_cols.append((col, int(match.group(1))))
        for inverter_id, inv_rows in frame.groupby("Inverter_ID", sort=True):
            wb_id = str(inverter_id)[:4].upper()
            empty_slots = {
                int(n) for n in empty_pv_map.get(str(inverter_id).upper(), [])
            }
            for day, day_rows in inv_rows.groupby(inv_rows["_ts"].dt.normalize()):
                ordered = day_rows.sort_values("_ts").set_index("_ts")
                for col, pv_n in power_cols:
                    if pv_n in empty_slots:
                        continue
                    if ordered[col].notna().sum() == 0:
                        continue
                    yield (
                        f"{inverter_id}-PV{pv_n}",
                        wb_id,
                        pd.Timestamp(day),
                        ordered,
                        col,
                    )
