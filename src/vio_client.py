"""
VIO Client - interface to the Aumovio VIO gateway (5 specialized agents).

All requests go to a single OpenAI-style endpoint:
    POST {VIO_API_BASE}/chat/completions
with the AGENT NAME used as the `model` field. Auth is a Bearer token read from
the environment (never hard-coded).

Environment variables (loaded from .env via python-dotenv in app.py):
    VIO_API_KEY    Bearer token                                   (required)
    VIO_API_BASE   e.g. https://vio.automotive-wan.com:446        (required)

Security notes:
    - TLS verification is disabled (verify=False) for the internal self-signed
      gateway, as required. The InsecureRequestWarning is suppressed.
    - The token is only ever sent in the Authorization header, never logged.

Public API:
    ping()                              -> bool
    chat(agent, prompt, ...)            -> dict {content, tokens, time_s, ok, error}
    generate_test(agent, prompt, ...)   -> dict (same shape; content = test code)
    validate(test_code, source)         -> dict {level, reasons, ok}
    fix_test(test_code, failures, src)  -> dict {content, ...}
    report(summary_text)                -> dict {content, ...}
"""

from __future__ import annotations

import os
import time
import json
import logging
from typing import Optional, Dict, List

import requests
from urllib3.exceptions import InsecureRequestWarning

requests.packages.urllib3.disable_warnings(InsecureRequestWarning)

logger = logging.getLogger(__name__)

# --- Agent registry -------------------------------------------------------
# The value is what we send in the `model` field of the request.
AGENTS: Dict[str, str] = {
    "master": "test_generation_master",       # Claude 5.0 Opus  - brain
    "unit": "test_unit_generator",              # GPT-5.4-nano
    "integration": "test_integration_generator",# DeepSeek-V4-Flash
    "e2e": "test_e2e_generator",                # Gemini-3.5-flash
    "security": "test_security_scanner",        # Claude 4.8 Opus
}

# Human-readable underlying model per agent (for the UI only).
AGENT_MODELS: Dict[str, str] = {
    "test_generation_master": "Claude 5.0 Opus",
    "test_unit_generator": "GPT-5.4-nano",
    "test_integration_generator": "DeepSeek-V4-Flash",
    "test_e2e_generator": "Gemini-3.5-flash",
    "test_security_scanner": "Claude 4.8 Opus",
}

# Map a UI test type to the agent that handles it.
TYPE_TO_AGENT: Dict[str, str] = {
    "unit_test": "test_unit_generator",
    "integration_test": "test_integration_generator",
    "e2e_test": "test_e2e_generator",
    "api_test": "test_e2e_generator",
    "security": "test_security_scanner",
    "vulnerability": "test_security_scanner",
    "linting": "test_unit_generator",
}


