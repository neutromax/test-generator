"""
Master Supervisor - Quality validation and worker oversight.

Core Responsibilities:
1. Monitor worker task execution
2. Validate output quality (GOLD/SILVER/BRONZE levels)
3. Detect stuck workers and timeout conditions
4. Supervise queue backlog and engage when needed
5. Handle corrective prompts for failed outputs
6. Generate supervision reports
"""

import logging
import re
import json
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple
from enum import Enum
from dataclasses import dataclass

logger = logging.getLogger(__name__)


class ValidationLevel(Enum):
    """Output quality validation levels."""
    GOLD = "GOLD"      # Output approved, meets all standards
    SILVER = "SILVER"  # Output has minor issues, correctable
    BRONZE = "BRONZE"  # Output invalid, redo required


@dataclass
class ValidationResult:
    """Result of output validation."""
    level: ValidationLevel
    is_approved: bool  # True if GOLD level
    needs_correction: bool  # True if SILVER (can fix)
    needs_redo: bool  # True if BRONZE (must redo)
    error_message: Optional[str] = None
    correction_prompt: Optional[str] = None
    confidence_score: float = 0.0  # 0-1 confidence in validation


@dataclass
class WorkerHealthStatus:
    """Health status of a worker model."""
    model_name: str
    is_healthy: bool
    response_time_ms: float
    task_queue_size: int
    last_heartbeat: Optional[datetime] = None
    consecutive_failures: int = 0
    last_error: Optional[str] = None


