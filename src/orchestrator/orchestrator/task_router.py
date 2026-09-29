"""
Task Router - Task assignment and queue management.

Manages:
- Test type selection from user
- Task creation and scheduling
- Queue ordering by priority + dependencies
- Parallel batch execution
- Task result collection
"""

from typing import List, Dict, Optional
from enum import Enum
from dataclasses import dataclass
import time


class TaskStatus(Enum):
    """Task execution status."""
    PENDING = "PENDING"
    IN_PROGRESS = "IN_PROGRESS"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    SKIPPED = "SKIPPED"


class TaskType(Enum):
    """Available test types."""
    UNIT_TEST = "unit_test"
    INTEGRATION_TEST = "integration_test"
    E2E_TEST = "e2e_test"
    SECURITY = "security"
    VULNERABILITY = "vulnerability"


@dataclass
class Task:
    """Represents a single test generation task."""
    task_type: TaskType
    status: TaskStatus = TaskStatus.PENDING
    priority: int = 0  # Higher = more urgent
    estimated_time: float = 0.0
    assigned_model: Optional[str] = None
    assigned_worker: Optional[int] = None
    result_path: Optional[str] = None
    error_message: Optional[str] = None
    created_at: float = 0.0
    started_at: Optional[float] = None
    completed_at: Optional[float] = None
    
    # Dependencies: which tasks must complete first
    dependencies: List[TaskType] = None
    
    def __post_init__(self):
        self.created_at = time.time()
        if self.dependencies is None:
            self.dependencies = []


class TaskRouter:
    """
    Routes and manages test generation tasks.
    
    Task dependency graph (RECOMMENDED):
    - Unit tests: no dependencies (priority 1)
    - Linting: no dependencies (priority 1)
    - Integration: depends on unit (priority 2)
    - Security: no dependencies (priority 2)
    - E2E: depends on integration (priority 3)
    - Vulnerability: no dependencies (priority 2)
    """
    
    # Task configuration
    TASK_CONFIG = {
        TaskType.UNIT_TEST: {
            "name": "Unit Tests",
            "priority": 1,
            "estimated_time": 15.0,
            "dependencies": [],
        },
        TaskType.INTEGRATION_TEST: {
            "name": "Integration Tests",
            "priority": 2,
            "estimated_time": 40.0,
            "dependencies": [TaskType.UNIT_TEST],
        },
        TaskType.E2E_TEST: {
            "name": "E2E Tests",
            "priority": 3,
            "estimated_time": 60.0,
            "dependencies": [TaskType.INTEGRATION_TEST],
        },
        TaskType.SECURITY: {
            "name": "Security Tests",
            "priority": 2,
            "estimated_time": 25.0,
            "dependencies": [],
        },
        TaskType.VULNERABILITY: {
            "name": "Vulnerability Assessment",
            "priority": 2,
            "estimated_time": 30.0,
            "dependencies": [],
        },
    }
    
    def __init__(self, repo_path: str, repo_hash: str):
        """Initialize task router."""
        self.repo_path = repo_path
        self.repo_hash = repo_hash
        self.tasks: Dict[TaskType, Task] = {}
        self.queue: List[Task] = []
        self.completed_tasks: List[Task] = []
        self.failed_tasks: List[Task] = []
    
    def create_tasks(self, selected_test_types: List[TaskType]) -> List[Task]:
        """
        Create tasks from user selection.
        
        Args:
            selected_test_types: List of TaskType user selected
            
        Returns:
            List of created Task objects
        """
        tasks = []
        for test_type in selected_test_types:
            if test_type in self.TASK_CONFIG:
                config = self.TASK_CONFIG[test_type]
                task = Task(
                    task_type=test_type,
                    priority=config["priority"],
                    estimated_time=config["estimated_time"],
                    dependencies=config["dependencies"]
                )
                self.tasks[test_type] = task
                tasks.append(task)
        
        return tasks
    
    def build_execution_queue(self) -> List[Task]:
        """
        Build execution queue respecting dependencies and priority.
        
        Strategy:
        - Tasks with no dependencies first
        - Higher priority first
        - Group parallelizable tasks in batches
        
        Returns:
            Ordered list of tasks to execute
        """
        # Topological sort considering dependencies
        # Sort by priority within dependency levels
        # Group parallelizable batches
        pass
    
    def get_next_batch(self) -> List[Task]:
        """
        Get next batch of tasks that can run in parallel.
        
        Returns:
            List of tasks (max 2-3 for worker pool)
        """
        # Find tasks with met dependencies
        # Return tasks that can run simultaneously
        pass
    
    def mark_task_complete(self, task_type: TaskType, result_path: str):
        """Mark task as completed."""
        if task_type in self.tasks:
            task = self.tasks[task_type]
            task.status = TaskStatus.COMPLETED
            task.result_path = result_path
            task.completed_at = time.time()
            self.completed_tasks.append(task)
    
    def mark_task_failed(self, task_type: TaskType, error: str):
        """Mark task as failed."""
        if task_type in self.tasks:
            task = self.tasks[task_type]
            task.status = TaskStatus.FAILED
            task.error_message = error
            task.completed_at = time.time()
            self.failed_tasks.append(task)
    
    def mark_task_skipped(self, task_type: TaskType, reason: str):
        """Mark task as skipped (usually due to cache)."""
        if task_type in self.tasks:
            task = self.tasks[task_type]
            task.status = TaskStatus.SKIPPED
            task.error_message = reason
            task.completed_at = time.time()
    
    def get_queue_status(self) -> Dict:
        """Get current queue status."""
        pending = sum(1 for t in self.tasks.values() 
                     if t.status == TaskStatus.PENDING)
        in_progress = sum(1 for t in self.tasks.values() 
                         if t.status == TaskStatus.IN_PROGRESS)
        completed = sum(1 for t in self.tasks.values() 
                       if t.status == TaskStatus.COMPLETED)
        failed = sum(1 for t in self.tasks.values() 
                    if t.status == TaskStatus.FAILED)
        
        return {
            "total": len(self.tasks),
            "pending": pending,
            "in_progress": in_progress,
            "completed": completed,
            "failed": failed,
            "estimated_time_remaining": sum(
                t.estimated_time for t in self.tasks.values()
                if t.status == TaskStatus.PENDING
            ),
        }
