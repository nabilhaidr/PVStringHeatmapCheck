"""M2a Low-Irradiance Performance Check (Fase 3 Part 2 Task #6).

Rancang ulang 2026-09-28: deviasi RELATIF TERHADAP TETANGGA se-WB.

Versi lama menilai tanda kemiringan PR-proxy (P/POA) terhadap POA per
inverter. Uji 4 hari nyata: tanda itu mengikuti cuaca, bukan modul --
kemiringan pita menengah negatif di ~100% inverter setiap hari, pita rendah
negatif di 91% inverter pada hari hujan dan 0% pada hari lain, dan klasifikasi
berbalik antar hari untuk modul yang sama (31 + 128 flag vs 0). Efek bersama
se-armada (pyranometer titik vs array, porsi difus/bifacial) jauh lebih besar
dari sinyal per inverter; tafsir "slope_low < 0 = Rs tinggi" juga terbalik
secara fisika (Rs merugikan di iradiansi TINGGI).

Algoritma (per inverter, per HARI):

1. Sampel siang: gerbang POA + elevasi + shutdown (seperti sebelumnya).
2. PR-proxy = P_inv / POA; efisiensi relatif e = PR-proxy / median PR-proxy
   inverter itu di pita menengah -- kapasitas inverter ternormalisasi.
3. dev(t) = e_i(t) / median e_j(t) inverter se-WB pada timestamp yang SAMA
   (minimal ``min_peers``). Satu WB berbagi satu pyranometer, jadi POA dan
   cuaca saling meniadakan.
4. low_ratio = median dev di pita rendah dengan elevasi matahari >=
   ``low_band_min_elevation_deg``: POA rendah karena AWAN di siang hari, bukan
   karena matahari rendah (pagi/sore tercampur bayangan geometri -- WB07-INV15
   turun hanya di hari cerah, normal di hari hujan).
5. Flag "low_irradiance_underperform" bila low_ratio < ``low_ratio_threshold``
   DAN robust z terhadap median/MAD low_ratio se-WB hari itu < -``robust_z_min``
   -- hari berawan konvektif memberi sebaran simetris lebar yang bukan cacat.

Kelas ``general_underperform`` tidak lagi dihasilkan: normalisasi per inverter
menghapus rugi seragam by design (wilayah detektor lain). Kuncinya tetap di
LowIrradianceSummary (nilai 0) demi kompatibilitas dasbor trends.

Default OFF (opt-in via config["m2a_low_irradiance"]["enabled"]=True).

Outputs
-------
    Findings : satu M2Finding per inverter-hari ter-flag;
               value = low_ratio, threshold = low_ratio_threshold.
    artifacts["LowIrradianceFit"]     : per (inverter, hari) -- low_ratio,
                                        median WB, robust z, jumlah sampel.
    artifacts["LowIrradianceSummary"] : jumlah per klasifikasi.
"""
from __future__ import annotations

import warnings
from typing import List, Optional, Tuple

import numpy as np
import pandas as pd

from pv_pipeline.availability import shutdown_keep_mask
from pv_pipeline.core import M2Finding, Severity, SubModule, load_empty_pv_map


# --- Defaults (mirror DEFAULT_M2_CONFIG["m2a_low_irradiance"]) -------------
DEFAULT_ENABLED: bool = False
DEFAULT_POA_LOW_RANGE: Tuple[float, float] = (50.0, 250.0)
DEFAULT_POA_MID_RANGE: Tuple[float, float] = (300.0, 800.0)
DEFAULT_MIN_LOW_SAMPLES: int = 12   # 1 jam @5 menit; 30 = 2,5 jam awan siang, jarang tercapai
DEFAULT_MIN_MID_SAMPLES: int = 30
DEFAULT_LOW_RATIO_THRESHOLD: float = 0.90   # low_ratio < ini -> kandidat flag
DEFAULT_ROBUST_Z_MIN: float = 3.0           # ...DAN pencilan bawah se-WB (z < -3)
DEFAULT_LOW_BAND_MIN_ELEVATION_DEG: float = 30.0  # pita rendah = awan, bukan matahari rendah
DEFAULT_MIN_PEERS: int = 5                  # inverter se-WB per timestamp
# Batas bawah MAD supaya WB yang seragam sempurna tidak membuat z tak terhingga.
_MAD_FLOOR: float = 0.005
DEFAULT_HOUR_RANGE: Tuple[float, float] = (6.0, 18.0)
DEFAULT_HOUR_CUTOFF_END: float = 18.0
DEFAULT_SOLAR_ELEV_MIN_DEG: float = 5.0
DEFAULT_RESPECT_INVERTER_SHUTDOWN: bool = True
DEFAULT_PV_MAX: int = 28

