/**
 * Dashboard public manifest sync for Google Sheets.
 *
 * Use this when Streamlit runs in public manifest mode and you do not want a
 * Google Cloud service account. The script reads your Google Drive folders and
 * fills the manifest sheet with the file names, Drive file IDs, and view links
 * expected by pv_pipeline.dashboard.
 *
 * Setup:
 * 1. Open the Google Sheet used as the dashboard manifest.
 * 2. Extensions > Apps Script.
 * 3. Paste this file.
 * 4. Fill FINDINGS_FOLDER_ID and BASELINE_FOLDER_ID.
 * 5. Run syncDashboardManifest once and approve the Drive permission prompt.
 */

const CONFIG = {
  MANIFEST_SHEET_NAME: "manifest",
  FINDINGS_FOLDER_ID: "replace-with-output-folder-id",
  BASELINE_FOLDER_ID: "replace-with-baseline-folder-id",
};

const DASHBOARD_COLUMNS = [
  "date",
  "baseline_csv_name",
  "baseline_csv_file_id",
  "baseline_csv_url",
  "findings_xlsx_name",
  "findings_xlsx_file_id",
  "findings_xlsx_url",
  "findings_jsonl_name",
  "findings_jsonl_file_id",
  "findings_jsonl_url",
];

function syncDashboardManifest() {
  const sheet = getOrCreateSheet_(CONFIG.MANIFEST_SHEET_NAME);
  const existing = readExistingRows_(sheet);
  const baselineCsv = collectArtifacts_(CONFIG.BASELINE_FOLDER_ID, parseBaselineCsvDate_);
  const findingsXlsx = collectArtifacts_(CONFIG.FINDINGS_FOLDER_ID, parseFindingsXlsxDate_);
  const findingsJsonl = collectArtifacts_(CONFIG.FINDINGS_FOLDER_ID, parseFindingsJsonlDate_);

  const dates = new Set(Object.keys(existing.byDate));
  Object.keys(baselineCsv).forEach((day) => dates.add(day));
  Object.keys(findingsXlsx).forEach((day) => dates.add(day));
  Object.keys(findingsJsonl).forEach((day) => dates.add(day));

  const headers = mergeHeaders_(existing.headers, DASHBOARD_COLUMNS);
  const rows = Array.from(dates).sort().map((day) => {
    const row = Object.assign({}, existing.byDate[day] || {});
    row.date = day;

    fillArtifact_(row, "baseline_csv", expectedBaselineCsvName_(day), baselineCsv[day]);
    fillArtifact_(row, "findings_xlsx", expectedFindingsXlsxName_(day), findingsXlsx[day]);
    fillArtifact_(row, "findings_jsonl", expectedFindingsJsonlName_(day), findingsJsonl[day]);

    return headers.map((header) => row[header] || "");
  });

  sheet.clearContents();
  sheet.getRange(1, 1, 1, headers.length).setValues([headers]);
  if (rows.length > 0) {
    sheet.getRange(2, 1, rows.length, headers.length).setValues(rows);
  }
  sheet.autoResizeColumns(1, headers.length);
}

function fillArtifact_(row, prefix, expectedName, file) {
  row[prefix + "_name"] = row[prefix + "_name"] || expectedName;
  if (!file) {
    row[prefix + "_file_id"] = row[prefix + "_file_id"] || "";
    row[prefix + "_url"] = row[prefix + "_url"] || "";
    return;
  }
  row[prefix + "_file_id"] = file.id;
  row[prefix + "_url"] = file.url;
}

function collectArtifacts_(folderId, dateParser) {
  const out = {};
  const root = DriveApp.getFolderById(folderId);
  scanFolder_(root, dateParser, out);
  return out;
}

function scanFolder_(folder, dateParser, out) {
  const files = folder.getFiles();
  while (files.hasNext()) {
    const file = files.next();
    const name = file.getName();
    const day = dateParser(name);
    if (day) {
      out[day] = {
        id: file.getId(),
        name: name,
        url: file.getUrl(),
      };
    }
  }

  const folders = folder.getFolders();
  while (folders.hasNext()) {
    scanFolder_(folders.next(), dateParser, out);
  }
}

function readExistingRows_(sheet) {
  const values = sheet.getDataRange().getValues();
  if (values.length === 0 || values[0].length === 0 || values[0][0] === "") {
    return { headers: [], byDate: {} };
  }

  const headers = values[0].map((value) => String(value).trim());
  const byDate = {};
  for (let i = 1; i < values.length; i += 1) {
    const row = {};
    headers.forEach((header, index) => {
      row[header] = values[i][index];
    });
    const day = normalizeDate_(row.date || row.source_date || row.day);
    if (day) {
      row.date = day;
      byDate[day] = row;
    }
  }
  return { headers: headers, byDate: byDate };
}

function mergeHeaders_(existingHeaders, requiredHeaders) {
  const headers = [];
  existingHeaders.concat(requiredHeaders).forEach((header) => {
    if (header && headers.indexOf(header) === -1) {
      headers.push(header);
    }
  });
  return headers;
}

function getOrCreateSheet_(name) {
  const spreadsheet = SpreadsheetApp.getActiveSpreadsheet();
  return spreadsheet.getSheetByName(name) || spreadsheet.insertSheet(name);
}

function parseBaselineCsvDate_(name) {
  const match = String(name).match(/^(\d{4})-(\d{2})-(\d{2})\.csv$/);
  return match ? match[1] + "-" + match[2] + "-" + match[3] : "";
}

function parseFindingsXlsxDate_(name) {
  const match = String(name).match(/^m2_findings_(\d{8})\.xlsx$/);
  return match ? compactToIsoDate_(match[1]) : "";
}

function parseFindingsJsonlDate_(name) {
  const match = String(name).match(/^m2_findings_(\d{8})\.jsonl$/);
  return match ? compactToIsoDate_(match[1]) : "";
}

function compactToIsoDate_(compact) {
  return compact.slice(0, 4) + "-" + compact.slice(4, 6) + "-" + compact.slice(6, 8);
}

function normalizeDate_(value) {
  if (!value) {
    return "";
  }
  if (Object.prototype.toString.call(value) === "[object Date]") {
    return Utilities.formatDate(value, Session.getScriptTimeZone(), "yyyy-MM-dd");
  }
  const text = String(value).trim();
  if (/^\d{4}-\d{2}-\d{2}$/.test(text)) {
    return text;
  }
  if (/^\d{8}$/.test(text)) {
    return compactToIsoDate_(text);
  }
  return "";
}

function expectedBaselineCsvName_(day) {
  return day + ".csv";
}

function expectedFindingsXlsxName_(day) {
  return "m2_findings_" + day.replace(/-/g, "") + ".xlsx";
}

function expectedFindingsJsonlName_(day) {
  return "m2_findings_" + day.replace(/-/g, "") + ".jsonl";
}
