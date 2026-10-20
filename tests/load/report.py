#!/usr/bin/env python3
"""Turn one load-test run into a self-contained HTML report, and refresh the index of runs.

    python3 tests/load/report.py tests/load/results/<run>
    python3 tests/load/report.py --export-passed reports-test   # only passed reports, to share

Reads what tests/load/run.sh leaves in the run folder:
  meta.json          test settings and outcome (written by run.sh)
  summary.json       k6 end-of-test summary (custom counters, thresholds)
  samples.csv.gz     every k6 metric sample (one row per request and metric)
  docker-stats.csv   container CPU and memory, sampled during the test
Writes report.html and report-data.json in the run folder, and results/index.html.
Standard library only; the page has no external scripts, fonts or images.
"""

from __future__ import annotations

import csv
import gzip
import html
import json
import math
import re
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone

UTC = timezone.utc  # datetime.UTC needs Python 3.11; servers may have 3.9
from pathlib import Path
from typing import Any

P95_LIMIT_MS = 1000.0  # "fast enough": 95% of answers within one second
FAIL_LIMIT_PCT = 1.0  # at most 1% of requests may fail
SETUP_NAMES = {"setup"}  # requests made by test setup/teardown, not by the simulated users


# ---- data ---------------------------------------------------------------------------------


def percentile(sorted_values: list[float], p: float) -> float:
    if not sorted_values:
        return 0.0
    rank = max(0, min(len(sorted_values) - 1, math.ceil(p / 100 * len(sorted_values)) - 1))
    return sorted_values[rank]


def load_samples(path: Path) -> tuple[list[dict[str, Any]], list[tuple[float, float]]]:
    """Return (requests, vus series). Each request: t (s since epoch), ms, name, status, ok.

    A million-request run has about 13 million rows, so rows are read with a plain csv.reader
    and skipped by metric name before anything else is parsed.
    """
    opener = gzip.open if path.suffix == ".gz" else open
    requests: list[dict[str, Any]] = []
    vus: list[tuple[float, float]] = []
    with opener(path, "rt", newline="") as fh:  # type: ignore[operator]
        reader = csv.reader(fh)
        header = next(reader, [])
        col = {name: i for i, name in enumerate(header)}
        i_metric, i_t, i_v = col["metric_name"], col["timestamp"], col["metric_value"]
        i_name, i_url = col.get("name"), col.get("url")
        i_status, i_expected = col.get("status"), col.get("expected_response")
        for row in reader:
            metric = row[i_metric]
            if metric != "http_req_duration" and metric != "vus":
                continue
            raw_t = float(row[i_t])
            t = raw_t / 1000 if raw_t > 1e11 else raw_t  # unix_milli or unix seconds
            if metric == "vus":
                vus.append((t, float(row[i_v])))
                continue
            name = (row[i_name] if i_name is not None else "") or (row[i_url] if i_url is not None else "") or "?"
            if name in SETUP_NAMES:
                continue
            requests.append(
                {
                    "t": t,
                    "ms": float(row[i_v]),
                    "name": name,
                    "status": (row[i_status] if i_status is not None else "") or "0",
                    "ok": ((row[i_expected] if i_expected is not None else "") or "true") == "true",
                }
            )
    return requests, vus


def load_docker_stats(path: Path, start: float) -> dict[str, list[tuple[float, float, float]]]:
    """Container name -> [(seconds since start, cpu %, memory MiB)]."""
    units = {"B": 1 / 1048576, "KiB": 1 / 1024, "kB": 1 / 1024, "MiB": 1, "MB": 1, "GiB": 1024, "GB": 1024}
    out: dict[str, list[tuple[float, float, float]]] = defaultdict(list)
    if not path.exists():
        return out
    day = datetime.fromtimestamp(start, UTC).replace(hour=0, minute=0, second=0, microsecond=0).timestamp()
    for row in csv.reader(path.open()):
        if len(row) < 4:
            continue
        stamp, name, cpu, mem = row[:4]
        try:
            h, m, s = (int(x) for x in stamp.split(":"))
        except ValueError:
            continue
        t = day + h * 3600 + m * 60 + s
        if t < start - 43200:
            t += 86400  # crossed midnight
        match = re.match(r"([\d.]+)\s*(\w+)", mem.split("/")[0].strip())
        mib = float(match.group(1)) * units.get(match.group(2), 1) if match else 0.0
        short = re.sub(r"^stagevote-[a-z]+-|-\d+$", "", name)
        out[short].append((t - start, float(cpu.rstrip("%") or 0), mib))
    return out


