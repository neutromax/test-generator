"""
Ollama Client - Interface to local Ollama API.

Handles:
- Connection to Ollama server (localhost:11434)
- Model listing and availability checks
- Text generation with streaming support
- Error handling and retries
- Performance metrics tracking
- Health checks and diagnostics
"""

import requests
import json
import time
from typing import Optional, List, Dict, Generator, Tuple
from dataclasses import dataclass
from datetime import datetime
import logging
from urllib3.exceptions import InsecureRequestWarning
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

# Disable SSL warnings
requests.packages.urllib3.disable_warnings(InsecureRequestWarning)

logger = logging.getLogger(__name__)


def _create_session_with_retries():
    """Create a requests session with retry strategy."""
    session = requests.Session()
    retry_strategy = Retry(
        total=2,
        backoff_factor=0.5,
        status_forcelist=[429, 500, 502, 503, 504],
        allowed_methods=["GET", "POST"]
    )
    adapter = HTTPAdapter(max_retries=retry_strategy)
    session.mount("http://", adapter)
    session.mount("https://", adapter)
    return session


@dataclass
class ModelInfo:
    """Information about an Ollama model."""
    name: str
    size: int  # bytes
    digest: str
    modified_at: str
    loaded: bool = False
    memory_used: int = 0  # bytes


@dataclass
class GenerationMetrics:
    """Metrics from a generation request."""
    model: str
    prompt_tokens: int
    response_tokens: int
    total_tokens: int
    load_duration: int  # nanoseconds
    prompt_eval_duration: int
    eval_duration: int
    eval_count: int
    total_time: float  # seconds


