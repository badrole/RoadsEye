# -*- coding: utf-8 -*-
"""Google Sheets Cloud Synchronization Module for RoadsEye.

Architecture:
- Sheet 'Vehicle_Events': Raw event database (2 columns only: Timestamp, Vehicle Class).
- Sheet 'MAIN': Primary dashboard aggregating data per day arranged horizontally:
    Row 1: RoadsEye
    Row 3: Date (e.g. 17-10-2026)
    Row 4: Vehicle Class | Count | Percentage (dynamically referenced from Vehicle_Events)
    Row 5-8: Motorcycle, Car, Bus, Truck
    Row 9: Total
    Row 11-23: Donut Chart per day
    Row 25: Peak Hour
- Periodic auto-sync every 15 minutes during pipeline run.
- Instant auto-sync on stream disconnect / pipeline stop.
"""
from __future__ import annotations

import datetime as _dt
import logging
import os
import sqlite3
import threading
from pathlib import Path
from typing import Any

logger = logging.getLogger("roadseye.sheets")

_SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]
_sync_lock = threading.Lock()


def get_credentials_path() -> Path:
    env_path = os.environ.get("GOOGLE_SHEETS_CREDENTIALS_FILE", "credentials.json").strip()
    p = Path(env_path)
    if not p.is_absolute():
        p = Path(__file__).resolve().parent.parent / p
    return p


def get_spreadsheet_id() -> str:
    return os.environ.get("GOOGLE_SHEETS_SPREADSHEET_ID", "").strip()


def is_configured() -> bool:
    sheet_id = get_spreadsheet_id()
    cred_path = get_credentials_path()
    return bool(sheet_id and cred_path.exists())


def _col_letter(n: int) -> str:
    """1-based column index to letter: 1->A, 2->B, 26->Z, 27->AA."""
    result = ""
    while n > 0:
        n, remainder = divmod(n - 1, 26)
        result = chr(65 + remainder) + result
    return result


def _format_peak_hour_range(raw_hour: str | None) -> str:
    if not raw_hour:
        return "—"
    try:
        h = -1
        if " " in raw_hour or "T" in raw_hour:
            time_part = raw_hour.replace("T", " ").split(" ")[1]
            h = int(time_part.split(":")[0])
        elif raw_hour.isdigit():
            h = int(raw_hour)
        if 0 <= h <= 23:
            next_h = (h + 1) % 24
            return f"{h:02d}:00-{next_h:02d}:00"
    except Exception:
        pass
    return str(raw_hour)


def _get_service():
    from google.oauth2 import service_account
    from googleapiclient.discovery import build

    cred_path = get_credentials_path()
    creds = service_account.Credentials.from_service_account_file(
        str(cred_path), scopes=_SCOPES
    )
    return build("sheets", "v4", credentials=creds, cache_discovery=False)


def setup_sheets_if_needed(service, spreadsheet_id: str) -> dict[str, int]:
    """Ensure 'MAIN' and 'Vehicle_Events' exist, removing any old 'Sheet1' or 'Traffic_Summary'."""
    meta = service.spreadsheets().get(spreadsheetId=spreadsheet_id).execute()
    sheet_map = {s["properties"]["title"]: s["properties"]["sheetId"] for s in meta.get("sheets", [])}

    requests = []

    # Rename Traffic_Summary to MAIN if present
    if "Traffic_Summary" in sheet_map and "MAIN" not in sheet_map:
        requests.append({
            "updateSheetProperties": {
                "properties": {"sheetId": sheet_map["Traffic_Summary"], "title": "MAIN"},
                "fields": "title"
            }
        })
        sheet_map["MAIN"] = sheet_map.pop("Traffic_Summary")
    elif "MAIN" not in sheet_map:
        requests.append({
            "addSheet": {
                "properties": {
                    "title": "MAIN",
                    "gridProperties": {"rowCount": 100, "columnCount": 60},
                    "tabColor": {"red": 1.0, "green": 0.82, "blue": 0.0},
                }
            }
        })

    if "Vehicle_Events" not in sheet_map:
        requests.append({
            "addSheet": {
                "properties": {
                    "title": "Vehicle_Events",
                    "gridProperties": {"rowCount": 2000, "columnCount": 2, "frozenRowCount": 1},
                    "tabColor": {"red": 0.13, "green": 0.77, "blue": 0.36},
                }
            }
        })

    # Delete default Sheet1 if other sheets exist
    if "Sheet1" in sheet_map and len(sheet_map) > 1:
        requests.append({"deleteSheet": {"sheetId": sheet_map["Sheet1"]}})

    if requests:
        res = service.spreadsheets().batchUpdate(
            spreadsheetId=spreadsheet_id,
            body={"requests": requests}
        ).execute()
        # Refresh sheet map
        meta = service.spreadsheets().get(spreadsheetId=spreadsheet_id).execute()
        sheet_map = {s["properties"]["title"]: s["properties"]["sheetId"] for s in meta.get("sheets", [])}

    return sheet_map


