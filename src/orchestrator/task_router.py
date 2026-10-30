"""
Task Router - Queue management with dependency handling.

Manages:
- Task creation and status tracking
- Execution queue with topological sorting
- Parallel batch grouping
- Dependency resolution (unit → integration → E2E)
- Task lifecycle management (PENDING → IN_PROGRESS → COMPLETED/FAILED)
"""

import logging
from typing import List, Dict, Optional, Set, Tuple
from datetime import datetime
from enum import Enum
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)


class TaskStatus(Enum):
    """Task execution status."""
    PENDING = "PENDING"          # Waiting to run
    IN_PROGRESS = "IN_PROGRESS"  # Currently executing
    COMPLETED = "COMPLETED"      # Successfully finished
    FAILED = "FAILED"            # Execution failed
    SKIPPED = "SKIPPED"          # Not needed (cached or deps failed)


class TaskType(Enum):
    """Types of test tasks."""
    UNIT_TEST = "unit_test"
    INTEGRATION_TEST = "integration_test"
    E2E_TEST = "e2e_test"
    API_TEST = "api_test"
    LINTING = "linting"
    SECURITY = "security"
    VULNERABILITY = "vulnerability"


@dataclass
class Task:
    """Represents a single task in the execution queue."""
    id: str                                    # Unique task ID
    task_type: TaskType                        # Type of task
    status: TaskStatus = TaskStatus.PENDING    # Current status
    priority: int = 1                          # Priority (lower = higher priority)
    estimated_time_sec: float = 15.0          # Estimated execution time
    assigned_model: Optional[str] = None       # Assigned model for execution
    dependencies: List[str] = field(default_factory=list)  # Task IDs this depends on
    created_at: datetime = field(default_factory=datetime.now)
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    result_path: Optional[str] = None          # Path to result file
    error_message: Optional[str] = None
    
    def duration_sec(self) -> Optional[float]:
        """Get actual execution duration."""
        if self.started_at and self.completed_at:
            return (self.completed_at - self.started_at).total_seconds()
        return None
    
    def is_ready_to_run(self, completed_tasks: Set[str]) -> bool:
        """Check if all dependencies are satisfied."""
        return all(dep_id in completed_tasks for dep_id in self.dependencies)


