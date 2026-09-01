# Copyright 2025 Ralph Lemke
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Built-in output-comparison handlers (``fw.compare``).

See ``facetwork/ffl/compare.ffl`` for why this reports a LEVEL rather than a
boolean. The short version, from the measurement that motivated it: two runs of
the same pipeline produced byte-different files containing identical data,
because the upstream tool serialises a nested dict in unstable key order. A
checksum comparison calls that a failed reproduction. It is not one.
"""

from __future__ import annotations

import csv
import io
import json
import logging
import math
import os
from typing import Any

from facetwork.handlers._heartbeat import heartbeating
from facetwork.runtime.storage import get_storage_backend

logger = logging.getLogger(__name__)

NAMESPACE = "fw.compare"

#: The ladder, weakest first. A verdict names the HIGHEST rung reached, so
#: "cardinality" means schema matched too, and "values" means everything below
#: it did. Ordered so `min()` over a fan-out yields the weakest pair.
LEVELS = ["missing", "exists", "schema", "cardinality", "keys", "values", "bytes"]


def _rank(level: str) -> int:
    return LEVELS.index(level) if level in LEVELS else 0


def _read_records(path: str) -> tuple[list[dict], list[str], bytes]:
    """Records, field names, and raw bytes for a csv/tsv/json-lines file.

    Returns the raw bytes as well because the byte comparison is a rung on the
    ladder, and reading twice for it would be wasteful and racy.
    """
    with get_storage_backend(path).open(path, "rb") as fh:
        raw = fh.read()
    name = path.lower().rsplit("?", 1)[0]
    if name.endswith(".gz"):
        # bgzip (as VCFs use) is block-gzip, so a TRUNCATED prefix still yields
        # whole records before it errors — which is what makes a ranged fetch of
        # a 126 MB benchmark usable for a comparison at all.
        import gzip
        import io as _io
        buf, dec = [], gzip.GzipFile(fileobj=_io.BytesIO(raw))
        try:
            while True:
                chunk = dec.read(1 << 20)
                if not chunk:
                    break
                buf.append(chunk)
        except (EOFError, OSError):
            pass  # truncated stream — keep what decompressed cleanly
        raw = b"".join(buf)
        name = name[: -len(".gz")]
    text = raw.decode("utf-8", errors="replace")

    if name.endswith((".vcf", ".bcf")):
        # VCF: '##' are metadata, the single '#CHROM' line is the header, and the
        # natural identity of a record is (CHROM, POS, REF, ALT) — pass those as
        # key_columns. Metadata is deliberately NOT compared: it carries the
        # producing command line and reference paths, which differ between any
        # two runs without the variants differing at all.
        lines = text.splitlines()
        head = next((i for i, ln in enumerate(lines) if ln.startswith("#CHROM")), None)
        if head is None:
            return [], [], raw
        fields = lines[head].lstrip("#").split("\t")
        rows = []
        for ln in lines[head + 1:]:
            if not ln or ln.startswith("#"):
                continue
            parts = ln.split("\t")
            if len(parts) < len(fields):
                continue  # truncated final record from a ranged fetch
            rows.append(dict(zip(fields, parts)))
        return rows, fields, raw

    if name.endswith((".jsonl", ".ndjson")):
        rows = [json.loads(line) for line in text.splitlines() if line.strip()]
        fields: list[str] = []
        for r in rows:  # union, order-preserving — json objects need not agree
            for k in r:
                if k not in fields:
                    fields.append(k)
        return rows, fields, raw

    if name.endswith(".json"):
        doc = json.loads(text) if text.strip() else []
        rows = doc if isinstance(doc, list) else [doc]
        fields = list(rows[0].keys()) if rows and isinstance(rows[0], dict) else []
        return rows, fields, raw

    delim = "\t" if name.endswith((".tsv", ".tab")) else ","
    reader = csv.DictReader(io.StringIO(text), delimiter=delim)
    rows = [dict(r) for r in reader]
    return rows, list(reader.fieldnames or []), raw


def _normalise(row: dict, *, ignore: set[str], sort_json: bool) -> dict:
    out = {}
    for k, v in row.items():
        if k in ignore:
            continue
        if sort_json and isinstance(v, str) and v[:1] in "{[":
            try:
                v = json.dumps(json.loads(v), sort_keys=True)
            except ValueError:
                pass  # not JSON after all — compare verbatim
        out[k] = v
    return out


def _values_equal(a: Any, b: Any, tolerance: float) -> bool:
    """Equality, with a RELATIVE tolerance for values that parse as numbers.

    Exact float equality is the wrong predicate for most scientific output, but
    tolerance is a claim about acceptable error — so the caller states it and it
    is reported with the verdict rather than applied invisibly.
    """
    if a == b:
        return True
    if tolerance <= 0:
        return False
    try:
        fa, fb = float(a), float(b)
    except (TypeError, ValueError):
        return False
    if math.isnan(fa) and math.isnan(fb):
        return True
    return math.isclose(fa, fb, rel_tol=tolerance, abs_tol=0.0)


def handle_tabular(params: dict[str, Any]) -> dict[str, Any]:
    log = params.get("_step_log") or (lambda *a, **k: None)
    expected, actual = params["expected"], params["actual"]
    keys = list(params.get("key_columns") or [])
    ignore = set(params.get("ignore_columns") or [])
    sort_json = bool(params.get("sort_json_keys"))
    tol = float(params.get("tolerance") or 0.0)
    max_ex = int(params.get("max_examples") or 10)

    applied: list[str] = []
    if ignore:
        applied.append(f"ignored columns: {', '.join(sorted(ignore))}")
    if sort_json:
        applied.append("sorted nested JSON keys")
    if tol:
        applied.append(f"relative tolerance {tol:g}")

    def _result(level, agree, differing, ec, ac, detail, report_path=""):
        return {"level": level, "agree": agree, "differing": differing,
                "expected_count": ec, "actual_count": ac,
                "normalised_by": applied, "report": report_path,
                "_detail": detail}

    # --- rung: exists -------------------------------------------------------
    missing = [p for p in (expected, actual) if not get_storage_backend(p).exists(p)]
    if missing:
        return _result("missing", False, 0, 0, 0, f"not readable: {'; '.join(missing)}")

    # Reading two large artifacts (S3 fetch + decompress + parse of millions of
    # records) is minutes of blocking work with no loop to hook — see _heartbeat.
    with heartbeating(params, f"reading {os.path.basename(expected)} / {os.path.basename(actual)}"):
        erows, efields, eraw = _read_records(expected)
        arows, afields, araw = _read_records(actual)

    # --- rung: bytes (checked first; if identical everything below holds) ----
    if eraw == araw:
        return _result("bytes", True, 0, len(erows), len(arows), "byte-identical")

    # --- rung: schema -------------------------------------------------------
    ef, af = [f for f in efields if f not in ignore], [f for f in afields if f not in ignore]
    if set(ef) != set(af):
        only_e, only_a = sorted(set(ef) - set(af)), sorted(set(af) - set(ef))
        return _result("exists", False, 0, len(erows), len(arows),
                       f"field mismatch — only in expected: {only_e or '-'}; "
                       f"only in actual: {only_a or '-'}")
    if ef != af:
        applied.append("ignored field ORDER")

    en = [_normalise(r, ignore=ignore, sort_json=sort_json) for r in erows]
    an = [_normalise(r, ignore=ignore, sort_json=sort_json) for r in arows]

    # --- rung: cardinality --------------------------------------------------
    # ⚠️ A count mismatch short-circuits ONLY when there are no keys to do better
    # with. With keys, stopping here would be actively unhelpful for the question
    # this facet exists to answer: measured on a 13,979-record published dataset
    # with ONE record dropped, the count check reported "counts differ" and hid
    # the fact that the other 13,978 were identical. "13,978 identical, 1
    # missing" is the finding; "counts differ" is not.
    if len(en) != len(an) and not keys:
        return _result("schema", False, abs(len(en) - len(an)), len(en), len(an),
                       f"record count differs: expected {len(en)}, actual {len(an)}"
                       " (no key_columns given, so records cannot be matched up)")

    # --- rung: keys ---------------------------------------------------------
    if keys:
        def kf(r):
            return tuple(str(r.get(k, "")) for k in keys)
        emap, amap = {kf(r): r for r in en}, {kf(r): r for r in an}
        only_e, only_a = set(emap) - set(amap), set(amap) - set(emap)
        shared = set(emap) & set(amap)
        pairs = [(k, emap[k], amap[k]) for k in shared]
        if only_e or only_a:
            # Characterise the INTERSECTION too, so the report distinguishes
            # "a few records are missing but the rest reproduce exactly" from
            # "the records differ everywhere". Those need different responses.
            bad = 0
            for k in shared:
                if any(not _values_equal(emap[k].get(f), amap[k].get(f), tol) for f in ef):
                    bad += 1
            ex = "; ".join(str(k) for k in sorted(only_e)[:max_ex]) or "-"
            note = (f"the {len(shared)} shared record(s) match"
                    if not bad else
                    f"and {bad} of the {len(shared)} shared record(s) also differ in values")
            return _result("cardinality", False, len(only_e) + len(only_a) + bad,
                           len(en), len(an),
                           f"{len(only_e)} record(s) only in expected, "
                           f"{len(only_a)} only in actual — {note}. missing e.g. {ex}")
    else:
        applied.append("matched records BY POSITION (no key_columns given)")
        pairs = [(i, e, a) for i, (e, a) in enumerate(zip(en, an))]

    # --- rung: values -------------------------------------------------------
    diffs = []
    with heartbeating(params, f"comparing {len(pairs)} record(s)"):
        for k, e, a in pairs:
            bad = [f for f in ef if not _values_equal(e.get(f), a.get(f), tol)]
            if bad:
                diffs.append((k, bad, {f: (e.get(f), a.get(f)) for f in bad[:3]}))
    if diffs:
        ex = "; ".join(f"{k}: {d}" for k, _b, d in diffs[:max_ex])
        return _result("keys", False, len(diffs), len(en), len(an),
                       f"{len(diffs)} record(s) differ in values. e.g. {ex}")

    detail = "same data" + (f" (after {'; '.join(applied)})" if applied else "")
    log(f"{os.path.basename(actual)}: {detail}; bytes differ")
    return _result("values", True, 0, len(en), len(an), detail)


def handle_summarise(params: dict[str, Any]) -> dict[str, Any]:
    """Aggregate per-pair verdicts into ONE report.

    Reports the WEAKEST level any pair reached, because a reproduction is only
    as good as its worst file — a summary that averaged, or reported the best,
    would hide exactly the pair a reader needs to see.
    """
    verdicts = list(params.get("verdicts") or [])
    title = params.get("title") or "reproduction report"
    dest = params.get("report") or ""

    if not verdicts:
        # An empty fan-out must not read as success; see the OSM county tier,
        # where "0 built" and "all built" were indistinguishable for weeks.
        return {"level": "missing", "agree": False, "pairs": 0, "disagreeing": 0,
                "report": "", "_detail": "no verdicts supplied — nothing was compared"}

    worst = min(verdicts, key=lambda v: _rank(str(v.get("level", "missing"))))
    disagreeing = [v for v in verdicts if not v.get("agree")]
    lines = [f"# {title}", "",
             f"pairs compared : {len(verdicts)}",
             f"weakest level  : {worst.get('level')}",
             f"disagreeing    : {len(disagreeing)} of {len(verdicts)}", ""]
    if disagreeing:
        lines.append("## Discrepancies")
        for v in disagreeing:
            lines.append(f"- **{v.get('path','?')}** — level `{v.get('level')}`, "
                         f"{v.get('differing', 0)} differing: {v.get('detail', '')}")
    else:
        lines.append("All pairs agree at level "
                     f"`{worst.get('level')}` or better.")
    body = "\n".join(lines) + "\n"

    if dest:
        fs = get_storage_backend(dest)
        parent = os.path.dirname(dest)
        if parent:
            try:
                fs.makedirs(parent, exist_ok=True)
            except Exception:  # noqa: BLE001 — backend may not need it
                pass
        with fs.open(dest, "wb") as fh:
            fh.write(body.encode("utf-8"))

    return {"level": str(worst.get("level", "missing")),
            "agree": not disagreeing, "pairs": len(verdicts),
            "disagreeing": len(disagreeing), "report": dest or body}


_DISPATCH: dict[str, Any] = {
    f"{NAMESPACE}.Tabular": handle_tabular,
    f"{NAMESPACE}.Summarise": handle_summarise,
}


def handle(payload: dict) -> dict:
    """RegistryRunner entrypoint — one entrypoint, dispatching on ``_facet_name``."""
    facet = payload["_facet_name"]
    handler = _DISPATCH.get(facet)
    if handler is None:
        raise KeyError(f"no built-in compare handler for {facet!r}")
    return handler(payload)


def facet_names() -> list[str]:
    return sorted(_DISPATCH)
