"""Schema-drift handling: map whatever headers arrive onto the canonical columns.

Order of attempts (each source column maps to at most one canonical column):
  1. exact name match
  2. known synonym match
  3. token-containment / fuzzy match above a cutoff
  4. optional LLM fallback for whatever is still unmapped (opt-in)
"""
from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from typing import Callable, Iterable

import requests

_CAMEL = re.compile(r"(?<=[a-z0-9])(?=[A-Z])")


def normalize(name: object) -> str:
    """'Shipment ID' / 'shipmentId' / 'SHIPMENT-ID' -> 'shipment_id'."""
    s = _CAMEL.sub("_", str(name).strip())
    return re.sub(r"[^0-9a-zA-Z]+", "_", s).strip("_").lower()


@dataclass
class ColumnMapping:
    source: str
    canonical: str
    method: str  # exact | synonym | contains | fuzzy | llm
    score: float


@dataclass
class SchemaReport:
    mappings: list[ColumnMapping] = field(default_factory=list)
    missing_required: list[str] = field(default_factory=list)
    missing_optional: list[str] = field(default_factory=list)
    unknown_columns: list[str] = field(default_factory=list)

    @property
    def rename_map(self) -> dict[str, str]:
        return {m.source: m.canonical for m in self.mappings}

    @property
    def renamed(self) -> list[ColumnMapping]:
        return [m for m in self.mappings if m.source != m.canonical]

    @property
    def has_drift(self) -> bool:
        return bool(self.renamed or self.missing_required or self.missing_optional or self.unknown_columns)

    def to_dict(self) -> dict:
        return {
            "renamed": [
                {"source": m.source, "canonical": m.canonical, "method": m.method, "score": round(m.score, 2)}
                for m in self.renamed
            ],
            "missing_required": self.missing_required,
            "missing_optional": self.missing_optional,
            "unknown_columns": self.unknown_columns,
        }


LlmFn = Callable[[list[str], list[str], dict[str, list[str]]], dict[str, str | None]]


def _candidates(canonical: str, spec: dict) -> list[tuple[str, str]]:
    cands = [(normalize(canonical), "canonical")]
    cands += [(normalize(s), "synonym") for s in spec.get("synonyms", [])]
    return cands


def _best_score(src_norm: str, cands: list[tuple[str, str]], cutoff: float) -> tuple[float, str] | None:
    best: tuple[float, str] | None = None
    src_tokens = set(src_norm.split("_"))
    for cand, kind in cands:
        if src_norm == cand:
            score, method = (1.0, "exact") if kind == "canonical" else (0.97, "synonym")
        else:
            score, method = 0.0, "fuzzy"
            # Short names (eta, to, id) are too easy to confuse; require exact/synonym for them.
            if len(cand) >= 6 and len(src_norm) >= 4:
                score = SequenceMatcher(None, src_norm, cand).ratio()
            cand_tokens = cand.split("_")
            if len(cand_tokens) >= 2 and set(cand_tokens) <= src_tokens:
                if 0.9 > score:
                    score, method = 0.9, "contains"
            if score < cutoff:
                continue
        if best is None or score > best[0]:
            best = (score, method)
    return best


def map_schema(
    columns: Iterable[str],
    cfg: dict,
    samples: dict[str, list[str]] | None = None,
    llm_fn: LlmFn | None = None,
) -> SchemaReport:
    """Build a SchemaReport describing how source headers map to canonical columns."""
    columns = list(columns)
    spec_all: dict[str, dict] = cfg["schema"]["columns"]
    cutoff = float(cfg["schema"].get("fuzzy_cutoff", 0.82))

    pairs: list[tuple[float, int, str, str, str]] = []
    canon_order = {c: i for i, c in enumerate(spec_all)}
    for col in columns:
        src_norm = normalize(col)
        for canon, spec in spec_all.items():
            hit = _best_score(src_norm, _candidates(canon, spec), cutoff)
            if hit:
                pairs.append((hit[0], canon_order[canon], col, canon, hit[1]))

    pairs.sort(key=lambda p: (-p[0], p[1]))
    used_src: set[str] = set()
    used_canon: set[str] = set()
    report = SchemaReport()
    for score, _, col, canon, method in pairs:
        if col in used_src or canon in used_canon:
            continue
        used_src.add(col)
        used_canon.add(canon)
        report.mappings.append(ColumnMapping(col, canon, method, score))

    unmapped_src = [c for c in columns if c not in used_src]
    unmapped_canon = [c for c in spec_all if c not in used_canon]

    if llm_fn and unmapped_src and unmapped_canon:
        try:
            suggestion = llm_fn(unmapped_src, unmapped_canon, samples or {}) or {}
        except Exception:  # LLM help is best-effort; never break the audit
            suggestion = {}
        for src, canon in suggestion.items():
            if src in unmapped_src and canon in unmapped_canon and src not in used_src and canon not in used_canon:
                used_src.add(src)
                used_canon.add(canon)
                report.mappings.append(ColumnMapping(src, canon, "llm", 0.5))

    report.unknown_columns = [c for c in columns if c not in used_src]
    for canon, spec in spec_all.items():
        if canon not in used_canon:
            (report.missing_required if spec.get("required", True) else report.missing_optional).append(canon)
    return report


def anthropic_llm_mapper(model: str | None = None, api_key: str | None = None, timeout: int = 30) -> LlmFn:
    """Return an LlmFn backed by the Anthropic Messages API (opt-in).

    Only column headers and a few sample values per unmapped column are sent.
    """
    key = api_key or os.environ.get("ANTHROPIC_API_KEY")
    if not key:
        raise RuntimeError("ANTHROPIC_API_KEY is not set; cannot use the LLM column mapper.")
    model_name = model or os.environ.get("DQ_LLM_MODEL", "claude-sonnet-5-5")

    def _fn(unmapped_src: list[str], unmapped_canon: list[str], samples: dict[str, list[str]]):
        prompt = (
            "You map spreadsheet column headers to a fixed shipment schema.\n"
            f"Canonical columns: {unmapped_canon}\n"
            f"Unmapped source columns with sample values: "
            f"{json.dumps({c: samples.get(c, [])[:3] for c in unmapped_src})}\n"
            "Return ONLY a JSON object mapping each source column to a canonical column, "
            "or null if it matches none. Use each canonical column at most once."
        )
        resp = requests.post(
            "https://api.anthropic.com/v1/messages",
            headers={"x-api-key": key, "anthropic-version": "2023-06-01", "content-type": "application/json"},
            json={"model": model_name, "max_tokens": 500, "messages": [{"role": "user", "content": prompt}]},
            timeout=timeout,
        )
        resp.raise_for_status()
        text = "".join(b.get("text", "") for b in resp.json().get("content", []))
        start, end = text.find("{"), text.rfind("}")
        return json.loads(text[start : end + 1]) if start != -1 and end > start else {}

    return _fn
