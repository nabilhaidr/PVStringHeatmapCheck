from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from pathlib import Path
import re

import numpy as np
import pandas as pd

from pv_pipeline.physics import compute_active_power_integration_kwh
from pv_pipeline.string_yield_report import (
    DownloadedInputs,
    SourceManifest,
    download_manifest,
    inventory_drive_folder,
    select_source_manifest,
    validate_drive_folder_url,
)
from pv_pipeline.transformations import add_inverter_id


DETAIL_COLUMNS = [
    "date",
    "pv_string",
    "inverter_id",
    "pv_label",
    "string_yield_kwh",
    "valid_power_samples",
    "expected_samples",
    "coverage_pct",
    "missing_power_samples",
    "source_csv",
    "status",
]
INVERTER_RE = re.compile(r"^WB(\d{2})-INV(\d{2})$", re.I)
POWER_RE = re.compile(r"^PV0*(\d{1,2}) Power\(kW\)$", re.I)
VOLTAGE_RE = re.compile(r"^PV0*(\d{1,2}) Voltage\(V\)$", re.I)
CURRENT_RE = re.compile(r"^PV0*(\d{1,2}) Current\(A\)$", re.I)


@dataclass
class AllStringYieldData:
    summary: pd.DataFrame
    daily: pd.DataFrame
    metadata: dict[str, object]


def download_csv_inputs(
    url_csv: str,
    dates: pd.DatetimeIndex,
    destination: Path,
) -> tuple[SourceManifest, DownloadedInputs]:
    url_csv = validate_drive_folder_url(url_csv)
    csv_items = inventory_drive_folder(url_csv)
    manifest = select_source_manifest(
        csv_items,
        [],
        dates,
        url_csv=url_csv,
        include_poa=False,
    )
    return manifest, download_manifest(manifest, Path(destination))


def _find_column(frame: pd.DataFrame, expected: str) -> str | None:
    return next(
        (
            str(column)
            for column in frame.columns
            if str(column).casefold() == expected.casefold()
        ),
        None,
    )


def _natural_string_key(value: str) -> tuple[int, int, int]:
    match = re.fullmatch(
        r"WB(\d{2})-INV(\d{2})-PV(\d{1,2})",
        value,
    )
    if not match:
        raise ValueError(f"Invalid canonical PV string: {value}")
    return tuple(int(part) for part in match.groups())


def _power_source_columns(frame: pd.DataFrame):
    direct = {}
    voltage = {}
    current = {}
    for column in frame.columns:
        name = str(column)
        for pattern, target in (
            (POWER_RE, direct),
            (VOLTAGE_RE, voltage),
            (CURRENT_RE, current),
        ):
            match = pattern.fullmatch(name)
            if match and 1 <= int(match.group(1)) <= 28:
                target[int(match.group(1))] = name
    sources = {}
    for pv_number in sorted(set(direct) | (set(voltage) & set(current))):
        if pv_number in direct:
            sources[pv_number] = ("direct", direct[pv_number], None)
        else:
            sources[pv_number] = (
                "voltage_current",
                voltage[pv_number],
                current[pv_number],
            )
    return sources


def _extract_all_string_power(
    frame: pd.DataFrame,
    requested_day: date,
):
    timestamp_column = _find_column(frame, "Start Time")
    if timestamp_column is None:
        raise KeyError("CSV missing Start Time column.")

    inverter_column = _find_column(frame, "Inverter_ID")
    if inverter_column is None:
        manage_column = _find_column(frame, "ManageObject")
        if manage_column is None:
            raise KeyError("CSV missing Inverter_ID and ManageObject columns.")
        normalized = frame.rename(columns={manage_column: "ManageObject"})
        frame = add_inverter_id(normalized)
        inverter_column = "Inverter_ID"

    sources = _power_source_columns(frame)
    if not sources:
        raise KeyError("CSV has no usable PV power columns.")

    timestamps = pd.to_datetime(frame[timestamp_column], errors="coerce")
    wrong_date_mask = timestamps.notna() & (
        timestamps.dt.date != requested_day
    )
    aligned_mask = (
        timestamps.notna()
        & timestamps.dt.minute.mod(5).eq(0)
        & timestamps.dt.second.eq(0)
        & timestamps.dt.microsecond.eq(0)
    )
    inverter_ids = (
        frame[inverter_column]
        .astype("string")
        .str.strip()
        .str.upper()
    )
    valid_inverter = inverter_ids.str.fullmatch(INVERTER_RE, na=False)
    parts = []
    source_labels = set()
    for pv_number, (source, first_column, second_column) in sources.items():
        first = pd.to_numeric(frame[first_column], errors="coerce")
        if source == "direct":
            power = first
        else:
            second = pd.to_numeric(frame[second_column], errors="coerce")
            power = first * second / 1000
        pv_label = f"PV{pv_number}"
        part = pd.DataFrame({
            "timestamp": timestamps,
            "inverter_id": inverter_ids,
            "pv_label": pv_label,
            "power_kw": power,
        })
        part = part.loc[
            ~wrong_date_mask
            & aligned_mask
            & valid_inverter
            & part["timestamp"].notna()
            & part["power_kw"].notna()
        ].copy()
        part["pv_string"] = part["inverter_id"] + f"-{pv_label}"
        parts.append(part)
        source_labels.add(f"{pv_label}:{source}")

    tidy = pd.concat(parts, ignore_index=True)
    duplicate_rows = int(
        len(tidy)
        - len(tidy.drop_duplicates(["timestamp", "pv_string"]))
    )
    tidy = (
        tidy.groupby(
            ["timestamp", "inverter_id", "pv_string", "pv_label"],
            as_index=False,
        )["power_kw"]
        .mean()
        .sort_values(["timestamp", "pv_string"])
    )
    return tidy, {
        "wrong_date_rows": int(wrong_date_mask.sum()),
        "duplicate_rows": duplicate_rows,
        "negative_samples": int((tidy["power_kw"] < 0).sum()),
        "power_sources": sorted(source_labels),
    }


