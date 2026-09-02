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

import collections
import csv
import gzip
import hashlib
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


def _iter_records(path: str):
    """Yield (fields, record) without ever holding the whole file.

    ⚠️ The load-everything version cost 5.44 GB on two 38 MB slices and PROJECTED
    16.7 GB on the real 126 MB + 107 MB GIAB benchmarks — on a 23 GiB VM shared
    with 23 other runners. The container ran out of memory, the runner stalled,
    its server heartbeat went silent, and the reaper reclaimed the task; it
    dead-lettered at retry 5 after 24 minutes. A task-level heartbeat cannot help
    a process that is thrashing, which is why the earlier fix did not.

    Records are yielded one at a time so peak memory is a function of the INDEX
    built by the caller, not of the file.
    """
    fs = get_storage_backend(path)
    name = path.lower().rsplit("?", 1)[0]
    gz = name.endswith(".gz")
    if gz:
        name = name[: -len(".gz")]

    def _lines(text_stream):
        """Yield lines, stopping cleanly at a TRUNCATED stream.

        ⚠️ bgzip is block-gzip, so a ranged fetch of a huge benchmark yields whole
        records and then an EOFError at the cut. That is a supported way to work
        with a 126 MB VCF, so the truncation must end iteration rather than fail
        the comparison — the non-streaming reader already tolerated it and this
        keeps the two consistent.
        """
        try:
            for ln in text_stream:
                yield ln
        except (EOFError, OSError) as exc:
            logger.debug("truncated stream for %s: %s", path, exc)

    with fs.open(path, "rb") as fh:
        stream = gzip.GzipFile(fileobj=fh) if gz else fh
        text = _lines(io.TextIOWrapper(stream, encoding="utf-8", errors="replace"))
        if name.endswith((".vcf", ".bcf")):
            fields = None
            for line in text:
                if line.startswith("##"):
                    continue
                if line.startswith("#"):
                    fields = line.lstrip("#").rstrip("\n").split("\t")
                    continue
                if fields is None or not line.strip():
                    continue
                parts = line.rstrip("\n").split("\t")
                if len(parts) >= len(fields):
                    yield fields, dict(zip(fields, parts))
        elif name.endswith((".jsonl", ".ndjson")):
            for line in text:
                if line.strip():
                    rec = json.loads(line)
                    yield list(rec), rec
        else:
            delim = "\t" if name.endswith((".tsv", ".tab")) else ","
            reader = csv.DictReader(text, delimiter=delim)
            for rec in reader:
                yield list(reader.fieldnames or []), dict(rec)


# A key column with more distinct values than this is an IDENTITY, not a
# partition, and "which partitions exist on each side" is a meaningless
# question about it. Bounded so the counter cannot grow with the file.
_PARTITION_CAP = 512

# Refuse to explode one record beyond this many rows. A delimiter that appears
# in free text would otherwise turn a single record into millions.
_SPLIT_MAX_ROWS = 256


def _split_rows(norm, split, delim):
    """Explode a record whose columns hold a delimited multi-value list.

    One VCF line `G  A,ATTTACAACCTTAACTCCA` is TWO alleles sharing a row. Keyed
    on the whole ALT field it matches nothing when the other release lists only
    `A` — so the comparison reports total disagreement where the two releases in
    fact AGREE on one allele. Measured on the GIAB HG001 benchmarks: 396 records
    reported as wholly missing/added that share an allele once split.

    Returns (rows, overflowed).
    """
    rows = [norm]
    over = False
    for col in split:
        val = norm.get(col)
        if not isinstance(val, str) or delim not in val:
            continue
        parts = val.split(delim)
        if len(rows) * len(parts) > _SPLIT_MAX_ROWS:
            over = True
            continue
        rows = [dict(r, **{col: pt}) for r in rows for pt in parts]
    return rows, over