# Column templates (Huawei xlsx schema). Same as M2aShading.
PV_V_COL_TEMPLATE: str = "PV{pv} input voltage(V)"
PV_I_COL_TEMPLATE: str = "PV{pv} input current(A)"
PV_POWER_COL_TEMPLATE: str = "PV{pv} Power(kW)"

INVERTER_SHUTDOWN_COL_CANDIDATES: List[str] = [
    "Inverter shutdown time",
    "Shutdown time",
]


# --- Utilities ---------------------------------------------------------------
def _find_shutdown_col(df: pd.DataFrame) -> Optional[str]:
    for cand in INVERTER_SHUTDOWN_COL_CANDIDATES:
        if cand in df.columns:
            return cand
    return None


def _wb_from_inverter_id(inverter_id: str) -> str:
    if not inverter_id:
        return ""
    parts = str(inverter_id).split("-")
    return parts[0].upper() if parts else str(inverter_id).upper()


def _normalize_pv_columns(df: pd.DataFrame) -> pd.DataFrame:
    """Wave 11 hotfix #11 mirror: Title Case -> lowercase canonical."""
    rename_map = {}
    for col in df.columns:
        if "Input Voltage" in col:
            rename_map[col] = col.replace("Input Voltage", "input voltage")
        elif "Input Current" in col:
            rename_map[col] = col.replace("Input Current", "input current")
    if rename_map:
        return df.rename(columns=rename_map)
    return df


def build_inverter_power_series(
    group: pd.DataFrame,
    pv_indices: List[int],
) -> np.ndarray:
    """Per-timestamp inverter total power (kW), nansum across PVs.

    Prefer ``PV{n} Power(kW)`` column when available; else V*I/1000 fallback.
    Mirrors M2aShading.build_pv_power_matrix but only returns the sum.
    """
    n_ts = len(group)
    n_pv = len(pv_indices)
    p_mat = np.full((n_ts, n_pv), np.nan, dtype=float)

    for j, pv in enumerate(pv_indices):
        p_col = PV_POWER_COL_TEMPLATE.format(pv=pv)
        if p_col in group.columns:
            p_mat[:, j] = pd.to_numeric(group[p_col], errors="coerce").to_numpy()
            continue
        v_col = PV_V_COL_TEMPLATE.format(pv=pv)
        i_col = PV_I_COL_TEMPLATE.format(pv=pv)
        if v_col in group.columns and i_col in group.columns:
            v = pd.to_numeric(group[v_col], errors="coerce").to_numpy()
            i = pd.to_numeric(group[i_col], errors="coerce").to_numpy()
            p_mat[:, j] = (v * i) / 1000.0

    return np.nansum(p_mat, axis=1)


def _severity_from_ratio(low_ratio: float) -> Severity:
    """Tangga severity dari seberapa jauh efisiensi low-light di bawah tetangga."""
    if low_ratio < 0.80:
        return Severity.CRITICAL
    if low_ratio < 0.85:
        return Severity.HIGH
    return Severity.MEDIUM