def analyse(run: Path) -> dict[str, Any]:
    meta = json.loads((run / "meta.json").read_text()) if (run / "meta.json").exists() else {}
    summary = json.loads((run / "summary.json").read_text()) if (run / "summary.json").exists() else {}
    samples = run / "samples.csv.gz"
    if not samples.exists():
        samples = run / "samples.csv"
    requests, vus = load_samples(samples) if samples.exists() else ([], [])
    requests.sort(key=lambda r: r["t"])
    start = requests[0]["t"] - requests[0]["ms"] / 1000 if requests else float(meta.get("started_epoch", 0))
    end = requests[-1]["t"] if requests else float(meta.get("ended_epoch", start))
    active = max(end - start, 0.001)

    durations = sorted(r["ms"] for r in requests)
    failed = [r for r in requests if not r["ok"]]
    statuses = Counter(r["status"] for r in requests)

    per_second: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for r in requests:
        per_second[int(r["t"] - start)].append(r)
    seconds = range(0, int(active) + 1)
    timeline = []
    for s in seconds:
        bucket = per_second.get(s, [])
        ms = sorted(r["ms"] for r in bucket)
        timeline.append(
            {
                "s": s,
                "count": len(bucket),
                "failed": sum(1 for r in bucket if not r["ok"]),
                "p50": percentile(ms, 50),
                "p95": percentile(ms, 95),
            }
        )
    vus_series: dict[int, float] = {}
    for t, value in vus:
        vus_series[int(t - start)] = max(vus_series.get(int(t - start), 0), value)

    endpoints = []
    by_name: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for r in requests:
        by_name[r["name"]].append(r)
    for name, rows in sorted(by_name.items(), key=lambda kv: -len(kv[1])):
        ms = sorted(r["ms"] for r in rows)
        endpoints.append(
            {
                "name": name,
                "count": len(rows),
                "failed": sum(1 for r in rows if not r["ok"]),
                "p50": percentile(ms, 50),
                "p95": percentile(ms, 95),
                "p99": percentile(ms, 99),
                "max": ms[-1] if ms else 0,
                "statuses": dict(Counter(r["status"] for r in rows)),
            }
        )

    metrics = summary.get("metrics", {})

    def count(name: str) -> int:
        return int(metrics.get(name, {}).get("values", {}).get("count", 0) or 0)

    thresholds = []
    for name, m in metrics.items():
        for rule, verdict in (m.get("thresholds") or {}).items():
            thresholds.append({"metric": name, "rule": rule, "ok": bool(verdict.get("ok"))})

    resources = load_docker_stats(run / "docker-stats.csv", start)
    total = len(requests)
    fail_pct = 100 * len(failed) / total if total else 0.0
    p95 = percentile(durations, 95)
    checks = [
        {
            "label": f"Failed requests below {FAIL_LIMIT_PCT:g}%",
            "ok": total > 0 and fail_pct < FAIL_LIMIT_PCT,
            "value": f"{fail_pct:.2f}% ({len(failed):,} of {total:,})",
        },
        {
            "label": f"95% of answers within {P95_LIMIT_MS:,.0f} ms",
            "ok": total > 0 and p95 < P95_LIMIT_MS,
            "value": f"{p95:,.0f} ms",
        },
        {
            "label": "No unexpected errors reported by the test",
            "ok": count("unexpected_errors") == 0,
            "value": f"{count('unexpected_errors'):,}",
        },
    ]
    if meta.get("acknowledged_votes") is not None and meta.get("stored_votes") is not None:
        checks.append(
            {
                "label": "Every accepted vote is stored in the database (no lost or extra votes)",
                "ok": str(meta["acknowledged_votes"]) == str(meta["stored_votes"]),
                "value": f"{int(meta['acknowledged_votes']):,} accepted, {int(meta['stored_votes']):,} stored",
            }
        )
    if meta.get("expected_requests"):
        checks.append(
            {
                "label": "All planned requests were sent",
                "ok": total >= int(meta["expected_requests"]),
                "value": f"{total:,} of {int(meta['expected_requests']):,}",
            }
        )

    return {
        "run": run.name,
        "meta": meta,
        "start": start,
        "duration": active,
        "total": total,
        "failed": len(failed),
        "fail_pct": fail_pct,
        "rps_avg": total / active if total else 0,
        "rps_peak": max((b["count"] for b in timeline), default=0),
        # Most the server kept up for 5 seconds in a row: its throughput when fully busy.
        "rps_sustained": max(
            (sum(b["count"] for b in timeline[i : i + 5]) / 5 for i in range(max(len(timeline) - 5, 0))),
            default=total / active if total else 0,
        ),
        "p50": percentile(durations, 50),
        "p90": percentile(durations, 90),
        "p95": p95,
        "p99": percentile(durations, 99),
        "max": durations[-1] if durations else 0,
        "min": durations[0] if durations else 0,
        "statuses": dict(statuses),
        "timeline": timeline,
        "vus": [vus_series.get(s, 0) for s in seconds],
        "endpoints": endpoints,
        "thresholds": thresholds,
        "checks": checks,
        "passed": all(c["ok"] for c in checks),
        "resources": resources,
        "counters": {
            k: count(k)
            for k in ("votes_accepted", "ballots_confirmed", "unexpected_errors", "receipt_mismatches", "sockets_opened")
            if k in metrics
        },
    }


