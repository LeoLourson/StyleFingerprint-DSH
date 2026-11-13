# -*- coding: utf-8 -*-
"""Checks of the metric engine, catalogue and gate targets.

Run: .venv\\Scripts\\python.exe -m pytest tests/test_metrics.py
"""

import importlib.util
import json
import re
import subprocess
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
ANALYZE = ROOT / "tools" / "analyze.py"
FIXTURES = ROOT / "tests" / "fixtures"
CLEAN_TEXT = FIXTURES / "sample-clean.txt"
CORRUPTED_TEXT = FIXTURES / "sample-corrupted.txt"
SAMPLE = FIXTURES / "metrics-sample.json"

GROUP_MINIMUMS = {
    "syntax": 14,
    "morphology": 12,
    "morphology.depth": 8,
    "nominal": 4,
    "discourse": 10,
    "narrative": 8,
    "lexis": 6,
    "punctuation": 10,
    "paragraph": 4,
    "style": 14,
    "ai": 10,
}

UNITS = {"per1000", "share", "ratio", "mean", "count", "words", "entropy"}
NEEDS = {"none", "morphology", "syntax"}
PROFILES = {"chat_answer", "technical_doc", "business_letter", "prose_essay", "math_paper"}


def load_engine_module():
    spec = importlib.util.spec_from_file_location("analyze_engine", ANALYZE)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def load_json(path):
    with open(path, encoding="utf-8") as handle:
        return json.load(handle)


def read_text(path):
    with open(path, encoding="utf-8") as handle:
        return handle.read()


@pytest.fixture(scope="module")
def engine_module():
    return load_engine_module()


@pytest.fixture(scope="module")
def engine(engine_module):
    return engine_module.Engine()


@pytest.fixture(scope="module")
def catalogue():
    return load_json(ROOT / "data" / "metrics.json")


@pytest.fixture(scope="module")
def targets():
    return load_json(ROOT / "data" / "targets.json")


@pytest.fixture(scope="module")
def clean_result(engine):
    return engine.measure(read_text(CLEAN_TEXT))


def gate_violations(result, profile_targets):
    violations = []
    for metric_id, gate in profile_targets.items():
        value = result["metrics"].get(metric_id)
        if value is None or not (gate["min"] <= value <= gate["max"]):
            violations.append((metric_id, value))
    return violations


def test_catalogue_contract(catalogue):
    metrics = catalogue["metrics"]
    assert catalogue["version"] == 1
    assert set(catalogue["groups"]) == set(GROUP_MINIMUMS)
    ids = [item["id"] for item in metrics]
    assert len(ids) == len(set(ids)), "идентификаторы метрик повторяются"
    assert len(metrics) >= 100
    counts = {}
    for item in metrics:
        assert set(item) == {"id", "label", "unit", "needs", "group", "spec"}
        assert item["unit"] in UNITS
        assert item["needs"] in NEEDS
        assert item["group"] in GROUP_MINIMUMS
        assert item["label"].strip()
        assert item["spec"].strip()
        counts[item["group"]] = counts.get(item["group"], 0) + 1
    for group, minimum in GROUP_MINIMUMS.items():
        assert counts.get(group, 0) >= minimum, "группа %s меньше %d" % (group, minimum)


def test_specification_lists_every_metric(catalogue):
    text = read_text(ROOT / "reference" / "metrics.md")
    found = re.findall(r"^### (\d+\.\d+) `([^`]+)` — ", text, re.MULTILINE)
    ids = [metric_id for _, metric_id in found]
    expected = [item["id"] for item in catalogue["metrics"]]
    assert ids == expected
    specs = [spec for spec, _ in found]
    assert specs == [item["spec"] for item in catalogue["metrics"]]
    for metric_id in expected:
        assert "`%s`" % metric_id in text


def test_targets_contract(targets, catalogue):
    assert targets["version"] == 1
    assert set(targets["profiles"]) == PROFILES
    ids = {item["id"] for item in catalogue["metrics"]}
    for profile, gates in targets["profiles"].items():
        assert 8 <= len(gates) <= 14, "у профиля %s %d ворот" % (profile, len(gates))
        for metric_id, gate in gates.items():
            assert metric_id in ids, "ворота ссылаются на неизвестную метрику %s" % metric_id
            assert set(gate) == {"min", "max", "source"}
            assert gate["source"] in {"rule", "corpus", "author"}
            assert gate["min"] < gate["max"]


def test_specification_gate_tables_match_targets(targets):
    text = read_text(ROOT / "reference" / "metrics.md")
    sections = re.split(r"^### ([a-z_]+) — ", text, flags=re.MULTILINE)
    tables = {}
    for index in range(1, len(sections), 2):
        profile, body = sections[index], sections[index + 1]
        rows = re.findall(r"^\| `([^`]+)` \| (-?[\d.]+)\u2013(-?[\d.]+) \|", body,
                          re.MULTILINE)
        tables[profile] = {metric_id: (float(lo), float(hi)) for metric_id, lo, hi in rows}
    assert set(tables) == PROFILES
    for profile, gates in targets["profiles"].items():
        assert set(tables[profile]) == set(gates), profile
        for metric_id, gate in gates.items():
            lo, hi = tables[profile][metric_id]
            assert abs(lo - gate["min"]) < 1e-9, (profile, metric_id)
            assert abs(hi - gate["max"]) < 1e-9, (profile, metric_id)


def test_catalogue_action(engine, engine_module):
    answer = engine_module.handle(engine, {"id": "c1", "action": "catalogue"})
    assert answer["ok"] is True
    assert answer["id"] == "c1"
    assert len(answer["result"]["metrics"]) == len(engine.metric_ids)


