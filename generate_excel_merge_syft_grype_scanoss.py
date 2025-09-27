#!/usr/bin/env python3
"""
generate_excel_merge_syft_grype_scanoss.py
Merge outputs from Syft, Grype, and SCANOSS into Excel / JSON reports.
"""

import os
import sys
import json
import time
import requests
import pandas as pd
from collections import defaultdict

# ──────────────────────────────────────────────────────────────────────────────
# Helper: ensure values are hash-friendly for pandas .drop_duplicates()
# ──────────────────────────────────────────────────────────────────────────────
import json as _json

def _make_hashable(value):
    """Convert list / dict to deterministic JSON string; leave scalars untouched."""
    return _json.dumps(value, sort_keys=True) if isinstance(value, (dict, list)) else value


# ──────────────────────────────────────────────────────────────────────────────
# Locate file names & output names
# ──────────────────────────────────────────────────────────────────────────────
image_name = os.getenv("IMAGE_NAME", "scan").replace(":", "_").replace("/", "_").replace("@", "_")

syft_file   = "syft-sbom.spdx.json"
grype_file  = "grype-scan.json"
scanoss_file= "scanoss-results.json"

excel_out   = f"{image_name}_compliance_merged_report.xlsx"
json_out    = f"{image_name}_compliance_merged_report.json"
grype_excel = f"{image_name}_grype_components_report.xlsx"
scanoss_excel=f"{image_name}_scanoss_components_report.xlsx"
syft_excel  = f"{image_name}_syft_components_report.xlsx"

# ──────────────────────────────────────────────────────────────────────────────
# Parsers
# ──────────────────────────────────────────────────────────────────────────────
def parse_syft(path):
    if not os.path.exists(path):
        return []
    with open(path, "r") as f:
        data = json.load(f)

    comps = []
    for pkg in data.get("packages", []):
        comps.append({
            "component"        : pkg.get("name"),
            "version"          : pkg.get("versionInfo") or pkg.get("version"),
            "source"           : "syft",
            "license"          : pkg.get("licenseDeclared"),
            "license_source"   : "syft" if pkg.get("licenseDeclared") else "",
            "enriched_license" : None,
            "license_url"      : "unknown",
        })
    return comps

def parse_grype(path):
    if not os.path.exists(path):
        return defaultdict(str), []
    with open(path, "r") as f:
        data = json.load(f)

    lic_map = defaultdict(str)
    rows = []
    for match in data.get("matches", []):
        pkg = match.get("artifact", {})
        name, ver = pkg.get("name"), pkg.get("version")
        lic  = match.get("licenses", [])
        lic  = lic[0].get("spdx") if lic else pkg.get("license")

        if name and ver:
            lic_map[f"{name}@{ver}"] = lic
            rows.append({
                "component"      : name,
                "version"        : ver,
                "source"         : "grype",
                "license"        : lic,
                "license_source" : "grype",
                "enriched_license": None,
                "license_url"    : "unknown",
            })
    return lic_map, rows

def parse_scanoss(path):
    if not os.path.exists(path):
        return []
    try:
        with open(path, "r") as f:
            data = json.load(f)
    except Exception as exc:
        print(f"[ERROR] Failed to parse SCANOSS JSON: {exc}")
        return []

    rows = []
    for lst in data.values():
        for m in lst:
            comp  = m.get("component")
            ver   = m.get("version") or m.get("latest")
            lics  = m.get("licenses", [])
            names = [l.get("name") for l in lics if "name" in l]
            lic   = ", ".join(names) if names else None
            url   = lics[0].get("url") if lics and "url" in lics[0] else "unknown"

            if comp:
                rows.append({
                    "component"        : comp,
                    "version"          : ver,
                    "source"           : "scanoss",
                    "license"          : lic,
                    "license_source"   : "scanoss",
                    "enriched_license" : None,
                    "license_url"      : url,
                })
    return rows

# ──────────────────────────────────────────────────────────────────────────────
# External enrichment (very light, best-effort)
# ──────────────────────────────────────────────────────────────────────────────
def enrich_license(comp):
    """Attempt to fetch SPDX license from public APIs."""
    name = comp["component"]
    headers = {"Accept": "application/json"}

    # GitHub
    try:
        time.sleep(0.2)
        r = requests.get(f"https://api.github.com/repos/{name}/license", headers=headers, timeout=5)
        if r.status_code == 200:
            data = r.json()
            return data.get("license", {}).get("spdx_id"), r.url
    except Exception:
        pass

    # npm
    try:
        time.sleep(0.2)
        r = requests.get(f"https://registry.npmjs.org/{name}", timeout=5)
        if r.status_code == 200:
            return r.json().get("license"), r.url
    except Exception:
        pass

    # PyPI
    try:
        time.sleep(0.2)
        r = requests.get(f"https://pypi.org/pypi/{name}/json", timeout=5)
        if r.status_code == 200:
            return r.json().get("info", {}).get("license"), r.url
    except Exception:
        pass

    return None, "unknown"

# ──────────────────────────────────────────────────────────────────────────────
# Parse all inputs
# ──────────────────────────────────────────────────────────────────────────────
syft_comps              = parse_syft(syft_file)
grype_license_map, gcom = parse_grype(grype_file)
scanoss_comps           = parse_scanoss(scanoss_file)

# Enrich Syft comps via Grype & external APIs
for comp in syft_comps:
    key = f"{comp['component']}@{comp['version']}"
    if not comp["license"] and key in grype_license_map:
        comp["license"] = grype_license_map[key]
        comp["license_source"] = "grype"
    lic, url = enrich_license(comp)
    if lic:
        comp["enriched_license"] = lic
        comp["license_url"] = url

# ──────────────────────────────────────────────────────────────────────────────
# Merge + ensure hashable
# ──────────────────────────────────────────────────────────────────────────────
merged = syft_comps + scanoss_comps
for rec in merged:
    rec["component"]        = _make_hashable(rec.get("component"))
    rec["version"]          = _make_hashable(rec.get("version"))
    rec["license"]          = _make_hashable(rec.get("license"))
    rec["enriched_license"] = _make_hashable(rec.get("enriched_license"))

# ──────────────────────────────────────────────────────────────────────────────
# Write merged, Grype, SCANOSS, and Syft reports
# ──────────────────────────────────────────────────────────────────────────────
pd.DataFrame(merged).drop_duplicates(
    subset=["component", "version", "license", "enriched_license"]
).to_excel(excel_out, index=False)

pd.DataFrame(merged).to_json(json_out, orient="records", indent=2)

# Grype components
pd.DataFrame(gcom).drop_duplicates().to_excel(grype_excel, index=False)

# SCANOSS components
pd.DataFrame(scanoss_comps).drop_duplicates().to_excel(scanoss_excel, index=False)

# Syft components (simple view)
df_syft = pd.DataFrame(syft_comps)
if df_syft.empty:
    df_syft = pd.DataFrame(columns=["component", "version", "license", "license_source", "license_url"])
else:
    df_syft = df_syft[["component", "version", "license", "license_source", "license_url"]].drop_duplicates()
df_syft.to_excel(syft_excel, index=False)

print(f"✅ Reports generated for: {image_name}")