# ---- page ---------------------------------------------------------------------------------

E = html.escape


def nice_ceiling(value: float) -> float:
    if value <= 0:
        return 1
    exp = 10 ** math.floor(math.log10(value))
    for step in (1, 2, 2.5, 5, 10):
        if value <= step * exp:
            return step * exp
    return 10 * exp


def fmt_ms(ms: float) -> str:
    return f"{ms / 1000:.2f} s" if ms >= 1000 else f"{ms:.0f} ms" if ms >= 10 else f"{ms:.1f} ms"


def fmt_duration(seconds: float) -> str:
    seconds = round(seconds)
    return f"{seconds // 60} min {seconds % 60} s" if seconds >= 60 else f"{seconds} s"


def line_chart(
    series: list[tuple[str, str, list[float]]], *, label: str, unit: str, fmt: Any = None, step_x: float = 1
) -> str:
    """Inline SVG line chart. series: (name, css colour variable, values per x step)."""
    width, height = 760, 230
    left, right, top, bottom = 58, 14, 12, 30
    n = max((len(v) for _, _, v in series), default=0)
    if n < 2:
        return '<p class="muted">Not enough samples to draw this chart.</p>'
    y_max = nice_ceiling(max((max(v) for _, _, v in series if v), default=1) * 1.08)
    fmt = fmt or (lambda v: f"{v:,.0f}")

    def sx(i: float) -> float:
        return left + (width - left - right) * i / (n - 1)

    def sy(v: float) -> float:
        return top + (height - top - bottom) * (1 - v / y_max)

    parts = [
        f'<svg viewBox="0 0 {width} {height}" class="chart" role="img" aria-label="{E(label)}">',
    ]
    for k in range(5):
        v = y_max * k / 4
        y = sy(v)
        parts.append(f'<line x1="{left}" x2="{width - right}" y1="{y:.1f}" y2="{y:.1f}" class="grid"/>')
        parts.append(f'<text x="{left - 8}" y="{y + 4:.1f}" class="tick" text-anchor="end">{E(fmt(v))}</text>')
    total_x = (n - 1) * step_x
    tick = nice_ceiling(total_x / 6) or 1
    x = 0.0
    while x <= total_x + 1e-9:
        px = sx(x / step_x)
        parts.append(f'<line x1="{px:.1f}" x2="{px:.1f}" y1="{top}" y2="{height - bottom}" class="grid faint"/>')
        parts.append(f'<text x="{px:.1f}" y="{height - 10}" class="tick" text-anchor="middle">{x:,.0f}s</text>')
        x += tick
    for name, colour, values in series:
        if not values:
            continue
        points = " ".join(f"{sx(i):.1f},{sy(v):.1f}" for i, v in enumerate(values))
        parts.append(
            f'<polyline points="{points}" fill="none" stroke="var({colour})" stroke-width="2" '
            f'stroke-linejoin="round" stroke-linecap="round"><title>{E(name)}</title></polyline>'
        )
    parts.append("</svg>")
    legend = "".join(
        f'<span class="key"><i style="background:var({colour})"></i>{E(name)}</span>' for name, colour, _ in series
    )
    return f'<figure class="figure">{"".join(parts)}<figcaption>{legend}<span class="unit">{E(unit)}</span></figcaption></figure>'


def resample(values: list[float], buckets: int = 240) -> tuple[list[float], float]:
    """Average into at most `buckets` points so long tests stay readable; returns (values, step)."""
    if len(values) <= buckets:
        return values, 1
    size = math.ceil(len(values) / buckets)
    return [sum(values[i : i + size]) / len(values[i : i + size]) for i in range(0, len(values), size)], size