def _index(path, keys, *, ignore, sort_json, tol, split=(), delim=","):
    """Stream a file into {key -> digest of its comparable values}.

    A digest rather than the record itself: the comparison needs to know WHETHER
    two records agree, and keeping ~1 KB of dict per record is what exhausted
    memory. The digest is ~40 bytes.

    ⚠️ Values are digested only when the comparison is EXACT. With a tolerance,
    two records may be equal without being identical, so a hash cannot decide it
    — those keep their values and the caller compares numerically.
    """
    idx, fields, count, rows_n, over = {}, [], 0, 0, 0
    # Distinct values of the FIRST key column, which by convention is the
    # coarsest: chromosome, region, tenant, date. `None` once it proves to be
    # high-cardinality, i.e. not a partition at all.
    part: collections.Counter | None = collections.Counter() if keys else None
    for flds, rec in _iter_records(path):
        if not fields:
            fields = [f for f in flds if f not in ignore]
        count += 1
        norm = _normalise(rec, ignore=ignore, sort_json=sort_json)
        if split:
            rows, ov = _split_rows(norm, split, delim)
            over += ov
        else:
            rows = (norm,)
        for row in rows:
            rows_n += 1
            k = tuple(str(row.get(c, "")) for c in keys)
            if part is not None:
                part[k[0]] += 1
                if len(part) > _PARTITION_CAP:
                    part = None
            if tol > 0:
                idx[k] = tuple(row.get(f) for f in fields)
            else:
                h = hashlib.blake2b(digest_size=16)
                for f in fields:
                    h.update(str(row.get(f, "")).encode("utf-8", "replace"))
                    h.update(b"\x1f")
                idx[k] = h.digest()
    return fields, idx, count, rows_n, part, over


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


def _residual_structure(residuals, min_n=8, alpha=0.01):
    """Is the error SYSTEMATIC, or is it noise?

    ⚠️ A tolerance is a PER-VALUE predicate, and a per-value predicate cannot
    see a bias. Measured reproducing NOAA's 1991-2020 Climate Normals from the
    GHCN-Daily observations they are derived from: every one of 36 values landed
    within 1.0 degF, so `tolerance` reported the reproduction as SUCCESSFUL —
    while TMAX was high in all 12 months (+0.21..+0.31) and TMIN low in all 12
    (-0.46..-0.54). "36 small random errors" and "36 errors all in the same
    direction" are completely different claims about a reproduction, and only
    the second one says the method differs.

    An exact sign test, so there is no dependency and no distributional
    assumption: under "the signs are random" the count of positives is
    Binomial(n, 0.5). Reports the two-sided p-value.
    """
    vals = [r for r in residuals if r == r and r != 0.0]  # drop NaN and exact ties
    n = len(vals)
    if n < min_n:
        return None
    pos = sum(1 for r in vals if r > 0)
    neg = n - pos
    k = min(pos, neg)
    # two-sided exact binomial tail at p=0.5
    tail = sum(math.comb(n, i) for i in range(k + 1))
    pval = min(1.0, 2.0 * tail / (2 ** n))
    mean = sum(vals) / n
    return {
        "n": n, "positive": pos, "negative": neg,
        "mean_residual": mean, "p_value": pval,
        "systematic": pval < alpha,
    }


def _write_report(dest, level, agree, differing, ec, ac, detail, applied,
                  expected, actual, out_of_scope=0):
    """Write one comparison's verdict as a readable document.

    Records what was compared and WHAT NORMALISATION WAS APPLIED, because a
    verdict without its normalisations cannot be judged: "same data" after
    sorting keys and allowing 1e-6 drift is a different claim from "same data"
    exactly, and only one of them is a reproduction.
    """
    lines = [
        f"# Comparison — {os.path.basename(actual)} vs {os.path.basename(expected)}",
        "",
        f"- verdict        : **{'agree' if agree else 'DIFFER'}** at level `{level}`",
        f"- expected       : `{expected}` ({ec:,} records)",
        f"- actual         : `{actual}` ({ac:,} records)",
        f"- differing      : {differing:,}",
        f"- normalised by  : {', '.join(applied) if applied else '(nothing — compared as-is)'}",
    ]
    if out_of_scope:
        lines += [
            f"- **out of scope : {out_of_scope:,}** of the unmatched records lie in "
            f"key partitions the other artifact does not cover at all. They are "
            f"not a content difference and must not be read as one.",
        ]
    lines += [
        "",
        "## Detail",
        "",
        detail,
        "",
    ]
    body = "\n".join(lines)
    try:
        fs = get_storage_backend(dest)
        parent = os.path.dirname(dest)
        if parent:
            try:
                fs.makedirs(parent, exist_ok=True)
            except Exception:  # noqa: BLE001 — backend may not need it
                pass
        with fs.open(dest, "wb") as fh:
            fh.write(body.encode("utf-8"))
        return dest
    except Exception:
        logger.warning("could not write comparison report to %s", dest, exc_info=True)
        return ""


