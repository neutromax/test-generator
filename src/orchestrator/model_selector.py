"""
Model Selector - Intelligent model routing based on task type.

Routes tasks to optimal model considering:
- Task type preferences
- Model availability (loaded, memory, responsiveness)
- Performance history (speed, quality)
- Fallback options if primary model unavailable
"""

import logging
from typing import List, Optional, Dict, Tuple
from datetime import datetime
from src.ollama_client import OllamaClient, ModelInfo

logger = logging.getLogger(__name__)


class ModelSelector:
    """
    Intelligent model routing system.
    
    Architecture:
    - Master Model: Llama 3.1:8b (reasoning, validation, supervision)
    - Worker 1: Qwen 2.5-Coder:7b (code generation)
    - Worker 2: Gemma3 (balanced tasks, E2E, validation)
    
    Task Routing:
    - Unit/Integration: Qwen (code-focused) > Llama (reasoning) > Gemma (fallback)
    - E2E/API: Gemma (balanced) > Qwen (code) > Llama (reasoning)
    - Security/Vulnerability: Llama (reasoning) > Gemma (analysis) > Qwen (code)
    - Linting/Code Quality: Qwen (coder) > Gemma (balanced) > Llama (reasoning)
    """
    
    # Model availability thresholds
    MIN_MEMORY_MB = 2000  # Minimum 2GB free memory to consider available
    RESPONSE_TIMEOUT_SEC = 30  # Model must respond within 30s
    
    # Task-to-model preferences (primary, secondary, tertiary)
    MODEL_ROUTING = {
        "unit_test": ["qwen2.5-coder:7b", "llama3.1:8b", "gemma3"],
        "integration_test": ["qwen2.5-coder:7b", "llama3.1:8b", "gemma3"],
        "e2e_test": ["gemma3", "qwen2.5-coder:7b", "llama3.1:8b"],
        "api_test": ["gemma3", "qwen2.5-coder:7b", "llama3.1:8b"],
        "linting": ["qwen2.5-coder:7b", "gemma3", "llama3.1:8b"],
        "code_quality": ["qwen2.5-coder:7b", "gemma3", "llama3.1:8b"],
        "security": ["llama3.1:8b", "gemma3", "qwen2.5-coder:7b"],
        "vulnerability": ["llama3.1:8b", "gemma3", "qwen2.5-coder:7b"],
        "documentation": ["llama3.1:8b", "qwen2.5-coder:7b", "gemma3"],
        "review": ["llama3.1:8b", "gemma3", "qwen2.5-coder:7b"],
    }
    
    def __init__(self, ollama_client: Optional[OllamaClient] = None):
        """
        Initialize model selector.
        
        Args:
            ollama_client: OllamaClient instance (default: create new)
        """
        self.ollama_client = ollama_client or OllamaClient()
        
        # Performance tracking (per model per task type)
        self.performance_stats: Dict[str, Dict] = {
            "qwen2.5-coder:7b": {
                "task_type_stats": {},
                "total_executions": 0,
                "success_count": 0,
                "success_rate": 0.0,
                "total_time_ms": 0,
                "avg_time_ms": 0,
            },
            "llama3.1:8b": {
                "task_type_stats": {},
                "total_executions": 0,
                "success_count": 0,
                "success_rate": 0.0,
                "total_time_ms": 0,
                "avg_time_ms": 0,
            },
            "gemma3": {
                "task_type_stats": {},
                "total_executions": 0,
                "success_count": 0,
                "success_rate": 0.0,
                "total_time_ms": 0,
                "avg_time_ms": 0,
            },
        }
        
        # Cache available models (refresh periodically)
        self.available_models_cache: Dict[str, ModelInfo] = {}
        self.last_availability_check = None
        
        # Initialize cache immediately
        try:
            self._refresh_model_cache()
        except Exception as e:
            logger.warning(f"Could not initialize model cache: {e}")
        
        logger.info("✓ Model Selector initialized")
    
    def select_model(self, task_type: str, exclude_models: List[str] = None) -> Optional[str]:
        """
        Select best available model for task type.
        
        Considers:
        1. Task type preference order
        2. Model availability (loaded, memory, responsive)
        3. Performance history
        4. Fallback options if primary unavailable
        
        Args:
            task_type: Type of task to route
            exclude_models: Models to skip (already tried or failed)
            
        Returns:
            Model name to use, or None if no suitable model available
        """
        exclude_models = exclude_models or []
        
        # Get preferred models for this task type
        preferred_models = self.MODEL_ROUTING.get(
            task_type.lower(), 
            ["llama3.1:8b", "qwen2.5-coder:7b", "gemma3"]  # Default fallback
        )
        
        logger.debug(f"Model selection for '{task_type}': preferred={preferred_models}")
        
        # Try each preferred model in order
        for model_name in preferred_models:
            if model_name in exclude_models:
                logger.debug(f"  {model_name}: excluded")
                continue
            
            # Check availability
            if self.is_model_available(model_name):
                logger.info(f"✓ Selected '{model_name}' for task '{task_type}'")
                return model_name
            else:
                logger.debug(f"  {model_name}: not available")
        
        # No suitable model found
        logger.warning(f"✗ No available model for task '{task_type}'")
        return None
    
    def is_model_available(self, model_name: str) -> bool:
        """
        Check if model is available for use.
        
        Availability checks:
        1. Model exists on server (from Ollama list)
        2. Sufficient memory available
        3. Not overloaded (queue checks via supervisor)
        
        Args:
            model_name: Name of model to check (base name, e.g., "gemma3")
            
        Returns:
            True if model is available and responsive
        """
        try:
            # Refresh model list if cache stale (older than 30 seconds)
            if not self._is_cache_fresh():
                self._refresh_model_cache()
            
            # Check if model is in available models (with flexible name matching)
            matching_model = self._find_model_by_base_name(model_name)
            
            if not matching_model:
                logger.debug(f"Model '{model_name}' not found in Ollama server")
                return False
            
            # Model exists - it's available
            logger.debug(f"✓ Model '{model_name}' is available")
            return True
        
        except Exception as e:
            logger.error(f"✗ Availability check failed for '{model_name}': {e}")
            return False
    
    def _find_model_by_base_name(self, model_base_name: str) -> Optional[ModelInfo]:
        """
        Find model info by base name, handling :tag suffixes.
        
        E.g., "gemma3" matches both "gemma3" and "gemma3:latest"
        
        Args:
            model_base_name: Base model name (e.g., "gemma3")
            
        Returns:
            ModelInfo if found, None otherwise
        """
        # Exact match first
        if model_base_name in self.available_models_cache:
            return self.available_models_cache[model_base_name]
        
        # Try matching base name (before colon)
        for cached_name, model_info in self.available_models_cache.items():
            # Extract base name (everything before :)
            cached_base_name = cached_name.split(':')[0]
            model_base_only = model_base_name.split(':')[0]
            
            if cached_base_name == model_base_only or cached_name.startswith(model_base_name):
                return model_info
        
        return None
    
    def get_best_alternative_model(self, task_type: str, 
                                   exclude_model: str = None) -> Optional[str]:
        """
        Get fallback model if primary fails.
        
        Args:
            task_type: Type of task
            exclude_model: Previously tried model to exclude
            
        Returns:
            Name of fallback model, or None if none available
        """
        exclude_list = [exclude_model] if exclude_model else []
        return self.select_model(task_type, exclude_list)
    
    def record_execution(self, model_name: str, task_type: str, 
                        execution_time_ms: float, success: bool, 
                        error_msg: Optional[str] = None) -> None:
        """
        Record model execution metrics for optimization.
        
        Tracks per-model and per-task performance to improve:
        - Future model selection
        - Load balancing
        - Performance monitoring
        
        Args:
            model_name: Model that executed task
            task_type: Type of task executed
            execution_time_ms: Time in milliseconds
            success: Whether execution succeeded
            error_msg: Error message if failed
        """
        try:
            if model_name not in self.performance_stats:
                self.performance_stats[model_name] = {
                    "task_type_stats": {},
                    "total_executions": 0,
                    "success_count": 0,
                    "total_time_ms": 0,
                }
            
            stats = self.performance_stats[model_name]
            
            # Update overall stats
            stats["total_executions"] += 1
            stats["total_time_ms"] += execution_time_ms
            stats["avg_time_ms"] = stats["total_time_ms"] / stats["total_executions"]
            
            if success:
                stats["success_count"] = stats.get("success_count", 0) + 1
            
            stats["success_rate"] = (
                stats.get("success_count", 0) / stats["total_executions"]
            ) if stats["total_executions"] > 0 else 0.0
            
            # Update task-type specific stats
            if task_type not in stats["task_type_stats"]:
                stats["task_type_stats"][task_type] = {
                    "executions": 0,
                    "successes": 0,
                    "total_time_ms": 0,
                    "avg_time_ms": 0,
                }
            
            task_stats = stats["task_type_stats"][task_type]
            task_stats["executions"] += 1
            task_stats["total_time_ms"] += execution_time_ms
            task_stats["avg_time_ms"] = task_stats["total_time_ms"] / task_stats["executions"]
            
            if success:
                task_stats["successes"] = task_stats.get("successes", 0) + 1
            
            log_level = "✓" if success else "⚠"
            logger.debug(
                f"{log_level} Recorded: {model_name} + {task_type} "
                f"({execution_time_ms:.0f}ms, success={success})"
            )
        
        except Exception as e:
            logger.error(f"✗ Failed to record execution metrics: {e}")
    
    def get_performance_summary(self, model_name: Optional[str] = None) -> Dict:
        """
        Get performance statistics for model(s).
        
        Args:
            model_name: Specific model to get stats for (None = all)
            
        Returns:
            Dict with performance metrics
        """
        if model_name:
            return self.performance_stats.get(model_name, {})
        
        return self.performance_stats
    
    def get_model_stats_by_task(self, task_type: str) -> Dict[str, Dict]:
        """
        Get performance for all models on specific task type.
        
        Args:
            task_type: Task type to analyze
            
        Returns:
            Dict mapping model names to performance stats
        """
        result = {}
        
        for model_name, stats in self.performance_stats.items():
            if task_type in stats.get("task_type_stats", {}):
                result[model_name] = stats["task_type_stats"][task_type]
        
        return result
    
    def _is_cache_fresh(self) -> bool:
        """Check if model availability cache is fresh (< 30 seconds old)."""
        if self.last_availability_check is None:
            return False
        
        time_elapsed = (datetime.now() - self.last_availability_check).total_seconds()
        return time_elapsed < 30
    
    def _refresh_model_cache(self) -> None:
        """Refresh available models list from Ollama server."""
        try:
            models = self.ollama_client.list_models()
            self.available_models_cache = {model.name: model for model in models}
            self.last_availability_check = datetime.now()
            
            loaded_models = [m.name for m in models if m.loaded]
            logger.debug(f"✓ Model cache refreshed: {len(loaded_models)} loaded models")
        
        except Exception as e:
            logger.error(f"✗ Failed to refresh model cache: {e}")
    
    def get_all_available_models(self) -> List[str]:
        """Get list of all currently available (loaded) models."""
        try:
            if not self._is_cache_fresh():
                self._refresh_model_cache()
            
            return [
                model.name 
                for model in self.available_models_cache.values() 
                if model.loaded
            ]
        
        except Exception as e:
            logger.error(f"✗ Failed to get available models: {e}")
            return []
    
    def get_master_model(self) -> Optional[str]:
        """
        Get the Master supervisor model (Llama 3.1:8b).
        
        Master handles:
        - Quality validation
        - Worker supervision
        - Error correction
        - Final approval
        
        Returns:
            Model name if available, None otherwise
        """
        master_model = "llama3.1:8b"
        
        if self.is_model_available(master_model):
            return master_model
        
        logger.warning("✗ Master model (Llama 3.1:8b) not available for supervision")
        return None
    
    def get_worker_models(self) -> List[str]:
        """
        Get available worker models (Qwen + Gemma).
        
        Workers handle:
        - Parallel task execution
        - Unit/Integration tests (Qwen)
        - E2E/API tests (Gemma)
        
        Returns:
            List of available worker models
        """
        workers = []
        
        for model in ["qwen2.5-coder:7b", "gemma3"]:
            if self.is_model_available(model):
                workers.append(model)
        
        return workers