def peak_resample(values: list[float], buckets: int = 240) -> tuple[list[float], float]:
    if len(values) <= buckets:
        return values, 1
    size = math.ceil(len(values) / buckets)
    return [max(values[i : i + size]) for i in range(0, len(values), size)], size


CSS = """
:root{--bg:#f7f2ea;--card:#fffdf8;--ink:#2b1631;--soft:#6b5670;--line:#eadfcf;--faint:#f1e9dc;
--ok:#1f7a4d;--ok-bg:#e6f4ec;--bad:#b3261e;--bad-bg:#fbe9e7;--warn:#8a5a00;
--c1:#5b34c4;--c2:#e0702a;--c3:#1f8a7a;--c4:#b02a55;--c5:#3b6fd8;--c6:#8a8a12;color-scheme:light}
@media (prefers-color-scheme:dark){:root:not([data-theme=light]){--bg:#17111a;--card:#211a25;--ink:#f3ecf5;
--soft:#b9a9bf;--line:#3a2f40;--faint:#2b2330;--ok:#5fd39b;--ok-bg:#16302a;--bad:#ff8a80;--bad-bg:#3a1c1c;
--warn:#f0c060;--c1:#a98bff;--c2:#ff9d5c;--c3:#4fd1bd;--c4:#ff7aa2;--c5:#7aa7ff;--c6:#d4d45a;color-scheme:dark}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);
font:16px/1.55 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Helvetica,Arial,sans-serif}
main{max-width:1100px;margin:0 auto;padding:28px 16px 64px}
h1{font-size:clamp(1.6rem,4vw,2.3rem);line-height:1.15;margin:0 0 4px}
h2{font-size:1.2rem;margin:0 0 4px}h3{font-size:1rem;margin:18px 0 6px}
.muted{color:var(--soft)}.small{font-size:.875rem}
.card{background:var(--card);border:1px solid var(--line);border-radius:20px;padding:20px;margin-top:18px}
.verdict{border-width:2px}.verdict.ok{border-color:var(--ok);background:var(--ok-bg)}
.verdict.bad{border-color:var(--bad);background:var(--bad-bg)}
.verdict .big{font-size:1.35rem;font-weight:700;margin:0 0 6px}
.checks{list-style:none;padding:0;margin:12px 0 0;display:grid;gap:8px}
.checks li{display:flex;gap:10px;align-items:flex-start}
.mark{flex:none;width:22px;height:22px;border-radius:50%;display:inline-flex;align-items:center;
justify-content:center;font-weight:700;font-size:13px;color:#fff;margin-top:1px}
.mark.ok{background:var(--ok)}.mark.bad{background:var(--bad)}
.grid4{display:grid;gap:12px;grid-template-columns:repeat(auto-fit,minmax(170px,1fr));margin-top:14px}
.stat{background:var(--card);border:1px solid var(--line);border-radius:16px;padding:14px 16px}
.stat .v{font-size:1.6rem;font-weight:700;font-variant-numeric:tabular-nums;line-height:1.2}
.stat .l{font-size:.85rem;color:var(--soft)}.stat .h{font-size:.8rem;color:var(--soft);margin-top:4px}
.figure{margin:10px 0 0}.chart{width:100%;height:auto;display:block}
.chart .grid{stroke:var(--line);stroke-width:1}.chart .faint{stroke:var(--faint)}
.chart .tick{fill:var(--soft);font-size:11px;font-variant-numeric:tabular-nums}
figcaption{display:flex;flex-wrap:wrap;gap:6px 16px;font-size:.85rem;color:var(--soft);margin-top:6px}
.key{display:inline-flex;align-items:center;gap:6px}.key i{width:14px;height:4px;border-radius:2px;display:inline-block}
.unit{margin-left:auto}
.table{width:100%;border-collapse:collapse;font-size:.92rem;font-variant-numeric:tabular-nums}
.table th,.table td{padding:9px 10px;border-bottom:1px solid var(--line);text-align:right;white-space:nowrap}
.table th:first-child,.table td:first-child{text-align:left;white-space:normal}
.table thead th{color:var(--soft);font-weight:600;font-size:.82rem}
.scroll{overflow-x:auto;margin-top:8px}
.bar{display:inline-block;height:8px;border-radius:4px;background:var(--c1);vertical-align:middle;margin-left:8px}
.pill{display:inline-block;padding:1px 9px;border-radius:999px;font-size:.8rem;font-weight:600}
.pill.ok{background:var(--ok-bg);color:var(--ok)}.pill.bad{background:var(--bad-bg);color:var(--bad)}
dl.glossary{display:grid;grid-template-columns:minmax(120px,max-content) 1fr;gap:8px 16px;margin:10px 0 0}
dl.glossary dt{font-weight:600}dl.glossary dd{margin:0;color:var(--soft)}
.two{display:grid;gap:18px;grid-template-columns:1fr}@media(min-width:900px){.two{grid-template-columns:1fr 1fr}}
code{font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;font-size:.9em}
a{color:var(--c1)}
"""