class M2aLowIrradiance(SubModule):
    """Low-irradiance detector: efisiensi pita rendah relatif terhadap tetangga se-WB.

    Dependency injection (optional):
        prov = POAProvider.from_yaml(...)
        sm = M2aLowIrradiance(poa=prov)
    """

    name: str = "M2a_low_irradiance"

    def __init__(self, poa=None):
        super().__init__()
        self.poa = poa

    def _ensure_providers(self, config: dict) -> None:
        if self.poa is None:
            from pv_pipeline.poa.provider import POAProvider
            geom_path = (
                config.get("poa", {})
                .get("site_geometry_path", "config/site_geometry.yaml")
            )
            self.poa = POAProvider.from_yaml(geom_path)

    def _build_gate_mask(
        self,
        group_clean: pd.DataFrame,
        ts_clean: pd.DatetimeIndex,
        wb_id: str,
        cfg: dict,
        shutdown_col: Optional[str],
    ) -> Tuple[pd.Series, np.ndarray]:
        """Compute daylight gate mask + return POA values aligned to ts_clean."""
        hour_cutoff_end = float(cfg.get("hour_cutoff_end", DEFAULT_HOUR_CUTOFF_END))
        solar_elev_min = float(cfg.get("solar_elevation_min_deg", DEFAULT_SOLAR_ELEV_MIN_DEG))
        respect_shutdown = bool(cfg.get(
            "respect_inverter_shutdown", DEFAULT_RESPECT_INVERTER_SHUTDOWN
        ))

        try:
            # "auto" (default lama) mengisi celah pyranometer dengan clear-sky.
            # M2f memaksa sumber terukur lewat kunci ini: fit PR-proxy yang
            # dibangun di atas clear-sky tidak sebanding dengan POA terukur
            # yang M2f pakai untuk mengevaluasinya.
            poa_series = self.poa.get_poa(
                ts_clean, wb_id, source=str(cfg.get("poa_source", "auto")),
            )
        except Exception as exc:
            warnings.warn(
                f"[M2aLowIrradiance] POA query failed (wb={wb_id}): "
                f"{exc.__class__.__name__}: {exc}. Skipping inverter.",
                stacklevel=2,
            )
            return pd.Series(False, index=ts_clean), np.zeros(len(ts_clean))

        poa_aligned = poa_series.reindex(ts_clean).fillna(0.0)
        mask_daylight = poa_aligned > 0.0

        try:
            elev = self.poa.get_solar_elevation(ts_clean)
            elev_aligned = elev.reindex(ts_clean)
            if elev_aligned.notna().any():
                elev_mask = elev_aligned.fillna(-90.0).values > solar_elev_min
                hour_arr = ts_clean.hour + ts_clean.minute / 60.0
                hour_mask = hour_arr < hour_cutoff_end
                mask_time = pd.Series(elev_mask & hour_mask, index=ts_clean)
            else:
                hour_arr = ts_clean.hour + ts_clean.minute / 60.0
                mask_time = pd.Series(hour_arr < hour_cutoff_end, index=ts_clean)
        except Exception:
            hour_arr = ts_clean.hour + ts_clean.minute / 60.0
            mask_time = pd.Series(hour_arr < hour_cutoff_end, index=ts_clean)

        mask_shutdown = pd.Series(True, index=ts_clean)
        if respect_shutdown and shutdown_col is not None and shutdown_col in group_clean.columns:
            # Per baris: inverter sedang shutdown HARI ITU (lihat
            # availability.shutdown_keep_mask). min() lama selalu jatuh ke
            # waktu mati kemarin, jadi filter tidak pernah berlaku.
            mask_shutdown = pd.Series(
                shutdown_keep_mask(group_clean[shutdown_col], ts_clean),
                index=ts_clean,
            )

        full_mask = mask_daylight & mask_time & mask_shutdown
        return full_mask, poa_aligned.values

    def run(self, combined_df: pd.DataFrame, config: dict) -> List[M2Finding]:
        cfg = config.get("m2a_low_irradiance", {}) or {}
        enabled = bool(cfg.get("enabled", DEFAULT_ENABLED))
        if not enabled:
            return []

        poa_low_min, poa_low_max = (float(v) for v in cfg.get("poa_low_range", DEFAULT_POA_LOW_RANGE))
        poa_mid_min, poa_mid_max = (float(v) for v in cfg.get("poa_mid_range", DEFAULT_POA_MID_RANGE))
        min_low_samples = int(cfg.get("min_low_samples", DEFAULT_MIN_LOW_SAMPLES))
        min_mid_samples = int(cfg.get("min_mid_samples", DEFAULT_MIN_MID_SAMPLES))
        ratio_threshold = float(cfg.get("low_ratio_threshold", DEFAULT_LOW_RATIO_THRESHOLD))
        z_min = float(cfg.get("robust_z_min", DEFAULT_ROBUST_Z_MIN))
        low_elev_min = float(cfg.get(
            "low_band_min_elevation_deg", DEFAULT_LOW_BAND_MIN_ELEVATION_DEG,
        ))
        min_peers = int(cfg.get("min_peers", DEFAULT_MIN_PEERS))
        hour_lo, hour_hi = (float(v) for v in cfg.get("hour_range", DEFAULT_HOUR_RANGE))
        pv_max = int(cfg.get("pv_max", DEFAULT_PV_MAX))
        poa_source = str(cfg.get("poa_source", "auto"))

        if "Inverter_ID" not in combined_df.columns or "Start Time" not in combined_df.columns:
            warnings.warn(
                "[M2aLowIrradiance] missing 'Inverter_ID' or 'Start Time'; skipping.",
                stacklevel=2,
            )
            return []

        combined_df = _normalize_pv_columns(combined_df)
        shutdown_col = _find_shutdown_col(combined_df)
        empty_map = load_empty_pv_map(config)

        try:
            self._ensure_providers(config)
        except Exception as exc:
            warnings.warn(
                f"[M2aLowIrradiance] POA provider init failed: "
                f"{exc.__class__.__name__}: {exc}. Skipping.",
                stacklevel=2,
            )
            return []

        summary_counts = {"normal": 0, "low_irradiance_underperform": 0,
                          # Tidak lagi dihasilkan (2026-09-28); kunci dipertahankan
                          # supaya dasbor trends tidak rusak.
                          "general_underperform": 0, "skipped": 0}

        # Tahap 1 -- efisiensi relatif per (inverter, hari, timestamp).
        units: List[dict] = []
        samples: List[pd.DataFrame] = []
        day_key = pd.to_datetime(combined_df["Start Time"], errors="coerce").dt.normalize()
        for (inverter_id, day), group in combined_df.groupby(
            [combined_df["Inverter_ID"], day_key], sort=True,
        ):
            wb_id = _wb_from_inverter_id(inverter_id)
            inv_empties = set(int(n) for n in empty_map.get(str(inverter_id).upper(), []))
            pv_indices = [n for n in range(1, pv_max + 1) if n not in inv_empties]
            if not pv_indices:
                summary_counts["skipped"] += 1
                continue

            timestamps = pd.to_datetime(group["Start Time"], errors="coerce")
            valid_idx = timestamps.notna()
            ts_clean = pd.DatetimeIndex(timestamps[valid_idx].values)
            group_clean = group.loc[valid_idx].copy()
            group_clean.index = ts_clean

            hours = ts_clean.hour + ts_clean.minute / 60.0
            mask_hour = (hours >= hour_lo) & (hours < hour_hi)
            if mask_hour.sum() == 0:
                summary_counts["skipped"] += 1
                continue
            ts_h = ts_clean[mask_hour]
            # Boolean mask posisional, BUKAN .loc[ts_h]: duplicate "Start Time"
            # menggelembungkan jumlah baris (fix 2026-06-01, lihat shading.py).
            group_h = group_clean.loc[mask_hour]

            mask_gate, poa_values = self._build_gate_mask(
                group_h, ts_h, wb_id, cfg, shutdown_col,
            )
            mask_gate_arr = mask_gate.values
            if mask_gate_arr.sum() == 0:
                summary_counts["skipped"] += 1
                continue
            ts_qual = ts_h[mask_gate_arr]
            poa_qual = poa_values[mask_gate_arr]
            p_inv = build_inverter_power_series(group_h.iloc[mask_gate_arr], pv_indices)
            with np.errstate(divide="ignore", invalid="ignore"):
                pr_proxy = np.where(poa_qual > 0, p_inv / poa_qual, np.nan)

            mid = (
                (poa_qual >= poa_mid_min) & (poa_qual <= poa_mid_max)
                & np.isfinite(pr_proxy) & (pr_proxy > 0)
            )
            units.append({
                "inverter_id": inverter_id, "wb_id": wb_id, "day": day,
                "n_mid_samples": int(mid.sum()),
            })
            if mid.sum() < min_mid_samples:
                continue
            try:
                elev = np.asarray(
                    self.poa.get_solar_elevation(ts_qual).reindex(ts_qual), dtype=float,
                )
            except Exception:
                # Tanpa elevasi, pita rendah tak bisa dibedakan dari matahari
                # rendah -- inverter-hari ini berakhir insufficient_data.
                elev = np.full(len(ts_qual), np.nan)
            samples.append(pd.DataFrame({
                "inverter_id": inverter_id, "wb_id": wb_id, "day": day,
                "ts": ts_qual, "poa": poa_qual, "elev": elev,
                "e": pr_proxy / float(np.median(pr_proxy[mid])),
            }))

        # Tahap 2 -- deviasi terhadap median tetangga se-WB pada timestamp sama.
        low_stats = pd.DataFrame(columns=["low_ratio", "n_low"])
        if samples:
            frame = pd.concat(samples, ignore_index=True)
            peers = frame.groupby(["wb_id", "ts"])["e"]
            enough = peers.transform("count") >= min_peers
            frame["dev"] = (frame["e"] / peers.transform("median")).where(enough)
            low = frame[
                (frame["poa"] >= poa_low_min) & (frame["poa"] <= poa_low_max)
                & (frame["elev"] >= low_elev_min) & frame["dev"].notna()
            ]
            low_stats = low.groupby(["inverter_id", "day"])["dev"].agg(
                low_ratio="median", n_low="size",
            )

        # Tahap 3 -- robust z per (WB, hari), klasifikasi, findings.
        fit_rows: List[dict] = []
        for unit in units:
            key = (unit["inverter_id"], unit["day"])
            n_low = int(low_stats.loc[key, "n_low"]) if key in low_stats.index else 0
            evaluated = n_low >= min_low_samples and unit["n_mid_samples"] >= min_mid_samples
            fit_rows.append({
                **unit, "poa_source": poa_source, "n_low_samples": n_low,
                "low_ratio": float(low_stats.loc[key, "low_ratio"]) if evaluated else np.nan,
                "wb_median_low_ratio": np.nan, "robust_z": np.nan,
                "classification": "normal" if evaluated else "insufficient_data",
                "severity": "NORMAL",
            })
        fit = pd.DataFrame(fit_rows)

        findings: List[M2Finding] = []
        if not fit.empty:
            ev = fit["classification"] == "normal"
            by_wb_day = fit[ev].groupby(["wb_id", "day"])["low_ratio"]
            median = by_wb_day.transform("median")
            mad = (fit.loc[ev, "low_ratio"] - median).abs().groupby(
                [fit.loc[ev, "wb_id"], fit.loc[ev, "day"]],
            ).transform("median")
            fit.loc[ev, "wb_median_low_ratio"] = median
            fit.loc[ev, "robust_z"] = (fit.loc[ev, "low_ratio"] - median) / (
                1.4826 * mad.clip(lower=_MAD_FLOOR)
            )
            flagged = ev & (fit["low_ratio"] < ratio_threshold) & (fit["robust_z"] < -z_min)
            fit.loc[flagged, "classification"] = "low_irradiance_underperform"

            for idx, row in fit[flagged].iterrows():
                severity = _severity_from_ratio(row["low_ratio"])
                fit.loc[idx, "severity"] = severity.value
                ts_finding = pd.Timestamp(row["day"]) + pd.Timedelta(hours=12)
                findings.append(M2Finding(
                    timestamp=ts_finding.to_pydatetime(),
                    inverter_id=str(row["inverter_id"]),
                    pv_string=None,
                    sub_module=self.name,
                    severity=severity,
                    value=float(row["low_ratio"]),
                    threshold=ratio_threshold,
                    message=(
                        f"Low-light relatif tetangga: low_ratio={row['low_ratio']:.3f} "
                        f"< {ratio_threshold} (median {row['wb_id']} "
                        f"{row['wb_median_low_ratio']:.3f}, z={row['robust_z']:.1f}, "
                        f"n={int(row['n_low_samples'])})"
                    ),
                    fault_type="low_irradiance_underperform",
                    confidence=float(min(95.0, 50.0 + 5.0 * abs(row["robust_z"]))),
                    evidence={
                        "low_ratio": float(row["low_ratio"]),
                        "wb_median_low_ratio": float(row["wb_median_low_ratio"]),
                        "robust_z": float(row["robust_z"]),
                        "n_low_samples": int(row["n_low_samples"]),
                        "n_mid_samples": int(row["n_mid_samples"]),
                        "poa_low_range": [poa_low_min, poa_low_max],
                        "low_band_min_elevation_deg": low_elev_min,
                        "low_ratio_threshold": ratio_threshold,
                        "robust_z_min": z_min,
                        "poa_source": poa_source,
                    },
                ))

            counts = fit["classification"].value_counts()
            summary_counts["normal"] += int(counts.get("normal", 0))
            summary_counts["low_irradiance_underperform"] += int(
                counts.get("low_irradiance_underperform", 0)
            )
            summary_counts["skipped"] += int(counts.get("insufficient_data", 0))
            self.artifacts["LowIrradianceFit"] = fit
        self.artifacts["LowIrradianceSummary"] = pd.DataFrame([summary_counts])

        return findings