def _scope(epart, apart, keys):
    """Partitions present on one side and wholly absent from the other.

    ⚠️ This is the check that was missing, and it is not a small omission.
    Comparing GIAB HG001 v3.3.2 against v4.2.1 reported 242,958 records
    "retired — no longer benchmarked". 99,980 of them (41.2%) are on chrX,
    which v4.2.1 does not cover AT ALL: the releases are named `CHROM1-X` and
    `1_22`. Those variants were not retired, they are out of scope, and the two
    claims call for completely different action from a lab that validated
    against v3.3.2.

    A ladder that reports content differences between artifacts spanning
    different key space is measuring the wrong thing, confidently. So the scope
    difference is reported SEPARATELY and never folded into the record counts.
    """
    if epart is None or apart is None or not keys:
        return 0, 0, ""
    only_e = sorted(set(epart) - set(apart))
    only_a = sorted(set(apart) - set(epart))
    if not only_e and not only_a:
        return 0, 0, ""
    ne = sum(epart[v] for v in only_e)
    na = sum(apart[v] for v in only_a)
    col = keys[0]
    bits = []
    if only_e:
        bits.append(f"{ne:,} record(s) in {col} {only_e[:8]} which `actual` "
                    f"does not cover at all")
    if only_a:
        bits.append(f"{na:,} record(s) in {col} {only_a[:8]} which `expected` "
                    f"does not cover at all")
    return ne, na, ("SCOPE: " + "; ".join(bits) +
                    " — out of scope, NOT a content difference")


def _compare_streaming(expected, actual, keys, *, ignore, sort_json, tol,
                       max_ex, applied, params, result, split=(), delim=","):
    """Keyed comparison in O(keys) memory rather than O(file).

    Two passes, one file at a time: index `expected`, then stream `actual` and
    decide each record as it arrives. Peak memory is the index, not the data —
    measured 16.7 GB projected for the load-everything version on the GIAB
    benchmarks, which exhausted a 23 GiB VM.
    """
    with heartbeating(params, f"indexing {os.path.basename(expected)}"):
        efields, eidx, ecount, erows, epart, eover = _index(
            expected, keys, ignore=ignore, sort_json=sort_json, tol=tol,
            split=split, delim=delim)
    with heartbeating(params, f"comparing against {os.path.basename(actual)}"):
        afields, aidx, acount, arows, apart, aover = _index(
            actual, keys, ignore=ignore, sort_json=sort_json, tol=tol,
            split=split, delim=delim)

    if split:
        # Splitting CHANGES THE UNIT OF COMPARISON from record to element, so
        # the counts must say so rather than silently meaning something else.
        applied.append(
            f"split {', '.join(split)} on {delim!r}: "
            f"{ecount:,}->{erows:,} and {acount:,}->{arows:,} comparable rows")
        if eover or aover:
            applied.append(f"{eover + aover} record(s) too wide to split "
                           f"(> {_SPLIT_MAX_ROWS} rows) — compared unsplit")
        ecount, acount = erows, arows

    scope_e, scope_a, scope_note = _scope(epart, apart, keys)

    if not efields or not afields:
        return result("missing", False, 0, ecount, acount, "no records readable")
    if set(efields) != set(afields):
        only_e = sorted(set(efields) - set(afields))
        only_a = sorted(set(afields) - set(efields))
        return result("exists", False, 0, ecount, acount,
                      f"field mismatch — only in expected: {only_e or '-'}; "
                      f"only in actual: {only_a or '-'}")

    only_e = set(eidx) - set(aidx)
    only_a = set(aidx) - set(eidx)
    shared = set(eidx) & set(aidx)

    changed = []
    # ⚠️ PER COLUMN, never pooled. Pooling destroys the signal it is meant to
    # find: in the NOAA normals case TMAX runs high and TMIN runs low, so a
    # pooled sign test sees a balanced mix and reports "no bias" while BOTH
    # columns are systematically wrong in opposite directions.
    residuals: dict[str, list[float]] = collections.defaultdict(list)
    for k in shared:
        ev, av = eidx[k], aidx[k]
        if tol > 0:
            # The SIGNED residual of every numeric pair, whether or not it
            # passes the tolerance: a value inside tolerance still carries
            # direction, and direction is what a per-value predicate cannot see.
            for fname, x, y in zip(efields, ev, av):
                try:
                    fx, fy = float(x), float(y)
                except (TypeError, ValueError):
                    continue
                if fx == fx and fy == fy:  # not NaN
                    residuals[fname].append(fy - fx)
            if any(not _values_equal(x, y, tol) for x, y in zip(ev, av)):
                changed.append(k)
        elif ev != av:
            changed.append(k)

    bias = None
    if tol > 0:
        per = {f: st for f, v in residuals.items()
               if (st := _residual_structure(v)) is not None}
        flagged = {f: st for f, st in per.items() if st["systematic"]}
        if flagged:
            bias = {"systematic": True, "columns": flagged,
                    "mean_residual": max((st["mean_residual"] for st in flagged.values()),
                                         key=abs)}
            for f, st in sorted(flagged.items()):
                applied.append(
                    f"⚠️ `{f}` residuals are SYSTEMATIC, not noise: "
                    f"{st['positive']}+/{st['negative']}- of {st['n']}, mean "
                    f"{st['mean_residual']:+.4g} (sign test p={st['p_value']:.2g})")
        elif per:
            bias = {"systematic": False, "columns": per,
                    "mean_residual": max((st["mean_residual"] for st in per.values()),
                                         key=abs)}

    # The scope note leads, because it changes how every number below it reads.
    pre = (scope_note + ". ") if scope_note else ""

    if only_e or only_a:
        note = ("the %d shared record(s) match" % len(shared) if not changed
                else "and %d of the %d shared record(s) also differ in values"
                     % (len(changed), len(shared)))
        ex = "; ".join(str(k) for k in sorted(only_e)[:max_ex]) or "-"
        return result("cardinality", False,
                      len(only_e) + len(only_a) + len(changed), ecount, acount,
                      f"{pre}{len(only_e)} record(s) only in expected, "
                      f"{len(only_a)} only in actual — {note}. missing e.g. {ex}",
                      out_of_scope=scope_e + scope_a, bias=bias)
    if changed:
        ex = "; ".join(str(k) for k in sorted(changed)[:max_ex])
        return result("keys", False, len(changed), ecount, acount,
                      f"{pre}{len(changed)} record(s) differ in values. e.g. {ex}",
                      out_of_scope=scope_e + scope_a, bias=bias)

    detail = "same data" + (f" (after {'; '.join(applied)})" if applied else "")
    return result("values", True, 0, ecount, acount, pre + detail,
                  out_of_scope=scope_e + scope_a, bias=bias)


