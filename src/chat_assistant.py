"""Free-text request understanding for the chat workspace (no Streamlit imports)."""

from __future__ import annotations

import json
import re
from typing import Callable, Dict, List, Optional

TEST_TYPES = ("unit_test", "integration_test", "e2e_test", "security")
MODES = ("recommended", "continue", "all")
CUSTOM_MODE = "custom"
ENGINES = ("vio", "ollama")
INTENTS = ("generate", "rerun", "status", "count", "other")

_TYPE_PATTERNS = {
    "unit_test": r"\bunit\b",
    "integration_test": r"\bintegration\b",
    "e2e_test": r"\be2e\b|end[- ]to[- ]end",
    "security": r"\bsecurity\b|\bvulnerab\w*",
}
_URL_RE = re.compile(r"https?://[^\s/<>\"')]*github[^\s/<>\"')]*/[\w.-]+/[\w.-]+", re.IGNORECASE)
_TARGET_RES = (
    re.compile(r"\b(?:class|function|method|component|module)\s+`?([A-Za-z_][\w.]*)`?", re.IGNORECASE),
    re.compile(r"`?\b([A-Za-z_][\w.]*)`?\s+(?:class|function|method|component|module)\b", re.IGNORECASE),
)
_CAPITAL_TARGET_RE = re.compile(r"\b(?:for|of|on|around|covering)\s+(?:the\s+)?`?([A-Z][A-Za-z0-9_.]*)`?")
_STOP_TARGETS = {"the", "a", "an", "this", "that", "each", "every", "all", "public", "main", "my", "its", "of"}

HELP_TEXT = (
    "I generate pytest specs and runnable test scripts for a GitHub repository. Share the repository link and "
    "the test type you need (unit, integration, e2e or security). You can also ask me to re-run failed specs "
    "or report progress."
)


def new_request() -> Dict:
    return {"repo_url": "", "repo_name": "", "test_types": [], "target": "", "max_components": 0,
            "mode": "", "engine": "", "confirmed": False}


def interpret_locally(text: str, repo_names: List[str]) -> Dict:
    """Rule-based extraction; works offline and is the fallback when the model is unavailable."""
    lowered = text.lower()
    found: Dict = {"intent": "other"}

    match = _URL_RE.search(text)
    if match:
        url = match.group(0).rstrip(".,;:!?")
        found["repo_url"] = url[:-4] if url.endswith(".git") else url
    for name in sorted(repo_names, key=len, reverse=True):
        if re.search(rf"(?<![\w-]){re.escape(name.lower())}(?![\w-])", lowered):
            found["repo_name"] = name
            break

    types = [kind for kind, pattern in _TYPE_PATTERNS.items() if re.search(pattern, lowered)]
    if types:
        found["test_types"] = types
    for engine in ENGINES:
        if re.search(rf"\b{engine}\b", lowered):
            found["engine"] = engine

    count = re.search(r"\b(?:first|top|next)?\s*(\d{1,3})\s+(?:components?|specs?|classes|functions?|items?)\b", lowered)
    if count:
        found["max_components"] = int(count.group(1))

    if re.search(r"\ball (?:the )?(?:meaningful )?(?:components|classes|functions)\b|\beverything\b", lowered):
        found["mode"] = "all"
    elif re.search(r"\bcontinue\b|\bremaining\b|\buncovered\b|\bthe rest\b|\bmore components\b", lowered):
        found["mode"] = "continue"
    elif re.search(r"\bfresh\b|from scratch|start over", lowered):
        found["mode"] = "recommended"

    for pattern in _TARGET_RES:
        for hit in pattern.finditer(text):
            if hit.group(1).lower() not in _STOP_TARGETS:
                found["target"] = hit.group(1)
                break
        if found.get("target"):
            break
    if not found.get("target"):
        hit = _CAPITAL_TARGET_RE.search(text)
        if hit and hit.group(1).lower() not in _STOP_TARGETS:
            found["target"] = hit.group(1)
    repo_words = {name.lower() for name in repo_names}
    if found.get("target", "").lower() in repo_words or (
            found.get("repo_url") and found.get("target", "").lower() == found["repo_url"].rsplit("/", 1)[-1].lower()):
        del found["target"]

    if re.search(r"\bhow many\b|\bnumber of (?:components|classes|functions|specs)\b", lowered):
        found["intent"] = "count"
    elif re.search(r"\bre-?run\b|\bretry\b|\btry again\b|\bfailed (?:specs?|tests?)\b", lowered):
        found["intent"] = "rerun"
    elif re.search(r"\bstatus\b|\bprogress\b|how(?:'s| is)? it going|\bdone yet\b|\bresults?\b", lowered):
        found["intent"] = "status"
    elif re.search(r"\b(?:generate|create|write|build|make|cover|test|tests|specs?|add)\b", lowered) or types or match:
        found["intent"] = "generate"
    return found


