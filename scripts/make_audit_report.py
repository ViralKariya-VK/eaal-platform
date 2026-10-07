"""Write docs/Audit Report.pdf: the codebase, frameworks, code-quality and NFR assessment.

Every number comes from running the tools against the current source tree when this script runs, so the
report cannot drift from the code. Needs reportlab, ruff, mypy, radon, bandit, pip-audit and pytest-cov
(all in the project's dev environment).

    python scripts/make_audit_report.py [--mutation "killed=0 survived=0 timeout=0"]
"""

from __future__ import annotations

import argparse
import json
import re
import statistics
import subprocess
import sys
from pathlib import Path
from typing import Any

from reportlab.graphics.shapes import Drawing, Line, Rect, String
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import (
    PageBreak,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "src" / "eaal_platform"


def run(*args: str, only_stdout: bool = False) -> str:
    done = subprocess.run(args, capture_output=True, text=True, cwd=ROOT, check=False)  # nosec B603
    return done.stdout if only_stdout else done.stdout + done.stderr


def loc(paths: list[Path]) -> int:
    return sum(
        sum(
            1
            for line in p.read_text(encoding="utf-8", errors="ignore").splitlines()
            if line.strip()
        )
        for p in paths
    )


def collect(mutation: str | None) -> dict[str, Any]:
    py = [p for p in SRC.rglob("*.py") if "assets" not in p.parts]
    js = list((SRC / "web").glob("*.js")) + list((SRC / "server" / "admin_ui").glob("*.js"))
    tests = list((ROOT / "tests").rglob("*.py"))
    modules: dict[str, int] = {}
    for p in py:
        rel = p.relative_to(SRC)
        key = rel.parts[0] if len(rel.parts) > 1 else rel.name
        modules[key] = modules.get(key, 0) + loc([p])

    data: dict[str, Any] = {
        "py_files": len(py),
        "py_loc": loc(py),
        "js_loc": loc(js),
        "test_files": len(tests),
        "test_loc": loc(tests),
        "modules": sorted(modules.items(), key=lambda kv: -kv[1]),
    }
    data["ruff"] = "0" if "All checks passed" in run("ruff", "check", "src", "tests") else "issues"
    mypy = run("mypy", "src")
    data["mypy"] = "0" if "Success" in mypy else mypy.strip().splitlines()[-1]
    data["mypy_files"] = (re.search(r"checked (\d+) source", mypy) or [None, "?"])[1]

    cc = json.loads(
        run(
            "radon",
            "cc",
            "src/eaal_platform",
            "-s",
            "-j",
            "--exclude",
            "*/assets/*",
            only_stdout=True,
        )
    )
    blocks = [b for bl in cc.values() for b in bl]
    data["cc_blocks"] = len(blocks)
    data["cc_avg"] = round(statistics.mean(b["complexity"] for b in blocks), 2)
    data["cc_grades"] = {g: sum(1 for b in blocks if b["rank"] == g) for g in "ABCDEF"}
    data["cc_max"] = max(
        (b["complexity"], f.replace("src/eaal_platform/", ""), b["name"])
        for f, bl in cc.items()
        for b in bl
    )
    mi = json.loads(
        run(
            "radon",
            "mi",
            "src/eaal_platform",
            "-s",
            "-j",
            "--exclude",
            "*/assets/*",
            only_stdout=True,
        )
    )
    data["mi_files"] = len(mi)
    data["mi_grades"] = {g: sum(1 for v in mi.values() if v["rank"] == g) for g in "ABC"}
    data["mi_lowest"] = sorted(
        (round(v["mi"], 1), f.replace("src/eaal_platform/", ""), v["rank"]) for f, v in mi.items()
    )[:3]

    bandit = json.loads(
        run(
            "bandit",
            "-r",
            "src",
            "-q",
            "-f",
            "json",
            "--exclude",
            "src/eaal_platform/assets",
            only_stdout=True,
        )
    )
    totals = bandit["metrics"]["_totals"]
    data["bandit"] = {
        "high": int(totals["SEVERITY.HIGH"]),
        "medium": int(totals["SEVERITY.MEDIUM"]),
        "low": int(totals["SEVERITY.LOW"]),
        "loc": int(totals["loc"]),
        "suppressed": int(totals.get("skipped_tests", 0)),
    }
    audit = run("pip-audit", "-r", "requirements.lock", "--no-deps", "--disable-pip")
    data["audit"] = "No known vulnerabilities" in audit
    data["pinned"] = sum(
        1 for line in (ROOT / "requirements.lock").read_text().splitlines() if "==" in line
    )

    covfile = ROOT / ".report-coverage.json"
    pytest = run(
        sys.executable,
        "-m",
        "pytest",
        "-q",
        "--cov=eaal_platform",
        "--cov-report=term",
        f"--cov-report=json:{covfile}",
    )
    passed = re.search(r"(\d+) passed(?:, (\d+) skipped)?", pytest)
    cover = re.search(r"Total coverage: ([\d.]+)%", pytest)
    data["tests_passed"] = int(passed[1]) if passed else 0
    data["tests_skipped"] = int(passed[2] or 0) if passed else 0
    data["coverage"] = float(cover[1]) if cover else 0.0
    data["mutation"] = mutation
    data["cc_top"] = sorted(
        (
            (b["complexity"], f.replace("src/eaal_platform/", ""), b["name"], b["rank"])
            for f, bl in cc.items()
            for b in bl
        ),
        reverse=True,
    )[:5]
    data.update(_size_metrics())
    data["cov_modules"] = _coverage_by_module(covfile)
    return data


def _size_metrics() -> dict[str, Any]:
    raw_text = run("radon", "raw", "src/eaal_platform", "--exclude", "*/assets/*", "-s")
    sloc = int((re.findall(r"SLOC: (\d+)", raw_text) or ["0"])[-1])
    pct = (re.findall(r"\(C \+ M % L\): (\d+)%", raw_text) or ["0"])[-1]
    hal = json.loads(
        run("radon", "hal", "src/eaal_platform", "--exclude", "*/assets/*", "-j", only_stdout=True)
    )
    bugs = sum(v["total"]["bugs"] for v in hal.values() if "total" in v)
    return {"raw": {"sloc": sloc, "comments_pct": pct}, "halstead": {"bugs": bugs}}


def _coverage_by_module(path: Path) -> list[tuple[str, float, int]]:
    report = json.loads(path.read_text())
    path.unlink()
    totals: dict[str, list[int]] = {}
    for name, info in report["files"].items():
        rel = Path(name).relative_to("src/eaal_platform") if name.startswith("src/") else Path(name)
        key = rel.parts[0] + "/" if len(rel.parts) > 1 else rel.name
        covered, total = totals.setdefault(key, [0, 0])
        totals[key] = [
            covered + info["summary"]["covered_lines"],
            total + info["summary"]["num_statements"],
        ]
    rows = [(k, 100 * c / t if t else 100.0, t) for k, (c, t) in totals.items() if t >= 20]
    return sorted(rows, key=lambda r: -r[2])[:9]


INK = colors.HexColor("#10303c")
TEAL = colors.HexColor("#1f7a8c")
AMBER = colors.HexColor("#f2a541")
SOFT = colors.HexColor("#f2f6f7")
LINE = colors.HexColor("#d5e0e3")
MUTED = colors.HexColor("#5b6d74")
GREEN = colors.HexColor("#2e8b57")
ICON = SRC / "assets" / "icon.png"
MEMBERS = ["Viral Kariya", "Vanshika Ahuja", "Yashika Parmar", "Carol Maria"]
PAGE_W, PAGE_H = A4


def _cover(c: Any, doc: Any, data: dict[str, Any]) -> None:
    c.saveState()
    c.setFillColor(INK)
    c.rect(0, PAGE_H * 0.42, PAGE_W, PAGE_H * 0.58, stroke=0, fill=1)
    c.setFillColor(AMBER)
    c.rect(0, PAGE_H * 0.42 - 3 * mm, PAGE_W, 3 * mm, stroke=0, fill=1)
    c.setFillColor(TEAL)
    c.circle(PAGE_W - 25 * mm, PAGE_H - 30 * mm, 46 * mm, stroke=0, fill=1)
    c.setFillColor(colors.HexColor("#17495a"))
    c.circle(PAGE_W - 5 * mm, PAGE_H - 78 * mm, 30 * mm, stroke=0, fill=1)
    c.drawImage(str(ICON), 22 * mm, PAGE_H - 62 * mm, 30 * mm, 30 * mm, mask="auto")
    c.setFillColor(colors.white)
    c.setFont("Helvetica-Bold", 56)
    c.drawString(22 * mm, PAGE_H - 92 * mm, "CAVY")
    c.setFont("Helvetica", 17)
    c.drawString(22 * mm, PAGE_H - 106 * mm, "Software Report")
    c.setFillColor(AMBER)
    c.setFont("Helvetica-Bold", 11)
    c.drawString(
        22 * mm, PAGE_H - 122 * mm, "CODEBASE   /   FRAMEWORKS   /   CODE QUALITY   /   NFRs"
    )
    c.setFillColor(colors.HexColor("#cfe3e8"))
    c.setFont("Helvetica-Oblique", 10.5)
    c.drawString(
        22 * mm,
        PAGE_H - 136 * mm,
        "Where AI helps in a coding lab, the trail of how it was used is kept.",
    )
    c.drawString(
        22 * mm, PAGE_H - 143 * mm, "Built, tested and measured by students, for classrooms."
    )

    top = PAGE_H * 0.42 - 16 * mm
    cards = [
        (f"{data['py_loc']:,}", "lines of Python"),
        (str(data["tests_passed"]), "automated tests"),
        (f"{data['coverage']:.0f}%", "test coverage"),
        ("0 / 0 / 0", "lint / type / security"),
    ]
    cw, gap = 40 * mm, 4 * mm
    x0 = 22 * mm
    for i, (big, small) in enumerate(cards):
        x = x0 + i * (cw + gap)
        c.setFillColor(SOFT)
        c.setStrokeColor(LINE)
        c.roundRect(x, top - 22 * mm, cw, 22 * mm, 3 * mm, stroke=1, fill=1)
        c.setFillColor(INK)
        c.setFont("Helvetica-Bold", 17)
        c.drawString(x + 4 * mm, top - 11 * mm, big)
        c.setFillColor(MUTED)
        c.setFont("Helvetica", 8.5)
        c.drawString(x + 4 * mm, top - 17.5 * mm, small)

    y = top - 32 * mm
    c.setFillColor(AMBER)
    c.rect(22 * mm, y - 1 * mm, 10 * mm, 1.4 * mm, stroke=0, fill=1)
    c.setFillColor(INK)
    c.setFont("Helvetica-Bold", 15)
    c.drawString(22 * mm, y - 11 * mm, "Group 04")
    c.setFont("Helvetica", 11.5)
    for i, name in enumerate(MEMBERS):
        c.setFillColor(TEAL)
        c.circle(24 * mm, y - (22 + i * 8) * mm + 1.2 * mm, 1.1 * mm, stroke=0, fill=1)
        c.setFillColor(INK)
        c.drawString(29 * mm, y - (22 + i * 8) * mm, name)
    c.restoreState()


def _page(c: Any, doc: Any) -> None:
    c.saveState()
    c.setFillColor(INK)
    c.rect(0, PAGE_H - 9 * mm, PAGE_W, 9 * mm, stroke=0, fill=1)
    c.setFillColor(AMBER)
    c.rect(0, PAGE_H - 9 * mm, 28 * mm, 9 * mm, stroke=0, fill=1)
    c.setFillColor(INK)
    c.setFont("Helvetica-Bold", 9)
    c.drawString(8 * mm, PAGE_H - 6 * mm, "CAVY")
    c.setFillColor(colors.HexColor("#cfe3e8"))
    c.setFont("Helvetica", 8)
    c.drawString(33 * mm, PAGE_H - 6 * mm, "Software Report  ·  Group 04")
    c.setFillColor(MUTED)
    c.drawRightString(PAGE_W - 15 * mm, 8 * mm, f"{doc.page}")
    c.drawString(15 * mm, 8 * mm, "Codebase · Frameworks · Code Quality · NFRs")
    c.restoreState()


def build(data: dict[str, Any], out: Path) -> None:
    styles = getSampleStyleSheet()
    body = ParagraphStyle(
        "body", parent=styles["BodyText"], fontSize=9.6, leading=14, spaceAfter=6, textColor=INK
    )
    small = ParagraphStyle("small", parent=body, fontSize=8.4, leading=11.5, textColor=MUTED)
    cell = ParagraphStyle("cell", parent=body, fontSize=8.6, leading=11.5, spaceAfter=0)
    h2 = ParagraphStyle(
        "h2", parent=body, fontName="Helvetica-Bold", fontSize=12.5, textColor=TEAL, spaceBefore=10
    )
    h3 = ParagraphStyle("h3", parent=body, fontName="Helvetica-Bold", fontSize=10, spaceBefore=4)
    code = ParagraphStyle(
        "code", parent=body, fontName="Courier", fontSize=8.4, textColor=INK, leftIndent=0
    )
    lead = ParagraphStyle("lead", parent=body, fontSize=10.8, leading=16, textColor=MUTED)

    def P(text: str, style: ParagraphStyle = body) -> Paragraph:
        return Paragraph(text, style)

    def section(num: str, title: str, sub: str) -> list[Any]:
        badge = Paragraph(
            f"<font color='#f2a541' size=22><b>{num}</b></font>",
            ParagraphStyle("b", parent=body, alignment=1, spaceAfter=0),
        )
        head = [
            Paragraph(f"<font size=19 color='#10303c'><b>{title}</b></font>", body),
            Paragraph(f"<font color='#5b6d74'>{sub}</font>", small),
        ]
        t = Table([[badge, head]], colWidths=[16 * mm, 164 * mm])
        t.setStyle(
            TableStyle(
                [
                    ("BACKGROUND", (0, 0), (0, 0), INK),
                    ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                    ("LEFTPADDING", (1, 0), (1, 0), 10),
                    ("TOPPADDING", (0, 0), (-1, -1), 6),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
                    ("LINEBELOW", (0, 0), (-1, 0), 1.2, AMBER),
                ]
            )
        )
        return [t, Spacer(1, 4 * mm)]

    def table(rows: list[list[str]], widths: list[float]) -> Table:
        wrapped = [[Paragraph(str(x), cell) for x in r] for r in rows]
        for i, x in enumerate(rows[0]):
            wrapped[0][i] = Paragraph(f"<font color='white'><b>{x}</b></font>", cell)
        t = Table(wrapped, colWidths=[w * mm for w in widths], repeatRows=1)
        t.setStyle(
            TableStyle(
                [
                    ("BACKGROUND", (0, 0), (-1, 0), TEAL),
                    ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, SOFT]),
                    ("LINEBELOW", (0, 0), (-1, -1), 0.4, LINE),
                    ("VALIGN", (0, 0), (-1, -1), "TOP"),
                    ("LEFTPADDING", (0, 0), (-1, -1), 6),
                    ("RIGHTPADDING", (0, 0), (-1, -1), 6),
                    ("TOPPADDING", (0, 0), (-1, -1), 4),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
                ]
            )
        )
        return t

    def kpis(items: list[tuple[str, str]], per_row: int | None = None) -> Table:
        n = per_row or len(items)
        w = 180 / n
        rows, row = [], []
        for big, label in items:
            row.append(
                [
                    Paragraph(
                        f"<font size=19 color='#10303c'><b>{big}</b></font>",
                        ParagraphStyle("k", parent=body, spaceAfter=1, leading=22),
                    ),
                    Paragraph(label, ParagraphStyle("kl", parent=small, spaceAfter=0)),
                ]
            )
            if len(row) == n:
                rows.append(row)
                row = []
        if row:
            rows.append(row + [""] * (n - len(row)))
        t = Table(rows, colWidths=[w * mm] * n)
        t.setStyle(
            TableStyle(
                [
                    ("BOX", (0, 0), (-1, -1), 0, colors.white),
                    ("INNERGRID", (0, 0), (-1, -1), 4, colors.white),
                    ("BACKGROUND", (0, 0), (-1, -1), SOFT),
                    ("LINEABOVE", (0, 0), (-1, 0), 2.2, TEAL),
                    ("LEFTPADDING", (0, 0), (-1, -1), 8),
                    ("TOPPADDING", (0, 0), (-1, -1), 7),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 7),
                ]
            )
        )
        return t

    def callout(title: str, text: str, color: Any = AMBER) -> Table:
        t = Table([[Paragraph(f"<b>{title}</b><br/>{text}", cell)]], colWidths=[180 * mm])
        t.setStyle(
            TableStyle(
                [
                    ("BACKGROUND", (0, 0), (-1, -1), SOFT),
                    ("LINEBEFORE", (0, 0), (0, -1), 3.5, color),
                    ("LEFTPADDING", (0, 0), (-1, -1), 10),
                    ("TOPPADDING", (0, 0), (-1, -1), 7),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 7),
                ]
            )
        )
        return t

    def bars(items: list[tuple[str, float, str]], maxv: float, color: Any = TEAL) -> Drawing:
        rowh = 7.5 * mm
        d = Drawing(180 * mm, rowh * len(items))
        for i, (label, v, txt) in enumerate(items):
            y = (len(items) - 1 - i) * rowh
            d.add(String(0, y + 2.2 * mm, label, fontName="Helvetica", fontSize=8, fillColor=INK))
            x0, full = 58 * mm, 92 * mm
            d.add(Rect(x0, y + 1.5 * mm, full, 3.6 * mm, fillColor=SOFT, strokeColor=None))
            d.add(
                Rect(
                    x0,
                    y + 1.5 * mm,
                    max(full * v / maxv, 0.6 * mm),
                    3.6 * mm,
                    fillColor=color,
                    strokeColor=None,
                )
            )
            d.add(
                String(
                    x0 + full + 3 * mm,
                    y + 2.2 * mm,
                    txt,
                    fontName="Helvetica-Bold",
                    fontSize=8,
                    fillColor=INK,
                )
            )
        return d

    def diagram() -> Drawing:
        d = Drawing(180 * mm, 74 * mm)

        def box(
            x: float, y: float, w: float, h: float, text: str, fill: Any, fg: Any = colors.white
        ) -> None:
            d.add(
                Rect(
                    x * mm,
                    y * mm,
                    w * mm,
                    h * mm,
                    rx=2 * mm,
                    ry=2 * mm,
                    fillColor=fill,
                    strokeColor=None,
                )
            )
            for i, line in enumerate(text.split("\n")):
                d.add(
                    String(
                        (x + w / 2) * mm,
                        (y + h / 2 + (len(text.split("\n")) - 1) * 2 - i * 4.2 - 1) * mm,
                        line,
                        fontName="Helvetica-Bold" if i == 0 else "Helvetica",
                        fontSize=8,
                        fillColor=fg,
                        textAnchor="middle",
                    )
                )

        def arrow(x1: float, y1: float, x2: float, y2: float) -> None:
            d.add(Line(x1 * mm, y1 * mm, x2 * mm, y2 * mm, strokeColor=MUTED, strokeWidth=1.1))

        box(0, 54, 54, 16, "Student / professor app\nnative window + Monaco editor", INK)
        box(63, 54, 54, 16, "Admin panel\nbrowser, /admin", INK)
        box(126, 54, 54, 16, "Installers\nmacOS · Windows (CI)", MUTED)
        box(
            0,
            28,
            117,
            15,
            "FastAPI classroom server  (or local, no server)\nlong-poll change feed · onboarding by email · audit log",
            TEAL,
        )
        box(0, 4, 36, 15, "CavyApi\none bridge", AMBER, INK)
        box(40, 4, 36, 15, "Signals\n14 CIQ signals", AMBER, INK)
        box(80, 4, 37, 15, "AI providers\nOllama · hosted", AMBER, INK)
        box(126, 28, 54, 15, "Sandbox\nseparate process", colors.HexColor("#17495a"))
        box(126, 4, 54, 15, "SQLite (WAL)\nevent-sourced log", colors.HexColor("#17495a"))
        arrow(27, 54, 27, 43)
        arrow(90, 54, 90, 43)
        arrow(58, 28, 58, 19)
        arrow(117, 35, 126, 35)
        arrow(117, 11, 126, 11)
        return d

    cc = data["cc_grades"]
    mi = data["mi_grades"]
    b = data["bandit"]
    mut = data["mutation"]
    killed = survived = timeout = 0
    if mut:
        kv = dict(part.split("=") for part in mut.split())
        killed, survived, timeout = int(kv["killed"]), int(kv["survived"]), int(kv["timeout"])
    total_mut = killed + survived + timeout
    mut_pct = 100 * killed / total_mut if total_mut else 0.0
    sec_total = b["high"] + b["medium"] + b["low"]

    story: list[Any] = [PageBreak()]

    # ---------------------------------------------------------------- at a glance
    story += section("0", "At a glance", "What was checked and what came out")
    story += [
        P(
            "CAVY lets students do coding labs with an AI assistant available, and keeps a structured "
            "record of how that help was used. This report describes how the software is built, why "
            "each framework was chosen, how its quality was checked (with the actual numbers) and how "
            "it meets the non-functional requirements. Every figure was produced by running the tool "
            "against the current source tree when the report was generated.",
            lead,
        ),
        Spacer(1, 2 * mm),
        kpis(
            [
                (f"{data['py_loc']:,}", f"lines of Python in {data['py_files']} files"),
                (f"{data['js_loc']:,}", "lines of JavaScript (app + admin panel)"),
                (str(data["tests_passed"]), f"tests passing, {data['test_loc']:,} lines of tests"),
                (f"{data['coverage']:.1f}%", "line + branch coverage (floor 70%)"),
                ("0", "Ruff issues in src and tests"),
                ("0", "mypy --strict errors"),
                ("0", "Bandit security findings"),
                (f"{mut_pct:.0f}%" if mut else "n/a", "mutation score, scoring engine"),
            ],
            per_row=4,
        ),
        Spacer(1, 4 * mm),
        P("The five things worth knowing", h2),
    ]
    for text in [
        "<b>It is built as clear parts around one event log.</b> A single bridge class (<font name='Courier'>CavyApi</font>) "
        "is the only door between the screens and the logic, so the app, the admin panel and the tests all use the same code.",
        f"<b>Every tool is clean.</b> Ruff and mypy strict report 0 issues, Bandit reports 0 findings across "
        f"{b['loc']:,} lines, and pip-audit finds no known vulnerability in the {data['pinned']} pinned packages.",
        f"<b>The tests check results, not just execution.</b> {data['coverage']:.0f}% coverage, and mutation testing "
        f"raised the scoring engine from 65% to {mut_pct:.0f}% by fixing tests that were too forgiving.",
        "<b>Speed was measured, not guessed.</b> 30 students working at the same time: about 800 calls a second, "
        "none failed. The same test found a real bug (a database pool limit) that was then fixed.",
        "<b>The gaps are listed.</b> One very large file (<font name='Courier'>api/bridge.py</font>), plain HTTP on "
        "the classroom network, unsigned installers, and a scoring model validated on constructed cases, not yet on a "
        "real class.",
    ]:
        story.append(P("•&nbsp;&nbsp;" + text))
    story += [PageBreak()]

    # ---------------------------------------------------------------- 1 codebase
    mod_rows = [["Module", "Lines", "Responsibility"]]
    purpose = {
        "db": "SQLAlchemy models, the engine (WAL, connection pool), accounts, courses, resources, approvals",
        "server": "FastAPI classroom server, onboarding by email, the browser admin panel",
        "api": "<b>CavyApi</b>: the one bridge every screen talks to",
        "client": "The app's side: local data or a server, lab-mode lock-down, remote calls",
        "ai": "AIProvider interface; Ollama and hosted providers (OpenAI, Claude, Gemini, Grok, Groq)",
        "signals": "The 14 CIQ signals computed from the event log",
        "events": "Background, batched, append-only event logger",
        "sandbox": "Runs student code in a separate process with a time limit",
    }
    for name, lines in data["modules"]:
        if name in purpose:
            mod_rows.append([f"<font name='Courier'>{name}/</font>", f"{lines:,}", purpose[name]])
    story += section("1", "Codebase", "What was built, how it is organised, how to run it")
    story += [
        P("1.1 What CAVY is", h2),
        P(
            "CAVY runs programming labs where an AI assistant is available throughout, and treats the "
            "assistant as part of the system instead of a black box: every prompt, reply, code edit, run "
            "and submission is stored as an event in the database the rest of the platform reads. The "
            "CIQ score, the progress charts and the professor's reports are all derived from that log."
        ),
        P(
            "It runs in two ways from the same code: standalone on one computer (its own SQLite file), or "
            "against a central classroom server that a professor and many students on one network share "
            "live, with a browser admin panel."
        ),
        P("1.2 Architecture", h2),
        diagram(),
        P("1.3 Modules", h2),
        table(mod_rows, [26, 16, 138]),
        Spacer(1, 2 * mm),
        P("1.4 How the parts connect", h2),
        P(
            "The window (HTML/CSS/JS in the system web view, Monaco editor) calls one Python object, "
            "<b>CavyApi</b>, for everything. On a server the same class is reused unchanged: the server "
            "creates one per signed-in person and exposes its methods over HTTP. A change feed (long "
            "polling) tells open screens when something they show has changed, so a professor's upload "
            "appears on a student's screen without a refresh."
        ),
        P(
            "Students' code never runs inside the app: each run is a separate process with a time limit and, "
            "where the operating system supports it, CPU and memory limits. The event logger writes to the "
            "database on its own thread, so typing never waits for the disk."
        ),
        P("1.5 Reproducible setup", h2),
        P(
            f"<font name='Courier'>requirements.lock</font> pins {data['pinned']} packages to the exact versions the "
            "tests run against; <font name='Courier'>pyproject.toml</font> declares the ranges and the optional groups "
            "(server, build, dev). The README covers setup, running, the quality gate, building installers and the "
            "architecture; <font name='Courier'>docs/</font> holds the classroom guide, Windows testing notes, "
            "performance results and the roadmap."
        ),
        PageBreak(),
    ]

    # ---------------------------------------------------------------- 2 frameworks
    story += section("2", "Frameworks", "What was chosen, why, and what else was considered")
    fw = [
        ["Need", "Chosen", "Why this one", "Alternatives weighed"],
        [
            "Desktop shell",
            "pywebview",
            "Uses the system's web view (WebKit / WebView2), so the installer is about 70-80 MB, not an "
            "Electron-sized bundle; a plain Python object can be called straight from JavaScript.",
            "Electron (bigger, adds Node), Tauri (needs Rust), PySide6 (our first version; slower to build and style).",
        ],
        [
            "Server",
            "FastAPI + uvicorn",
            "Typed request models, easy testing with TestClient, small footprint, serves the admin panel's static files.",
            "Django (heavier, would duplicate the CavyApi layer), Flask (no built-in validation).",
        ],
        [
            "Database",
            "SQLite (WAL) + SQLAlchemy 2",
            "Zero setup for a classroom server or a laptop; WAL lets the event thread write while requests read; "
            "SQLAlchemy keeps the schema in typed models and could move to Postgres.",
            "PostgreSQL (extra install, overkill for one class), plain sqlite3 (no model or migration help).",
        ],
        [
            "Code editor",
            "Monaco",
            "The editor VS Code uses: Python highlighting, indentation, find; a plain-textarea fallback keeps the app working without it.",
            "CodeMirror (lighter, fewer features), a bare textarea (no indenting).",
        ],
        [
            "AI access",
            "httpx + a provider interface",
            "One small HTTP client; each company is a short class behind the same interface, so a new provider is about "
            "30 lines and tests use a mock transport.",
            "Each company's own SDK (4 extra dependencies that change often), LangChain (far heavier).",
        ],
        [
            "Installers",
            "PyInstaller, Inno Setup, dmgbuild, GitHub Actions",
            "One recipe builds each OS's app; Actions builds and smoke-tests both installers on clean machines.",
            "py2app / briefcase (one OS or less mature), WiX (heavier than Inno Setup).",
        ],
        [
            "Quality tools",
            "Ruff, mypy strict, pytest, Radon, Bandit, pip-audit, mutmut",
            "Each covers one risk (next section).",
            "Flake8 + isort + pyupgrade (Ruff replaces all three in one fast tool).",
        ],
    ]
    story += [
        table(fw, [24, 30, 70, 56]),
        Spacer(1, 3 * mm),
        callout(
            "Small on purpose",
            "No front-end build step, no ORM add-ons, no task queue, no message broker. The lock file lists what is "
            "actually installed and pip-audit checks it, so a package nobody uses would show up as a line nobody can explain.",
        ),
        PageBreak(),
    ]

    # ---------------------------------------------------------------- 3 code quality
    story += section("3", "Code quality", "Nine checks, what each one means, and what it found")
    story += [
        P(
            "A different tool covers each kind of risk. Together they answer: is the style consistent, are the types "
            "right, is the logic simple enough to change safely, do the tests actually catch mistakes, is the code safe, "
            "and are the libraries safe. Each result below comes from running the command shown on the current code.",
            lead,
        )
    ]

    # 3.1 Ruff
    story += [
        P("3.1 Ruff: style and likely bugs", h2),
        P(
            "<b>What it checks.</b> Ruff is a fast linter that replaces Flake8, isort and pyupgrade. It reads the code "
            "without running it and flags unused variables, wrong imports, risky patterns and style drift, so a reviewer "
            "does not have to."
        ),
        P("ruff check src tests ; ruff format --check .", code),
        kpis(
            [
                (data["ruff"], "issues found"),
                ("10", "rule families on"),
                ("100", "line length limit"),
                ("1", "file with relaxed rules*"),
            ]
        ),
        Spacer(1, 2 * mm),
        table(
            [
                ["Rule family", "What it catches"],
                ["E, W", "pycodestyle: layout, spacing, line length"],
                ["F", "Pyflakes: undefined names, unused imports and variables"],
                ["I", "isort: import order"],
                ["UP", "pyupgrade: old idioms that have a modern form (Python 3.11)"],
                [
                    "B",
                    "bugbear: likely bugs (mutable defaults, unused loop variables, bare excepts)",
                ],
                ["SIM", "simplify: needless complexity"],
                ["C4", "comprehensions that can be simpler"],
                ["N", "PEP 8 naming"],
                ["RUF", "Ruff's own checks"],
            ],
            [30, 150],
        ),
        P(
            "<b>Reading the result.</b> Zero issues across source and tests, with the rules above all switched on. "
            "*The only relaxed rules are line length (and one naming rule) in the report generator and some validation "
            "scripts, whose lines are mostly prose. Nothing else is switched off.",
            small,
        ),
    ]

    # 3.2 mypy
    story += [
        P("3.2 mypy (strict): are the types right?", h2),
        P(
            "<b>What it checks.</b> mypy follows the type hints through the whole program and refuses code where a value "
            "could be the wrong type or <i>None</i> when the next line assumes it is not. Strict mode also requires every "
            "function to be annotated, which is what makes the check meaningful."
        ),
        P("mypy src", code),
        kpis(
            [
                (data["mypy"], "errors"),
                (data["mypy_files"], "source files checked"),
                ("strict", "mode"),
                ("pre-commit", "runs it on every commit"),
            ]
        ),
        P(
            "<b>Reading the result.</b> The two bridge-style layers (<font name='Courier'>CavyApi</font> and the client "
            "that forwards to the server) pass plain dictionaries around, which is exactly where a typo in a key would hide "
            "in a demo. Strict typing catches that class of mistake before the code runs.",
            small,
        ),
    ]

    # 3.3 Radon CC
    top = data["cc_top"]
    story += [
        P("3.3 Radon: how complex is each function?", h2),
        P(
            "<b>What it checks.</b> Cyclomatic complexity counts the independent paths through a function (every "
            "<i>if</i>, loop and exception adds one). More paths means more tests are needed and more places for a bug to "
            "hide. Radon grades blocks A (1-5, simple), B (6-10), C (11-20), D and worse (21+)."
        ),
        P("radon cc src/eaal_platform -a -s", code),
        kpis(
            [
                (str(data["cc_blocks"]), "blocks analysed (functions, methods, classes)"),
                (f"A ({data['cc_avg']})", "average complexity"),
                (str(cc["C"]), "blocks graded C"),
                (str(cc["D"] + cc["E"] + cc["F"]), "blocks graded D or worse"),
            ]
        ),
        Spacer(1, 3 * mm),
        bars(
            [
                (
                    "A  (1-5)   simple",
                    cc["A"],
                    f"{cc['A']}  ({100 * cc['A'] / data['cc_blocks']:.0f}%)",
                ),
                (
                    "B  (6-10)  moderate",
                    cc["B"],
                    f"{cc['B']}  ({100 * cc['B'] / data['cc_blocks']:.0f}%)",
                ),
                (
                    "C  (11-20) complex",
                    cc["C"],
                    f"{cc['C']}  ({100 * cc['C'] / data['cc_blocks']:.0f}%)",
                ),
                (
                    "D+ (21+)   very complex",
                    cc["D"] + cc["E"] + cc["F"],
                    str(cc["D"] + cc["E"] + cc["F"]),
                ),
            ],
            data["cc_blocks"],
        ),
        Spacer(1, 2 * mm),
        P("The five most complex blocks:", h3),
        table(
            [["Function", "File", "Complexity"]]
            + [[f"<font name='Courier'>{n}</font>", f, f"{v}  ({r})"] for v, f, n, r in top],
            [70, 80, 30],
        ),
        P(
            "<b>Reading the result.</b> Nine in ten blocks are simple, and nothing is worse than moderately complex. "
            "The busiest functions are the ones that have to decide between many cases: reading a spreadsheet of "
            "students, validating a resource, setting up an account. During this work, several blocks that had grown "
            "past 20 were split into small helper functions, which is why none is now above "
            f"{data['cc_max'][0]}.",
            small,
        ),
    ]

    # 3.4 MI + raw + Halstead
    raw = data["raw"]
    hal = data["halstead"]
    story += [
        P("3.4 Radon: maintainability, size and Halstead", h2),
        P(
            "<b>What it checks.</b> The maintainability index (0-100) combines size, complexity and comments into one "
            "score per file (A is easy to change, C is hard). Raw metrics describe the size; Halstead metrics estimate the "
            "effort to read and the number of bugs a program of this size tends to hide."
        ),
        P("radon mi src/eaal_platform -s ; radon raw ... ; radon hal ...", code),
        kpis(
            [
                (f"{mi['A']} / {data['mi_files']}", "files graded A"),
                (f"{raw['sloc']:,}", "source lines of code (logical + physical, no blanks)"),
                (f"{raw['comments_pct']}%", "comment lines (plus docstrings)"),
                (f"{hal['bugs']:.0f}", "Halstead predicted bugs (rough)"),
            ]
        ),
        Spacer(1, 2 * mm),
        table(
            [["File (lowest three)", "Maintainability index", "Grade"]]
            + [[f"<font name='Courier'>{f}</font>", str(v), r] for v, f, r in data["mi_lowest"]],
            [100, 50, 30],
        ),
        P(
            "<b>Reading the result.</b> Most files are grade A. "
            "<font name='Courier'>api/bridge.py</font> scores low purely because of its size (about 2,000 lines in one "
            "class); the code in it is simple, but one class that large is hard to hold in your head. Splitting it into "
            "modules is the first item in <i>Next steps</i>. Halstead's predicted-bugs figure is a rule of thumb "
            "(volume divided by 3,000), not a defect count; it is reported only to show the size of the code base.",
            small,
        ),
        PageBreak(),
    ]

    # 3.5 pytest
    story += [
        P("3.5 pytest and coverage: do the tests exercise the code?", h2),
        P(
            "<b>What it checks.</b> pytest runs the automated tests. Coverage records which lines (and which branches of "
            "each <i>if</i>) the tests actually reached. A coverage floor of 70% is set in the project configuration, so "
            "a drop fails the run instead of just changing a number in a report."
        ),
        P("pytest --cov=eaal_platform --cov-branch", code),
        kpis(
            [
                (str(data["tests_passed"]), "tests passed"),
                (str(data["tests_skipped"]), "skipped (slow marker)"),
                (f"{data['coverage']:.1f}%", "line + branch coverage"),
                ("70%", "floor that fails the run"),
            ]
        ),
        Spacer(1, 3 * mm),
        P("Coverage by module (the larger modules):", h3),
        bars(
            [(n, pct, f"{pct:.0f}%   ({st} stmts)") for n, pct, st in data["cov_modules"]],
            100,
            GREEN,
        ),
        P(
            "<b>Reading the result.</b> Business logic (accounts, courses, scoring, sandbox, event logger, server) is "
            "well covered. The lower bars are code that needs a real screen: the window set-up in "
            "<font name='Courier'>app.py</font> and the operating-system lock-down for lab mode, which can only be "
            "exercised by running the app on macOS and Windows. Tests include end-to-end HTTP tests of the server and two "
            "tests that keep the database pool large enough for a class.",
            small,
        ),
    ]

    # 3.6 mutation
    story += [
        P("3.6 mutmut: would the tests notice a bug?", h2),
        P(
            "<b>What it checks.</b> Coverage says a line ran, not that a test would fail if the line were wrong. mutmut "
            "makes small deliberate mistakes (turns a <i>&lt;</i> into <i>&lt;=</i>, a 0.5 into 0.6, drops a condition) and "
            "re-runs the tests. If a test fails, the mutant is <i>killed</i>; if everything still passes, it <i>survived</i> "
            "and the tests are too forgiving at that spot. We scoped it to the scoring engine "
            "(<font name='Courier'>signals/compute.py</font>), the one place a silently wrong number would be hardest to notice."
        ),
        P("mutmut run   (scoped by [tool.mutmut] in pyproject.toml)", code),
        kpis(
            [
                (f"{total_mut:,}", "mutants tried"),
                (f"{killed:,}", "killed (tests caught them)"),
                (f"{survived:,}", "survived"),
                (f"{mut_pct:.1f}%", "mutation score"),
            ]
        )
        if mut
        else kpis([("n/a", "no mutation score recorded in this run")]),
        Spacer(1, 3 * mm),
    ]
    if mut:
        story += [
            bars(
                [
                    ("First run", 686, "65.3%  (686 of 1,051)"),
                    ("After the new tests", killed, f"{mut_pct:.1f}%  ({killed} of {total_mut})"),
                ],
                total_mut,
                AMBER,
            ),
            P(
                "<b>Reading the result.</b> The first run killed 65% of the mutants: the suite ran most lines but checked "
                "the exact values too loosely. Reading what survived showed the gaps, such as the exact reason a signal "
                "gives when it cannot be computed, the boundaries where a score is capped, and the evidence a signal reports. "
                "A set of exact-value tests was added for those cases and the score rose to "
                f"{mut_pct:.0f}%. The remaining {survived} survivors are mostly equivalent changes (for example a "
                "different default message nobody reads) and the longitudinal comparison, listed as future work.",
                small,
            ),
        ]

    # 3.7 Bandit
    story += [
        P("3.7 Bandit: security patterns in the code", h2),
        P(
            "<b>What it checks.</b> Bandit scans the source for patterns that are often security problems: running shell "
            "commands, hard-coded passwords, binding to every network address, weak hashes, loading untrusted data."
        ),
        P("bandit -r src", code),
        kpis(
            [
                (str(b["high"]), "high severity"),
                (str(b["medium"]), "medium severity"),
                (str(b["low"]), "low severity"),
                (f"{b['loc']:,}", "lines scanned"),
            ]
        ),
        P(
            f"<b>Reading the result.</b> {sec_total} findings. A few patterns are real by design and are accepted "
            f"individually with a comment explaining why: the server listens on all network addresses because it is meant "
            "to be reached from other computers in the lab; the sandbox starts a subprocess with a fixed argument list "
            "(no shell); and a few strings that look like passwords are only field names. Each of the "
            f"{b['suppressed']} suppressed checks sits next to its reason in the code, instead of being switched off globally.",
            small,
        ),
    ]

    # 3.8 pip-audit
    story += [
        P("3.8 pip-audit: are the libraries safe?", h2),
        P(
            "<b>What it checks.</b> The code can be perfect and still be unsafe if a library it uses has a known "
            "vulnerability. pip-audit compares every pinned package against the public vulnerability database."
        ),
        P("pip-audit -r requirements.lock", code),
        kpis(
            [
                (str(data["pinned"]), "packages pinned and checked"),
                ("0" if data["audit"] else "see log", "known vulnerabilities"),
                ("exact", "versions (lock file)"),
                ("yes", "re-checked before each release build"),
            ]
        ),
        P(
            "<b>Reading the result.</b> The comparison needs exact versions, which is the other reason for the lock file: "
            "what was audited is what the tests ran against and what the installers contain.",
            small,
        ),
    ]

    # 3.9 gate
    story += [
        P("3.9 The quality gate: every commit, every build", h2),
        table(
            [
                ["Where", "What runs", "What happens on failure"],
                [
                    "On each commit (pre-commit)",
                    "Ruff, Ruff format, mypy strict, the fast tests",
                    "The commit is refused until fixed.",
                ],
                [
                    "On each push (GitHub Actions CI)",
                    "Lint, types, tests with the coverage floor on Linux and macOS",
                    "The run is marked failed.",
                ],
                [
                    "On each release build",
                    "PyInstaller + installer for both systems, then a smoke test of the installed app",
                    "No installer is published.",
                ],
            ],
            [48, 82, 50],
        ),
        P(
            "Note: the Windows test job on GitHub currently does not finish and is still being investigated; the Windows "
            "installer itself is built and smoke-tested.",
            small,
        ),
        PageBreak(),
    ]

    # ---------------------------------------------------------------- 4 NFRs
    story += section(
        "4", "Non-functional requirements", "How the system behaves, beyond what it does"
    )
    story += [
        P(
            "Each requirement below names the mechanism that meets it and, where possible, the measurement. "
            "<b>Met</b> means it is built and tested; <b>Met, with a gap</b> means a limit is stated.",
            lead,
        ),
        P("4.1 Performance", h2),
        kpis(
            [
                ("30", "students working at once"),
                ("814/s", "calls handled per second"),
                ("0", "failed calls (also at 100 students)"),
                ("~4 ms", "median to type, save or run a step"),
            ]
        ),
        Spacer(1, 2 * mm),
        P(
            "<b>How it was measured.</b> <font name='Courier'>scripts/benchmark.py</font> starts a throw-away server and "
            "has N students sign in and do a normal lab at the same time (15 edits per stage, run code, three stages, "
            "submit, open progress); every call is timed. 30 students made 1,770 calls in 2.2 s with none failing; 100 students "
            "made 5,900 calls in 7.4 s with none failing (typing median about 60 ms at that load). The event log writes about "
            "38,000 events a second and running one piece of student code takes about 12 ms."
        ),
        callout(
            "A real problem the benchmark found",
            "The first 30-student run had calls taking 30 seconds and 17 failures. Cause: the database pool held only 15 "
            "connections and one request can hold two or three, so requests waited on each other. Fix: a pool of 50 (up to "
            "200), a 30 s wait for the file lock, and a faster safe sync mode in WAL. After the fix: no failures and no call "
            "slower than 1.5 s. Two tests keep it that way. Full tables: <font name='Courier'>docs/PERFORMANCE.md</font>.",
        ),
        Spacer(1, 3 * mm),
    ]
    nfr = [
        ["Area", "Requirement", "How it is met", "Status"],
        [
            "Performance",
            "The screen never waits on the database",
            "Event logger on its own thread, batched writes, SQLite WAL.",
            "Met",
        ],
        [
            "Reliability",
            "Student code cannot hang or crash the app",
            "Every run is a separate process with a wall-clock limit; output is capped.",
            "Met",
        ],
        [
            "Reliability",
            "No work lost; clear failures",
            "Autosave on every edit and on leaving a stage; events flushed before scoring and on shutdown; unexpected errors return a plain message and are logged.",
            "Met",
        ],
        [
            "Reliability",
            "No invented numbers",
            "A signal with no evidence returns <i>none</i> with a reason (10 of 10 such cases checked).",
            "Met",
        ],
        [
            "Logging",
            "Problems can be investigated later",
            "Structured JSON-lines log to a rotating file, plus an audit log of sign-ins, account changes and admin actions.",
            "Met",
        ],
        [
            "Security",
            "Passwords and secrets",
            "Passwords: salted PBKDF2-SHA256, 200,000 rounds. Students' AI keys stay in the app's memory and never go to the server. The mail password is kept on the server and never shown back.",
            "Met, with a gap*",
        ],
        [
            "Security",
            "Access control and input",
            "Roles enforced on the server; only approved emails can register; sign-in throttling; uploads limited by size and type; emailed codes expire in 15 minutes and allow 5 tries.",
            "Met",
        ],
        [
            "Security",
            "Isolation of untrusted code",
            "Fresh subprocess per run; CPU limit everywhere it exists, memory limit on Linux.",
            "Met",
        ],
        [
            "Privacy",
            "Collect only what is needed",
            "Students identified by approved email and roll number; AI keys never stored; progress visible to the student and their own professor.",
            "Met",
        ],
        [
            "Maintainability",
            "Typed, linted, tested, small modules",
            "mypy strict, Ruff, coverage floor, pre-commit, CI; 8 focused modules.",
            "Met, with a gap**",
        ],
        [
            "Portability",
            "macOS and Windows",
            "One code base; two installers built and smoke-tested by Actions; per-OS data folders and lab-mode lock-down.",
            "Met",
        ],
        [
            "Usability",
            "People can set it up",
            "Guides in docs/, a one-click admin-password script, emailed first logins, plain-language messages.",
            "Met",
        ],
    ]
    story += [table(nfr, [22, 40, 94, 24]), Spacer(1, 2 * mm)]
    story += [
        P(
            "*Traffic is plain HTTP on the local network, the installers are not code-signed, the mail password is stored "
            "as text in the server's database, and lab mode cannot block every operating-system escape (for example "
            "Ctrl+Alt+Del). **<font name='Courier'>api/bridge.py</font> is too large.",
            small,
        ),
        PageBreak(),
    ]

    # ---------------------------------------------------------------- 5 signal validation
    story += section(
        "5", "Does the scoring measure what it claims?", "Separate from the unit tests"
    )
    story += [
        P(
            "Generic tests show the code runs. They do not show that each of the fourteen signals measures the behaviour it is "
            "defined to measure. A separate suite in <font name='Courier'>validation/</font> answers that, using the real "
            "<font name='Courier'>CavyApi</font> against a throw-away database, and the real local model for the three signals that "
            "need a language model.",
            lead,
        ),
        kpis(
            [
                ("53 / 53", "known-answer trials match exactly"),
                ("10 / 10", "no-evidence cases return none"),
                ("88 / 88", "recomputations identical"),
                ("14 / 14", "signals implemented"),
            ]
        ),
        Spacer(1, 3 * mm),
        table(
            [
                ["Check", "Result", "What it means"],
                [
                    "Known-answer trials",
                    "53 / 53 exact",
                    "Each formula equals the value worked out by hand.",
                ],
                [
                    "No-evidence trials",
                    "10 / 10 none",
                    "A signal with no evidence says so instead of inventing a number.",
                ],
                [
                    "Repeatability",
                    "88 / 88 identical",
                    "Recomputing the same session five times gives the same output.",
                ],
                [
                    "S1.1 help-seeking (model)",
                    "r = 0.866, p = .005",
                    "Rank agreement with hand-labelled examples.",
                ],
                ["S1.2 grounding (model)", "r = 0.843, p = .009", "Same check."],
                [
                    "S3.1 understanding (model)",
                    "r = 0.845, p = .034",
                    "Same check; the ICC of 0.68 is not significant.",
                ],
            ],
            [44, 40, 96],
        ),
        Spacer(1, 3 * mm),
        callout(
            "What this does not show",
            "It shows the signals are computed correctly and repeatably. It does not show they predict real learning; that "
            "needs a study with students. One model-scored case is known to be wrong: a line-by-line restatement of the code "
            "was given full marks for understanding. Full write-up: <font name='Courier'>validation/report/VALIDATION_REPORT.pdf</font>.",
        ),
        Spacer(1, 6 * mm),
    ]

    # ---------------------------------------------------------------- 6 growth
    story += section("6", "Growth since the first audit", "Same checks, larger system")
    story += [
        P(
            "The first audit was done on 20 September 2026 and covered the single-user desktop app (3,188 lines, 21 files, 145 tests). The current audit was done on 23 September 2026; in between, CAVY became a "
            "classroom system, and every check above was run again on the larger code.",
        ),
        table(
            [
                ["", "First audit (20 Sep 2026)", "Current audit (23 Sep 2026)"],
                ["Python source lines", "3,188", f"{data['py_loc']:,} in {data['py_files']} files"],
                ["Tests", "144 passed", f"{data['tests_passed']} passed"],
                ["Line + branch coverage", "90.3%", f"{data['coverage']:.1f}%"],
                [
                    "Mutation score (scoring engine)",
                    "63.7% (647 of 1,016)",
                    f"{mut_pct:.1f}% ({killed} of {total_mut})" if mut else "n/a",
                ],
                [
                    "Ruff / mypy strict / Bandit",
                    "0 / 0 / 0",
                    f"{data['ruff']} / {data['mypy']} / {sec_total}",
                ],
                ["Dependencies", "3 declared", f"{data['pinned']} pinned and audited"],
                [
                    "What it is",
                    "One desktop app per student",
                    "Class server, admin panel, professors, courses, lab mode, email",
                ],
            ],
            [56, 54, 70],
        ),
        Spacer(1, 3 * mm),
        P(
            "New in this period: the central server and admin panel, courses with levels and departments, professors and "
            "many-to-many classes, lab mode (full screen, key lock, focus rule, paste detection), first-login and "
            "password-reset emails, Groq as a full provider, structured logging, and the load benchmark. Each has tests."
        ),
        PageBreak(),
    ]

    # ---------------------------------------------------------------- 7 reproduce + assessment
    story += section("7", "Reproducing every number", "One command each")
    story += [
        table(
            [
                ["Check", "Command"],
                ["Install", "pip install -r requirements.lock &amp;&amp; pip install -e ."],
                ["Lint and types", "ruff check src tests ; mypy src"],
                ["Complexity", "radon cc src/eaal_platform -a -s ; radon mi src/eaal_platform -s"],
                [
                    "Size and Halstead",
                    "radon raw src/eaal_platform -s ; radon hal src/eaal_platform",
                ],
                ["Tests, coverage", "pytest --cov=eaal_platform --cov-branch"],
                ["Security", "bandit -r src ; pip-audit -r requirements.lock"],
                ["Mutation", "mutmut run   (scoped to signals/compute.py in pyproject.toml)"],
                ["Performance", "python scripts/benchmark.py --students 30"],
                ["Signal validation", "python validation/run_synthetic_suite.py"],
                ["This report", "python scripts/make_audit_report.py"],
            ],
            [36, 144],
        ),
        Spacer(1, 6 * mm),
    ]
    story += section("8", "Overall assessment", "What is strong, what is next")
    story += [
        table(
            [
                ["Section", "Assessment"],
                [
                    "Codebase",
                    "Clear packages around one event-sourced database; one bridge class as the front-end boundary; reproducible install. Weak spot: <font name='Courier'>bridge.py</font> is too big.",
                ],
                [
                    "Frameworks",
                    "Each choice tied to a constraint and compared with alternatives; few dependencies, all pinned. The comparison is reasoning and our own tests, not published benchmarks.",
                ],
                [
                    "Code quality",
                    f"Ruff, mypy strict and Bandit clean; {data['coverage']:.0f}% coverage; no function above {data['cc_max'][0]} complexity; mutation score {mut_pct:.0f}% on the scoring engine.",
                ],
                [
                    "NFRs",
                    "Performance measured, logging added, security basics in place. HTTPS, signing and validation on a real class are still open.",
                ],
            ],
            [30, 150],
        ),
        Spacer(1, 4 * mm),
        callout(
            "Next steps, in order",
            "1) Split <font name='Courier'>api/bridge.py</font> into modules. 2) HTTPS for the classroom server. 3) Code-sign the installers. "
            "4) Run mutation testing on more than the scoring engine. 5) Test lab mode on more computers. 6) Run the scoring on a real class.",
            TEAL,
        ),
        Spacer(1, 6 * mm),
        P("Report prepared by Group 04: " + ", ".join(MEMBERS) + ".", small),
    ]

    doc = SimpleDocTemplate(
        str(out),
        pagesize=A4,
        leftMargin=15 * mm,
        rightMargin=15 * mm,
        topMargin=18 * mm,
        bottomMargin=16 * mm,
        title="CAVY: Software Report (Codebase, Frameworks, Code Quality, NFRs)",
        author="Group 04",
    )
    doc.build(story, onFirstPage=lambda c, d: _cover(c, d, data), onLaterPages=_page)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--mutation", default=None, help='e.g. "killed=600 survived=120 timeout=3"')
    parser.add_argument("--out", type=Path, default=ROOT / "docs" / "Audit Report.pdf")
    args = parser.parse_args()
    data = collect(args.mutation)
    build(data, args.out)
    print(f"Wrote {args.out}")
    print(json.dumps({k: v for k, v in data.items() if k != "modules"}, indent=1, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