def delete_day_rows(iso_date: str) -> dict[str, Any]:
    """Delete Vehicle_Events rows whose Timestamp is within the WIB calendar
    day `iso_date` ('YYYY-MM-DD').

    Rows are deleted bottom-up in one batchUpdate per contiguous block so
    indices stay valid. The MAIN day columns rebuild on the next sync because
    COUNTIFS formulas reference Vehicle_Events live.
    """
    if not is_configured():
        return {"deleted_rows": 0, "reason": "Google Sheets not configured"}

    spreadsheet_id = get_spreadsheet_id()
    service = _get_service()
    sheet_map = setup_sheets_if_needed(service, spreadsheet_id)
    ve_id = sheet_map.get("Vehicle_Events")
    if ve_id is None:
        return {"deleted_rows": 0, "reason": "Vehicle_Events sheet missing"}

    resp = service.spreadsheets().values().get(
        spreadsheetId=spreadsheet_id, range="Vehicle_Events!A2:A"
    ).execute()
    values = resp.get("values", [])
    prefix = f"{iso_date} "          # e.g. "2026-10-17 17:15:01 WIB"
    targets = [
        i + 2                        # 1-based row number incl. header row
        for i, row in enumerate(values)
        if row and str(row[0]).startswith(prefix)
    ]
    if not targets:
        return {"deleted_rows": 0, "reason": f"No rows on {iso_date}"}

    # Collapse consecutive 1-based row numbers into contiguous blocks
    blocks: list[tuple[int, int]] = []
    for r in targets:
        if blocks and r == blocks[-1][1] + 1:
            blocks[-1] = (blocks[-1][0], r)
        else:
            blocks.append((r, r))

    # Bottom-up: later rows first so earlier indices stay valid
    requests = []
    for start, end in reversed(blocks):
        requests.append({
            "deleteDimension": {
                "range": {
                    "sheetId": ve_id,
                    "dimension": "ROWS",
                    "startIndex": start - 1,   # API: 0-based, half-open
                    "endIndex": end,
                }
            }
        })
    service.spreadsheets().batchUpdate(
        spreadsheetId=spreadsheet_id, body={"requests": requests}
    ).execute()
    return {"deleted_rows": len(targets), "blocks": len(blocks)}


