"""Gemini layer (spec 6). Five jobs, each with a strict response schema; nothing
else goes through the model, and no number it returns is written anywhere
without passing ``engine.validation`` first.

THE RULE: the model identifies food, parses words and writes prose. It never
computes a target, a rollup value or a guardrail.

Every call — including refused ones — writes an ``llm_calls`` row. Hard daily
cap. Photo identification is cached for 24 h by perceptual hash, so a
re-submitted photo costs nothing.
"""
from __future__ import annotations

import base64
import hashlib
import io
import json
import logging
import sqlite3
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any

from pydantic import BaseModel, Field, ValidationError

from .config import Settings
from .engine import validation

log = logging.getLogger("health.gemini")

GEMINI_TIMEOUT_S = 20
GEMINI_MAX_RETRIES = 2  # exponential backoff
GEMINI_DAILY_CALL_CAP = 80  # hard stop, logged
PHOTO_CACHE_HOURS = 24
IMAGE_MAX_EDGE = 1024
IMAGE_JPEG_QUALITY = 80
ENDPOINT = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"


class GeminiDisabled(Exception):
    """No API key configured. Callers fall back to manual entry."""


class GeminiUnavailable(Exception):
    """The call failed (network, 5xx, timeout, cap, bad output). Callers fall back."""


class CapReached(GeminiUnavailable):
    pass


# --- response schemas (Pydantic = validation; the dict form = Gemini responseSchema) ---