def stat(value: str, label: str, hint: str = "") -> str:
    return f'<div class="stat"><div class="v">{E(value)}</div><div class="l">{E(label)}</div>' + (
        f'<div class="h">{E(hint)}</div>' if hint else ""
    ) + "</div>"


def render(a: dict[str, Any]) -> str:
    meta = a["meta"]
    when = datetime.fromtimestamp(a["start"], UTC).strftime("%d %b %Y, %H:%M UTC") if a["start"] else a["run"]
    users = meta.get("users") or meta.get("voters") or "?"
    mode = meta.get("mode", "event")
    title_mode = "API stress test" if mode == "api" else "Live event simulation"
    ok = a["passed"]
    if a["total"] == 0:
        sentence = "The test sent no requests. Check k6.log in this folder."
    else:
        sentence = (
            f"{'It handled' if ok else 'It struggled with'} {users} users sending {a['total']:,} requests in "
            f"{fmt_duration(a['duration'])}: {a['rps_avg']:,.0f} requests per second on average. "
            f"{a['failed']:,} failed ({a['fail_pct']:.2f}%). Half of all answers came within {fmt_ms(a['p50'])}, "
            f"and 95% within {fmt_ms(a['p95'])}."
        )
    failing = {c["label"] for c in a["checks"] if not c["ok"]}
    if ok:
        headline = "Passed: the server handled this load"
    elif failing and all(label.startswith("95% of answers") for label in failing):
        headline = "Every request was answered, but too slowly: 1 in 20 requests took over 1 second"
    else:
        headline = "Problems found under this load"
    context = ""
    if mode == "api":
        think = int(meta.get("think_ms") or 0)
        pace = (
            "Every user sent the next request the moment the previous answer arrived, with no pause. "
            "Real people stop to read and tap, so this is a worst-case stress test: it measures the most "
            "the server can answer, and waiting times grow because requests queue up."
            if think == 0
            else f"Every user paused {think:,} ms after each answer before sending the next request."
        )
        context = (
            f'<p class="small">{E(pace)} At full load the server kept up {a["rps_sustained"]:,.0f} requests per '
            "second. For comparison, in the realistic live-event simulation (run.sh without --requests) 500 "
            "voters averaged about 32 requests per second, with short bursts when a round opens.</p>"
        )
    checks = "".join(
        f'<li><span class="mark {"ok" if c["ok"] else "bad"}" aria-hidden="true">{"✓" if c["ok"] else "✕"}</span>'
        f'<span><strong>{E(c["label"])}</strong><br><span class="muted small">{E(c["value"])}</span>'
        f'<span class="sr-only"> {"passed" if c["ok"] else "failed"}</span></span></li>'
        for c in a["checks"]
    )

    cards = "".join(
        [
            stat(f"{users}", "Users at the same time", "each signs in, then keeps sending requests"),
            stat(f"{a['total']:,}", "Requests answered", f"in {fmt_duration(a['duration'])}"),
            stat(f"{a['rps_avg']:,.0f}/s", "Average speed", "requests per second"),
            stat(f"{a['rps_peak']:,}/s", "Busiest second", "most requests in one second"),
            stat(f"{a['rps_sustained']:,.0f}/s", "Capacity", "most kept up for 5 seconds"),
            stat(f"{a['failed']:,}", "Failed requests", f"{a['fail_pct']:.2f}% of all"),
            stat(fmt_ms(a["p50"]), "Typical answer (p50)", "half of answers were faster"),
            stat(fmt_ms(a["p95"]), "95% answered within", "only 1 in 20 was slower"),
            stat(fmt_ms(a["p99"]), "99% answered within", "only 1 in 100 was slower"),
            stat(fmt_ms(a["max"]), "Slowest answer", "the single worst request"),
        ]
    )
    for key, label in (("votes_accepted", "Votes accepted"), ("ballots_confirmed", "Votes confirmed")):
        if key in a["counters"]:
            cards += stat(f"{a['counters'][key]:,}", label, "checked against the database")

    tl = a["timeline"]
    rps, step = resample([b["count"] for b in tl])
    fails, _ = resample([b["failed"] for b in tl])
    p50s, _ = resample([b["p50"] for b in tl])
    p95s, _ = peak_resample([b["p95"] for b in tl])
    vus, _ = resample(a["vus"])
    charts = [
        (
            "Requests per second",
            "How many requests the server answered each second. A flat line means steady service; "
            "dips with a rising response time mean the server was busy.",
            line_chart(
                [("Answered", "--c1", rps)] + ([("Failed", "--c4", fails)] if a["failed"] else []),
                label="Requests per second over time",
                unit="requests / second",
                step_x=step,
            ),
        ),
        (
            "Response time",
            "How long users waited for an answer, second by second. p95 is the time within which "
            "95% of that second's requests were answered.",
            line_chart(
                [("Typical (p50)", "--c3", p50s), ("95% within (p95)", "--c2", p95s)],
                label="Response time over time",
                unit="milliseconds",
                fmt=lambda v: f"{v:,.0f}",
                step_x=step,
            ),
        ),
        (
            "Active users",
            "How many simulated users were sending requests at each moment.",
            line_chart([("Users", "--c5", vus)], label="Active users over time", unit="users", step_x=step),
        ),
    ]
    chart_html = "".join(
        f'<section class="card"><h2>{E(t)}</h2><p class="muted small">{E(d)}</p>{c}</section>' for t, d, c in charts
    )

    worst = max((e["p95"] for e in a["endpoints"]), default=1) or 1
    rows = "".join(
        f"<tr><td><code>{E(e['name'])}</code></td><td>{e['count']:,}</td>"
        f"<td>{100 * e['count'] / a['total']:.1f}%</td>"
        f"<td>{e['failed']:,}</td><td>{fmt_ms(e['p50'])}</td>"
        f"<td>{fmt_ms(e['p95'])}<span class=\"bar\" style=\"width:{max(4, 70 * e['p95'] / worst):.0f}px\"></span></td>"
        f"<td>{fmt_ms(e['p99'])}</td><td>{fmt_ms(e['max'])}</td>"
        f"<td>{E(', '.join(f'{k}×{v:,}' for k, v in sorted(e['statuses'].items())))}</td></tr>"
        for e in a["endpoints"]
    )
    status_names = {
        "200": "OK",
        "201": "Created (a new vote)",
        "401": "Not signed in",
        "403": "Refused",
        "409": "Conflict (already voted or voting closed: expected)",
        "422": "Invalid request",
        "429": "Too many requests (rate limit)",
        "500": "Server error",
        "502": "Bad gateway (API not reachable)",
        "503": "Service unavailable",
        "504": "Gateway timeout",
        "0": "No answer (timeout or connection closed)",
    }
    status_rows = "".join(
        f"<tr><td><code>{E(code)}</code> {E(status_names.get(code, ''))}</td><td>{n:,}</td>"
        f"<td>{100 * n / a['total']:.1f}%</td></tr>"
        for code, n in sorted(a["statuses"].items(), key=lambda kv: -kv[1])
    )

    res = a["resources"]
    if res:
        peak_rows = "".join(
            f"<tr><td>{E(name)}</td><td>{max(p[1] for p in pts):,.0f}%</td>"
            f"<td>{sum(p[1] for p in pts) / len(pts):,.0f}%</td><td>{max(p[2] for p in pts):,.0f} MiB</td></tr>"
            for name, pts in sorted(res.items())
        )
        palette = ["--c1", "--c2", "--c3", "--c4", "--c5", "--c6"]

        def series_of(index: int) -> list[tuple[str, str, list[float]]]:
            out = []
            span = int(a["duration"]) + 1
            for k, (name, pts) in enumerate(sorted(res.items())):
                grid = [0.0] * span
                last = 0.0
                filled = {int(t): v for t, *vals in pts for v in [vals[index]] if 0 <= t < span}
                for s in range(span):
                    last = filled.get(s, last)
                    grid[s] = last
                values, _ = resample(grid)
                out.append((name, palette[k % len(palette)], values))
            return out

        resources_html = (
            '<section class="card"><h2>Server resources</h2><p class="muted small">CPU and memory of each part of '
            "the system, sampled every few seconds. 100% CPU means one processor core fully busy, so 400% means "
            f"four cores. This machine has {E(str(meta.get('host_cpus', '?')))} cores and "
            f"{E(str(meta.get('host_mem', '?')))} of memory.</p>"
            '<div class="scroll"><table class="table"><thead><tr><th>Part</th><th>Peak CPU</th><th>Average CPU</th>'
            f"<th>Peak memory</th></tr></thead><tbody>{peak_rows}</tbody></table></div>"
            "<h3>CPU</h3>"
            + line_chart(series_of(0), label="CPU over time", unit="% of one core", step_x=step)
            + "<h3>Memory</h3>"
            + line_chart(series_of(1), label="Memory over time", unit="MiB", step_x=step)
            + "</section>"
        )
    else:
        resources_html = ""

    settings = {
        "Test type": title_mode,
        "Users at the same time": users,
        "Requests planned": f"{int(meta['expected_requests']):,}" if meta.get("expected_requests") else "set by the event script",
        "Pause between a user's requests": f"{meta.get('think_ms', 0)} ms" if mode == "api" else "realistic (people read, then tap)",
        "API worker processes": meta.get("api_workers", "?"),
        "Machine": f"{meta.get('host_cpus', '?')} CPU cores, {meta.get('host_mem', '?')} memory",
        "Target": "isolated test copy of StageVote with synthetic accounts (never the real site)",
        "Results folder": f"tests/load/results/{a['run']}",
    }
    if mode == "event":
        settings["Rounds"] = meta.get("rounds", "?")
    setting_rows = "".join(f"<tr><td>{E(str(k))}</td><td>{E(str(v))}</td></tr>" for k, v in settings.items())
    threshold_rows = "".join(
        f"<tr><td><code>{E(t['metric'])}</code></td><td><code>{E(t['rule'])}</code></td>"
        f'<td><span class="pill {"ok" if t["ok"] else "bad"}">{"passed" if t["ok"] else "failed"}</span></td></tr>'
        for t in a["thresholds"]
    )

    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>StageVote load report</title><style>{CSS}
