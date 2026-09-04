"""Survey the OSM extract stores and render a status report.

WHY THIS EXISTS
---------------
Two independent things call themselves "the OSM cache" and they are NOT
interchangeable:

  * the MinIO `osm-extracts` bucket  — 8 continents + ~3,900 sub-regions
    (country / state / county). This is what the county-atlas and every
    admin-set workflow read.
  * the served tree at :8088        — continents ONLY. This is what the
    `osmosis_replication_base_url` stamped into every PBF header points at,
    i.e. what a third-party consumer follows.

Repointing a consumer from one to the other 404s every sub-region, and that
has already happened here. So the report always shows BOTH, side by side,
with the same columns — the divergence is the point, not a footnote.

WHAT "LAST UPDATED" MEANS — TWO DIFFERENT CLOCKS
-----------------------------------------------
`mtime` is when we WROTE the file. The replication `timestamp` in the
sibling `-updates/state.txt` is when the DATA was current. They routinely
differ by weeks, and only the second one answers "is this extract stale?".

⚠️ Not every extract can answer it. Measured on this store: continent and
US-county extracts carry a full `sequenceNumber` + `timestamp`; the country
tier carries a timestamp with no sequence; the German Kreise carry an EMPTY
state.txt. So "age unknown" is reported as its own category, never folded
into "old" and never silently treated as fresh — an extract that cannot say
how current it is is a worse problem than one that says it is old.
"""
from __future__ import annotations

import concurrent.futures as cf
import datetime as dt
import json
import os
import re
import urllib.error
import urllib.request

UTC = dt.timezone.utc
PBF_SUFFIX = "-latest.osm.pbf"


# --------------------------------------------------------------------------
# state.txt

