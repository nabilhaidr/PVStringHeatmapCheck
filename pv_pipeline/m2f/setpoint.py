"""Plafon daya AC inverter dari set point busbar -- curtailment jaringan 20 kV.

Penyaluran PLTS dibatasi jaringan distribusi eksternal 20 kV: set point per
busbar (riwayat 10 menit di ``IKN Generation.xlsx``, sheet ``Setpoint``)
dibagi proporsional ke inverter,

    cap(t) = setpoint_busbar(t) x Pmax_inverter / Pmax_busbar,

sehingga WB01-02 (215 kW) dan WB03-10 (330 kW) tertahan di plafon berbeda.
Sampel di plafon SERING berstatus "Grid connected", bukan "power limited",
jadi status saja tidak cukup untuk mengenali curtailment.

Level NORMAL per busbar = modus harian (06-18) tertinggi dalam 30 hari
terakhir (:meth:`SetpointCaps.below_normal`). Di bawahnya = dispatch/henti
dari jaringan (M2f ``curtailment``, sejalan dengan Deem Dispatch operator:
98,6% hari Deem Dispatch > 0 tertangkap, 2024-12..2026-08); di level normal =
batas kapasitas penyaluran yang berlaku terus (M2f ``grid_export_limit``).

Dua penanda, dipakai bersama oleh M2f:

* :func:`capped_mask` -- dari riwayat: daya AC >= ``CAP_FRAC`` x cap(t), dan
  cap(t) memang membatasi (< ``HARDWARE_FRAC`` x Pmax; set point penuh tidak).
* :func:`plateau_mask` -- dari data, cadangan bila riwayat kosong/keliru:
  daya AC datar dalam 1% dari maksimum harian sementara POA menyebar >= 5%,
  dan plafonnya di bawah Pmax (plafon = Pmax adalah clipping perangkat keras).
"""
from __future__ import annotations

import warnings
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

CAP_FRAC = 0.98               # daya >= 98% plafon = tertahan di plafon
HARDWARE_FRAC = 0.97          # plafon >= 97% Pmax = tidak membatasi / clipping
PLATEAU_CEILING_FRAC = 0.99
PLATEAU_MIN_SAMPLES = 6       # 30 menit @5 menit
PLATEAU_MIN_POA_SPAN = 0.05
HOLD = pd.Timedelta("10min")  # interval riwayat set point
NORMAL_WINDOW = "30D"         # level normal = modus harian tertinggi jendela ini
DAYLIGHT = ("06:00", "17:59")


def _daily_normal(setpoint: pd.Series) -> pd.Series:
    """Level normal per hari: modus set point siang, maksimum bergulir 30 hari."""
    day = setpoint.dropna().between_time(*DAYLIGHT)
    if day.empty:
        return pd.Series(dtype=float)
    mode = day.groupby(day.index.normalize()).agg(lambda x: x.round(0).mode().max())
    return mode.rolling(NORMAL_WINDOW).max()


def capped_mask(p_ac, cap_kw, pmax_kw: float, *, frac: float = CAP_FRAC) -> np.ndarray:
    """Timestamp dengan daya AC tertahan di plafon set point yang membatasi."""
    p_ac = np.asarray(p_ac, dtype=float)
    cap = np.asarray(cap_kw, dtype=float)
    limiting = np.isfinite(cap) & (cap < HARDWARE_FRAC * pmax_kw)
    return limiting & np.isfinite(p_ac) & (p_ac >= frac * cap)


def plateau_mask(
    p_ac, poa, pmax_kw: float = np.inf, *,
    ceiling_frac: float = PLATEAU_CEILING_FRAC,
    min_samples: int = PLATEAU_MIN_SAMPLES,
    min_poa_span: float = PLATEAU_MIN_POA_SPAN,
) -> np.ndarray:
    """Timestamp di plafon datar satu inverter-hari, tanpa riwayat set point.

    Puncak kurva cerah (daya ikut POA) tidak lolos karena POA di sampel dekat
    maksimum hampir tidak menyebar; clipping perangkat keras tidak lolos
    karena plafonnya >= ``HARDWARE_FRAC`` x Pmax.
    """
    p_ac = np.asarray(p_ac, dtype=float)
    poa = np.asarray(poa, dtype=float)
    none = np.zeros(p_ac.shape, dtype=bool)
    ok = np.isfinite(p_ac) & np.isfinite(poa)
    if not ok.any():
        return none
    peak = float(p_ac[ok].max())
    if peak <= 0.0 or peak >= HARDWARE_FRAC * pmax_kw:
        return none
    near = ok & (p_ac >= ceiling_frac * peak)
    if near.sum() < min_samples:
        return none
    median_poa = float(np.median(poa[near]))
    if median_poa <= 0.0:
        return none
    return near if np.ptp(poa[near]) / median_poa >= min_poa_span else none