.sr-only{{position:absolute;width:1px;height:1px;overflow:hidden;clip:rect(0 0 0 0)}}</style></head>
<body><main>
<p class="muted small"><a href="../index.html">All test runs</a></p>
<h1>StageVote {E(title_mode[0].lower() + title_mode[1:] if mode != "api" else title_mode)}</h1>
<p class="muted">{E(when)} · {E(str(users))} users · {a['total']:,} requests</p>

<section class="card verdict {'ok' if ok else 'bad'}" aria-labelledby="verdict">
<p class="big" id="verdict">{E(headline)}</p>
<p>{E(sentence)}</p>
{context}
<ul class="checks">{checks}</ul>
</section>

<section aria-label="Key numbers"><div class="grid4">{cards}</div></section>

{chart_html}

<section class="card"><h2>Each API call</h2>
<p class="muted small">The same numbers for every kind of request. A slow row shows which part of the system to look at first.</p>
<div class="scroll"><table class="table"><thead><tr><th>Request</th><th>Count</th><th>Share</th><th>Failed</th>
<th>p50</th><th>p95</th><th>p99</th><th>Slowest</th><th>Answers</th></tr></thead><tbody>{rows}</tbody></table></div>
</section>

<section class="card"><h2>Answers by status code</h2>
<div class="scroll"><table class="table"><thead><tr><th>Status</th><th>Count</th><th>Share</th></tr></thead>
<tbody>{status_rows}</tbody></table></div></section>