def sync_to_google_sheets() -> dict[str, Any]:
    """Synchronize SQLite events to Google Sheets:
    1. Upload raw events to 'Vehicle_Events' (only Timestamp & Vehicle Class).
    2. Format 'MAIN' with horizontal daily summary, dynamic formulas, and donut charts.
    """
    if not is_configured():
        return {"status": "skipped", "reason": "Google Sheets integration not configured in .env"}

    with _sync_lock:
        spreadsheet_id = get_spreadsheet_id()
        service = _get_service()
        sheet_map = setup_sheets_if_needed(service, spreadsheet_id)

        import api.events_db as events_db
        db_path = events_db.DB_PATH
        conn = sqlite3.connect(str(db_path))
        conn.row_factory = sqlite3.Row

        wib_tz = _dt.timezone(_dt.timedelta(hours=7))

        # ── 1. Sync Raw Events to Vehicle_Events ──────────────────────────────
        events_resp = service.spreadsheets().values().get(
            spreadsheetId=spreadsheet_id,
            range="Vehicle_Events!A1:C"
        ).execute()
        existing_rows = events_resp.get("values", [])
        num_existing = len(existing_rows)

        if num_existing == 0:
            # Init Header
            service.spreadsheets().values().update(
                spreadsheetId=spreadsheet_id,
                range="Vehicle_Events!A1",
                valueInputOption="USER_ENTERED",
                body={"values": [["Timestamp", "Vehicle Class", "Event ID"]]}
            ).execute()
            num_existing = 1

        existing_ids = set()
        for r in existing_rows[1:]:
            if len(r) >= 3 and r[2]:
                existing_ids.add(str(r[2]))
            elif len(r) >= 1 and r[0]:
                existing_ids.add(str(r[0]))

        # Fetch all events from SQLite
        all_events = conn.execute(
            "SELECT event_id, timestamp_epoch, vehicle_type FROM vehicle_events ORDER BY timestamp_epoch ASC"
        ).fetchall()

        append_values = []
        for ev in all_events:
            ev_id = str(ev["event_id"])
            if ev_id in existing_ids:
                continue
            dt = _dt.datetime.fromtimestamp(ev["timestamp_epoch"], tz=wib_tz)
            ts_str = dt.strftime("%Y-%m-%d %H:%M:%S WIB")
            if ts_str in existing_ids:
                continue
            cls_str = str(ev["vehicle_type"]).capitalize()
            append_values.append([ts_str, cls_str, ev_id])

        new_events_written = 0
        if append_values:
            service.spreadsheets().values().append(
                spreadsheetId=spreadsheet_id,
                range="Vehicle_Events!A1",
                valueInputOption="USER_ENTERED",
                insertDataOption="INSERT_ROWS",
                body={"values": append_values}
            ).execute()
            new_events_written = len(append_values)

        # ── 2. Build Daily Summary on 'MAIN' ──────────────────────────────────
        dates_rows = conn.execute("""
            SELECT strftime('%Y-%m-%d', datetime(timestamp_epoch, 'unixepoch', '+7 hours')) as d, count(*) as c
            FROM vehicle_events
            GROUP BY d
            ORDER BY d ASC
        """).fetchall()

        daily_stats = []
        for dr in dates_rows:
            d = dr["d"]
            dt = _dt.date.fromisoformat(d)
            display_date = dt.strftime("%d-%m-%Y")  # 17-10-2026

            # Peak hour for this day
            hourly = conn.execute("""
                SELECT strftime('%H', datetime(timestamp_epoch, 'unixepoch', '+7 hours')) as h, count(*) as c
                FROM vehicle_events
                WHERE strftime('%Y-%m-%d', datetime(timestamp_epoch, 'unixepoch', '+7 hours')) = ?
                GROUP BY h
                ORDER BY c DESC
                LIMIT 1
            """, [d]).fetchone()

            if hourly:
                h = int(hourly["h"])
                peak_range = f"{h:02d}:00-{(h+1)%24:02d}:00"
                peak_cnt = hourly["c"]
            else:
                peak_range = "—"
                peak_cnt = 0

            daily_stats.append({
                "iso_date": d,
                "display_date": display_date,
                "peak_range": peak_range,
                "peak_cnt": peak_cnt,
            })

        main_id = sheet_map.get("MAIN")

        if daily_stats and main_id is not None:
            total_cols = max(len(daily_stats) * 4, 12)
            grid = [["" for _ in range(total_cols)] for _ in range(26)]
            grid[0][0] = "RoadsEye"

            for i, ds in enumerate(daily_stats):
                c_idx = i * 4
                c2 = _col_letter(c_idx + 2)

                # Row 3: Date
                grid[2][c_idx] = ds["display_date"]

                # Row 4: Column Headers
                grid[3][c_idx] = "Vehicle Class"
                grid[3][c_idx + 1] = "Count"
                grid[3][c_idx + 2] = "Percentage"

                # Rows 5-8: Classes with formulas referencing Vehicle_Events
                classes = ["Motorcycle", "Car", "Bus", "Truck"]
                for offset, cls in enumerate(classes):
                    r_num = 5 + offset
                    grid[4 + offset][c_idx] = cls
                    grid[4 + offset][c_idx + 1] = f'=COUNTIFS(Vehicle_Events!$B:$B; "{cls}"; Vehicle_Events!$A:$A; "*{ds["iso_date"]}*")'
                    grid[4 + offset][c_idx + 2] = f'=IF({c2}$9>0; {c2}{r_num}/{c2}$9; 0)'

                # Row 9: Total
                grid[8][c_idx] = "Total"
                grid[8][c_idx + 1] = f'=SUM({c2}5:{c2}8)'
                grid[8][c_idx + 2] = f'=IF({c2}9>0; 1; 0)'

                # Row 25: Peak Hour below the chart
                grid[24][c_idx] = "Peak Hour"
                grid[24][c_idx + 1] = f"{ds['peak_range']} ({ds['peak_cnt']:,} vehicles)"

            # Clear MAIN sheet values first
            service.spreadsheets().values().clear(
                spreadsheetId=spreadsheet_id,
                range="MAIN!A1:ZZ100"
            ).execute()

            # Write updated grid
            service.spreadsheets().values().update(
                spreadsheetId=spreadsheet_id,
                range="MAIN!A1",
                valueInputOption="USER_ENTERED",
                body={"values": grid}
            ).execute()

            # ── 3. Apply Formatting & Create Donut Charts ───────────────────────
            _format_main_sheet(service, spreadsheet_id, main_id, daily_stats)

        # Apply formatting on Vehicle_Events
        ve_id = sheet_map.get("Vehicle_Events")
        if ve_id is not None:
            _format_vehicle_events_sheet(service, spreadsheet_id, ve_id)

        now_wib = _dt.datetime.now(wib_tz).strftime("%Y-%m-%d %H:%M:%S WIB")
        return {
            "status": "success",
            "spreadsheet_id": spreadsheet_id,
            "last_synced": now_wib,
            "days_tracked": len(daily_stats),
            "new_events_written": new_events_written,
        }