class OllamaClient:
    """
    Client for interacting with Ollama API.
    
    Connects to http://localhost:11434
    Supports streaming and non-streaming requests.
    """
    
    def __init__(self, base_url: str = "http://localhost:11434", timeout: int = 1200):
        """
        Initialize Ollama client.
        
        Args:
            base_url: Ollama API base URL (default: localhost:11434)
            timeout: Request timeout in seconds (default: 1200)
        """
        self.base_url = base_url
        self.timeout = timeout
        self.api_version = "v1"
        
        # Performance tracking
        self.metrics: Dict[str, list] = {}
        self.model_cache: Dict[str, ModelInfo] = {}
        
        # Health check
        self.is_healthy = False
        self.last_health_check = None
    
    def health_check(self) -> bool:
        """
        Check if Ollama server is running.
        
        Returns:
            True if server is accessible, False otherwise
        """
        try:
            session = _create_session_with_retries()
            response = session.get(
                f"{self.base_url}/api/tags",
                timeout=10,
                verify=False
            )
            session.close()
            
            self.is_healthy = response.status_code == 200
            self.last_health_check = datetime.now()
            
            if self.is_healthy:
                logger.info(f"✓ Ollama server health check: OK at {self.base_url}")
            else:
                logger.warning(f"✗ Ollama server returned status {response.status_code}")
            
            return self.is_healthy
            
        except requests.exceptions.ConnectionError as e:
            logger.error(f"✗ Cannot connect to Ollama at {self.base_url}: Connection error")
            self.is_healthy = False
            return False
        except requests.exceptions.Timeout as e:
            logger.error(f"✗ Ollama connection timeout at {self.base_url}")
            self.is_healthy = False
            return False
        except requests.exceptions.RequestException as e:
            logger.error(f"✗ Ollama request failed: {type(e).__name__}")
            self.is_healthy = False
            return False
        except Exception as e:
            logger.error(f"✗ Ollama health check failed: {type(e).__name__}: {str(e)[:100]}")
            self.is_healthy = False
            return False
    
    def list_models(self) -> List[ModelInfo]:
        """
        Get list of available models on Ollama server.
        
        Returns:
            List of ModelInfo objects
            
        Raises:
            ConnectionError: If Ollama server unreachable
            Exception: If API request fails
        """
        try:
            session = _create_session_with_retries()
            response = session.get(
                f"{self.base_url}/api/tags",
                timeout=15,
                verify=False
            )
            session.close()
            
            response.raise_for_status()
            
            data = response.json()
            models = []
            
            for model_data in data.get("models", []):
                model = ModelInfo(
                    name=model_data.get("name"),
                    size=model_data.get("size", 0),
                    digest=model_data.get("digest", ""),
                    modified_at=model_data.get("modified_at", ""),
                )
                models.append(model)
                self.model_cache[model.name] = model
            
            logger.info(f"✓ Found {len(models)} models on Ollama server")
            return models
        
        except requests.exceptions.ConnectionError:
            logger.error(f"✗ Cannot connect to Ollama server at {self.base_url}")
            raise ConnectionError(f"Ollama server not running at {self.base_url}")
        except requests.exceptions.Timeout:
            logger.error(f"✗ Request to Ollama server timed out")
            raise TimeoutError(f"Ollama server at {self.base_url} is not responding")
        except Exception as e:
            logger.error(f"✗ Failed to list models: {str(e)[:200]}")
            raise
    
    def is_model_available(self, model_name: str) -> bool:
        """
        Check if a specific model is available.
        
        Matching is tolerant of the ``:latest`` tag, so ``gemma3`` matches
        ``gemma3:latest`` and vice versa.
        
        Args:
            model_name: Name of model to check
            
        Returns:
            True if model exists, False otherwise
        """
        def _normalize(name: str) -> str:
            name = name.strip()
            return name[:-7] if name.endswith(":latest") else name
        
        try:
            target = _normalize(model_name)
            return any(_normalize(m.name) == target for m in self.list_models())
        except Exception:
            return False
    
    def get_model_size(self, model_name: str) -> Optional[int]:
        """Get size of a model in bytes."""
        if model_name in self.model_cache:
            return self.model_cache[model_name].size
        
        try:
            models = self.list_models()
            for m in models:
                if m.name == model_name:
                    return m.size
        except Exception:
            pass
        
        return None
    
    def generate(self, model: str, prompt: str, stream: bool = False,
                raw: bool = False, format: str = "",
                options: Optional[Dict] = None) -> Tuple[str, GenerationMetrics]:
        """
        Generate text using specified model.
        
        Args:
            model: Model name (e.g., "llama3.1:8b")
            prompt: Input prompt
            stream: Whether to stream response
            raw: If True, raw prompt, not templated
            format: Response format (e.g., "json")
            options: Additional options (temperature, top_k, etc.)
            
        Returns:
            Tuple of (generated_text, metrics)
            
        Raises:
            ValueError: If model not found
            TimeoutError: If request times out
            Exception: If generation fails
        """
        if not self.is_model_available(model):
            raise ValueError(f"Model not found: {model}")
        
        # Merge caller options with sensible defaults tuned for slow (CPU) inference.
        # num_predict caps output length so generation reliably finishes within the
        # request timeout; on CPU this hardware runs ~2 tokens/sec, so ~1024 tokens
        # (~500s) stays comfortably under the 900s timeout.
        merged_options = {
            "num_predict": 512,
            "num_ctx": 4096,
            "temperature": 0.3,
            "top_p": 0.9,
        }
        if options:
            merged_options.update(options)
        
        payload = {
            "model": model,
            "prompt": prompt,
            "stream": stream,
            "raw": raw,
            # Keep the model resident in memory between tasks so we don't pay the
            # multi-GB reload cost on every generate() call.
            "keep_alive": "15m",
            "options": merged_options,
        }
        
        if format:
            payload["format"] = format
        
        start_time = time.time()
        
        try:
            response = requests.post(
                f"{self.base_url}/api/generate",
                json=payload,
                timeout=self.timeout,
                stream=stream,
                verify=False
            )
            response.raise_for_status()
            
            if stream:
                return self._handle_stream_response(model, response, start_time)
            else:
                return self._handle_normal_response(model, response, start_time)
        
        except requests.exceptions.Timeout:
            logger.error(f"✗ Generation timeout for {model}")
            raise TimeoutError(f"Generation timeout after {self.timeout}s")
        except Exception as e:
            logger.error(f"✗ Generation failed for {model}: {e}")
            raise
    
    def _handle_normal_response(self, model: str, response: requests.Response,
                               start_time: float) -> Tuple[str, GenerationMetrics]:
        """Handle non-streaming response."""
        data = response.json()
        
        elapsed = time.time() - start_time
        
        metrics = GenerationMetrics(
            model=model,
            prompt_tokens=data.get("prompt_eval_count", 0),
            response_tokens=data.get("eval_count", 0),
            total_tokens=data.get("prompt_eval_count", 0) + data.get("eval_count", 0),
            load_duration=data.get("load_duration", 0),
            prompt_eval_duration=data.get("prompt_eval_duration", 0),
            eval_duration=data.get("eval_duration", 0),
            eval_count=data.get("eval_count", 0),
            total_time=elapsed
        )
        
        # Track metrics
        if model not in self.metrics:
            self.metrics[model] = []
        self.metrics[model].append(metrics)
        
        generated_text = data.get("response", "")
        
        logger.info(
            f"✓ Generated with {model}: "
            f"{metrics.response_tokens} tokens in {elapsed:.2f}s"
        )
        
        return generated_text, metrics
    
    def _handle_stream_response(self, model: str, response: requests.Response,
                               start_time: float) -> Tuple[str, GenerationMetrics]:
        """Handle streaming response."""
        full_response = ""
        total_metrics = None
        
        for line in response.iter_lines():
            if line:
                try:
                    data = json.loads(line)
                    full_response += data.get("response", "")
                    
                    # Last chunk contains metrics
                    if data.get("done"):
                        elapsed = time.time() - start_time
                        
                        total_metrics = GenerationMetrics(
                            model=model,
                            prompt_tokens=data.get("prompt_eval_count", 0),
                            response_tokens=data.get("eval_count", 0),
                            total_tokens=data.get("prompt_eval_count", 0) + data.get("eval_count", 0),
                            load_duration=data.get("load_duration", 0),
                            prompt_eval_duration=data.get("prompt_eval_duration", 0),
                            eval_duration=data.get("eval_duration", 0),
                            eval_count=data.get("eval_count", 0),
                            total_time=elapsed
                        )
                
                except json.JSONDecodeError:
                    continue
        
        # Track metrics
        if total_metrics:
            if model not in self.metrics:
                self.metrics[model] = []
            self.metrics[model].append(total_metrics)
            
            logger.info(
                f"✓ Generated with {model} (streaming): "
                f"{total_metrics.response_tokens} tokens in {total_metrics.total_time:.2f}s"
            )
        
        return full_response, total_metrics
    
    def generate_stream(self, model: str, prompt: str,
                       options: Optional[Dict] = None) -> Generator[str, None, None]:
        """
        Stream text generation token-by-token.
        
        Args:
            model: Model name
            prompt: Input prompt
            options: Additional options
            
        Yields:
            Generated text tokens
        """
        if not self.is_model_available(model):
            raise ValueError(f"Model not found: {model}")
        
        payload = {
            "model": model,
            "prompt": prompt,
            "stream": True,
        }
        
        if options:
            payload["options"] = options
        
        try:
            response = requests.post(
                f"{self.base_url}/api/generate",
                json=payload,
                timeout=self.timeout,
                stream=True,
                verify=False
            )
            response.raise_for_status()
            
            for line in response.iter_lines():
                if line:
                    try:
                        data = json.loads(line)
                        yield data.get("response", "")
                    except json.JSONDecodeError:
                        continue
        
        except Exception as e:
            logger.error(f"✗ Stream generation failed for {model}: {e}")
            raise
    
    def get_performance_stats(self, model: str) -> Dict:
        """
        Get performance statistics for a model.
        
        Args:
            model: Model name
            
        Returns:
            Dict with average time, success rate, etc.
        """
        if model not in self.metrics or not self.metrics[model]:
            return {
                "model": model,
                "calls": 0,
                "avg_time_sec": 0.0,
                "avg_tokens": 0,
                "min_time_sec": 0.0,
                "max_time_sec": 0.0,
            }
        
        metrics_list = self.metrics[model]
        times = [m.total_time for m in metrics_list]
        tokens = [m.response_tokens for m in metrics_list]
        
        return {
            "model": model,
            "calls": len(metrics_list),
            "avg_time_sec": sum(times) / len(times),
            "avg_tokens": sum(tokens) / len(tokens) if tokens else 0,
            "min_time_sec": min(times),
            "max_time_sec": max(times),
            "total_tokens_generated": sum(tokens),
        }
    
    def get_all_stats(self) -> Dict[str, Dict]:
        """Get statistics for all models."""
        return {
            model: self.get_performance_stats(model)
            for model in self.metrics.keys()
        }
    
    def clear_metrics(self):
        """Clear performance metrics."""
        self.metrics.clear()
        logger.info("✓ Metrics cleared")