class FoodItemOut(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    confidence: float = Field(ge=0, le=1)
    estimated_grams: float | None = Field(default=None, ge=0, le=5000)
    kcal_100g: float = Field(ge=0, le=2000)
    protein_100g: float = Field(ge=0, le=200)
    carbs_100g: float = Field(ge=0, le=200)
    fat_100g: float = Field(ge=0, le=200)
    portion_basis: str = "none"


class FoodIdentifyOut(BaseModel):
    items: list[FoodItemOut] = Field(max_length=12)
    notes: str = ""


class TextItemOut(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    quantity_grams: float = Field(ge=0, le=5000)
    confidence: float = Field(ge=0, le=1)
    kcal_100g: float = Field(ge=0, le=2000)
    protein_100g: float = Field(ge=0, le=200)
    carbs_100g: float = Field(ge=0, le=200)
    fat_100g: float = Field(ge=0, le=200)


class TextParseOut(BaseModel):
    items: list[TextItemOut] = Field(max_length=20)
    notes: str = ""


class NarrativeOut(BaseModel):
    narrative: str = Field(min_length=20, max_length=3000)


class AnswerOut(BaseModel):
    answer: str = Field(min_length=1, max_length=2000)


class SeverityOut(BaseModel):
    severity: str = Field(pattern="^(mild|moderate|gi)$")
    fever_likely: bool
    rationale: str = Field(max_length=500)


class PhysiqueOut(BaseModel):
    body_fat_estimate_pct: float = Field(ge=3, le=60)
    muscularity_score: float = Field(ge=1, le=10)
    progress_to_reference_pct: float = Field(ge=0, le=100)
    actor_match_name: str = Field(min_length=1, max_length=80)
    actor_match_why: str = Field(max_length=600)
    visible_changes: str = Field(max_length=600)
    coaching_notes: str = Field(max_length=800)
    confidence: float = Field(ge=0, le=1)


def _schema(model: type[BaseModel]) -> dict:
    """Gemini's responseSchema is an OpenAPI subset: strip what it rejects."""
    js = model.model_json_schema()

    def clean(node: Any) -> Any:
        if isinstance(node, dict):
            out = {}
            for k, v in node.items():
                if k in ("title", "default", "$defs", "minLength", "maxLength", "minItems", "maxItems", "pattern", "exclusiveMinimum", "exclusiveMaximum", "minimum", "maximum"):
                    continue
                if k == "anyOf" and isinstance(v, list):
                    non_null = [x for x in v if x.get("type") != "null"]
                    if len(non_null) == 1:
                        out.update(clean(non_null[0]))
                        out["nullable"] = True
                        continue
                if k == "$ref":
                    name = v.split("/")[-1]
                    return clean(js["$defs"][name])
                out[k] = clean(v)
            return out
        if isinstance(node, list):
            return [clean(x) for x in node]
        return node

    return clean(js)


SYSTEM_FOOD = (
    "You identify foods in a photo for a nutrition log. Return per-100 g nutrition values for each distinct food. "
    "Give estimated_grams only when a size reference is visible (plate, hand, cutlery, packaging); otherwise leave it null "
    "and set portion_basis to 'none'. Set confidence below 0.5 when the portion or preparation is genuinely ambiguous. "
    "Prefer common preparations. Return several plausible candidates rather than one overconfident guess. "
    "Never invent nutrition for something you cannot see."
)
SYSTEM_TEXT = (
    "You parse a short free-text description of a meal into structured items with quantities in grams and per-100 g "
    "nutrition values. Convert household units (a cup, two slices, a tablespoon, a medium apple) to grams using typical "
    "sizes. Set confidence below 0.5 when a quantity is vague. Do not add foods that were not mentioned."
)
SYSTEM_NARRATIVE = (
    "You write a calm, specific weekly summary for one person's nutrition and training log, 150-250 words, plain prose, "
    "second person. Use ONLY the numbers given; never compute new ones, never suggest calorie or protein targets, never "
    "give medical advice. If the data says intake figures are estimates, say so. If a rehydration rebound after illness "
    "is present, explain that the weight change is water, not fat. No headings, no bullet points, no emojis."
)
SYSTEM_ASK = (
    "You answer one question about the user's own logged data, given as JSON. Answer briefly and concretely from the "
    "data only. If the data does not contain the answer, say so. Do not compute or suggest targets and do not give "
    "medical advice."
)
SYSTEM_SEVERITY = (
    "Given a short description of symptoms, propose an illness severity for a fitness tracker: 'mild' (above the neck: "
    "sniffles, sore throat, mild headache), 'moderate' (fever, body aches, fatigue, chest symptoms), or 'gi' (vomiting, "
    "diarrhoea, stomach bug). Set fever_likely when a fever is mentioned or probable. This is a suggestion the user "
    "confirms; keep the rationale to one sentence."
)
SYSTEM_PHYSIQUE = (
    "You assess a progress photo of the same person over time for a personal fitness log. Estimate body-fat percentage "
    "and a 1-10 muscularity score, describe visible changes versus the previous assessment if one is given, name the "
    "actor or athlete whose physique the person currently resembles most and why, and estimate progress toward the "
    "reference physique described, as a percentage. Be honest and specific, never flattering for its own sake, and keep "
    "a consistent scale with the previous assessment. Set confidence low if lighting, pose or clothing hide the body."
)


@dataclass(frozen=True)
class CallResult:
    data: dict
    cached: bool
    call_id: int | None
    input_tokens: int | None
    output_tokens: int | None
    latency_ms: int


class Gemini:
    def __init__(self, settings: Settings, conn: sqlite3.Connection, *, today: date):
        self.settings = settings
        self.conn = conn
        self.today = today

    # --- public jobs -------------------------------------------------------------

    def identify_food(self, image_jpeg: bytes) -> tuple[list[validation.FoodCandidate], str, int, CallResult]:
        """Photo -> validated candidates. Returns (candidates, notes, dropped, call)."""
        phash = perceptual_hash(image_jpeg)
        res = self._call("identify_food", SYSTEM_FOOD, "Identify the foods in this photo.", FoodIdentifyOut,
                         image_jpeg=image_jpeg, cache_key=f"phash:{phash}")
        out = FoodIdentifyOut.model_validate(res.data)
        candidates, dropped = [], 0
        for it in out.items:
            c = validation.FoodCandidate(name=it.name, confidence=it.confidence, kcal_100g=it.kcal_100g, protein_100g=it.protein_100g,
                                         carbs_100g=it.carbs_100g, fat_100g=it.fat_100g, estimated_grams=it.estimated_grams,
                                         portion_basis=it.portion_basis if it.portion_basis in ("reference_object", "plate_fraction", "none") else "none")  # type: ignore[arg-type]
            if validation.validate_candidate(c):
                dropped += 1
                continue
            candidates.append(c)
        return candidates, out.notes, dropped, res

    def parse_meal_text(self, text: str) -> tuple[list[dict], str, int, CallResult]:
        res = self._call("parse_meal_text", SYSTEM_TEXT, f"Meal description: {text.strip()[:600]}", TextParseOut)
        out = TextParseOut.model_validate(res.data)
        items, dropped = [], 0
        for it in out.items:
            c = validation.FoodCandidate(name=it.name, confidence=it.confidence, kcal_100g=it.kcal_100g, protein_100g=it.protein_100g,
                                         carbs_100g=it.carbs_100g, fat_100g=it.fat_100g, estimated_grams=it.quantity_grams or None)
            if validation.validate_candidate(c):
                dropped += 1
                continue
            items.append({"name": c.name, "confidence": c.confidence, "grams": it.quantity_grams, "kcal_100g": c.kcal_100g,
                          "protein_100g": c.protein_100g, "carbs_100g": c.carbs_100g, "fat_100g": c.fat_100g})
        return items, out.notes, dropped, res

    def weekly_narrative(self, summary: dict) -> tuple[str, CallResult]:
        payload = json.dumps(summary, default=str)[:12000]
        res = self._call("weekly_narrative", SYSTEM_NARRATIVE, f"Weekly data (JSON):\n{payload}", NarrativeOut)
        return NarrativeOut.model_validate(res.data).narrative.strip(), res

    def answer_query(self, question: str, rows: dict) -> tuple[str, CallResult]:
        payload = json.dumps(rows, default=str)[:20000]
        res = self._call("answer_query", SYSTEM_ASK, f"Question: {question.strip()[:400]}\n\nData (JSON):\n{payload}", AnswerOut)
        return AnswerOut.model_validate(res.data).answer.strip(), res

    def suggest_symptom_level(self, text: str) -> tuple[dict, CallResult]:
        res = self._call("suggest_symptom_level", SYSTEM_SEVERITY, f"Symptoms: {text.strip()[:500]}", SeverityOut)
        return SeverityOut.model_validate(res.data).model_dump(), res

    def analyze_physique(self, image_jpeg: bytes, *, reference_name: str, reference_description: str,
                         previous: dict | None, profile: dict | None) -> tuple[dict, CallResult]:
        context = {"reference_physique": {"name": reference_name, "description": reference_description},
                   "previous_assessment": previous, "profile": profile}
        prompt = "Assess this progress photo.\nContext (JSON):\n" + json.dumps(context, default=str)[:6000]
        res = self._call("analyze_physique", SYSTEM_PHYSIQUE, prompt, PhysiqueOut, image_jpeg=image_jpeg)
        return PhysiqueOut.model_validate(res.data).model_dump(), res

    # --- plumbing ------------------------------------------------------------------

    def calls_today(self) -> int:
        """Successful calls since UTC midnight — ``called_at`` is written by SQLite in UTC."""
        row = self.conn.execute("SELECT COUNT(*) FROM llm_calls WHERE called_at >= ? AND ok = 1", (_utc_day_start(),)).fetchone()
        return int(row[0])

    def _call(self, purpose: str, system: str, prompt: str, out_model: type[BaseModel], *, image_jpeg: bytes | None = None,
              cache_key: str | None = None) -> CallResult:
        if not self.settings.gemini_enabled:
            raise GeminiDisabled("GEMINI_API_KEY is not configured")
        if cache_key:
            hit = self._cached(purpose, cache_key)
            if hit is not None:
                return CallResult(hit, cached=True, call_id=None, input_tokens=0, output_tokens=0, latency_ms=0)
        if self.calls_today() >= GEMINI_DAILY_CALL_CAP:
            self._log(purpose, ok=False, error=f"daily cap {GEMINI_DAILY_CALL_CAP} reached", request_hash=cache_key, latency_ms=0)
            raise CapReached(f"daily Gemini call cap ({GEMINI_DAILY_CALL_CAP}) reached; manual entry until tomorrow")

        parts: list[dict] = [{"text": prompt}]
        if image_jpeg is not None:
            parts.append({"inline_data": {"mime_type": "image/jpeg", "data": base64.b64encode(image_jpeg).decode("ascii")}})
        body = {
            "system_instruction": {"parts": [{"text": system}]},
            "contents": [{"role": "user", "parts": parts}],
            "generationConfig": {"temperature": 0.2, "response_mime_type": "application/json", "response_schema": _schema(out_model)},
        }
        request_hash = cache_key or hashlib.sha256(json.dumps(body, sort_keys=True).encode()).hexdigest()[:32]

        started = time.perf_counter()
        last_error: str | None = None
        for attempt in range(GEMINI_MAX_RETRIES + 1):
            try:
                raw = self._post(body)
                text = raw["candidates"][0]["content"]["parts"][0]["text"]
                data = json.loads(text)
                out_model.model_validate(data)  # bounds before anything touches the DB
                usage = raw.get("usageMetadata") or {}
                latency = int((time.perf_counter() - started) * 1000)
                call_id = self._log(purpose, ok=True, error=None, request_hash=request_hash, latency_ms=latency,
                                    input_tokens=usage.get("promptTokenCount"), output_tokens=usage.get("candidatesTokenCount"),
                                    response_json=json.dumps(data))
                return CallResult(data, cached=False, call_id=call_id, input_tokens=usage.get("promptTokenCount"),
                                  output_tokens=usage.get("candidatesTokenCount"), latency_ms=latency)
            except (ValidationError, KeyError, IndexError, ValueError) as exc:
                last_error = f"bad output: {type(exc).__name__}: {str(exc)[:200]}"
                break  # a malformed answer is not worth retrying
            except TransientError as exc:
                last_error = str(exc)
                if attempt < GEMINI_MAX_RETRIES:
                    time.sleep(0.8 * (2**attempt))
                    continue
            except PermanentError as exc:
                last_error = str(exc)
                break
        latency = int((time.perf_counter() - started) * 1000)
        self._log(purpose, ok=False, error=last_error, request_hash=request_hash, latency_ms=latency)
        raise GeminiUnavailable(last_error or "unknown error")

    def _post(self, body: dict) -> dict:
        key = self.settings.gemini_api_key.get_secret_value() if self.settings.gemini_api_key else ""
        url = ENDPOINT.format(model=self.settings.gemini_model)
        req = urllib.request.Request(url, data=json.dumps(body).encode("utf-8"), method="POST",
                                     headers={"Content-Type": "application/json", "x-goog-api-key": key})
        try:
            with urllib.request.urlopen(req, timeout=GEMINI_TIMEOUT_S) as resp:  # noqa: S310
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", "replace")[:300]
            if exc.code in (429, 500, 502, 503, 504):
                raise TransientError(f"HTTP {exc.code}: {detail}") from exc
            raise PermanentError(f"HTTP {exc.code}: {detail}") from exc  # 400/401/403/404: never contains the key
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise TransientError(f"network: {exc}") from exc

    def _cached(self, purpose: str, cache_key: str) -> dict | None:
        cutoff = (datetime.utcnow() - timedelta(hours=PHOTO_CACHE_HOURS)).strftime("%Y-%m-%d %H:%M:%S")
        row = self.conn.execute(
            "SELECT response_json FROM llm_calls WHERE purpose = ? AND request_hash = ? AND ok = 1 AND called_at >= ? "
            "ORDER BY id DESC LIMIT 1", (purpose, cache_key, cutoff),
        ).fetchone()
        return json.loads(row[0]) if row and row[0] else None

    def _log(self, purpose: str, *, ok: bool, error: str | None, request_hash: str | None, latency_ms: int,
             input_tokens: int | None = None, output_tokens: int | None = None, response_json: str | None = None) -> int:
        cur = self.conn.execute(
            "INSERT INTO llm_calls (purpose, model, input_tokens, output_tokens, latency_ms, ok, error, request_hash, response_json) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (purpose, self.settings.gemini_model, input_tokens, output_tokens, latency_ms, int(ok), error, request_hash, response_json),
        )
        return int(cur.lastrowid)  # type: ignore[arg-type]


class TransientError(Exception):
    pass


class PermanentError(Exception):
    pass


# --- images --------------------------------------------------------------------

def prepare_image(data: bytes, *, max_edge: int = IMAGE_MAX_EDGE, quality: int = IMAGE_JPEG_QUALITY) -> bytes:
    """Resize to max_edge on the long side and re-encode as JPEG (spec 3): bounded
    memory, cheaper tokens, and the original is never kept."""
    from PIL import Image, ImageOps

    img = Image.open(io.BytesIO(data))
    img = ImageOps.exif_transpose(img)
    if img.mode not in ("RGB", "L"):
        img = img.convert("RGB")
    img.thumbnail((max_edge, max_edge))
    out = io.BytesIO()
    img.convert("RGB").save(out, format="JPEG", quality=quality, optimize=True)
    return out.getvalue()


def perceptual_hash(jpeg: bytes) -> str:
    """Average hash over an 8x8 grayscale thumbnail: identical or near-identical
    photos share it, so a re-submitted picture hits the cache."""
    from PIL import Image

    img = Image.open(io.BytesIO(jpeg)).convert("L").resize((8, 8))
    px = list(img.getdata())
    mean = sum(px) / len(px)
    bits = "".join("1" if p > mean else "0" for p in px)
    return f"{int(bits, 2):016x}"


def _utc_day_start() -> str:
    return datetime.utcnow().strftime("%Y-%m-%d 00:00:00")


def stats(conn: sqlite3.Connection, *, today: date) -> dict:
    start = _utc_day_start()
    day = conn.execute("SELECT COUNT(*), COALESCE(SUM(ok), 0) FROM llm_calls WHERE called_at >= ?", (start,)).fetchone()
    since = (datetime.utcnow() - timedelta(hours=24)).strftime("%Y-%m-%d %H:%M:%S")
    last24 = conn.execute("SELECT COUNT(*), COALESCE(SUM(ok), 0) FROM llm_calls WHERE called_at >= ?", (since,)).fetchone()
    total, ok = int(last24[0]), int(last24[1])
    return {
        "calls_today": int(day[0]), "ok_today": int(day[1]), "cap": GEMINI_DAILY_CALL_CAP,
        "cap_reached": int(day[1]) >= GEMINI_DAILY_CALL_CAP,
        "error_rate_24h": round((total - ok) / total, 3) if total else 0.0, "calls_24h": total,
    }


def recent_calls(conn: sqlite3.Connection, limit: int = 20) -> list[dict]:
    rows = conn.execute("SELECT id, called_at, purpose, model, input_tokens, output_tokens, latency_ms, ok, error FROM llm_calls "
                        "ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
    return [dict(r) for r in rows]


__all__ = ["Gemini", "GeminiDisabled", "GeminiUnavailable", "CapReached", "prepare_image", "perceptual_hash", "stats", "recent_calls"]
