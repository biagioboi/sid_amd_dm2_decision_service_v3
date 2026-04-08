from __future__ import annotations

"""Tiny DMN Decision Table runtime.

This is a minimal DMN (XML) evaluator sufficient for the included decision tables.
It supports a small FEEL subset in inputEntry <text>:

  - '-' (wildcard)
  - true / false
  - numbers
  - string literals in double quotes
  - comparisons: =, !=, <, <=, >, >=
  - membership: in("A","B")  OR  in ["A","B"]  OR  "A" in ["A","B"]

Hit policy supported: FIRST (default).
"""

import re
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any, Dict, List, Tuple

import yaml


BASE_DIR = Path(__file__).resolve().parent.parent
DMN_XML_DIR = BASE_DIR / "knowledge" / "dmn_xml"
DMN_YAML_DIR = BASE_DIR / "knowledge" / "dmn"  # legacy fallback


def _get_value(facts: Dict[str, Any], dotted: str) -> Any:
    value: Any = facts
    for part in dotted.split("."):
        if isinstance(value, dict):
            value = value.get(part)
        else:
            return None
    return value


def _parse_literal(token: str) -> Any:
    token = token.strip()
    if token.lower() == "true":
        return True
    if token.lower() == "false":
        return False
    if token.startswith('"') and token.endswith('"'):
        return token[1:-1]
    # number
    try:
        if "." in token:
            return float(token)
        return int(token)
    except ValueError:
        return token


_CMP_RE = re.compile(r"^(?P<op>=|!=|<=|>=|<|>)\s*(?P<rhs>.+)$")


def _match_feel(actual: Any, feel: str) -> bool:
    feel = (feel or "").strip()
    if feel in ("", "-"):
        return True

    # membership: in("A","B")
    if feel.startswith("in(") and feel.endswith(")"):
        inside = feel[3:-1]
        parts = [p.strip() for p in inside.split(",") if p.strip()]
        values = [_parse_literal(p) for p in parts]
        return actual in values

    # membership: in [..]
    if feel.startswith("in "):
        rhs = feel[3:].strip()
        if rhs.startswith("[") and rhs.endswith("]"):
            values = yaml.safe_load(rhs)  # parse list
            return actual in values

    # expression like: "HF" in ["HF","X"]
    if " in " in feel:
        left, rhs = feel.split(" in ", 1)
        left_val = _parse_literal(left)
        if rhs.strip().startswith("[") and rhs.strip().endswith("]"):
            values = yaml.safe_load(rhs.strip())
            return left_val in values

    m = _CMP_RE.match(feel)
    if m:
        op = m.group("op")
        rhs = _parse_literal(m.group("rhs"))
        if op == "=":
            return actual == rhs
        if op == "!=":
            return actual != rhs
        if actual is None:
            return False
        if op == "<":
            return actual < rhs
        if op == "<=":
            return actual <= rhs
        if op == ">":
            return actual > rhs
        if op == ">=":
            return actual >= rhs
    # Fallback: direct literal compare
    return actual == _parse_literal(feel)


def _ns(tag: str) -> str:
    # Support both DMN 1.2/1.3 namespaces and no-namespace.
    return tag


def _load_dmn_xml(name: str) -> ET.Element:
    path = DMN_XML_DIR / f"{name}.dmn"
    if not path.exists():
        raise FileNotFoundError(path)
    return ET.parse(path).getroot()


def _find(root: ET.Element, local_name: str) -> List[ET.Element]:
    out = []
    for el in root.iter():
        if el.tag.endswith(local_name):
            out.append(el)
    return out


def _text(el: ET.Element) -> str:
    return (el.text or "").strip()


def evaluate_table(name: str, facts: Dict[str, Any]) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]], Dict[str, Any]]:
    """Evaluate a decision table by name.

    Returns: (outputs, matched_rules, meta)
    """
    # Prefer DMN XML; fall back to legacy YAML if needed.
    try:
        root = _load_dmn_xml(name)
        return _evaluate_dmn_root(root, facts)
    except FileNotFoundError:
        return _evaluate_legacy_yaml(name, facts)


def _evaluate_legacy_yaml(name: str, facts: Dict[str, Any]) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]], Dict[str, Any]]:
    table_path = DMN_YAML_DIR / f"{name}.yaml"
    with table_path.open("r", encoding="utf-8") as handle:
        table = yaml.safe_load(handle)

    matched_rules: List[Dict[str, Any]] = []
    outputs: List[Dict[str, Any]] = []
    for rule in table.get("rules", []):
        clauses = rule.get("when", [])
        ok = True
        for c in clauses:
            actual = _get_value(facts, c["field"])
            op = c.get("op", "eq")
            expected = c.get("value")
            if op == "eq" and actual != expected:
                ok = False
                break
            if op == "lt" and not (actual is not None and actual < expected):
                ok = False
                break
        if ok:
            matched_rules.append(rule)
            outputs.append(rule.get("then", {}))
            if table.get("hitPolicy", "FIRST") == "FIRST":
                break
    return outputs, matched_rules, table


def _evaluate_dmn_root(root: ET.Element, facts: Dict[str, Any]) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]], Dict[str, Any]]:
    decision_tables = _find(root, "decisionTable")
    if not decision_tables:
        raise ValueError("No decisionTable found in DMN")
    dt = decision_tables[0]
    hit_policy = dt.attrib.get("hitPolicy", "FIRST")

    inputs = []
    for inp in [x for x in dt if x.tag.endswith("input")]:
        input_expr = None
        for child in inp.iter():
            if child.tag.endswith("inputExpression"):
                # text inside inputExpression/text
                for t in child.iter():
                    if t.tag.endswith("text"):
                        input_expr = _text(t)
        inputs.append(input_expr or "")

    outputs_meta = []
    for out in [x for x in dt if x.tag.endswith("output")]:
        outputs_meta.append(out.attrib.get("name") or out.attrib.get("label") or "output")

    outputs: List[Dict[str, Any]] = []
    matched_rules: List[Dict[str, Any]] = []

    for idx, rule in enumerate([x for x in dt if x.tag.endswith("rule")], start=1):
        input_entries = [x for x in rule if x.tag.endswith("inputEntry")]
        output_entries = [x for x in rule if x.tag.endswith("outputEntry")]

        ok = True
        for i, inp_expr in enumerate(inputs):
            actual = _get_value(facts, inp_expr)
            feel_text = "-"
            if i < len(input_entries):
                # inputEntry/text
                tnode = None
                for t in input_entries[i].iter():
                    if t.tag.endswith("text"):
                        tnode = t
                        break
                feel_text = _text(tnode) if tnode is not None else "-"
            if not _match_feel(actual, feel_text):
                ok = False
                break
        if not ok:
            continue

        out_obj: Dict[str, Any] = {}
        for j, out_name in enumerate(outputs_meta):
            text_node = None
            if j < len(output_entries):
                for t in output_entries[j].iter():
                    if t.tag.endswith("text"):
                        text_node = t
                        break
            raw = _text(text_node) if text_node is not None else ""
            # If output is JSON-like, parse with yaml.safe_load
            try:
                val = yaml.safe_load(raw)
            except Exception:
                val = raw
            out_obj[out_name] = val

        matched_rules.append({"id": f"{root.attrib.get('name', 'DMN')}-R{idx}", "then": out_obj})
        outputs.append(out_obj)
        if hit_policy.upper() == "FIRST":
            break

    meta = {"name": root.attrib.get("name", ""), "hitPolicy": hit_policy}
    return outputs, matched_rules, meta