def _format_vehicle_events_sheet(service, spreadsheet_id: str, ve_id: int) -> None:
    """Style Vehicle_Events sheet header & column widths."""
    try:
        requests = [
            # Header Row 1
            {
                "repeatCell": {
                    "range": {"sheetId": ve_id, "startRowIndex": 0, "endRowIndex": 1, "startColumnIndex": 0, "endColumnIndex": 2},
                    "cell": {
                        "userEnteredFormat": {
                            "backgroundColor": {"red": 0.12, "green": 0.16, "blue": 0.24},
                            "textFormat": {"foregroundColor": {"red": 1.0, "green": 0.82, "blue": 0.0}, "fontSize": 10, "bold": True},
                            "horizontalAlignment": "CENTER",
                        }
                    },
                    "fields": "userEnteredFormat(backgroundColor,textFormat,horizontalAlignment)",
                }
            },
            # Timestamp width
            {
                "updateDimensionProperties": {
                    "range": {"sheetId": ve_id, "dimension": "COLUMNS", "startIndex": 0, "endIndex": 1},
                    "properties": {"pixelSize": 220},
                    "fields": "pixelSize",
                }
            },
            # Vehicle Class width
            {
                "updateDimensionProperties": {
                    "range": {"sheetId": ve_id, "dimension": "COLUMNS", "startIndex": 1, "endIndex": 2},
                    "properties": {"pixelSize": 150},
                    "fields": "pixelSize",
                }
            },
        ]
        service.spreadsheets().batchUpdate(spreadsheetId=spreadsheet_id, body={"requests": requests}).execute()
    except Exception as e:
        logger.warning(f"Failed to format Vehicle_Events: {e}")


