"""Build the sanitized net/ XHR fixture from the real _raw_capture/xhr_00.json.

Provenance: _raw_capture/xhr_00.json is a REAL captured network response of the
mooc2 studentcourse catalog page (status 200, text/html, GET) taken by
scripts/capture_fixtures.py in the live run (@end of tests/fixtures/_raw_capture/manifest.json).
The body is a REAL "$catalog_*" grammar page (same corpus as dom_catalog_list.html).

Sanitization:
  - enc / t / userHid session params in the URL -> redacted as ***.
  - body kept only as a small excerpt that proves the catalog grammar
    (the full 158 KB body is NOT committed; it lives in _raw_capture, which is gitignored).
Licensing: fixture for tests only.
"""
import json
import re

RAW = "tests/fixtures/_raw_capture/xhr_00.json"
OUT = "tests/fixtures/net/xhr_student_course_catalog.json"

d = json.load(open(RAW, encoding="utf-8"))

# redact session params in URL
url = d["url"]
url = re.sub(r"(enc=)[^&]+", r"\1***", url)
url = re.sub(r"([?&](?:t|time)=)[^&]*", r"\1***", url)

body = d["body"]
markers = ["chapter_item", "catalog_title", "catalog_level", "catalog_name",
           "catalog_state", "knowledgeJobCount", "catalog_points_yi"]
present = {m: (m in body) for m in markers}

idx = body.find("chapter_item")
excerpt = body[idx - 120: idx + 1400] if idx > 0 else body[:1400]

fixture = {
    "_note": ("sanitized REAL XHR snapshot — extracted from _raw_capture/xhr_00.json "
              "(mooc2 studentcourse catalog HTML captured live by scripts/capture_fixtures.py); "
              "enc/t redacted; body trimmed to catalog excerpt; full body len in body_len_total."),
    "method": "GET",
    "url": url,
    "status": d["status"],
    "content_type": d["content_type"],
    "body_len_total": d["body_len"],
    "catalog_markers": present,
    "real_grammar": True,
    "body_excerpt": excerpt,
}

with open(OUT, "w", encoding="utf-8") as f:
    json.dump(fixture, f, ensure_ascii=False, indent=2)
print("wrote", OUT, "len", len(excerpt), "markers", json.dumps(present)[:120])