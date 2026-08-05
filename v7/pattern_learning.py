"""Learns the user's drag/drop patterns and surfaces suggestions -- it
never acts on its own; predictions only ever get logged as an
informational suggestion in the activity log.

Every confirmed drop is logged to usage_history.jsonl (app title, source
zone, target zone, time-of-day features). A small incremental neural
network (sklearn's MLPClassifier, trained via partial_fit so it updates
after every single event rather than needing a batch retrain) learns to
predict which zone a given app tends to end up on at a given time of
day. This is a genuinely small model appropriate to how little data one
person generates by dragging windows around -- not a claim that it's
doing anything more sophisticated than that.
"""

import collections
import datetime
import html
import json
import os
import zlib

import joblib
import numpy as np
from sklearn.neural_network import MLPClassifier

HISTORY_PATH = os.path.join(os.path.dirname(__file__), "usage_history.jsonl")
MODEL_PATH = os.path.join(os.path.dirname(__file__), "pattern_model.joblib")

MIN_SAMPLES_FOR_PREDICTION = 8
CONFIDENCE_THRESHOLD = 0.6
APP_BUCKETS = 47  # stable hash bucket count for the app-title feature
SAVE_EVERY_N_SAMPLES = 1  # drops are infrequent; saving every time is cheap


def _app_bucket(app_title):
    if not app_title:
        return 0
    return zlib.crc32(app_title.encode("utf-8", "ignore")) % APP_BUCKETS


def _features(app_title, hour, weekday, source_zone, num_zones):
    return [
        _app_bucket(app_title) / APP_BUCKETS,
        hour / 24.0,
        weekday / 7.0,
        (source_zone if source_zone is not None else -1) / max(1, num_zones),
    ]


def log_event(app_title, source_zone, target_zone):
    now = datetime.datetime.now()
    entry = {
        "ts": now.timestamp(),
        "hour": now.hour,
        "weekday": now.weekday(),
        "app": app_title or "",
        "source_zone": source_zone,
        "target_zone": target_zone,
    }
    with open(HISTORY_PATH, "a") as f:
        f.write(json.dumps(entry) + "\n")


def load_history():
    if not os.path.exists(HISTORY_PATH):
        return []
    entries = []
    with open(HISTORY_PATH) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                entries.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return entries


class PatternLearner:
    """One instance per engine session. num_zones must match the current
    monitor count -- if it changes (a monitor got connected/disconnected)
    the model is retrained from scratch on the history log rather than
    trying to reconcile mismatched class counts."""

    def __init__(self, num_zones):
        self.num_zones = num_zones
        self.sample_count = 0
        self.model = None
        if num_zones < 2:
            return  # nothing meaningful to classify with a single monitor
        self._load_or_rebuild()

    def _load_or_rebuild(self):
        if os.path.exists(MODEL_PATH):
            try:
                saved = joblib.load(MODEL_PATH)
                if saved.get("num_zones") == self.num_zones:
                    self.model = saved["model"]
                    self.sample_count = saved.get("sample_count", 0)
                    return
            except (OSError, EOFError, KeyError, ValueError):
                pass
        self._rebuild_from_history()

    def _rebuild_from_history(self):
        # partial_fit is the incremental-learning API here; warm_start is a
        # separate (and conflicting) mechanism meant for repeated .fit()
        # calls, not for use alongside partial_fit. The learning rate is
        # raised well above sklearn's default (0.001): each partial_fit
        # call is a single gradient step on a single sample, and personal
        # usage generates at most tens of drops a day -- the default rate
        # would take far too many samples to converge on anything.
        self.model = MLPClassifier(hidden_layer_sizes=(16,), max_iter=1, random_state=0, learning_rate_init=0.02)
        self.sample_count = 0
        classes = list(range(self.num_zones))
        for entry in load_history():
            tz = entry.get("target_zone")
            if tz is None or tz >= self.num_zones:
                continue
            feats = _features(entry.get("app", ""), entry.get("hour", 12), entry.get("weekday", 0),
                               entry.get("source_zone"), self.num_zones)
            self.model.partial_fit([feats], [tz], classes=classes)
            self.sample_count += 1
        self._save()

    def _save(self):
        try:
            joblib.dump({"model": self.model, "num_zones": self.num_zones, "sample_count": self.sample_count},
                        MODEL_PATH)
        except OSError:
            pass

    def learn(self, app_title, source_zone, target_zone, hour, weekday):
        if self.model is None:
            return
        feats = _features(app_title, hour, weekday, source_zone, self.num_zones)
        self.model.partial_fit([feats], [target_zone], classes=list(range(self.num_zones)))
        self.sample_count += 1
        if self.sample_count % SAVE_EVERY_N_SAMPLES == 0:
            self._save()

    def predict(self, app_title, source_zone, hour, weekday):
        """Return (predicted_zone, confidence) if the model has enough
        data and is confident enough, else None. Purely informational --
        callers should only ever surface this as a suggestion."""
        if self.model is None or self.sample_count < MIN_SAMPLES_FOR_PREDICTION:
            return None
        feats = _features(app_title, hour, weekday, source_zone, self.num_zones)
        try:
            proba = self.model.predict_proba(np.array([feats]))[0]
        except Exception:
            return None
        best_idx = int(np.argmax(proba))
        confidence = float(proba[best_idx])
        if confidence < CONFIDENCE_THRESHOLD:
            return None
        return best_idx, confidence