class VIOClient:
    """Client for the Aumovio VIO multi-agent gateway."""

    def __init__(
        self,
        base_url: Optional[str] = None,
        api_key: Optional[str] = None,
        timeout: int = 120,
    ):
        self.base_url = (base_url or os.getenv("VIO_API_BASE", "")).rstrip("/")
        self.api_key = api_key or os.getenv("VIO_API_KEY", "")
        self.timeout = timeout
        self.token_usage: Dict[str, int] = {}  # per-agent cumulative tokens

    # ------------------------------------------------------------------ config
    @property
    def is_configured(self) -> bool:
        return bool(self.base_url and self.api_key)

    @property
    def endpoint(self) -> str:
        return f"{self.base_url}/chat/completions"

    def _headers(self) -> Dict[str, str]:
        return {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

    # ------------------------------------------------------------------ low-level
    def _post(self, agent: str, messages: List[Dict], max_tokens: int,
              temperature: float) -> dict:
        """POST to /chat/completions. Returns a normalized result dict."""
        result = {"content": "", "tokens": 0, "time_s": 0.0, "ok": False, "error": ""}
        if not self.is_configured:
            result["error"] = "VIO not configured (set VIO_API_BASE and VIO_API_KEY)."
            return result

        payload = {
            "model": agent,
            "messages": messages,
            "max_tokens": max_tokens,
            "temperature": temperature,
        }
        start = time.time()
        try:
            resp = requests.post(
                self.endpoint,
                headers=self._headers(),
                json=payload,
                timeout=self.timeout,
                verify=False,
            )
            result["time_s"] = time.time() - start
            if resp.status_code != 200:
                result["error"] = f"HTTP {resp.status_code}: {resp.text[:200]}"
                logger.error(f"✗ VIO {agent} {result['error']}")
                return result

            data = resp.json()
            result["content"] = self._extract_content(data)
            result["tokens"] = self._extract_tokens(data)
            result["ok"] = True
            self.token_usage[agent] = self.token_usage.get(agent, 0) + result["tokens"]
            logger.info(f"✓ VIO {agent}: {result['tokens']} tok in {result['time_s']:.1f}s")
        except requests.exceptions.Timeout:
            result["error"] = f"Timeout after {self.timeout}s"
        except requests.exceptions.ConnectionError as e:
            result["error"] = f"Connection error: {e}"
        except Exception as e:  # pragma: no cover - defensive
            result["error"] = f"{type(e).__name__}: {e}"
        if result["error"]:
            logger.error(f"✗ VIO {agent} failed: {result['error']}")
        return result

    @staticmethod
    def _extract_content(data: dict) -> str:
        """Pull the assistant text from an OpenAI-style response (robust)."""
        try:
            choices = data.get("choices") or []
            if choices:
                msg = choices[0].get("message") or {}
                if msg.get("content"):
                    return msg["content"]
                # Some gateways use "text" instead of message.content
                if choices[0].get("text"):
                    return choices[0]["text"]
            # Fallbacks for non-standard shapes
            for key in ("content", "response", "output", "result"):
                if isinstance(data.get(key), str):
                    return data[key]
        except Exception:
            pass
        return ""

    @staticmethod
    def _extract_tokens(data: dict) -> int:
        usage = data.get("usage") or {}
        return int(usage.get("total_tokens", 0) or 0)

    # ------------------------------------------------------------------ health
    def ping(self) -> bool:
        """Lightweight connectivity check against the master agent."""
        if not self.is_configured:
            return False
        res = self._post(
            AGENTS["master"],
            [{"role": "user", "content": "ping"}],
            max_tokens=5,
            temperature=0.0,
        )
        return res["ok"]

    # ------------------------------------------------------------------ high-level
    def chat(self, agent: str, prompt: str, max_tokens: int = 1500,
             temperature: float = 0.3, system: Optional[str] = None) -> dict:
        messages = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})
        return self._post(agent, messages, max_tokens, temperature)

    def generate_test(self, agent: str, prompt: str, max_tokens: int = 2000) -> dict:
        """Generate a test file. `prompt` is the grounded prompt built by the app."""
        return self.chat(agent, prompt, max_tokens=max_tokens, temperature=0.3)

    def validate(self, test_code: str, source_context: str) -> dict:
        """Ask test_master to judge quality. Returns {level, reasons, ok}."""
        prompt = (
            "You are a senior QA reviewer. Judge the pytest test below against the "
            "real source. Respond with STRICT JSON only:\n"
            '{"level": "GOLD|SILVER|BRONZE", "reasons": "one short sentence"}\n\n'
            "GOLD = correct and should pass; SILVER = minor fixable issues; "
            "BRONZE = wrong/won't run.\n\n"
            f"TEST:\n```python\n{test_code}\n```\n\n"
            f"REAL SOURCE:\n{source_context[:4000]}\n"
        )
        res = self.chat(AGENTS["master"], prompt, max_tokens=200, temperature=0.0)
        verdict = {"level": "BRONZE", "reasons": "", "ok": res["ok"]}
        if res["ok"]:
            parsed = _safe_json(res["content"])
            if parsed:
                verdict["level"] = str(parsed.get("level", "BRONZE")).upper()
                verdict["reasons"] = parsed.get("reasons", "")
        else:
            verdict["reasons"] = res["error"]
        return verdict

    def fix_test(self, test_code: str, pytest_failures: str,
                 source_context: str) -> dict:
        """Ask test_master to diagnose failures and return a corrected test."""
        prompt = (
            "You are an expert Python test engineer. The pytest test below FAILED. "
            "Diagnose the failures and return a CORRECTED test that passes against "
            "the real source. Fix wrong assertions, constructor args, and imports.\n"
            "Output ONLY one ```python fenced block, closed with ```.\n\n"
            f"CURRENT TEST:\n```python\n{test_code}\n```\n\n"
            f"PYTEST FAILURES:\n{pytest_failures[:1500]}\n\n"
            f"REAL SOURCE:\n{source_context[:4000]}\n"
        )
        return self.chat(AGENTS["master"], prompt, max_tokens=2000, temperature=0.2)

    def report(self, summary_text: str) -> dict:
        """Ask test_master for a concise, readable final report."""
        prompt = (
            "Write a short, professional test-run report (Markdown) from this data. "
            "Include an overview, pass/fail summary, notable failures, and next steps.\n\n"
            f"{summary_text[:3000]}"
        )
        return self.chat(AGENTS["master"], prompt, max_tokens=800, temperature=0.3)


def _safe_json(text: str) -> Optional[dict]:
    """Extract the first JSON object from a possibly-noisy LLM response."""
    if not text:
        return None
    try:
        return json.loads(text)
    except Exception:
        pass
    # Find the first {...} block.
    start = text.find("{")
    end = text.rfind("}")
    if start != -1 and end != -1 and end > start:
        try:
            return json.loads(text[start:end + 1])
        except Exception:
            return None
    return None