_SYSTEM_PROMPT = (
    "You turn a user's chat message about generating Python tests into JSON. Reply with ONLY a JSON object with "
    'keys: "intent" (generate|rerun|status|count|other), "repo_url" (GitHub URL or ""), "repo_name" (one of the known '
    'repositories or ""), "test_types" (subset of unit_test, integration_test, e2e_test, security), "target" '
    '(class/function/module name the user wants tested or ""), "max_components" (integer, 0 if unspecified), '
    '"mode" (recommended|continue|all or ""), "reply" (one short friendly sentence, only when intent is '
    "other: greet or ask which repository and component to test, and make no claims about limits or counts). "
    "Use empty values for anything the user did not say. Never invent a repository."
)


def interpret_with_model(
    chat: Callable[..., dict], text: str, history: List[Dict], repo_names: List[str], current: Dict,
) -> Optional[Dict]:
    """Ask the model for structured intent; returns None when the call or the JSON is unusable."""
    recent = "\n".join(f"{m['role']}: {m['content'][:300]}" for m in history[-6:])
    prompt = (
        f"Known cloned repositories: {', '.join(repo_names) or 'none'}\n"
        f"Request so far: {json.dumps(current)}\n"
        f"Recent conversation:\n{recent}\n\nNew user message: {text}"
    )
    try:
        result = chat(prompt, system=_SYSTEM_PROMPT)
    except Exception:
        return None
    if not result.get("ok"):
        return None
    block = re.search(r"\{.*\}", result.get("content", ""), re.DOTALL)
    if not block:
        return None
    try:
        data = json.loads(block.group(0))
    except ValueError:
        return None
    return data if isinstance(data, dict) else None


def _clean(data: Dict, repo_names: List[str], text: str) -> Dict:
    """Keep only well-formed fields from model output; scope fields come from explicit wording only."""
    cleaned: Dict = {}
    if data.get("intent") in INTENTS:
        cleaned["intent"] = data["intent"]
    url = str(data.get("repo_url") or "")
    match = _URL_RE.search(url)
    if match:
        cleaned["repo_url"] = match.group(0)
    name = str(data.get("repo_name") or "")
    if name in repo_names:
        cleaned["repo_name"] = name
    target = str(data.get("target") or "").strip()
    if re.fullmatch(r"[A-Za-z_][\w.]*", target) and target.lower() in text.lower():
        cleaned["target"] = target
    if isinstance(data.get("reply"), str) and data["reply"].strip():
        cleaned["reply"] = data["reply"].strip()
    return cleaned


def interpret(
    text: str, history: List[Dict], repo_names: List[str], current: Dict,
    chat: Optional[Callable[..., dict]] = None,
) -> Dict:
    """Combine rule-based and model extraction; rule-based fields fill any gap the model leaves."""
    parsed = interpret_locally(text, repo_names)
    if chat is not None:
        modeled = interpret_with_model(chat, text, history, repo_names, current)
        if modeled:
            parsed = {**parsed, **_clean(modeled, repo_names, text)}
    return parsed


def merge_request(current: Dict, parsed: Dict) -> Dict:
    """Later messages refine the request; a new repository starts a fresh target and scope."""
    merged = {**current}
    new_repo = (parsed.get("repo_url") and parsed["repo_url"] != current["repo_url"]) or (
        parsed.get("repo_name") and parsed["repo_name"] != current["repo_name"])
    if new_repo:
        merged.update(repo_url="", repo_name="", target="", max_components=0, mode="")
    for key in ("repo_url", "repo_name", "test_types", "target", "max_components", "mode", "engine"):
        if parsed.get(key):
            merged[key] = parsed[key]
    if parsed.get("repo_url"):
        merged["repo_name"] = ""
    elif parsed.get("repo_name"):
        merged["repo_url"] = ""
    return merged


def missing_question(request: Dict, repo_names: List[str]) -> str:
    """The one question needed before generation can start, or '' when the request is complete."""
    if request["repo_url"] or request["repo_name"]:
        return ""
    known = f" Already cloned: {', '.join(repo_names)}." if repo_names else ""
    return "Please share the GitHub repository link." + known


def type_label(test_types: List[str]) -> str:
    return ", ".join(t.replace("_test", "").replace("_", " ") for t in (test_types or ["unit_test"]))


def describe_request(request: Dict, repo_label: str) -> str:
    scope = f" for `{request['target']}`" if request["target"] else ""
    return f"Generating {type_label(request['test_types'])} tests{scope} in {repo_label}."