def _format_main_sheet(service, spreadsheet_id: str, main_id: int, daily_stats: list[dict]) -> None:
    """Apply clean anti-slop formatting and add donut charts per day."""
    try:
        # Delete existing charts on MAIN to avoid duplicates
        meta = service.spreadsheets().get(spreadsheetId=spreadsheet_id).execute()
        main_sheet = next((s for s in meta.get("sheets", []) if s["properties"]["sheetId"] == main_id), None)
        if main_sheet and main_sheet.get("charts"):
            del_reqs = [{"deleteEmbeddedObject": {"objectId": c["chartId"]}} for c in main_sheet["charts"]]
            service.spreadsheets().batchUpdate(spreadsheetId=spreadsheet_id, body={"requests": del_reqs}).execute()

        requests = []

        # Title: RoadsEye
        requests.append({
            "repeatCell": {
                "range": {"sheetId": main_id, "startRowIndex": 0, "endRowIndex": 1, "startColumnIndex": 0, "endColumnIndex": 1},
                "cell": {
                    "userEnteredFormat": {
                        "textFormat": {"fontSize": 16, "bold": True, "foregroundColor": {"red": 0.1, "green": 0.1, "blue": 0.1}},
                    }
                },
                "fields": "userEnteredFormat(textFormat)",
            }
        })

        for i, ds in enumerate(daily_stats):
            c_idx = i * 4

            # Date Header (Row 3)
            requests.append({
                "repeatCell": {
                    "range": {"sheetId": main_id, "startRowIndex": 2, "endRowIndex": 3, "startColumnIndex": c_idx, "endColumnIndex": c_idx + 3},
                    "cell": {
                        "userEnteredFormat": {
                            "backgroundColor": {"red": 0.12, "green": 0.16, "blue": 0.24},
                            "textFormat": {"foregroundColor": {"red": 1.0, "green": 0.82, "blue": 0.0}, "fontSize": 12, "bold": True},
                            "horizontalAlignment": "CENTER",
                        }
                    },
                    "fields": "userEnteredFormat(backgroundColor,textFormat,horizontalAlignment)",
                }
            })

            # Subheaders: Vehicle Class, Count, Percentage (Row 4)
            requests.append({
                "repeatCell": {
                    "range": {"sheetId": main_id, "startRowIndex": 3, "endRowIndex": 4, "startColumnIndex": c_idx, "endColumnIndex": c_idx + 3},
                    "cell": {
                        "userEnteredFormat": {
                            "backgroundColor": {"red": 0.22, "green": 0.28, "blue": 0.38},
                            "textFormat": {"foregroundColor": {"red": 1.0, "green": 1.0, "blue": 1.0}, "fontSize": 10, "bold": True},
                            "horizontalAlignment": "CENTER",
                        }
                    },
                    "fields": "userEnteredFormat(backgroundColor,textFormat,horizontalAlignment)",
                }
            })

            # Total Row borders (Row 9)
            requests.append({
                "repeatCell": {
                    "range": {"sheetId": main_id, "startRowIndex": 8, "endRowIndex": 9, "startColumnIndex": c_idx, "endColumnIndex": c_idx + 3},
                    "cell": {
                        "userEnteredFormat": {
                            "textFormat": {"bold": True},
                            "borders": {
                                "top": {"style": "SOLID", "color": {"red": 0.7, "green": 0.7, "blue": 0.7}},
                                "bottom": {"style": "DOUBLE", "color": {"red": 0.7, "green": 0.7, "blue": 0.7}},
                            }
                        }
                    },
                    "fields": "userEnteredFormat(textFormat,borders)",
                }
            })

            # Format Percentage Column
            requests.append({
                "repeatCell": {
                    "range": {"sheetId": main_id, "startRowIndex": 4, "endRowIndex": 9, "startColumnIndex": c_idx + 2, "endColumnIndex": c_idx + 3},
                    "cell": {
                        "userEnteredFormat": {
                            "numberFormat": {"type": "PERCENT", "pattern": "0.0%"},
                            "horizontalAlignment": "RIGHT",
                        }
                    },
                    "fields": "userEnteredFormat(numberFormat,horizontalAlignment)",
                }
            })

            # Peak Hour Label (Row 25)
            requests.append({
                "repeatCell": {
                    "range": {"sheetId": main_id, "startRowIndex": 24, "endRowIndex": 25, "startColumnIndex": c_idx, "endColumnIndex": c_idx + 1},
                    "cell": {
                        "userEnteredFormat": {
                            "textFormat": {"bold": True, "fontSize": 10},
                            "backgroundColor": {"red": 0.95, "green": 0.95, "blue": 0.95},
                        }
                    },
                    "fields": "userEnteredFormat(textFormat,backgroundColor)",
                }
            })
            requests.append({
                "repeatCell": {
                    "range": {"sheetId": main_id, "startRowIndex": 24, "endRowIndex": 25, "startColumnIndex": c_idx + 1, "endColumnIndex": c_idx + 3},
                    "cell": {
                        "userEnteredFormat": {
                            "textFormat": {"bold": True, "foregroundColor": {"red": 0.85, "green": 0.45, "blue": 0.0}, "fontSize": 10},
                            "backgroundColor": {"red": 0.98, "green": 0.97, "blue": 0.94},
                        }
                    },
                    "fields": "userEnteredFormat(textFormat,backgroundColor)",
                }
            })

            # Column dimensions
            requests.append({
                "updateDimensionProperties": {
                    "range": {"sheetId": main_id, "dimension": "COLUMNS", "startIndex": c_idx, "endIndex": c_idx + 1},
                    "properties": {"pixelSize": 130},
                    "fields": "pixelSize",
                }
            })
            requests.append({
                "updateDimensionProperties": {
                    "range": {"sheetId": main_id, "dimension": "COLUMNS", "startIndex": c_idx + 1, "endIndex": c_idx + 2},
                    "properties": {"pixelSize": 90},
                    "fields": "pixelSize",
                }
            })
            requests.append({
                "updateDimensionProperties": {
                    "range": {"sheetId": main_id, "dimension": "COLUMNS", "startIndex": c_idx + 2, "endIndex": c_idx + 3},
                    "properties": {"pixelSize": 100},
                    "fields": "pixelSize",
                }
            })
            # Spacer column width
            requests.append({
                "updateDimensionProperties": {
                    "range": {"sheetId": main_id, "dimension": "COLUMNS", "startIndex": c_idx + 3, "endIndex": c_idx + 4},
                    "properties": {"pixelSize": 35},
                    "fields": "pixelSize",
                }
            })

            # Add Donut Chart per day
            requests.append({
                "addChart": {
                    "chart": {
                        "spec": {
                            "title": f"Komposisi {ds['display_date']}",
                            "pieChart": {
                                "legendPosition": "RIGHT_LEGEND",
                                "domain": {
                                    "sourceRange": {
                                        "sources": [{
                                            "sheetId": main_id,
                                            "startRowIndex": 4,
                                            "endRowIndex": 8,
                                            "startColumnIndex": c_idx,
                                            "endColumnIndex": c_idx + 1,
                                        }]
                                    }
                                },
                                "series": {
                                    "sourceRange": {
                                        "sources": [{
                                            "sheetId": main_id,
                                            "startRowIndex": 4,
                                            "endRowIndex": 8,
                                            "startColumnIndex": c_idx + 1,
                                            "endColumnIndex": c_idx + 2,
                                        }]
                                    }
                                },
                                "pieHole": 0.45,
                            }
                        },
                        "position": {
                            "overlayPosition": {
                                "anchorCell": {
                                    "sheetId": main_id,
                                    "rowIndex": 10,
                                    "columnIndex": c_idx,
                                },
                                "widthPixels": 320,
                                "heightPixels": 230,
                            }
                        }
                    }
                }
            })

        if requests:
            service.spreadsheets().batchUpdate(spreadsheetId=spreadsheet_id, body={"requests": requests}).execute()
    except Exception as e:
        logger.warning(f"Failed to format MAIN sheet: {e}")