def build_all_string_daily_yield(
    csv_by_date,
    dates,
    *,
    source_manifest=None,
    download_errors=None,
) -> AllStringYieldData:
    if len(dates) == 0:
        raise ValueError("dates must contain at least one requested day.")

    requested_days = [pd.Timestamp(value).date() for value in dates]
    source_by_day = {}
    read_errors = {}
    wrong_date_rows = {}
    duplicate_rows = 0
    negative_samples = 0
    power_sources = set()
    power_parts = []
    readable_days = set()

    for requested_day in requested_days:
        raw_path = csv_by_date.get(requested_day)
        if raw_path is None:
            continue
        path = Path(raw_path)
        try:
            frame = pd.read_csv(path)
            tidy, diagnostics = _extract_all_string_power(
                frame,
                requested_day,
            )
        except Exception as exc:
            read_errors[path.name] = f"{type(exc).__name__}: {exc}"
            continue
        readable_days.add(requested_day)
        source_by_day[requested_day] = path.name
        wrong_date_rows[path.name] = diagnostics["wrong_date_rows"]
        duplicate_rows += diagnostics["duplicate_rows"]
        negative_samples += diagnostics["negative_samples"]
        power_sources.update(diagnostics["power_sources"])
        if not tidy.empty:
            power_parts.append(tidy)

    if not readable_days:
        raise RuntimeError("No requested CSV could be read and validated.")
    if not power_parts:
        raise RuntimeError(
            "No valid PV string power sample found in requested range."
        )

    power = pd.concat(power_parts, ignore_index=True)
    duplicate_rows += int(
        len(power)
        - len(power.drop_duplicates(["timestamp", "pv_string"]))
    )
    power = (
        power.groupby(
            ["timestamp", "inverter_id", "pv_string", "pv_label"],
            as_index=False,
        )["power_kw"]
        .mean()
        .sort_values(["timestamp", "pv_string"])
    )
    strings = sorted(power["pv_string"].unique(), key=_natural_string_key)

    daily_rows = []
    for requested_day in requested_days:
        raw_path = csv_by_date.get(requested_day)
        path_name = Path(raw_path).name if raw_path is not None else None
        day_power = power.loc[power["timestamp"].dt.date == requested_day]
        for pv_string in strings:
            values = day_power.loc[
                day_power["pv_string"] == pv_string,
                ["timestamp", "inverter_id", "pv_label", "power_kw"],
            ]
            valid = int(values["power_kw"].notna().sum())
            if raw_path is None:
                status = "MISSING_CSV"
            elif path_name in read_errors:
                status = "CSV_READ_ERROR"
            elif valid == 0:
                status = "NO_STRING_DATA"
            elif valid == 288:
                status = "COMPLETE"
            else:
                status = "PARTIAL"
            yield_kwh = (
                compute_active_power_integration_kwh(
                    values.set_index("timestamp")["power_kw"],
                    freq_hours=5 / 60,
                )
                if valid
                else np.nan
            )
            inverter_id, pv_label = pv_string.rsplit("-", 1)
            daily_rows.append({
                "date": requested_day,
                "pv_string": pv_string,
                "inverter_id": inverter_id,
                "pv_label": pv_label,
                "string_yield_kwh": yield_kwh,
                "valid_power_samples": valid,
                "expected_samples": 288,
                "coverage_pct": valid / 288 * 100,
                "missing_power_samples": 288 - valid,
                "source_csv": path_name,
                "status": status,
            })

    daily = pd.DataFrame(daily_rows, columns=DETAIL_COLUMNS)
    summary = daily.pivot(
        index="date",
        columns="pv_string",
        values="string_yield_kwh",
    ).reindex(index=requested_days, columns=strings)
    summary.columns.name = None
    summary = summary.reset_index()

    missing_dates = [
        value.isoformat()
        for value in requested_days
        if csv_by_date.get(value) is None
    ]
    inventory_missing_dates = [
        pd.Timestamp(value).date().isoformat()
        for value in (
            source_manifest.missing_csv_dates if source_manifest else []
        )
    ]
    warnings_list = []
    if any(wrong_date_rows.values()):
        warnings_list.append(
            "Rows outside their YYYYMMDD.csv date were dropped."
        )
    if negative_samples:
        warnings_list.append("Negative power samples were retained.")

    metadata = {
        "start_date": requested_days[0].isoformat(),
        "end_date": requested_days[-1].isoformat(),
        "generated_at_wita": pd.Timestamp.now(
            tz="Asia/Makassar"
        ).isoformat(),
        "source_url_csv": (
            source_manifest.url_csv if source_manifest else ""
        ),
        "csv_inventory_count": (
            source_manifest.csv_inventory_count if source_manifest else 0
        ),
        "requested_days": len(requested_days),
        "detected_string_count": len(strings),
        "loaded_csv_files": sorted(source_by_day.values()),
        "missing_csv_dates": missing_dates,
        "inventory_missing_csv_dates": inventory_missing_dates,
        "download_errors": dict(download_errors or {}),
        "csv_read_errors": read_errors,
        "wrong_date_rows": wrong_date_rows,
        "duplicate_rows": duplicate_rows,
        "negative_power_samples": negative_samples,
        "power_sources": sorted(power_sources),
        "yield_formula": "sum(power_kw_valid * 5/60)",
        "interval_minutes": 5,
        "warnings": warnings_list,
    }
    return AllStringYieldData(
        summary=summary,
        daily=daily,
        metadata=metadata,
    )