class MasterSupervisor:
    """
    Quality validation and worker oversight system.
    
    Configuration (Architecture Decisions):
    - VALIDATION_LEVELS: GOLD (approve), SILVER (correct), BRONZE (redo)
    - monitor_interval: 2 seconds (periodic health checks)
    - timeout_threshold: 60 seconds (worker stuck detection)
    - queue_backlog_threshold: 3+ tasks (master engagement trigger)
    - master_max_concurrent_tasks: 2 (supervisor capacity limit)
    """
    
    # Validation level constants
    GOLD = ValidationLevel.GOLD
    SILVER = ValidationLevel.SILVER
    BRONZE = ValidationLevel.BRONZE
    
    # Configuration constants (RECOMMENDED)
    MONITOR_INTERVAL_SEC = 2
    TIMEOUT_THRESHOLD_SEC = 60
    QUEUE_BACKLOG_THRESHOLD = 3
    MASTER_MAX_CONCURRENT = 2
    MAX_CONSECUTIVE_FAILURES = 3
    
    def __init__(self):
        """Initialize Master Supervisor."""
        self.worker_health_status: Dict[str, WorkerHealthStatus] = {}
        self.validation_history: List[Tuple[str, ValidationResult]] = []
        self.correction_attempts: Dict[str, int] = {}  # task_id -> attempt_count
        self.supervision_metrics: Dict = {
            "total_validations": 0,
            "gold_count": 0,
            "silver_count": 0,
            "bronze_count": 0,
            "corrections_applied": 0,
            "takeover_count": 0,
        }
        
        logger.info("✓ Master Supervisor initialized")
    
    def validate_worker_output(self, task_type: str, output: str,
                               model_name: str) -> ValidationResult:
        """
        Validate worker output quality.
        
        Validation Pipeline:
        1. Check output format validity
        2. Check content completeness
        3. Check for common errors
        4. Assess quality level
        5. Generate correction prompt if needed
        
        Returns:
            ValidationResult with level, approval status, and any correction prompt
        """
        try:
            logger.debug(f"Validating output from {model_name} for {task_type}")
            
            # Start with validation checks
            format_valid = self._check_format_validity(task_type, output)
            content_complete = self._check_content_completeness(task_type, output)
            has_errors = self._detect_common_errors(output)
            
            # Determine validation level
            if format_valid and content_complete and not has_errors:
                # GOLD level - meets all standards
                level = self.GOLD
                is_approved = True
                needs_correction = False
                needs_redo = False
                error_msg = None
                correction_prompt = None
                confidence = 0.95
            
            elif format_valid and content_complete and has_errors:
                # SILVER level - has minor issues, can correct
                level = self.SILVER
                is_approved = False
                needs_correction = True
                needs_redo = False
                error_msg = f"Minor issues detected in {task_type} output"
                correction_prompt = self._generate_correction_prompt(task_type, output, has_errors)
                confidence = 0.75
            
            else:
                # BRONZE level - invalid, must redo
                level = self.BRONZE
                is_approved = False
                needs_correction = False
                needs_redo = True
                error_msg = self._generate_error_message(format_valid, content_complete)
                correction_prompt = None
                confidence = 0.60
            
            # Create validation result
            result = ValidationResult(
                level=level,
                is_approved=is_approved,
                needs_correction=needs_correction,
                needs_redo=needs_redo,
                error_message=error_msg,
                correction_prompt=correction_prompt,
                confidence_score=confidence,
            )
            
            # Record validation
            self._record_validation(task_type, result)
            
            # Log result
            log_msg = f"✓ Validation: {level.value} (confidence={confidence:.0%})"
            logger.info(log_msg)
            
            return result
        
        except Exception as e:
            logger.error(f"✗ Validation failed: {e}")
            return ValidationResult(
                level=self.BRONZE,
                is_approved=False,
                needs_correction=False,
                needs_redo=True,
                error_message=f"Validation error: {str(e)}",
                confidence_score=0.0,
            )
    
    def check_worker_health(self, model_name: str, task_queue_size: int,
                           last_response_time_ms: float) -> WorkerHealthStatus:
        """
        Check if worker is healthy and responsive.
        
        Health Criteria:
        1. Response time < timeout threshold
        2. Not in consecutive failures state
        3. Task queue not overwhelmed
        4. Recent heartbeat signal
        
        Args:
            model_name: Worker model name
            task_queue_size: Number of tasks in worker's queue
            last_response_time_ms: Time to last response
            
        Returns:
            WorkerHealthStatus with health assessment
        """
        try:
            # Determine health status
            is_healthy = (
                last_response_time_ms < (self.TIMEOUT_THRESHOLD_SEC * 1000) and
                self.worker_health_status.get(model_name, WorkerHealthStatus(
                    model_name, True, last_response_time_ms, task_queue_size
                )).consecutive_failures < self.MAX_CONSECUTIVE_FAILURES
            )
            
            # Create health status
            status = WorkerHealthStatus(
                model_name=model_name,
                is_healthy=is_healthy,
                response_time_ms=last_response_time_ms,
                task_queue_size=task_queue_size,
                last_heartbeat=datetime.now(),
            )
            
            # Update consecutive failures
            if is_healthy:
                status.consecutive_failures = 0
            else:
                status.consecutive_failures = (
                    self.worker_health_status.get(model_name, status).consecutive_failures + 1
                )
            
            # Store status
            self.worker_health_status[model_name] = status
            
            health_msg = "✓" if is_healthy else "✗"
            logger.debug(
                f"{health_msg} {model_name} health: "
                f"response={last_response_time_ms:.0f}ms, "
                f"queue={task_queue_size}, "
                f"failures={status.consecutive_failures}"
            )
            
            return status
        
        except Exception as e:
            logger.error(f"✗ Health check failed for {model_name}: {e}")
            return WorkerHealthStatus(
                model_name=model_name,
                is_healthy=False,
                response_time_ms=0.0,
                task_queue_size=0,
                last_error=str(e),
            )
    
    def should_master_engage(self, queue_size: int, 
                            worker_health_status: Dict[str, WorkerHealthStatus],
                            master_current_tasks: int) -> Tuple[bool, str]:
        """
        Determine if Master should engage to handle queue tasks.
        
        Engagement Triggers:
        1. Queue backlog ≥ threshold (3+ tasks waiting)
        2. Worker stuck or unhealthy
        3. Worker response time degrading
        4. Master has capacity (< 2 concurrent tasks)
        
        Returns:
            Tuple of (should_engage: bool, reason: str)
        """
        try:
            # Check capacity
            if master_current_tasks >= self.MASTER_MAX_CONCURRENT:
                return False, "Master at capacity"
            
            # Check queue backlog
            if queue_size >= self.QUEUE_BACKLOG_THRESHOLD:
                return True, f"Queue backlog ({queue_size} tasks) exceeds threshold"
            
            # Check worker health
            unhealthy_workers = [
                model for model, health in worker_health_status.items()
                if not health.is_healthy
            ]
            
            if unhealthy_workers:
                return True, f"Workers unhealthy: {unhealthy_workers}"
            
            # Check response time degradation
            slow_workers = [
                model for model, health in worker_health_status.items()
                if health.response_time_ms > (self.TIMEOUT_THRESHOLD_SEC * 1000 * 0.8)
            ]
            
            if slow_workers:
                return True, f"Workers slow: {slow_workers}"
            
            return False, "Normal operation"
        
        except Exception as e:
            logger.error(f"✗ Engagement decision failed: {e}")
            return False, f"Error: {str(e)}"
    
    def handle_failed_output(self, task_id: str, task_type: str,
                            failed_output: str, error_msg: str,
                            correction_prompt: Optional[str] = None) -> Dict:
        """
        Handle failed worker output.
        
        Strategy:
        1. If SILVER level: Apply correction prompt and retry
        2. If BRONZE level: Mark for redo or handle via Master
        3. Track correction attempts (max 3)
        
        Args:
            task_id: Unique task identifier
            task_type: Type of task
            failed_output: The invalid output
            error_msg: Error description
            correction_prompt: Optional prompt for correction
            
        Returns:
            Dict with action recommendation
        """
        try:
            attempt_count = self.correction_attempts.get(task_id, 0) + 1
            self.correction_attempts[task_id] = attempt_count
            
            if correction_prompt and attempt_count < 3:
                # Retry with correction prompt
                logger.info(
                    f"Correcting {task_type} (attempt {attempt_count}/3): "
                    f"{error_msg}"
                )
                
                self.supervision_metrics["corrections_applied"] += 1
                
                return {
                    "action": "correct",
                    "attempt": attempt_count,
                    "correction_prompt": correction_prompt,
                    "retry": True,
                }
            
            else:
                # Mark for redo
                logger.warning(
                    f"Marking {task_type} for redo (attempts={attempt_count}): "
                    f"{error_msg}"
                )
                
                return {
                    "action": "redo",
                    "attempt": attempt_count,
                    "reason": error_msg,
                    "retry": False,
                }
        
        except Exception as e:
            logger.error(f"✗ Failed to handle output: {e}")
            return {
                "action": "error",
                "error": str(e),
                "retry": False,
            }
    
    def handle_stuck_worker(self, model_name: str, stuck_duration_sec: float) -> Dict:
        """
        Handle worker that appears stuck (no response).
        
        Actions:
        1. Send health probe
        2. Timeout after threshold
        3. Master takes over stuck task
        4. Restart worker or use fallback
        
        Args:
            model_name: Worker model name
            stuck_duration_sec: How long stuck
            
        Returns:
            Dict with recovery action
        """
        try:
            if stuck_duration_sec < self.TIMEOUT_THRESHOLD_SEC:
                # Still within tolerance, send probe
                logger.warning(
                    f"⚠ {model_name} slow ({stuck_duration_sec:.1f}s), sending probe"
                )
                return {
                    "action": "probe",
                    "model": model_name,
                    "recovery": "monitoring",
                }
            
            else:
                # Timeout exceeded, trigger master takeover
                logger.warning(
                    f"✗ {model_name} stuck ({stuck_duration_sec:.1f}s), "
                    f"exceeds threshold ({self.TIMEOUT_THRESHOLD_SEC}s)"
                )
                
                self.supervision_metrics["takeover_count"] += 1
                
                return {
                    "action": "takeover",
                    "model": model_name,
                    "recovery": "master_takeover",
                    "message": f"Master taking over task from {model_name}",
                }
        
        except Exception as e:
            logger.error(f"✗ Failed to handle stuck worker: {e}")
            return {
                "action": "error",
                "error": str(e),
            }
    
    def generate_report(self) -> Dict:
        """
        Generate Master Supervisor activity report.
        
        Includes:
        - Validation statistics
        - Worker health summary
        - Correction and takeover counts
        - Performance metrics
        
        Returns:
            Dict with supervision report
        """
        try:
            total_validations = self.supervision_metrics["total_validations"]
            gold_count = self.supervision_metrics["gold_count"]
            silver_count = self.supervision_metrics["silver_count"]
            bronze_count = self.supervision_metrics["bronze_count"]
            
            # Calculate percentages
            gold_pct = (gold_count / total_validations * 100) if total_validations > 0 else 0
            silver_pct = (silver_count / total_validations * 100) if total_validations > 0 else 0
            bronze_pct = (bronze_count / total_validations * 100) if total_validations > 0 else 0
            
            # Worker health summary
            worker_summary = {}
            for model, status in self.worker_health_status.items():
                worker_summary[model] = {
                    "healthy": status.is_healthy,
                    "response_time_ms": f"{status.response_time_ms:.0f}",
                    "task_queue_size": status.task_queue_size,
                    "consecutive_failures": status.consecutive_failures,
                }
            
            report = {
                "generated_at": datetime.now().isoformat(),
                "validation_summary": {
                    "total": total_validations,
                    "gold": f"{gold_count} ({gold_pct:.1f}%)",
                    "silver": f"{silver_count} ({silver_pct:.1f}%)",
                    "bronze": f"{bronze_count} ({bronze_pct:.1f}%)",
                },
                "corrections_applied": self.supervision_metrics["corrections_applied"],
                "takeovers": self.supervision_metrics["takeover_count"],
                "worker_health": worker_summary,
            }
            
            logger.info(f"✓ Supervision report: {gold_pct:.0f}% GOLD, {silver_pct:.0f}% SILVER")
            
            return report
        
        except Exception as e:
            logger.error(f"✗ Failed to generate report: {e}")
            return {"error": str(e)}
    
    # ========== PRIVATE VALIDATION HELPER METHODS ==========
    
    def _check_format_validity(self, task_type: str, output: str) -> bool:
        """Check if output has valid format for task type."""
        try:
            if not output or not output.strip():
                logger.debug("Format check FAILED: empty output")
                return False
            
            # Task-specific format checks
            if "test" in task_type.lower():
                # Should contain test function/class
                if not any(pattern in output.lower() for pattern in 
                          ["def test_", "class test", "@test", "it("]):
                    logger.debug("Format check FAILED: no test structure found")
                    return False
            
            elif "linting" in task_type.lower() or "quality" in task_type.lower():
                # Should contain quality feedback or fixes
                if len(output.strip()) < 50:
                    logger.debug("Format check FAILED: output too short")
                    return False
            
            elif "security" in task_type.lower() or "vulnerability" in task_type.lower():
                # Should contain security analysis
                if not any(word in output.lower() for word in
                          ["vulnerability", "issue", "risk", "attack", "injection", "auth"]):
                    logger.debug("Format check FAILED: no security content found")
                    return False
            
            logger.debug("Format check PASSED")
            return True
        
        except Exception as e:
            logger.warning(f"⚠ Format check error: {e}")
            return False
    
    def _check_content_completeness(self, task_type: str, output: str) -> bool:
        """Check if output is complete and substantive."""
        try:
            # Minimum content length (adjust per task type)
            min_length = 200 if "test" in task_type else 100
            
            if len(output.strip()) < min_length:
                logger.debug(f"Completeness check FAILED: too short ({len(output)} < {min_length})")
                return False
            
            # Check for incomplete markers that signal truly unfinished work.
            # Note: "..." and "etc." are intentionally excluded because LLMs use
            # them legitimately in code snippets and prose, causing false failures.
            incomplete_markers = ["[INCOMPLETE]", "<INCOMPLETE>", "YOUR CODE HERE"]
            
            if any(marker in output for marker in incomplete_markers):
                logger.debug("Completeness check FAILED: contains incomplete markers")
                return False
            
            logger.debug("Completeness check PASSED")
            return True
        
        except Exception as e:
            logger.warning(f"⚠ Completeness check error: {e}")
            return False
    
    def _extract_code_blocks(self, output: str) -> List[str]:
        """Return the contents of all fenced ``` code blocks in the output."""
        import re
        # Match ```lang\n ... ``` blocks (language tag optional)
        blocks = re.findall(r"```[a-zA-Z0-9_+-]*\n(.*?)```", output, re.DOTALL)
        return [b for b in blocks if b.strip()]

    def _detect_common_errors(self, output: str) -> List[str]:
        """Detect common errors in output."""
        errors = []
        
        try:
            # Only inspect fenced code blocks for balance checks; prose legitimately
            # contains unbalanced parentheses/brackets and would cause false positives.
            code = "\n".join(self._extract_code_blocks(output)) or ""
            
            # Syntax errors (within code only)
            if code:
                if code.count("(") != code.count(")"):
                    errors.append("Mismatched parentheses")
                
                if code.count("[") != code.count("]"):
                    errors.append("Mismatched brackets")
            
            # Common typos
            typos = {
                "imoprt": "import",
                "sepf": "self",
                "pritnt": "print",
            }
            
            for typo, correct in typos.items():
                if typo in output:
                    errors.append(f"Typo: '{typo}' should be '{correct}'")
            
            if errors:
                logger.debug(f"Errors detected: {errors}")
            else:
                logger.debug("No common errors detected")
            
            return errors
        
        except Exception as e:
            logger.warning(f"⚠ Error detection failed: {e}")
            return []
    
    def _generate_correction_prompt(self, task_type: str, output: str,
                                    errors: List[str]) -> str:
        """Generate correction prompt for SILVER level output."""
        prompt = (
            f"The {task_type} output has minor issues that can be fixed:\n\n"
            f"Issues found:\n"
        )
        
        for error in errors:
            prompt += f"- {error}\n"
        
        prompt += (
            f"\nPlease fix these issues and provide the corrected output. "
            f"Keep the overall structure intact, just fix the identified problems."
        )
        
        return prompt
    
    def _generate_error_message(self, format_valid: bool, content_complete: bool) -> str:
        """Generate descriptive error message."""
        issues = []
        
        if not format_valid:
            issues.append("Invalid output format")
        
        if not content_complete:
            issues.append("Incomplete content")
        
        return "Output rejected: " + ", ".join(issues)
    
    def _record_validation(self, task_type: str, result: ValidationResult) -> None:
        """Record validation result for history and metrics."""
        try:
            self.validation_history.append((task_type, result))
            self.supervision_metrics["total_validations"] += 1
            
            if result.level == self.GOLD:
                self.supervision_metrics["gold_count"] += 1
            elif result.level == self.SILVER:
                self.supervision_metrics["silver_count"] += 1
            elif result.level == self.BRONZE:
                self.supervision_metrics["bronze_count"] += 1
        
        except Exception as e:
            logger.warning(f"⚠ Failed to record validation: {e}")