def parse_state(text: str) -> dict:
    """An osmosis state file. Returns {} for the empty ones rather than raising.

    ⚠️ osmosis escapes the colons in the timestamp (`2026-08-30T00\\:00\\:00Z`).
    Unescaping is not cosmetic: without it every timestamp fails to parse and
    the whole store reports as age-unknown, which looks like a data problem
    rather than a parsing one.
    """
    out: dict = {}
    for line in (text or "").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        out[k.strip()] = v.strip().replace("\\:", ":")
    if "sequenceNumber" in out:
        try:
            out["sequenceNumber"] = int(out["sequenceNumber"])
        except ValueError:
            out.pop("sequenceNumber")
    if "timestamp" in out:
        try:
            out["timestamp"] = dt.datetime.strptime(
                out["timestamp"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC)
        except ValueError:
            out.pop("timestamp")
    return out


def tier_of(key: str) -> str:
    """Depth in the key IS the tier — the layout carries no other marker."""
    return {0: "continent", 1: "country", 2: "subnational", 3: "county"}.get(
        key.count("/"), "deeper")


# --------------------------------------------------------------------------
# the bucket

def survey_bucket(endpoint: str, bucket: str, *, access_key: str,
                  secret_key: str, threads: int = 32) -> dict:
    import boto3
    import botocore.config

    s3 = boto3.client(
        "s3", endpoint_url=endpoint,
        aws_access_key_id=access_key, aws_secret_access_key=secret_key,
        config=botocore.config.Config(read_timeout=60, connect_timeout=15,
                                      retries={"max_attempts": 3}))

    extracts: dict[str, dict] = {}
    state_keys: dict[str, str] = {}
    total_objects = 0
    total_bytes = 0
    for page in s3.get_paginator("list_objects_v2").paginate(Bucket=bucket):
        for o in page.get("Contents", []):
            k, total_objects, total_bytes = o["Key"], total_objects + 1, total_bytes + o["Size"]
            if k.endswith(PBF_SUFFIX):
                region = k[: -len(PBF_SUFFIX)]
                extracts[region] = {"region": region, "key": k, "bytes": o["Size"],
                                    "mtime": o["LastModified"].astimezone(UTC),
                                    "tier": tier_of(region)}
                state_keys[region] = region + "-updates/state.txt"

    # One small GET per extract. Threaded because ~4,000 sequential round trips
    # to a LAN MinIO is minutes, and a report nobody waits for is a report
    # nobody runs.
    def fetch(item):
        region, key = item
        try:
            body = s3.get_object(Bucket=bucket, Key=key)["Body"].read()
            return region, parse_state(body.decode("utf-8", "replace")), None
        except Exception as exc:                       # noqa: BLE001
            return region, {}, type(exc).__name__

    with cf.ThreadPoolExecutor(max_workers=threads) as pool:
        for region, st, err in pool.map(fetch, state_keys.items()):
            e = extracts[region]
            e["state_error"] = err
            e["sequence"] = st.get("sequenceNumber")
            e["vintage"] = st.get("timestamp")

    return {"kind": "bucket", "name": f"{bucket} @ {endpoint}",
            "objects": total_objects, "bytes": total_bytes,
            "extracts": extracts}


# --------------------------------------------------------------------------
# the served tree

_HREF = re.compile(r'href="([^"?#]+)"')


def survey_tree(base_url: str, *, timeout: int = 20) -> dict:
    """Walk the served directory listing.

    ⚠️ Depth is capped and the walk is breadth-first on purpose. This is a
    plain autoindex over an external volume; following it unbounded on a
    tree that grows a county tier would turn a status report into a crawl.
    """
    base = base_url.rstrip("/") + "/"

    def get(url, binary=False):
        with urllib.request.urlopen(url, timeout=timeout) as r:
            return r.read() if binary else r.read().decode("utf-8", "replace")

    extracts: dict[str, dict] = {}
    to_visit = [("", 0)]
    while to_visit:
        rel, depth = to_visit.pop(0)
        try:
            html = get(base + rel)
        except (urllib.error.URLError, OSError):
            continue
        for href in _HREF.findall(html):
            if href.startswith(("..", "/", "http")):
                continue
            if href.endswith("/"):
                if depth < 3 and not href.endswith(("-updates/", "_poly/")):
                    to_visit.append((rel + href, depth + 1))
            elif href.endswith(PBF_SUFFIX):
                region = (rel + href)[: -len(PBF_SUFFIX)]
                extracts[region] = {"region": region, "key": rel + href,
                                    "bytes": None, "mtime": None,
                                    "tier": tier_of(region)}

    def enrich(region):
        e = extracts[region]
        try:                                   # size + mtime from a HEAD
            req = urllib.request.Request(base + e["key"], method="HEAD")
            with urllib.request.urlopen(req, timeout=timeout) as r:
                cl = r.headers.get("Content-Length")
                lm = r.headers.get("Last-Modified")
                if cl:
                    e["bytes"] = int(cl)
                if lm:
                    e["mtime"] = dt.datetime.strptime(
                        lm, "%a, %d %b %Y %H:%M:%S %Z").replace(tzinfo=UTC)
        except Exception:                      # noqa: BLE001
            pass
        try:
            st = parse_state(get(base + region + "-updates/state.txt"))
            e["sequence"] = st.get("sequenceNumber")
            e["vintage"] = st.get("timestamp")
        except Exception as exc:               # noqa: BLE001
            e["state_error"] = type(exc).__name__
        return region

    with cf.ThreadPoolExecutor(max_workers=16) as pool:
        list(pool.map(enrich, list(extracts)))

    return {"kind": "tree", "name": base_url,
            "objects": len(extracts),
            "bytes": sum(e["bytes"] or 0 for e in extracts.values()),
            "extracts": extracts}


# --------------------------------------------------------------------------
# gaps
#
# Every finding below states the RULE that produced it. A report that lists
# "missing regions" without saying what it compared against cannot be argued
# with, and an unarguable report gets believed when it is wrong.
#
# ⚠️ The hard part is not finding gaps, it is not INVENTING them. A country
# with no sub-region tier is not missing anything — no set covers it. That is
# reported as `not_attempted`, in its own section, never as a defect.

# Fallback only. `expect` in the sets file is the declared answer; this parses
# the prose for a set that has not declared one yet, and the report says which
# mechanism it used so an inferred number is never mistaken for a stated one.
EXPECT_COUNT = re.compile(r"(\d[\d,]*)\s+(?:[\w-]+\s+){0,2}?"
                          r"(counties|countries|states|districts)", re.I)


def load_sets(path: str) -> dict:
    try:
        with open(path) as fh:
            return json.load(fh).get("sets", {})
    except (OSError, ValueError):
        return {}


def find_gaps(bucket: dict, tree: dict, sets: dict, *, now: dt.datetime,
              stale_days: int, county_reference: dict | None) -> dict:
    b, t = bucket.get("extracts", {}), tree.get("extracts", {})
    g: dict = {"stale_days": stale_days}

    # 1. Cross-store. The two stores name the same continent differently
    #    ("oceania" vs "australia-oceania"), so a name-based diff would report
    #    two phantom gaps. Compare only the continent tier and say so.
    bc = {r for r, e in b.items() if e["tier"] == "continent"}
    tc = {r for r, e in t.items() if e["tier"] == "continent"}
    g["cross_store"] = {
        "rule": "continent-tier region names present in one store but not the other",
        "bucket_only": sorted(bc - tc),
        "tree_only": sorted(tc - bc),
        "both": len(bc & tc),
        "note": ("the two stores use different names for the same continent "
                 "(oceania / australia-oceania); a name here is a NAMING "
                 "divergence, not necessarily absent data"),
    }

    # 2. Declared-vs-actual, from the set descriptions. The admin-sets file is
    #    the only place that states an expectation, so it is the only honest
    #    thing to check against.
    declared, unchecked = [], []
    for name, spec in sets.items():
        expected, how = spec.get("expect"), "declared"
        if expected is None:
            m = EXPECT_COUNT.search(spec.get("description", ""))
            if not m:
                unchecked.append(name)
                continue
            expected, how = int(m.group(1).replace(",", "")), "inferred from description"
        inputs = spec.get("inputs", {})
        prefix = spec.get("expect_prefix") or inputs.get("prefix") \
            or inputs.get("source_region") or ""
        # A '*' segment means "one level under EACH child" — the county tier is
        # keyed <continent>/us/<state>/<county>, so its parent is per-state.
        if prefix.endswith("/*"):
            stem, want_depth = prefix[:-2], prefix.count("/") + 1
        else:
            stem, want_depth = prefix, prefix.count("/") + 1
        actual = sum(1 for r in b
                     if r.startswith(stem + "/") and r.count("/") == want_depth)
        declared.append({"set": name, "prefix": prefix, "expected": expected,
                         "how": how, "actual": actual, "delta": actual - expected,
                         "shortfall": max(0, expected - actual),
                         "description": spec.get("description", "")})
    g["declared_vs_actual"] = {
        "rule": "each set's declared `expect` vs extracts at that depth under its prefix",
        "sets": sorted(declared, key=lambda d: -abs(d["delta"])),
        "unchecked": unchecked,
        "note": ("`expect` is the size that set produced when it last completed, so a "
                 "delta means the store no longer matches it — in EITHER direction. "
                 "A set with no declared expectation is listed as unchecked, not as complete"),
    }

    # 3. Children older than their parent. This is the correctness property the
    #    admin-sets file calls out: BuildAdminSet cuts children FROM THE BUCKET,
    #    so a child cut before its parent was last rebuilt is derived from data
    #    the parent no longer contains.
    behind = []
    for region, e in b.items():
        if "/" not in region:
            continue
        parent = b.get(region.rsplit("/", 1)[0])
        if not parent or not parent["mtime"] or not e["mtime"]:
            continue
        if e["mtime"] < parent["mtime"]:
            behind.append({"region": region, "parent": parent["region"],
                           "child_mtime": e["mtime"], "parent_mtime": parent["mtime"],
                           "days": (parent["mtime"] - e["mtime"]).days})
    by_parent: dict[str, dict] = {}
    for row in behind:
        p = by_parent.setdefault(row["parent"], {"parent": row["parent"], "children": 0,
                                                 "max_days": 0})
        p["children"] += 1
        p["max_days"] = max(p["max_days"], row["days"])
    g["cut_from_superseded_parent"] = {
        "rule": "child extract written BEFORE its parent was last rebuilt",
        "total": len(behind),
        "by_parent": sorted(by_parent.values(), key=lambda d: -d["children"]),
    }

    # 4. Cannot state a vintage. Distinct from stale — see the module docstring.
    unknown = [r for r, e in b.items() if not e.get("vintage")]
    by_tier: dict[str, int] = {}
    for r in unknown:
        by_tier[b[r]["tier"]] = by_tier.get(b[r]["tier"], 0) + 1
    g["vintage_unknown"] = {
        "rule": "sibling -updates/state.txt absent, empty, or carrying no parsable timestamp",
        "total": len(unknown), "by_tier": by_tier,
        "note": "an extract that cannot say how current it is is not the same as one that says it is old",
    }

    # 5. Stale by data vintage (only where a vintage exists to judge).
    stale = [{"region": r, "vintage": e["vintage"],
              "days": (now - e["vintage"]).days}
             for r, e in b.items()
             if e.get("vintage") and (now - e["vintage"]).days > stale_days]
    st_tier: dict[str, dict] = {}
    for row in stale:
        d = st_tier.setdefault(b[row["region"]]["tier"], {"tier": b[row["region"]]["tier"],
                                                          "count": 0, "max_days": 0})
        d["count"] += 1
        d["max_days"] = max(d["max_days"], row["days"])
    g["stale"] = {"rule": f"replication timestamp older than {stale_days} days",
                  "total": len(stale), "by_tier": sorted(st_tier.values(),
                                                         key=lambda d: -d["max_days"])}

    # 6. Parents with no child tier. NOT a defect — no set covers them.
    parents_with_kids = {r.rsplit("/", 1)[0] for r in b if "/" in r}
    g["not_attempted"] = {
        "rule": "extract exists but nothing has ever been cut from it; no set covers it",
        "countries": sorted(r for r, e in b.items()
                            if e["tier"] == "country" and r not in parents_with_kids),
    }
    g["not_attempted"]["count"] = len(g["not_attempted"]["countries"])

    # 6b. Parents that have children but no extract of their own. Invisible in a
    #     file listing (the directory is "there" because its children are), and it
    #     changes what a rebuild has to cut FROM.
    g["parent_without_extract"] = {
        "rule": "region has children in the store but no <region>-latest.osm.pbf of its own",
        "regions": sorted({p for p in parents_with_kids if p and p not in b}),
        "note": ("not necessarily wrong — the US state tier is cut from the whole "
                 "north-america extract by design — but it means no rebuild of this "
                 "region alone is possible, and it has no vintage of its own"),
    }
    g["parent_without_extract"]["count"] = len(g["parent_without_extract"]["regions"])

    # 7. US counties against the Census county-equivalent list.
    #    ⚠️ A difference is NOT automatically a defect: our counties come from
    #    OSM admin_level 6, which includes independent cities and other units
    #    Census does not count as counties. Reported per state, both directions.
    if county_reference:
        ours: dict[str, int] = {}
        for r in b:
            if r.startswith("north-america/us/") and r.count("/") == 3:
                state = r.split("/")[2]          # [2] is the state, [3] the county
                ours[state] = ours.get(state, 0) + 1
        rows = []
        for state, exp in sorted(county_reference.items()):
            got = ours.get(state, 0)
            if got != exp:
                rows.append({"state": state, "census": exp, "ours": got, "delta": got - exp})
        g["us_counties"] = {
            "rule": "extracts under north-america/us/<state>/ vs Census county-equivalents (PEP)",
            "states_covered": len(ours), "census_states": len(county_reference),
            "ours_total": sum(ours.values()), "census_total": sum(county_reference.values()),
            "empty_states": sorted(s for s in county_reference if s not in ours),
            "differs": rows,
            "note": ("ours are OSM admin_level 6 boundaries, which include units "
                     "Census does not call counties — a delta is a difference to "
                     "explain, not automatically a miss"),
        }
    else:
        g["us_counties"] = {"rule": "not checked", "unverified":
                            "no Census county reference available"}
    return g


# --------------------------------------------------------------------------
# rendering

def human_bytes(n) -> str:
    if not n:
        return "-"
    for unit in ("B", "KiB", "MiB", "GiB", "TiB"):
        if n < 1024 or unit == "TiB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:,.1f} {unit}"
        n /= 1024.0
    return ""


def _age(now, when):
    return "unknown" if not when else f"{(now - when).days}d"


def summarise_store(store: dict, now: dt.datetime) -> list[dict]:
    tiers: dict[str, dict] = {}
    for e in store.get("extracts", {}).values():
        d = tiers.setdefault(e["tier"], {"tier": e["tier"], "count": 0, "bytes": 0,
                                         "mtimes": [], "vintages": [], "unknown": 0})
        d["count"] += 1
        d["bytes"] += e.get("bytes") or 0
        if e.get("mtime"):
            d["mtimes"].append(e["mtime"])
        if e.get("vintage"):
            d["vintages"].append(e["vintage"])
        else:
            d["unknown"] += 1
    order = ["continent", "country", "subnational", "county", "deeper"]
    out = []
    for name in order:
        d = tiers.get(name)
        if not d:
            continue
        out.append({
            "tier": name, "count": d["count"], "bytes": d["bytes"],
            "written_oldest": min(d["mtimes"]) if d["mtimes"] else None,
            "written_newest": max(d["mtimes"]) if d["mtimes"] else None,
            "vintage_oldest": min(d["vintages"]) if d["vintages"] else None,
            "vintage_newest": max(d["vintages"]) if d["vintages"] else None,
            "vintage_unknown": d["unknown"],
        })
    return out


def render_markdown(rep: dict) -> str:
    now = dt.datetime.fromisoformat(rep["generated_at"])
    L: list[str] = []
    w = L.append
    w("# OSM extract store — status report")
    w("")
    w(f"**Generated {now:%Y-%m-%d %H:%M %Z}** on `{rep['generated_on']}`.")
    w("")
    w("> This page is generated from the stores themselves, so it is correct as of")
    w("> the timestamp above and no later. **The report's own age and the data's age")
    w("> are different numbers** — a report that stopped regenerating shows old data")
    w("> looking permanently fresh, which is the exact failure the OSM watchdog")
    w("> exists to catch. If the generated time above is not recent, believe nothing")
    w("> below it.")
    w("")

    # --- the two stores
    w("## Two stores, and they are not interchangeable")
    w("")
    w("| | what it holds | extracts | size | newest data |")
    w("|---|---|---:|---:|---|")
    for s in rep["stores"]:
        vin = [e["vintage"] for e in s["extracts"].values() if e.get("vintage")]
        newest = max(vin).strftime("%Y-%m-%d") if vin else "unknown"
        w(f"| `{s['name']}` | {s['holds']} | {len(s['extracts']):,} | "
          f"{human_bytes(s['bytes'])} | {newest} |")
    w("")
    w("⚠️ The served tree is what the `osmosis_replication_base_url` stamped into")
    w("every PBF header points at — it is the address a third-party consumer")
    w("follows. The bucket is what our own workflows read. **They hold different")
    w("region sets and they name continents differently**, so repointing a")
    w("consumer from one to the other 404s every sub-region.")
    w("")

    # --- tiers
    for s in rep["stores"]:
        w(f"## {s['label']} — by tier")
        w("")
        w("| tier | extracts | size | written | data vintage | vintage unknown |")
        w("|---|---:|---:|---|---|---:|")
        for t in s["tiers"]:
            wr = (f"{t['written_oldest']:%Y-%m-%d} → {t['written_newest']:%Y-%m-%d}"
                  if t["written_oldest"] else "-")
            vi = (f"{t['vintage_oldest']:%Y-%m-%d} → {t['vintage_newest']:%Y-%m-%d}"
                  if t["vintage_oldest"] else "-")
            w(f"| {t['tier']} | {t['count']:,} | {human_bytes(t['bytes'])} | {wr} | "
              f"{vi} | {t['vintage_unknown']:,} |")
        w("")
        w("*written* is when we saved the file; *data vintage* is when the data in it")
        w("was current, read from the sibling `-updates/state.txt`. Only the second")
        w("answers \"is this stale?\", and it is the one that is often missing.")
        w("")

    # --- continents
    w("## Continents")
    w("")
    w("| region | bucket size | bucket seq | bucket vintage | tree seq | tree vintage | countries | sub-regions |")
    w("|---|---:|---:|---|---:|---|---:|---:|")
    for row in rep["continents"]:
        w(f"| {row['region']} | {human_bytes(row['bytes'])} | {row['sequence'] or '-'} | "
          f"{row['vintage'] or '-'} | {row['tree_sequence'] or '-'} | "
          f"{row['tree_vintage'] or '-'} | {row['countries']:,} | {row['descendants']:,} |")
    w("")

    # --- gaps
    g = rep["gaps"]
    w("## Known gaps")
    w("")
    w("Every finding names the rule that produced it. A gap list you cannot argue")
    w("with is a gap list that gets believed when it is wrong.")
    w("")

    d = g["declared_vs_actual"]
    w("### Declared set size vs what is in the store")
    w("")
    w(f"*Rule: {d['rule']}.*")
    w("")
    w("| set | expected | in store | delta | expectation |")
    w("|---|---:|---:|---:|---|")
    for r in d["sets"]:
        flag = f"**{r['delta']:+,}**" if r["delta"] else "0"
        w(f"| `{r['set']}` | {r['expected']:,} | {r['actual']:,} | {flag} | {r['how']} |")
    w("")
    w(f"⚠️ {d['note']}.")
    w("")
    if d["unchecked"]:
        w(f"Not checked (no declared expectation): {', '.join('`'+n+'`' for n in d['unchecked'])}. "
          "Listed here rather than omitted — an unchecked set is not a passing one.")
        w("")

    c = g["cross_store"]
    w("### Present in one store only")
    w("")
    w(f"*Rule: {c['rule']}.* {c['both']} continent names appear in both.")
    w("")
    w(f"- bucket only: {', '.join('`'+x+'`' for x in c['bucket_only']) or 'none'}")
    w(f"- tree only: {', '.join('`'+x+'`' for x in c['tree_only']) or 'none'}")
    w("")
    w(f"⚠️ {c['note']}.")
    w("")

    v = g["vintage_unknown"]
    w("### Extracts that cannot state their own age")
    w("")
    w(f"*Rule: {v['rule']}.*")
    w("")
    w(f"**{v['total']:,}** extracts — " +
      ", ".join(f"{n:,} {t}" for t, n in sorted(v["by_tier"].items())) + ".")
    w("")
    w(f"⚠️ {v['note']}. These cannot be judged stale or fresh by anything; for them")
    w("the write time is the only signal there is.")
    w("")

    s_ = g["stale"]
    w(f"### Stale by data vintage (> {g['stale_days']} days)")
    w("")
    w(f"*Rule: {s_['rule']}.* Counted only where a vintage exists to judge.")
    w("")
    if s_["total"]:
        w("| tier | stale | oldest |")
        w("|---|---:|---:|")
        for r in s_["by_tier"]:
            w(f"| {r['tier']} | {r['count']:,} | {r['max_days']}d |")
    else:
        w("None.")
    w("")

    p = g["cut_from_superseded_parent"]
    w("### Cut from a parent that has since been rebuilt")
    w("")
    w(f"*Rule: {p['rule']}.*")
    w("")
    w("⚠️ This is a correctness property, not cosmetics: `BuildAdminSet` cuts")
    w("children **from the bucket**, so a child older than its parent was derived")
    w("from data that parent no longer contains — and it carries a write time that")
    w("says nothing about it.")
    w("")
    if p["total"]:
        w(f"**{p['total']:,}** extracts, under:")
        w("")
        w("| parent | children behind | worst |")
        w("|---|---:|---:|")
        for r in p["by_parent"][:20]:
            w(f"| `{r['parent']}` | {r['children']:,} | {r['max_days']}d |")
    else:
        w("None.")
    w("")

    u = g["us_counties"]
    w("### US county tier vs Census")
    w("")
    if "unverified" in u:
        w(f"**Could not verify** — {u['unverified']}. Not reported as complete.")
    else:
        w(f"*Rule: {u['rule']}.*")
        w("")
        w(f"{u['ours_total']:,} extracts across {u['states_covered']}/{u['census_states']} "
          f"states; Census lists {u['census_total']:,} county-equivalents.")
        w("")
        w(f"⚠️ {u['note']}.")
        w("")
        if u["empty_states"]:
            w(f"**States with no counties at all: {', '.join(u['empty_states'])}** — "
              "the fan-out never reached them.")
            w("")
        if u["differs"]:
            w("| state | Census | ours | delta |")
            w("|---|---:|---:|---:|")
            for r in sorted(u["differs"], key=lambda r: r["delta"])[:60]:
                w(f"| {r['state']} | {r['census']:,} | {r['ours']:,} | {r['delta']:+d} |")
        else:
            w("Every state matches the Census count exactly.")
    w("")

    pw = g["parent_without_extract"]
    w("### Regions with children but no extract of their own")
    w("")
    w(f"*Rule: {pw['rule']}.*")
    w("")
    if pw["regions"]:
        w(", ".join("`" + r + "`" for r in pw["regions"]) + ".")
        w("")
        w(f"⚠️ {pw['note']}.")
    else:
        w("None.")
    w("")

    n = g["not_attempted"]
    w("### Not attempted (not a defect)")
    w("")
    w(f"*Rule: {n['rule']}.*")
    w("")
    w(f"**{n['count']:,}** of the country extracts have no sub-region tier because")
    w("no set covers them. They are listed here so \"absent\" is never read as")
    w("\"lost\" — deliberate scope and a failure look identical in a bare file listing.")
    w("")
    return "\n".join(L) + "\n"


def render_html(md: str, rep: dict) -> str:
    """Minimal Markdown → HTML. Deliberately not a dependency.

    ⚠️ Everything interpolated here comes from OUR OWN stores (region names,
    counts, timestamps), but a region name is ultimately an OSM string, so it
    is escaped. A status page that can be made to render someone else's markup
    is a status page that can lie.
    """
    import html as _h

    def inline(s):
        s = _h.escape(s)
        s = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", s)
        s = re.sub(r"(?<!\*)\*([^*]+?)\*(?!\*)", r"<em>\1</em>", s)
        return re.sub(r"`([^`]+?)`", r"<code>\1</code>", s)

    out, in_tbl, in_quote = [], False, False
    for line in md.splitlines():
        if line.startswith("|"):
            cells = [c.strip() for c in line.strip("|").split("|")]
            if set("".join(cells)) <= set("-: "):
                continue
            if not in_tbl:
                out.append("<table>")
                in_tbl = True
                out.append("<tr>" + "".join(f"<th>{inline(c)}</th>" for c in cells) + "</tr>")
                continue
            out.append("<tr>" + "".join(f"<td>{inline(c)}</td>" for c in cells) + "</tr>")
            continue
        if in_tbl:
            out.append("</table>")
            in_tbl = False
        if line.startswith("> "):
            if not in_quote:
                out.append("<blockquote>")
                in_quote = True
            out.append(inline(line[2:]) + " ")
            continue
        if in_quote:
            out.append("</blockquote>")
            in_quote = False
        if m := re.match(r"^(#{1,3}) (.*)", line):
            out.append(f"<h{len(m.group(1))}>{inline(m.group(2))}</h{len(m.group(1))}>")
        elif line.startswith("- "):
            out.append(f"<ul><li>{inline(line[2:])}</li></ul>")
        elif line.strip():
            out.append(f"<p>{inline(line)}</p>")
    if in_tbl:
        out.append("</table>")
    if in_quote:
        out.append("</blockquote>")

    css = ("body{font-family:system-ui,-apple-system,sans-serif;max-width:60rem;"
           "margin:2.5rem auto;padding:0 1.2rem;line-height:1.55;color:#1a1a1a}"
           "h1{font-size:1.55rem;margin-bottom:.2rem}h2{font-size:1.15rem;"
           "margin:2rem 0 .4rem;border-bottom:1px solid #e3e3e3;padding-bottom:.25rem}"
           "h3{font-size:1rem;margin:1.4rem 0 .3rem}"
           "table{border-collapse:collapse;margin:.6rem 0 1rem;font-size:.9rem;"
           "display:block;overflow-x:auto;max-width:100%}"
           "th,td{border:1px solid #ddd;padding:.32rem .6rem;text-align:left;"
           "white-space:nowrap}th{background:#f6f6f6;font-weight:600}"
           "tr:nth-child(even) td{background:#fafafa}"
           "code{background:#f2f2f2;padding:.08rem .3rem;border-radius:3px;"
           "font-size:.88em}ul{margin:.2rem 0 .6rem 1.2rem}"
           "blockquote{margin:.8rem 0;padding:.5rem .9rem;border-left:3px solid #c9c9c9;"
           "background:#fafafa;color:#444;font-size:.93rem}"
           "p{margin:.45rem 0}"
           "@media(prefers-color-scheme:dark){body{background:#151515;color:#e6e6e6}"
           "h2{border-color:#333}th{background:#232323}tr:nth-child(even) td{background:#1c1c1c}"
           "th,td{border-color:#333}code{background:#242424}"
           "blockquote{background:#1c1c1c;border-color:#3a3a3a;color:#bbb}}")
    return (f"<!doctype html>\n<html lang=\"en\"><head><meta charset=\"utf-8\">\n"
            f"<meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">\n"
            f"<title>OSM extract store status</title>\n<style>{css}</style>\n"
            f"</head><body>\n" + "\n".join(out) +
            f"\n<p style=\"color:#888;font-size:.82rem;margin-top:2rem\">"
            f"Generated by <code>fw svc osm-report</code> at {rep['generated_at']}."
            f"</p>\n</body></html>\n")


def _jsonable(o):
    if isinstance(o, dt.datetime):
        return o.isoformat()
    raise TypeError(type(o).__name__)


def build(*, endpoint: str, bucket: str, access_key: str, secret_key: str,
          tree_url: str, sets_file: str, county_csv: str | None,
          stale_days: int) -> dict:
    now = dt.datetime.now(UTC)

    b = survey_bucket(endpoint, bucket, access_key=access_key, secret_key=secret_key)
    try:
        t = survey_tree(tree_url)
    except Exception as exc:                            # noqa: BLE001
        t = {"kind": "tree", "name": tree_url, "objects": 0, "bytes": 0,
             "extracts": {}, "error": type(exc).__name__}

    county_ref = None
    if county_csv:
        try:
            county_ref = load_census_counties(ensure_county_csv(county_csv))
        except Exception as exc:                        # noqa: BLE001
            # Deliberately not fatal and deliberately not silent: the check is
            # skipped and SAYS it was skipped. Reporting "every state matches"
            # because the reference could not be read is the failure mode this
            # whole report exists to make impossible.
            county_ref = None
            print(f"  census county reference unavailable ({type(exc).__name__}); "
                  f"county completeness NOT checked", flush=True)

    gaps = find_gaps(b, t, load_sets(sets_file), now=now, stale_days=stale_days,
                     county_reference=county_ref)

    # continent cross-table: the same row from both stores side by side, joined
    # on the SHARED SUFFIX, because "australia-oceania" and "oceania" are the
    # same continent under two names.
    tree_by_suffix = {}
    for r, e in t["extracts"].items():
        if e["tier"] == "continent":
            tree_by_suffix[r] = e
    rows = []
    for region, e in sorted(b["extracts"].items()):
        if e["tier"] != "continent":
            continue
        te = tree_by_suffix.get(region)
        if te is None:
            for name, cand in tree_by_suffix.items():
                if region.endswith(name) or name.endswith(region):
                    te = cand
                    break
        rows.append({
            "region": region, "bytes": e["bytes"], "sequence": e.get("sequence"),
            "vintage": e["vintage"].strftime("%Y-%m-%d") if e.get("vintage") else None,
            "tree_name": te["region"] if te else None,
            "tree_sequence": te.get("sequence") if te else None,
            "tree_vintage": (te["vintage"].strftime("%Y-%m-%d")
                             if te and te.get("vintage") else None),
            "countries": sum(1 for r in b["extracts"]
                             if r.startswith(region + "/") and r.count("/") == 1),
            "descendants": sum(1 for r in b["extracts"] if r.startswith(region + "/")),
        })

    stores = []
    for s, label, holds in ((b, "MinIO bucket", "8 continents + the country / state / county tiers"),
                            (t, "Served tree (:8088)", "continents only — what stamped PBF headers point at")):
        stores.append({"name": s["name"], "label": label, "holds": holds,
                       "kind": s["kind"], "objects": s["objects"], "bytes": s["bytes"],
                       "error": s.get("error"),
                       "tiers": summarise_store(s, now), "extracts": s["extracts"]})

    return {"generated_at": now.isoformat(), "generated_on": os.uname().nodename,
            "stores": stores, "continents": rows, "gaps": gaps}


CENSUS_COUNTY_URL = ("https://www2.census.gov/programs-surveys/popest/datasets/"
                     "2020-2023/counties/totals/co-est2023-alldata.csv")


def ensure_county_csv(path: str) -> str:
    """Cache the Census county list locally; fetch it once if absent.

    The file is an ENUMERATION, not a measurement — which counties exist, not
    how many people are in them — so a cached copy does not go stale in any way
    that matters here, and re-fetching it on every report would put a network
    dependency in the middle of a status page.
    """
    if os.path.exists(path) and os.path.getsize(path) > 100_000:
        return path
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    tmp = path + ".tmp"
    with urllib.request.urlopen(CENSUS_COUNTY_URL, timeout=60) as r, open(tmp, "wb") as fh:
        fh.write(r.read())
    os.replace(tmp, path)
    return path


def load_census_counties(path: str) -> dict:
    """SUMLEV 050 rows of the Census PEP county file, counted per state.

    State names are lowercased and hyphenated to match the extract keys
    (`north-america/us/new-hampshire/...`).
    """
    import csv
    counts: dict[str, int] = {}
    with open(path, newline="", encoding="utf-8-sig", errors="replace") as fh:
        for row in csv.DictReader(fh):
            if (row.get("SUMLEV") or "").strip() != "050":
                continue
            key = (row.get("STNAME") or "").strip().lower().replace(" ", "-")
            if key:
                counts[key] = counts.get(key, 0) + 1
    return counts


# --------------------------------------------------------------------------

def main(argv=None) -> int:
    """Exit 0 = report written. Exit 2 = could not survey.

    ⚠️ There is deliberately no exit 1. This describes; it does not alarm.
    `fw svc osm-watchdog` alarms, and it stays trivial precisely so that the
    thing whose job is to notice breakage has almost nothing in it to break.
    A report that also alarmed would give two jobs to one process and make the
    alarm as fragile as the renderer.
    """
    import argparse

    ap = argparse.ArgumentParser(prog="fw svc osm-report", description=__doc__)
    ap.add_argument("--endpoint", default=os.environ.get("FW_S3_ENDPOINT",
                                                         "http://afl-minio:9000"))
    ap.add_argument("--bucket", default=os.environ.get("FW_OSM_EXTRACT_BUCKET",
                                                       "osm-extracts"))
    ap.add_argument("--tree-url", default=os.environ.get("FW_OSM_SELFHOST_BASE_URL",
                                                         "http://server3.local:8088"))
    ap.add_argument("--sets-file", default=os.path.join(os.path.dirname(
        os.path.abspath(__file__)), "_osm-admin-sets.json"))
    ap.add_argument("--county-csv", default=os.environ.get(
        "FW_OSM_REPORT_COUNTY_CSV",
        os.path.expanduser("~/.facetwork/census-counties.csv")))
    ap.add_argument("--stale-days", type=int, default=14)
    ap.add_argument("--out-dir", default=os.path.expanduser("~/.facetwork"))
    ap.add_argument("--publish", action="store_true",
                    help="also write the report into the extract bucket")
    ap.add_argument("--tree-dir", default=os.environ.get("FW_OSM_SELFHOST_WWW"),
                    help="also drop the report into the served tree, so it is "
                         "browsable at <tree-url>/_report/ alongside the extracts "
                         "it describes (only useful on the host that owns the tree)")
    ap.add_argument("--json", action="store_true", help="print the JSON to stdout")
    a = ap.parse_args(argv)

    try:
        rep = build(endpoint=a.endpoint, bucket=a.bucket,
                    access_key=os.environ.get("FW_S3_ACCESS_KEY",
                                              os.environ.get("AWS_ACCESS_KEY_ID", "minioadmin")),
                    secret_key=os.environ.get("FW_S3_SECRET_KEY",
                                              os.environ.get("AWS_SECRET_ACCESS_KEY", "minioadmin")),
                    tree_url=a.tree_url, sets_file=a.sets_file,
                    county_csv=a.county_csv, stale_days=a.stale_days)
    except Exception as exc:                            # noqa: BLE001
        print(f"could not survey {a.bucket} @ {a.endpoint}: "
              f"{type(exc).__name__}: {exc}", flush=True)
        return 2                                        # could-not-verify, not "healthy"

    md = render_markdown(rep)
    os.makedirs(a.out_dir, exist_ok=True)
    slim = json.loads(json.dumps(rep, default=_jsonable))
    for s in slim["stores"]:                            # the per-extract dump is
        s.pop("extracts", None)                         # ~4k rows; keep the JSON usable
    paths = {
        "md": os.path.join(a.out_dir, "osm-cache-report.md"),
        "html": os.path.join(a.out_dir, "osm-cache-report.html"),
        "json": os.path.join(a.out_dir, "osm-cache-report.json"),
    }
    with open(paths["md"], "w") as fh:
        fh.write(md)
    with open(paths["html"], "w") as fh:
        fh.write(render_html(md, rep))
    with open(paths["json"], "w") as fh:
        json.dump(slim, fh, indent=2)

    if a.publish:
        import boto3
        import botocore.config
        s3 = boto3.client(
            "s3", endpoint_url=a.endpoint,
            aws_access_key_id=os.environ.get("FW_S3_ACCESS_KEY",
                                             os.environ.get("AWS_ACCESS_KEY_ID", "minioadmin")),
            aws_secret_access_key=os.environ.get("FW_S3_SECRET_KEY",
                                                 os.environ.get("AWS_SECRET_ACCESS_KEY", "minioadmin")),
            config=botocore.config.Config(read_timeout=60, retries={"max_attempts": 3}))
        # Under _report/ so it can never collide with a region key: every
        # extract key is <region>-latest.osm.pbf at the bucket root.
        for name, body, ctype in (
                ("_report/index.html", render_html(md, rep), "text/html; charset=utf-8"),
                ("_report/report.md", md, "text/markdown; charset=utf-8"),
                ("_report/status.json", json.dumps(slim, indent=2), "application/json")):
            s3.put_object(Bucket=a.bucket, Key=name,
                          Body=body.encode(), ContentType=ctype,
                          CacheControl="no-cache")
        print(f"published to s3://{a.bucket}/_report/")

    if a.tree_dir:
        # ⚠️ Written into the tree that is being SERVED, so write to a temp name
        # and rename: a reader hitting the file mid-write gets the old page, not
        # half a page. Same reason the extracts themselves are staged.
        try:
            d = os.path.join(a.tree_dir, "_report")
            os.makedirs(d, exist_ok=True)
            for name, body in (("index.html", render_html(md, rep)),
                               ("report.md", md),
                               ("status.json", json.dumps(slim, indent=2))):
                tmp = os.path.join(d, name + ".tmp")
                with open(tmp, "w") as fh:
                    fh.write(body)
                os.replace(tmp, os.path.join(d, name))
            print(f"wrote {d}/")
        except OSError as exc:
            print(f"could not write tree report ({exc}) — other outputs still written")

    if a.json:
        print(json.dumps(slim, indent=2))
    else:
        g = rep["gaps"]
        print(f"wrote {paths['md']}")
        print(f"      {paths['html']}")
        print(f"      {paths['json']}")
        tot = sum(len(s["extracts"]) for s in rep["stores"])
        print(f"  {tot:,} extracts surveyed across {len(rep['stores'])} stores")
        print(f"  {g['vintage_unknown']['total']:,} cannot state their data vintage")
        print(f"  {g['stale']['total']:,} stale (> {g['stale_days']}d)")
        print(f"  {g['cut_from_superseded_parent']['total']:,} cut from a superseded parent")
        short = [s for s in g["declared_vs_actual"]["sets"] if s["shortfall"]]
        print(f"  {len(short)} set(s) short of their declared size")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