REPORT_PATH = os.path.join(os.path.dirname(__file__), "usage_report.html")
WEEKDAY_NAMES = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]


def generate_report(monitors, path=None):
    """Write a self-contained HTML summary of logged usage to disk and
    return the path. This is a local file the user opens/shares
    themselves -- nothing here sends anything anywhere."""
    path = path or REPORT_PATH
    history = load_history()
    zone_names = {i: m["device"].replace("\\\\.\\", "") for i, m in enumerate(monitors)}

    def esc(s):
        return html.escape(str(s))

    if not history:
        body = "<p>No usage logged yet -- turn on pattern learning and drag a few windows around first.</p>"
    else:
        first_ts = min(e["ts"] for e in history)
        last_ts = max(e["ts"] for e in history)
        span = (f"{datetime.datetime.fromtimestamp(first_ts):%Y-%m-%d} to "
                f"{datetime.datetime.fromtimestamp(last_ts):%Y-%m-%d}")

        app_counts = collections.Counter(e["app"] for e in history if e.get("app"))
        zone_counts = collections.Counter(e["target_zone"] for e in history if e.get("target_zone") is not None)
        hour_counts = collections.Counter(e["hour"] for e in history)
        weekday_counts = collections.Counter(e["weekday"] for e in history)
        app_zone_pairs = collections.Counter(
            (e["app"], e["target_zone"]) for e in history if e.get("app") and e.get("target_zone") is not None
        )

        def bar_rows(counter, name_fn, total):
            rows = []
            for key, count in counter.most_common(10):
                pct = 100 * count / total if total else 0
                rows.append(
                    f"<tr><td>{esc(name_fn(key))}</td><td>{count}</td>"
                    f"<td><div class='bar' style='width:{pct:.0f}%'></div></td></tr>"
                )
            return "\n".join(rows)

        top_app_per_zone = {}
        for (app, zone), count in app_zone_pairs.items():
            if zone not in top_app_per_zone or count > top_app_per_zone[zone][1]:
                top_app_per_zone[zone] = (app, count)

        habits_rows = "\n".join(
            f"<tr><td>{esc(zone_names.get(z, f'zone {z}'))}</td><td>{esc(app)}</td><td>{count}</td></tr>"
            for z, (app, count) in sorted(top_app_per_zone.items())
        )

        body = f"""
        <p><b>{len(history)}</b> drops logged, {esc(span)}.</p>

        <h2>Most-moved apps</h2>
        <table>{bar_rows(app_counts, lambda k: k, len(history))}</table>

        <h2>Monitor usage</h2>
        <table>{bar_rows(zone_counts, lambda k: zone_names.get(k, f'zone {k}'), len(history))}</table>

        <h2>Busiest hours</h2>
        <table>{bar_rows(hour_counts, lambda k: f'{k:02d}:00', len(history))}</table>

        <h2>Busiest days</h2>
        <table>{bar_rows(weekday_counts, lambda k: WEEKDAY_NAMES[k], len(history))}</table>

        <h2>What usually goes where</h2>
        <table>
        <tr><th>Monitor</th><th>Most common app</th><th>Times</th></tr>
        {habits_rows}
        </table>
        """

    html_doc = f"""<!DOCTYPE html>
<html><head><meta charset="utf-8"><title>Hand Window Dragger -- Usage Report</title>
<style>
body {{ font-family: 'Segoe UI', sans-serif; max-width: 720px; margin: 32px auto; color: #222; }}
h1 {{ color: #0a5a6b; }}
h2 {{ color: #0a5a6b; margin-top: 28px; border-bottom: 1px solid #ddd; padding-bottom: 4px; }}
table {{ border-collapse: collapse; width: 100%; margin-top: 8px; }}
td, th {{ padding: 4px 8px; text-align: left; vertical-align: middle; }}
th {{ border-bottom: 2px solid #ccc; }}
.bar {{ background: #00b3cc; height: 12px; border-radius: 3px; }}
.generated {{ color: #888; font-size: 0.85em; margin-top: 32px; }}
</style></head>
<body>
<h1>Usage Report</h1>
{body}
<p class="generated">Generated {datetime.datetime.now():%Y-%m-%d %H:%M}. Local file only -- nothing here is sent anywhere.</p>
</body></html>"""

    with open(path, "w", encoding="utf-8") as f:
        f.write(html_doc)
    return path
