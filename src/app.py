"""Customer-facing feedback form.

Submissions are classified with the same rule engine the bot uses, shown to the customer
straight away, and appended to data/feedback_live.csv - which main.py picks up on its next
run, so a submission made here flows through the UiPath process end to end.
"""
from __future__ import annotations

import argparse
import csv
import json
import re
import threading
from datetime import datetime, timedelta
from pathlib import Path

from flask import Flask, redirect, render_template, request, url_for

from classifier import FeedbackClassifier
from pipeline import REQUIRED_COLUMNS, EMAIL_RE, PRIORITY_ORDER

ROOT = Path(__file__).resolve().parent.parent
LIVE_CSV = ROOT / "data" / "feedback_live.csv"
RULES = ROOT / "config" / "rules.json"
SUMMARY_JSON = ROOT / "output" / "summary.json"
CHANNEL = "Web App"
PRIORITY_RANK = ["Urgent", "High", "Normal", "Low"]
SENTIMENT_RANK = ["Positive", "Neutral", "Negative"]

app = Flask(__name__)
app.config.update(LIVE_CSV=LIVE_CSV, RULES=RULES, SUMMARY_JSON=SUMMARY_JSON)
_write_lock = threading.Lock()


def classifier() -> FeedbackClassifier:
    # Re-read per request so edits to rules.json show up without restarting the server.
    return FeedbackClassifier.from_file(app.config["RULES"])


def read_live() -> list[dict]:
    path = Path(app.config["LIVE_CSV"])
    if not path.exists():
        return []
    with open(path, newline="", encoding="utf-8-sig") as fh:
        return [{k: (v or "").strip() for k, v in row.items()} for row in csv.DictReader(fh)]


def next_id(rows: list[dict]) -> str:
    """W0001, W0002, ... - the W keeps web tickets distinct from the batch file's F ids."""
    numbers = [int(m.group(1)) for r in rows
               if (m := re.fullmatch(r"W(\d+)", r.get("feedback_id", "")))]
    return f"W{max(numbers, default=0) + 1:04d}"


def append_live(row: dict) -> None:
    path = Path(app.config["LIVE_CSV"])
    path.parent.mkdir(parents=True, exist_ok=True)
    is_new = not path.exists()
    with open(path, "a", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=REQUIRED_COLUMNS, extrasaction="ignore")
        if is_new:
            writer.writeheader()
        writer.writerow(row)


@app.get("/")
def form():
    return render_template("form.html", values={}, errors={})


@app.post("/")
def submit():
    values = {k: request.form.get(k, "").strip() for k in
              ("customer_name", "email", "rating", "feedback_text")}
    errors = {}
    if not values["customer_name"]:
        errors["customer_name"] = "Please tell us your name."
    if not values["email"]:
        errors["email"] = "Please enter your email address."
    elif not EMAIL_RE.match(values["email"]):
        errors["email"] = "That does not look like an email address."
    if len(values["feedback_text"]) < 10:
        errors["feedback_text"] = "Please give us a little more detail (at least 10 characters)."
    if values["rating"] and values["rating"] not in list("12345"):
        errors["rating"] = "Rating must be between 1 and 5."
    if errors:
        return render_template("form.html", values=values, errors=errors), 400

    with _write_lock:
        rows = read_live()
        feedback_id = next_id(rows)
        append_live({
            "feedback_id": feedback_id,
            "date": datetime.now().strftime("%Y-%m-%d"),
            "customer_name": values["customer_name"],
            "email": values["email"],
            "channel": CHANNEL,
            "rating": values["rating"],
            "feedback_text": " ".join(values["feedback_text"].split()),
        })
    return redirect(url_for("thanks", feedback_id=feedback_id))


@app.get("/thanks/<feedback_id>")
def thanks(feedback_id: str):
    row = next((r for r in read_live() if r["feedback_id"] == feedback_id), None)
    if row is None:
        return redirect(url_for("form"))
    clf = classifier()
    rating = int(row["rating"]) if row["rating"].isdigit() else None
    result = clf.classify(row["feedback_text"], rating)
    due = datetime.now() + timedelta(hours=clf.rules["sla_hours"][result.priority])
    return render_template("result.html", row=row, result=result,
                           due=due.strftime("%d %b %Y, %H:%M"))


def _ranked(counts: dict[str, int]) -> list[dict]:
    """Sort a {label: count} dict highest-first and size each bar against the largest
    value, so the chart template only ever draws, never calculates."""
    biggest = max(counts.values(), default=0) or 1
    return [{"label": label, "count": n, "pct_of_max": round(n / biggest * 100)}
            for label, n in sorted(counts.items(), key=lambda kv: -kv[1])]


@app.get("/dashboard")
def dashboard():
    """Automation report: reads the bot's own output/summary.json. Closes the loop -
    customers submit here, the bot classifies and routes, this page reports on it."""
    path = Path(app.config["SUMMARY_JSON"])
    if not path.exists():
        return render_template("dashboard.html", summary=None)
    summary = json.loads(path.read_text(encoding="utf-8"))

    priorities = [{"label": p, "count": summary["by_priority"].get(p, 0)} for p in PRIORITY_RANK]

    sent_total = sum(summary["by_sentiment"].values()) or 1
    sentiments = [{"label": s, "count": summary["by_sentiment"].get(s, 0),
                   "pct": round(summary["by_sentiment"].get(s, 0) / sent_total * 100)}
                  for s in SENTIMENT_RANK]
    sentiment_summary = ", ".join(f"{s['label']} {s['pct']}%" for s in sentiments)

    return render_template(
        "dashboard.html", summary=summary,
        departments=_ranked(summary["by_department"]),
        categories=_ranked(summary["by_category"]),
        priorities=priorities, sentiments=sentiments,
        sentiment_summary=sentiment_summary,
    )


@app.get("/queue")
def queue():
    """Staff view: every live submission, classified, grouped by the department it routes to."""
    clf = classifier()
    by_dept: dict[str, list[dict]] = {}
    for row in read_live():
        rating = int(row["rating"]) if row["rating"].isdigit() else None
        result = clf.classify(row["feedback_text"], rating)
        by_dept.setdefault(result.department, []).append({**row, "result": result})
    for items in by_dept.values():
        items.sort(key=lambda i: PRIORITY_ORDER[i["result"].priority])
    total = sum(len(v) for v in by_dept.values())
    return render_template("queue.html", by_dept=dict(sorted(by_dept.items())), total=total)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Run the customer feedback web form.")
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=5000)
    p.add_argument("--debug", action="store_true")
    args = p.parse_args(argv)
    print(f"\n  Customer form : http://{args.host}:{args.port}/")
    print(f"  Staff queue   : http://{args.host}:{args.port}/queue")
    print(f"  Saving to     : {LIVE_CSV}\n")
    app.run(host=args.host, port=args.port, debug=args.debug)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
