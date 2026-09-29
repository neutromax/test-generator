"""
Model Selector - Intelligent model routing based on task type.

Strategy:
- Route tasks to optimal model based on task type
- Check model availability (memory, currently loaded)
- Fallback to alternative models if preferred unavailable
- Track model performance metrics
"""

from typing import List, Optional, Dict


class ModelSelector:
    """
    Selects optimal Ollama model for each task type.
    
    Model capabilities:
    - Qwen 2.5-Coder: Code generation (unit, integration, linting)
    - Llama 3.1: Reasoning (security, vulnerability, documentation)
    - Gemma 3: Balanced (E2E, API tests, validation)
    """
    
    # Model routing preferences (RECOMMENDED)
    MODEL_ROUTING = {
        # Code generation tasks → Qwen (code specialist)
        "unit_test": ["qwen2.5-coder:7b", "llama3.1", "gemma3"],
        "integration_test": ["qwen2.5-coder:7b", "llama3.1", "gemma3"],
        "linting": ["qwen2.5-coder:7b", "gemma3", "llama3.1"],
        
        # Analysis/reasoning tasks → Llama (stronger reasoning)
        "security": ["llama3.1", "gemma3", "qwen2.5-coder:7b"],
        "vulnerability": ["llama3.1", "gemma3", "qwen2.5-coder:7b"],
        
        # Balanced tasks → Gemma (good all-rounder)
        "e2e_test": ["gemma3", "qwen2.5-coder:7b", "llama3.1"],
        "api_test": ["gemma3", "qwen2.5-coder:7b", "llama3.1"],
    }
    
    def __init__(self, ollama_client):
        """Initialize model selector."""
        self.ollama = ollama_client
        self.model_stats: Dict = {}  # Track performance
    
    def select_model(self, task_type: str) -> Optional[str]:
        """
        Select best available model for task.
        
        Selection priority:
        1. Check preferred models for task
        2. Check which are currently loaded (faster)
        3. Check model memory availability
        4. Return best available model
        
        Args:
            task_type: Type of task (unit_test, security, etc.)
            
        Returns:
            Model name string or None if unavailable
        """
        if task_type not in self.MODEL_ROUTING:
            return None
        
        preferred_models = self.MODEL_ROUTING[task_type]
        
        # Try each preferred model in order
        for model in preferred_models:
            if self.is_model_available(model):
                return model
        
        return None
    
    def is_model_available(self, model_name: str) -> bool:
        """Check if model is available for use."""
        # Check if model loaded in Ollama
        # Check memory constraints
        # Return availability status
        pass
    
    def get_model_status(self, model_name: str) -> Dict:
        """Get current status of a model."""
        # Check if loaded
        # Get memory usage
        # Return status dict
        pass
    
    def record_execution(self, model_name: str, task_type: str, 
                        execution_time: float, success: bool):
        """Record model execution metrics for optimization."""
        # Update model stats
        # Track avg time, success rate
        pass
    
    def get_best_alternative_model(self, task_type: str, 
                                  exclude_model: str) -> Optional[str]:
        """Get alternative model if preferred fails."""
        if task_type not in self.MODEL_ROUTING:
            return None
        
        alternatives = [
            m for m in self.MODEL_ROUTING[task_type] 
            if m != exclude_model
        ]
        
        for model in alternatives:
            if self.is_model_available(model):
                return model
        
        return None