class TaskRouter:
    """
    Task queue management with dependency-aware execution.
    
    Architecture:
    - Dependency Graph: Unit → Integration → E2E chain
    - Priority Levels: Security/Vulnerability (P2) > Integration (P2) > Unit (P1) > E2E (P3)
    - Parallel Batching: Up to 3 independent tasks simultaneously
    - Status Tracking: PENDING → IN_PROGRESS → COMPLETED/FAILED/SKIPPED
    
    Task Configuration (RECOMMENDED):
    - Unit Tests: Priority 1, 15s, no dependencies
    - Integration Tests: Priority 2, 40s, depends on unit tests
    - E2E Tests: Priority 3, 60s, depends on integration tests
    - Security Tests: Priority 2, 25s, no dependencies
    - Vulnerability Scans: Priority 2, 30s, no dependencies
    """
    
    # Task configuration: priority and estimated time
    TASK_CONFIG = {
        TaskType.UNIT_TEST: {
            "priority": 1,
            "estimated_time_sec": 15,
            "depends_on": [],
        },
        TaskType.INTEGRATION_TEST: {
            "priority": 2,
            "estimated_time_sec": 40,
            "depends_on": [TaskType.UNIT_TEST],
        },
        TaskType.E2E_TEST: {
            "priority": 3,
            "estimated_time_sec": 60,
            "depends_on": [TaskType.INTEGRATION_TEST],
        },
        TaskType.API_TEST: {
            "priority": 2,
            "estimated_time_sec": 45,
            "depends_on": [],
        },
        TaskType.LINTING: {
            "priority": 1,
            "estimated_time_sec": 10,
            "depends_on": [],
        },
        TaskType.SECURITY: {
            "priority": 2,
            "estimated_time_sec": 25,
            "depends_on": [],
        },
        TaskType.VULNERABILITY: {
            "priority": 2,
            "estimated_time_sec": 30,
            "depends_on": [],
        },
    }
    
    # Maximum tasks to process in parallel
    MAX_PARALLEL_TASKS = 3
    
    def __init__(self):
        """Initialize task router."""
        self.tasks: Dict[str, Task] = {}  # task_id -> Task
        self.execution_queue: List[str] = []  # Ordered list of task IDs
        self.completed_tasks: Set[str] = set()  # Successfully completed task IDs
        self.failed_tasks: Set[str] = set()  # Failed task IDs
        self.skipped_tasks: Set[str] = set()  # Skipped task IDs
        self.current_batch: List[str] = []  # Currently executing batch
        
        self.queue_metrics: Dict = {
            "total_tasks": 0,
            "completed": 0,
            "failed": 0,
            "skipped": 0,
            "pending": 0,
            "in_progress": 0,
        }
        
        logger.info("✓ Task Router initialized")
    
    def create_tasks(self, selected_types: List[TaskType]) -> List[Task]:
        """
        Create tasks for selected test types.
        
        Args:
            selected_types: List of task types to create
            
        Returns:
            List of created Task objects
        """
        try:
            created_tasks = []
            task_counter = {task_type: 0 for task_type in selected_types}
            
            for task_type in selected_types:
                task_id = f"{task_type.value}_{task_counter[task_type]}"
                task_counter[task_type] += 1
                
                config = self.TASK_CONFIG.get(task_type, {})
                
                # Resolve dependencies
                dep_task_types = config.get("depends_on", [])
                dependencies = [
                    f"{dep_type.value}_{task_counter[dep_type]}" 
                    for dep_type in dep_task_types
                    if dep_type in selected_types
                ]
                
                # Create task
                task = Task(
                    id=task_id,
                    task_type=task_type,
                    status=TaskStatus.PENDING,
                    priority=config.get("priority", 1),
                    estimated_time_sec=config.get("estimated_time_sec", 15),
                    dependencies=dependencies,
                )
                
                self.tasks[task_id] = task
                created_tasks.append(task)
                
                logger.debug(f"Created task: {task_id} (deps={dependencies})")
            
            self.queue_metrics["total_tasks"] = len(self.tasks)
            self.queue_metrics["pending"] = len(self.tasks)
            
            logger.info(f"✓ Created {len(created_tasks)} tasks")
            return created_tasks
        
        except Exception as e:
            logger.error(f"✗ Failed to create tasks: {e}")
            return []
    
    def build_execution_queue(self) -> List[str]:
        """
        Build execution queue using topological sort.
        
        Algorithm:
        1. Sort by priority (lower number = higher priority)
        2. Respect dependencies (unit → integration → E2E)
        3. Keep tasks with same priority in order
        
        Returns:
            Ordered list of task IDs ready for execution
        """
        try:
            # Topological sort respecting dependencies
            queue = []
            visited = set()
            in_progress = set()
            
            def visit(task_id: str):
                """DFS visit for topological sort."""
                if task_id in visited:
                    return
                
                if task_id in in_progress:
                    logger.warning(f"⚠ Circular dependency detected: {task_id}")
                    return
                
                in_progress.add(task_id)
                task = self.tasks.get(task_id)
                
                if task:
                    # Visit dependencies first
                    for dep_id in task.dependencies:
                        if dep_id in self.tasks:
                            visit(dep_id)
                    
                    # Add task to queue
                    if task_id not in visited:
                        queue.append(task_id)
                        visited.add(task_id)
                
                in_progress.discard(task_id)
            
            # Sort tasks by priority before visiting
            task_ids_by_priority = sorted(
                self.tasks.keys(),
                key=lambda tid: self.tasks[tid].priority
            )
            
            # Visit all tasks
            for task_id in task_ids_by_priority:
                visit(task_id)
            
            self.execution_queue = queue
            
            logger.info(f"✓ Built execution queue: {len(queue)} tasks")
            logger.debug(f"  Queue order: {' → '.join(queue)}")
            
            return queue
        
        except Exception as e:
            logger.error(f"✗ Failed to build execution queue: {e}")
            return []
    
    def get_next_batch(self) -> List[str]:
        """
        Get next batch of parallelizable tasks.
        
        Rules:
        1. Return up to MAX_PARALLEL_TASKS independent tasks
        2. All dependencies must be completed
        3. Tasks at same dependency level run in parallel
        
        Returns:
            List of task IDs to execute in parallel
        """
        try:
            if not self.execution_queue:
                logger.debug("Execution queue is empty")
                return []
            
            available_tasks = []
            completed_and_skipped = self.completed_tasks | self.skipped_tasks
            
            # Find tasks ready to run (dependencies satisfied)
            for task_id in self.execution_queue:
                if task_id in (self.completed_tasks | self.failed_tasks | self.skipped_tasks):
                    continue  # Already processed
                
                task = self.tasks[task_id]
                
                if task.status in (TaskStatus.IN_PROGRESS, TaskStatus.COMPLETED):
                    continue  # Already running or done
                
                # Check if dependencies are satisfied
                if task.is_ready_to_run(completed_and_skipped):
                    available_tasks.append(task_id)
            
            # Take up to MAX_PARALLEL_TASKS
            batch = available_tasks[:self.MAX_PARALLEL_TASKS]
            self.current_batch = batch
            
            # Mark as in progress
            for task_id in batch:
                self.tasks[task_id].status = TaskStatus.IN_PROGRESS
                self.tasks[task_id].started_at = datetime.now()
            
            if batch:
                logger.info(f"✓ Next batch: {batch} ({len(batch)} tasks)")
            else:
                logger.debug("No tasks ready to run")
            
            return batch
        
        except Exception as e:
            logger.error(f"✗ Failed to get next batch: {e}")
            return []
    
    def mark_task_complete(self, task_id: str, result_path: str) -> bool:
        """
        Mark task as successfully completed.
        
        Args:
            task_id: Task to mark complete
            result_path: Path to result file
            
        Returns:
            True if successful
        """
        try:
            if task_id not in self.tasks:
                logger.warning(f"⚠ Task not found: {task_id}")
                return False
            
            task = self.tasks[task_id]
            task.status = TaskStatus.COMPLETED
            task.completed_at = datetime.now()
            task.result_path = result_path
            
            self.completed_tasks.add(task_id)
            self.queue_metrics["completed"] += 1
            self.queue_metrics["in_progress"] -= 1
            self.queue_metrics["pending"] -= 1
            
            logger.info(f"✓ Completed: {task_id} ({task.duration_sec():.1f}s)")
            
            return True
        
        except Exception as e:
            logger.error(f"✗ Failed to mark complete: {e}")
            return False
    
    def mark_task_failed(self, task_id: str, error_msg: str) -> bool:
        """
        Mark task as failed.
        
        Args:
            task_id: Task to mark failed
            error_msg: Error description
            
        Returns:
            True if successful
        """
        try:
            if task_id not in self.tasks:
                logger.warning(f"⚠ Task not found: {task_id}")
                return False
            
            task = self.tasks[task_id]
            task.status = TaskStatus.FAILED
            task.completed_at = datetime.now()
            task.error_message = error_msg
            
            self.failed_tasks.add(task_id)
            self.queue_metrics["failed"] += 1
            self.queue_metrics["in_progress"] -= 1
            self.queue_metrics["pending"] -= 1
            
            # Mark dependent tasks as skipped
            skipped = self._skip_dependent_tasks(task_id)
            
            logger.warning(f"✗ Failed: {task_id} - {error_msg}")
            if skipped:
                logger.info(f"  Skipped dependent tasks: {skipped}")
            
            return True
        
        except Exception as e:
            logger.error(f"✗ Failed to mark failed: {e}")
            return False
    
    def mark_task_skipped(self, task_id: str, reason: str = "Cached") -> bool:
        """
        Mark task as skipped.
        
        Args:
            task_id: Task to skip
            reason: Reason for skipping
            
        Returns:
            True if successful
        """
        try:
            if task_id not in self.tasks:
                logger.warning(f"⚠ Task not found: {task_id}")
                return False
            
            task = self.tasks[task_id]
            task.status = TaskStatus.SKIPPED
            task.completed_at = datetime.now()
            
            self.skipped_tasks.add(task_id)
            self.queue_metrics["skipped"] += 1
            self.queue_metrics["pending"] -= 1
            
            logger.debug(f"⊘ Skipped: {task_id} ({reason})")
            
            return True
        
        except Exception as e:
            logger.error(f"✗ Failed to mark skipped: {e}")
            return False
    
    def get_queue_status(self) -> Dict:
        """
        Get current queue status and metrics.
        
        Returns:
            Dict with queue status
        """
        try:
            # Calculate totals
            total = len(self.tasks)
            completed = len(self.completed_tasks)
            failed = len(self.failed_tasks)
            skipped = len(self.skipped_tasks)
            pending = total - completed - failed - skipped
            in_progress = len(self.current_batch)
            
            # Calculate percentages
            completion_pct = (completed / total * 100) if total > 0 else 0
            success_rate = (completed / (completed + failed) * 100) if (completed + failed) > 0 else 0
            
            # Estimate remaining time
            remaining_tasks = [
                self.tasks[tid] for tid in self.execution_queue
                if tid not in (self.completed_tasks | self.failed_tasks | self.skipped_tasks)
            ]
            estimated_remaining_sec = sum(t.estimated_time_sec for t in remaining_tasks)
            
            status = {
                "total_tasks": total,
                "completed": completed,
                "failed": failed,
                "skipped": skipped,
                "pending": pending,
                "in_progress": in_progress,
                "completion_percent": f"{completion_pct:.1f}%",
                "success_rate": f"{success_rate:.1f}%",
                "current_batch": self.current_batch,
                "estimated_remaining_sec": estimated_remaining_sec,
                "queue_order": self.execution_queue,
            }
            
            logger.debug(
                f"Queue status: {completed}/{total} done, "
                f"{in_progress} in progress, {pending} pending"
            )
            
            return status
        
        except Exception as e:
            logger.error(f"✗ Failed to get queue status: {e}")
            return {}
    
    def _skip_dependent_tasks(self, failed_task_id: str) -> List[str]:
        """
        Skip tasks that depend on a failed task.
        
        (Q4: SMART Cascade)
        
        Args:
            failed_task_id: Task that failed
            
        Returns:
            List of skipped task IDs
        """
        skipped = []
        
        try:
            # Find tasks that depend on failed task
            for task_id, task in self.tasks.items():
                if failed_task_id in task.dependencies:
                    if task.status == TaskStatus.PENDING:
                        self.mark_task_skipped(
                            task_id, 
                            f"Dependency failed: {failed_task_id}"
                        )
                        skipped.append(task_id)
                        
                        # Recursively skip dependent tasks
                        skipped.extend(self._skip_dependent_tasks(task_id))
            
            return skipped
        
        except Exception as e:
            logger.error(f"✗ Failed to skip dependent tasks: {e}")
            return []
    
    def get_task(self, task_id: str) -> Optional[Task]:
        """Get a task by ID."""
        return self.tasks.get(task_id)
    
    def get_all_tasks(self) -> List[Task]:
        """Get all tasks."""
        return list(self.tasks.values())
    
    def clear_queue(self):
        """Clear all tasks and reset router."""
        self.tasks.clear()
        self.execution_queue.clear()
        self.completed_tasks.clear()
        self.failed_tasks.clear()
        self.skipped_tasks.clear()
        self.current_batch.clear()
        
        self.queue_metrics = {
            "total_tasks": 0,
            "completed": 0,
            "failed": 0,
            "skipped": 0,
            "pending": 0,
            "in_progress": 0,
        }
        
        logger.info("✓ Task queue cleared")