{resources_html}

<section class="card"><h2>How to read this</h2><dl class="glossary">
<dt>Requests per second</dt><dd>How much work the server finished each second. Higher is better.</dd>
<dt>p50 (typical)</dt><dd>Half of the answers were faster than this.</dd>
<dt>p95</dt><dd>95 of every 100 answers were faster than this. It is the usual measure of how fast a site feels.</dd>
<dt>p99</dt><dd>99 of every 100 answers were faster; the slowest 1% shows the worst moments.</dd>
<dt>Failed</dt><dd>An error answer (500s), no answer at all, or a refused request. A 409 when someone has already voted is expected and is not a failure.</dd>
<dt>Passed</dt><dd>Fewer than {FAIL_LIMIT_PCT:g}% failed, 95% of answers within {P95_LIMIT_MS:,.0f} ms, no unexpected errors, and every accepted vote was found in the database.</dd>
</dl></section>

<section class="card"><h2>Test settings</h2><div class="scroll"><table class="table"><tbody>{setting_rows}</tbody></table></div>
{f'<h3>k6 thresholds</h3><div class="scroll"><table class="table"><thead><tr><th>Metric</th><th>Rule</th><th>Result</th></tr></thead><tbody>{threshold_rows}</tbody></table></div>' if threshold_rows else ''}
</section>
</main></body></html>
"""


def render_index(results: Path, *, passed_only: bool = False) -> str:
    runs = []
    for folder in sorted((p for p in results.iterdir() if p.is_dir()), reverse=True):
        data = folder / "report-data.json"
        if data.exists():
            run = json.loads(data.read_text())
            if run["passed"] or not passed_only:
                runs.append(run)
    rows = "".join(
        f'<tr><td><a href="{E(r["run"])}/report.html">{E(r["when"])}</a></td><td>{E(r["mode"])}</td>'
        f"<td>{E(str(r['users']))}</td><td>{E(str(r.get('pause', '')))}</td><td>{r['total']:,}</td><td>{r['rps_avg']:,.0f}/s</td>"
        f"<td>{fmt_ms(r['p95'])}</td><td>{r['fail_pct']:.2f}%</td>"
        f'<td><span class="pill {"ok" if r["passed"] else "bad"}">{"passed" if r["passed"] else "problems"}</span></td></tr>'
        for r in runs
    )
    body = (
        f'<div class="scroll"><table class="table"><thead><tr><th>When</th><th>Type</th><th>Users</th><th>Pause</th><th>Requests</th>'
        f"<th>Average speed</th><th>p95</th><th>Failed</th><th>Result</th></tr></thead><tbody>{rows}</tbody></table></div>"
        if runs
        else '<p class="muted">No reports yet. Run <code>tests/load/run.sh --yes-synthetic-load --users 500 --requests 10000</code>.</p>'
    )
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>StageVote load tests</title><style>{CSS}</style></head>
<body><main><h1>StageVote load tests</h1>
<p class="muted">{"Load tests that passed" if passed_only else "Every run of <code>tests/load/run.sh</code>"}, newest first. Open one for the full report.</p>
<section class="card">{body}</section></main></body></html>
"""