class SetpointCaps:
    """Riwayat set point busbar + kapasitas AC maksimum per WB."""

    def __init__(
        self,
        history: pd.DataFrame,
        busbars: List[dict],
        inverter_max_ac_kw: Dict[str, float],
    ):
        self.history = history.sort_index()
        self._pmax = {str(wb).upper(): float(v) for wb, v in inverter_max_ac_kw.items()}
        self._bus: Dict[str, tuple] = {}
        for bus in busbars:
            for wb in bus["wbs"]:
                self._bus[str(wb).upper()] = (str(bus["column"]), float(bus["max_ac_kw"]))
        self._normal = {
            column: _daily_normal(self.history[column])
            for column in {c for c, _ in self._bus.values()} if column in self.history.columns
        }

    @classmethod
    def from_geometry_yaml(cls, geometry_path: str) -> Optional["SetpointCaps"]:
        """Dari seksi ``setpoint`` di site_geometry.yaml; None bila tidak ada."""
        import yaml  # noqa: WPS433

        with open(geometry_path, "r", encoding="utf-8") as fp:
            section = (yaml.safe_load(fp) or {}).get("setpoint")
        if not section:
            return None
        busbars = list(section.get("busbars") or [])
        pmax = dict(section.get("inverter_max_ac_kw") or {})
        ts_col = str(section.get("timestamp_col", "Tanggal/Waktu"))
        try:
            raw = pd.read_excel(
                str(section.get("xlsx_path", "raw data input/IKN Generation.xlsx")),
                sheet_name=str(section.get("sheet", "Setpoint")),
            )
            raw[ts_col] = pd.to_datetime(raw[ts_col], errors="coerce")
            history = raw.dropna(subset=[ts_col]).set_index(ts_col)
            columns = [b["column"] for b in busbars if b["column"] in history.columns]
            history = history[columns].apply(pd.to_numeric, errors="coerce")
        except (OSError, ValueError, KeyError) as err:
            warnings.warn(
                f"[setpoint] riwayat set point tidak terbaca ({err}); "
                "hanya penanda plateau yang aktif.",
                stacklevel=2,
            )
            history = pd.DataFrame()
        return cls(history, busbars, pmax)

    def inverter_max_ac_kw(self, wb_id: str) -> Optional[float]:
        return self._pmax.get(str(wb_id).upper())

    def cap_kw(self, timestamps, wb_id: str) -> pd.Series:
        """Plafon per inverter (kW); nilai set point berlaku sampai berubah,
        paling lama ``HOLD``. NaN di luar riwayat atau untuk WB tak dikenal."""
        idx = pd.DatetimeIndex(timestamps)
        wb = str(wb_id).upper()
        bus, pmax = self._bus.get(wb), self._pmax.get(wb)
        if bus is None or pmax is None or bus[0] not in self.history.columns:
            return pd.Series(np.nan, index=idx, dtype=float)
        column, bus_max = bus
        return pd.Series(self._held(column, idx) * pmax / bus_max, index=idx)

    def below_normal(self, timestamps, wb_id: str) -> Tuple[np.ndarray, np.ndarray]:
        """(below, known): set point busbar di bawah level normalnya.

        ``known`` False di luar riwayat atau untuk WB tak dikenal -- di sana
        dispatch tidak bisa dibedakan dari batas kapasitas.
        """
        idx = pd.DatetimeIndex(timestamps)
        none = np.zeros(len(idx), dtype=bool)
        bus = self._bus.get(str(wb_id).upper())
        if bus is None or bus[0] not in self._normal:
            return none, none.copy()
        setpoint = self._held(bus[0], idx)
        normal = self._normal[bus[0]].reindex(idx.normalize()).to_numpy(dtype=float)
        known = np.isfinite(setpoint) & np.isfinite(normal)
        return known & (setpoint < normal - 1.0), known

    def _held(self, column: str, idx: pd.DatetimeIndex) -> np.ndarray:
        """Set point busbar per timestamp; berlaku sampai berubah, paling lama HOLD."""
        setpoint = self.history[column].dropna()
        setpoint = setpoint[~setpoint.index.duplicated(keep="first")]
        return setpoint.reindex(idx, method="ffill", tolerance=HOLD).to_numpy(dtype=float)