def test_ping_action(engine, engine_module):
    answer = engine_module.handle(engine, {"id": "p1", "action": "ping"})
    assert answer["ok"] is True
    assert answer["result"]["metrics"] == len(engine.metric_ids)
    assert answer["result"]["version"] == "0.1"


def test_all_metrics_computed(catalogue, clean_result):
    expected = [item["id"] for item in catalogue["metrics"]]
    assert clean_result["unavailable"] == {}
    assert list(clean_result["metrics"]) == expected
    for metric_id, value in clean_result["metrics"].items():
        assert isinstance(value, (int, float)), metric_id
        assert value == value, metric_id
        assert abs(value) != float("inf"), metric_id


def test_counts(clean_result):
    counts = clean_result["counts"]
    assert counts == {"words": 332, "sentences": 26, "paragraphs": 6}


def test_repeated_measure_is_identical(engine):
    text = read_text(CLEAN_TEXT)
    first = engine.measure(text)
    second = engine.measure(text)
    assert first == second


def test_repeated_process_gives_same_numbers():
    request = json.dumps({"id": "d", "action": "measure", "text": read_text(CLEAN_TEXT),
                          "options": {"language": "ru"}}, ensure_ascii=False)
    outputs = []
    for _ in range(2):
        completed = subprocess.run(
            [sys.executable, str(ANALYZE), "--stdin"],
            input=request, capture_output=True, text=True, encoding="utf-8",
            cwd=str(ROOT), timeout=120)
        assert completed.returncode == 0, completed.stderr
        outputs.append(completed.stdout.strip())
    assert outputs[0] == outputs[1]


def test_fixture_values(clean_result):
    sample = load_json(SAMPLE)
    assert sample["counts"] == clean_result["counts"]
    assert sample["unavailable"] == clean_result["unavailable"]
    assert set(sample["metrics"]) == set(clean_result["metrics"])
    for metric_id, expected in sample["metrics"].items():
        assert abs(clean_result["metrics"][metric_id] - expected) <= 1e-6, metric_id


def test_clean_sample_passes_prose_essay(clean_result, targets):
    violations = gate_violations(clean_result, targets["profiles"]["prose_essay"])
    assert violations == []


def test_corrupted_sample_leaves_prose_targets(engine, targets):
    result = engine.measure(read_text(CORRUPTED_TEXT))
    violations = gate_violations(result, targets["profiles"]["prose_essay"])
    assert len(violations) >= 3, violations
    violated = {metric_id for metric_id, _ in violations}
    assert "ai.marker.per1000" in violated
    assert "style.cliche.per1000" in violated


def test_serve_mode():
    process = subprocess.Popen(
        [sys.executable, str(ANALYZE), "--serve"],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
        text=True, encoding="utf-8", cwd=str(ROOT))
    try:
        ready_line = process.stdout.readline()
        ready = json.loads(ready_line)
        assert ready["ready"] is True
        assert ready["version"] == "0.1"
        assert ready["metrics"] >= 100

        def request(payload):
            process.stdin.write(json.dumps(payload, ensure_ascii=False) + "\n")
            process.stdin.flush()
            return json.loads(process.stdout.readline())

        pong = request({"id": "1", "action": "ping"})
        assert pong["ok"] is True and pong["id"] == "1"
        catalogue_answer = request({"id": "2", "action": "catalogue"})
        assert len(catalogue_answer["result"]["metrics"]) == ready["metrics"]
        text = read_text(CLEAN_TEXT)
        started = time.time()
        answer = request({"id": "3", "action": "measure", "text": text,
                          "options": {"language": "ru"}})
        elapsed = time.time() - started
        assert answer["ok"] is True
        assert answer["result"]["counts"]["words"] > 300
        assert answer["result"]["unavailable"] == {}
        assert elapsed < 2.0, "ответ за %.2f с после прогрева" % elapsed
        broken = request({"id": "4", "action": "measure", "text": "   "})
        assert broken["ok"] is False
        assert broken["error"]
        after = request({"id": "5", "action": "ping"})
        assert after["ok"] is True
    finally:
        if process.stdin:
            process.stdin.close()
        process.wait(timeout=20)


def test_missing_syntax_reports_unavailable(engine_module, catalogue, targets):
    engine = engine_module.Engine(enable_syntax=False)
    result = engine.measure(read_text(CLEAN_TEXT))
    syntax_ids = {item["id"] for item in catalogue["metrics"]
                  if item["needs"] == "syntax"}
    assert syntax_ids
    assert all(metric_id in result["unavailable"] for metric_id in syntax_ids)
    assert all("natasha" in result["unavailable"][metric_id] for metric_id in syntax_ids)
    surface_ids = {item["id"] for item in catalogue["metrics"]
                   if item["needs"] != "syntax"}
    assert surface_ids - set(result["metrics"]) == set()
    prose = targets["profiles"]["prose_essay"]
    judged = [metric_id for metric_id in prose if metric_id in result["metrics"]]
    assert judged, "без синтаксиса не осталось ни одних ворот"


def test_headings_are_not_paragraphs(engine_module):
    """Заголовки разметки — не абзацы прозы: иначе средняя длина абзаца падает
    до трёх слов у любого структурированного документа."""
    text = "## Первый раздел\n\nПервый абзац прозы.\n\n## Второй раздел\n\nВторой абзац прозы."
    assert engine_module.split_paragraphs(text) == ["Первый абзац прозы.", "Второй абзац прозы."]