def handle_tabular(params: dict[str, Any]) -> dict[str, Any]:
    log = params.get("_step_log") or (lambda *a, **k: None)
    expected, actual = params["expected"], params["actual"]
    keys = list(params.get("key_columns") or [])
    ignore = set(params.get("ignore_columns") or [])
    sort_json = bool(params.get("sort_json_keys"))
    tol = float(params.get("tolerance") or 0.0)
    max_ex = int(params.get("max_examples") or 10)
    split = [c for c in (params.get("split_columns") or []) if c not in ignore]
    delim = params.get("split_delimiter") or ","

    applied: list[str] = []
    if split and not keys:
        # ⚠️ Splitting only means anything for a KEYED comparison — the
        # positional path matches row N against row N, and exploding one side
        # shifts every subsequent row. Accepting the parameter and quietly
        # doing nothing is how `report` went unnoticed; say it instead.
        applied.append("split_columns ignored — it requires key_columns")
        log("split_columns was given without key_columns and has no effect",
            level="WARNING")
        split = []
    if ignore:
        applied.append(f"ignored columns: {', '.join(sorted(ignore))}")
    if sort_json:
        applied.append("sorted nested JSON keys")
    if tol:
        applied.append(f"relative tolerance {tol:g}")

    dest = params.get("report") or ""

    def _result(level, agree, differing, ec, ac, detail, report_path="",
                out_of_scope=0, bias=None):
        # ⚠️ `report` was declared on the facet and silently ignored: a caller
        # could pass a destination, get a green verdict, and find nothing
        # written. A parameter that does nothing is worse than one that does not
        # exist — the FFL promises an artifact the handler never produced.
        out = report_path or (_write_report(dest, level, agree, differing, ec, ac,
                                            detail, applied, expected, actual,
                                            out_of_scope)
                              if dest else "")
        return {"level": level, "agree": agree, "differing": differing,
                "expected_count": ec, "actual_count": ac,
                "out_of_scope": out_of_scope,
                "systematic_bias": bool(bias and bias["systematic"]),
                "mean_residual": float(bias["mean_residual"]) if bias else 0.0,
                "normalised_by": applied, "report": out,
                "_detail": detail}

    # --- rung: exists -------------------------------------------------------
    missing = [p for p in (expected, actual) if not get_storage_backend(p).exists(p)]
    if missing:
        return _result("missing", False, 0, 0, 0, f"not readable: {'; '.join(missing)}")

    # KEYED comparison STREAMS — it is the path that has to scale, and the only
    # one a real reproduction uses. The positional path below still loads both
    # files; it cannot match records across a reordering anyway, and already
    # discloses that weaker claim in `normalised_by`.
    if keys:
        return _compare_streaming(expected, actual, keys, ignore=ignore,
                                  sort_json=sort_json, tol=tol, max_ex=max_ex,
                                  applied=applied, params=params, result=_result,
                                  split=split, delim=delim)

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