def export_passed(results: Path, target: Path) -> int:
    """Copy only the passed reports (HTML with its CSS inside) and an index of them to target."""
    import shutil

    target.mkdir(parents=True, exist_ok=True)
    count = 0
    for folder in sorted(p for p in results.iterdir() if p.is_dir()):
        data = folder / "report-data.json"
        if not data.exists() or not (folder / "report.html").exists():
            continue
        if not json.loads(data.read_text())["passed"]:
            continue
        (target / folder.name).mkdir(exist_ok=True)
        shutil.copyfile(folder / "report.html", target / folder.name / "report.html")
        count += 1
    for stale in (p for p in target.iterdir() if p.is_dir()):
        if not (results / stale.name / "report-data.json").exists() or not json.loads(
            (results / stale.name / "report-data.json").read_text()
        )["passed"]:
            shutil.rmtree(stale)  # a run that no longer passes (or was deleted) leaves the export
    (target / "index.html").write_text(render_index(results, passed_only=True), encoding="utf-8")
    print(f"Exported {count} passed report(s) to {target}")
    return 0


def main() -> int:
    if len(sys.argv) == 3 and sys.argv[1] == "--export-passed":
        here = Path(__file__).resolve().parent
        return export_passed(here / "results", Path(sys.argv[2]).resolve())
    if len(sys.argv) != 2:
        print(__doc__)
        return 2
    run = Path(sys.argv[1]).resolve()
    analysis = analyse(run)
    (run / "report.html").write_text(render(analysis), encoding="utf-8")
    meta = analysis["meta"]
    (run / "report-data.json").write_text(
        json.dumps(
            {
                "run": run.name,
                "when": datetime.fromtimestamp(analysis["start"], UTC).strftime("%d %b %Y %H:%M UTC")
                if analysis["start"]
                else run.name,
                "mode": "API stress" if meta.get("mode") == "api" else "Live event",
                "users": meta.get("users") or meta.get("voters") or "?",
                "pause": f"{int(meta.get('think_ms') or 0):,} ms" if meta.get("mode") == "api" else "realistic",
                **{k: analysis[k] for k in ("total", "rps_avg", "p95", "fail_pct", "passed")},
            }
        )
    )
    (run.parent / "index.html").write_text(render_index(run.parent), encoding="utf-8")
    verdict = "PASSED" if analysis["passed"] else "PROBLEMS FOUND"
    print(
        f"{verdict}: {analysis['total']:,} requests in {fmt_duration(analysis['duration'])}, "
        f"{analysis['rps_avg']:,.0f} req/s, p95 {fmt_ms(analysis['p95'])}, {analysis['failed']:,} failed"
    )
    print(f"Report: {run / 'report.html'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
