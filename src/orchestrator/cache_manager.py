"""
Cache Manager - Persistent cache for test results.

Handles:
- SQLite-based result caching
- File hash-based invalidation (smart detection)
- Cache hit/miss tracking
- Status management (DONE, FAILED, INVALID, IN_PROGRESS)
- Cache cleanup (7-day retention)
"""

import sqlite3
import hashlib
import json
import os
import subprocess
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional, Dict, List, Tuple
import logging

logger = logging.getLogger(__name__)


class CacheManager:
    """
    Hybrid cache system: Persistent storage + Session memory.
    
    CACHE LEVELS:
    1. PERSISTENT (SQLite): Ground truth, survives app restarts
    2. SESSION (RAM dict): Fast access during current run
    3. CONTEXT (per-task): Current execution state
    
    Cache Status Values:
    - PENDING: Not yet run
    - IN_PROGRESS: Currently executing
    - DONE: Successfully completed
    - FAILED: Execution failed (retry possible)
    - INVALID: Output invalid (marked for redo)
    - SKIPPED: Not needed (e.g., cached result valid)
    """
    
    # Cache status constants
    STATUS_PENDING = "PENDING"
    STATUS_IN_PROGRESS = "IN_PROGRESS"
    STATUS_DONE = "DONE"
    STATUS_FAILED = "FAILED"
    STATUS_INVALID = "INVALID"
    STATUS_SKIPPED = "SKIPPED"
    
    def __init__(self, cache_db_path: str = ".cache/test_results.db"):
        """Initialize cache manager."""
        self.cache_db_path = Path(cache_db_path)
        self.cache_db_path.parent.mkdir(parents=True, exist_ok=True)
        
        # Session memory (in-process cache)
        self.session_cache: Dict = {}
        self.session_metadata: Dict = {
            "hits": 0,
            "misses": 0,
            "created_at": datetime.now(),
        }
        
        # Invalidation configuration (RECOMMENDED SETTINGS)
        self.cache_strategy = "hybrid"  # Q1: D (file content + force refresh)
        self.cache_scope = "commit_hash"  # Q2: B (per repo + commit)
        self.retention_days = 7  # Q3: C (7-day retention)
        self.cascade_invalidation = "smart"  # Q4: C (only affected deps)
        self.validation_error_handling = "mark_invalid"  # Q5: C (mark INVALID)
        
        # Initialize database
        self._init_database()
    
    def _init_database(self):
        """Initialize SQLite database schema."""
        try:
            with sqlite3.connect(str(self.cache_db_path)) as conn:
                cursor = conn.cursor()
                
                # Create cache entries table
                cursor.execute("""
                    CREATE TABLE IF NOT EXISTS cache_entries (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        repo_hash TEXT NOT NULL,
                        test_type TEXT NOT NULL,
                        status TEXT NOT NULL,
                        output_path TEXT,
                        error_message TEXT,
                        input_file_hashes TEXT NOT NULL,
                        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        expires_at TIMESTAMP,
                        UNIQUE(repo_hash, test_type)
                    )
                """)
                
                # Create model performance stats table
                cursor.execute("""
                    CREATE TABLE IF NOT EXISTS model_stats (
                        model_name TEXT PRIMARY KEY,
                        last_used TIMESTAMP,
                        avg_time_ms REAL,
                        success_rate REAL,
                        total_runs INTEGER DEFAULT 0
                    )
                """)
                
                # Create cache access log
                cursor.execute("""
                    CREATE TABLE IF NOT EXISTS cache_access_log (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        repo_hash TEXT,
                        test_type TEXT,
                        cache_hit BOOLEAN,
                        accessed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                    )
                """)
                
                conn.commit()
                logger.info(f"✓ Cache database initialized: {self.cache_db_path}")
        
        except Exception as e:
            logger.error(f"✗ Failed to initialize cache database: {e}")
            raise
    
    def get_repo_hash(self, repo_path: str) -> str:
        """
        Generate hash for repository state.
        
        Combines:
        - Current git commit hash
        - Repository path
        - Configuration version
        
        Returns:
            Hash string for repository state
        """
        try:
            # Get git commit hash
            result = subprocess.run(
                ["git", "-C", repo_path, "rev-parse", "HEAD"],
                capture_output=True,
                text=True,
                timeout=5
            )
            
            if result.returncode == 0:
                commit_hash = result.stdout.strip()
            else:
                # Fallback: use repository path hash
                commit_hash = hashlib.md5(repo_path.encode()).hexdigest()
            
            # Combine with cache version for future compatibility
            cache_version = "v1"
            combined = f"{commit_hash}:{cache_version}"
            
            repo_hash = hashlib.sha256(combined.encode()).hexdigest()
            logger.debug(f"✓ Generated repo hash: {repo_hash[:16]}...")
            
            return repo_hash
        
        except Exception as e:
            logger.warning(f"⚠ Failed to generate repo hash via git, using fallback: {e}")
            return hashlib.sha256(repo_path.encode()).hexdigest()
    
    def get_file_hashes(self, file_paths: List[str]) -> Dict[str, str]:
        """
        Generate hashes for input files.
        
        Args:
            file_paths: List of file paths to hash
            
        Returns:
            Dict mapping file path to content hash
        """
        file_hashes = {}
        
        for file_path in file_paths:
            try:
                path = Path(file_path)
                if path.exists() and path.is_file():
                    with open(path, 'rb') as f:
                        content_hash = hashlib.sha256(f.read()).hexdigest()
                        file_hashes[file_path] = content_hash
            except Exception as e:
                logger.warning(f"⚠ Failed to hash file {file_path}: {e}")
        
        return file_hashes
    
    def is_cached(self, repo_hash: str, test_type: str, 
                  current_file_hashes: Dict, force_refresh: bool = False) -> bool:
        """
        Check if test result is cached and still valid.
        
        Validation checks:
        1. Check if entry exists in PERSISTENT cache
        2. Check status (DONE = valid, FAILED/INVALID = needs retry)
        3. Compare file hashes (if changed = invalidate)
        4. Check expiration (7-day retention)
        5. Honor force_refresh flag (user override)
        
        Returns:
            True if cached and valid, False otherwise
        """
        if force_refresh:
            logger.debug(f"Cache check skipped (force_refresh=True) for {test_type}")
            return False
        
        try:
            with sqlite3.connect(str(self.cache_db_path)) as conn:
                cursor = conn.cursor()
                
                cursor.execute("""
                    SELECT status, output_path, input_file_hashes, expires_at
                    FROM cache_entries
                    WHERE repo_hash = ? AND test_type = ?
                """, (repo_hash, test_type))
                
                row = cursor.fetchone()
                
                if not row:
                    # Log cache miss
                    self._log_cache_access(repo_hash, test_type, False)
                    self.session_metadata["misses"] += 1
                    logger.debug(f"Cache MISS for {test_type}: not found")
                    return False
                
                status, output_path, stored_hashes_json, expires_at = row
                
                # Check expiration
                if expires_at:
                    expires = datetime.fromisoformat(expires_at)
                    if datetime.now() > expires:
                        logger.debug(f"Cache EXPIRED for {test_type}")
                        self.mark_invalid(repo_hash, test_type, "Cache expired")
                        return False
                
                # Check status
                if status == self.STATUS_DONE:
                    # A cache row is not usable when its generated artifact was
                    # deleted or moved. Treat it as stale so the Ollama path
                    # regenerates the missing output instead of reporting a
                    # false cache hit with no runnable test file.
                    output_file = Path(output_path) if output_path else None
                    script_file = (
                        output_file.parent.parent / "test_scripts" / f"{test_type}_0_test.py"
                        if output_file
                        else None
                    )
                    if (
                        output_file is None
                        or not output_file.exists()
                        or script_file is None
                        or not script_file.exists()
                    ):
                        logger.debug(
                            f"Cache INVALID for {test_type}: generated output or script missing"
                        )
                        self.mark_invalid(
                            repo_hash,
                            test_type,
                            "Cached generated output or runnable script missing",
                        )
                        return False

                    # Verify file hashes match
                    try:
                        stored_hashes = json.loads(stored_hashes_json or "{}")
                        
                        # Compare hashes
                        if stored_hashes == current_file_hashes:
                            logger.debug(f"Cache HIT for {test_type}: valid and up-to-date")
                            self._log_cache_access(repo_hash, test_type, True)
                            self.session_metadata["hits"] += 1
                            return True
                        else:
                            logger.debug(f"Cache INVALID for {test_type}: file hashes changed")
                            self.mark_invalid(repo_hash, test_type, "Input files changed")
                            return False
                    
                    except Exception as e:
                        logger.warning(f"⚠ Failed to compare hashes: {e}")
                        return False
                
                elif status in (self.STATUS_FAILED, self.STATUS_INVALID):
                    logger.debug(f"Cache needs retry for {test_type}: status={status}")
                    return False
                
                else:
                    logger.debug(f"Cache MISS for {test_type}: status={status}")
                    return False
        
        except Exception as e:
            logger.error(f"✗ Cache lookup failed for {test_type}: {e}")
            return False
    
    def get_cached_result(self, repo_hash: str, test_type: str) -> Optional[str]:
        """
        Retrieve cached test result file path.
        
        Returns:
            Path to cached result file, or None if not found/invalid
        """
        try:
            with sqlite3.connect(str(self.cache_db_path)) as conn:
                cursor = conn.cursor()
                
                cursor.execute("""
                    SELECT output_path
                    FROM cache_entries
                    WHERE repo_hash = ? AND test_type = ? AND status = ?
                """, (repo_hash, test_type, self.STATUS_DONE))
                
                row = cursor.fetchone()
                
                if row:
                    output_path = row[0]
                    if Path(output_path).exists():
                        logger.debug(f"✓ Retrieved cached result for {test_type}")
                        return output_path
                    else:
                        logger.warning(f"⚠ Cached file missing: {output_path}")
                        self.mark_invalid(repo_hash, test_type, "Result file missing")
                        return None
        
        except Exception as e:
            logger.error(f"✗ Failed to retrieve cached result: {e}")
        
        return None
    
    def mark_in_progress(self, repo_hash: str, test_type: str):
        """
        Mark test as currently in progress.
        
        Prevents other workers from attempting same task.
        """
        try:
            with sqlite3.connect(str(self.cache_db_path)) as conn:
                cursor = conn.cursor()
                
                cursor.execute("""
                    INSERT OR REPLACE INTO cache_entries
                    (repo_hash, test_type, status, input_file_hashes, expires_at)
                    VALUES (?, ?, ?, ?, datetime('now', '+7 days'))
                """, (repo_hash, test_type, self.STATUS_IN_PROGRESS, "{}"))
                
                conn.commit()
                logger.debug(f"✓ Marked {test_type} as IN_PROGRESS")
        
        except Exception as e:
            logger.error(f"✗ Failed to mark in progress: {e}")
    
    def mark_complete(self, repo_hash: str, test_type: str, 
                      result_path: str, file_hashes: Dict):
        """
        Mark test as complete and cache result.
        
        Args:
            repo_hash: Repository hash
            test_type: Type of test
            result_path: Path to result file
            file_hashes: Dict of input file hashes
        """
        try:
            with sqlite3.connect(str(self.cache_db_path)) as conn:
                cursor = conn.cursor()
                
                file_hashes_json = json.dumps(file_hashes)
                
                cursor.execute("""
                    INSERT OR REPLACE INTO cache_entries
                    (repo_hash, test_type, status, output_path, input_file_hashes, 
                     updated_at, expires_at)
                    VALUES (?, ?, ?, ?, ?, datetime('now'), datetime('now', '+7 days'))
                """, (repo_hash, test_type, self.STATUS_DONE, result_path, file_hashes_json))
                
                conn.commit()
                
                # Add to session cache for current run
                key = f"{repo_hash}:{test_type}"
                self.session_cache[key] = {
                    "status": self.STATUS_DONE,
                    "result_path": result_path,
                    "cached_at": datetime.now()
                }
                
                logger.info(f"✓ Cached result for {test_type}")
        
        except Exception as e:
            logger.error(f"✗ Failed to mark complete: {e}")
    
    def mark_failed(self, repo_hash: str, test_type: str, error_msg: str):
        """
        Mark test as failed.
        
        (Q5: SMART) Failed results are stored but flagged for retry
        """
        try:
            with sqlite3.connect(str(self.cache_db_path)) as conn:
                cursor = conn.cursor()
                
                cursor.execute("""
                    INSERT OR REPLACE INTO cache_entries
                    (repo_hash, test_type, status, error_message, input_file_hashes, updated_at)
                    VALUES (?, ?, ?, ?, ?, datetime('now'))
                """, (repo_hash, test_type, self.STATUS_FAILED, error_msg, "{}"))
                
                conn.commit()
                logger.warning(f"⚠ Marked {test_type} as FAILED: {error_msg}")
        
        except Exception as e:
            logger.error(f"✗ Failed to mark failed: {e}")
    
    def mark_invalid(self, repo_hash: str, test_type: str, reason: str):
        """
        Mark result as invalid but keep for reference.
        
        (Q5: SMART) Marks for retry while preserving history
        """
        try:
            with sqlite3.connect(str(self.cache_db_path)) as conn:
                cursor = conn.cursor()
                
                cursor.execute("""
                    UPDATE cache_entries
                    SET status = ?, error_message = ?, updated_at = datetime('now')
                    WHERE repo_hash = ? AND test_type = ?
                """, (self.STATUS_INVALID, reason, repo_hash, test_type))
                
                if cursor.rowcount == 0:
                    # Entry doesn't exist, create it
                    cursor.execute("""
                        INSERT INTO cache_entries
                        (repo_hash, test_type, status, error_message, input_file_hashes)
                        VALUES (?, ?, ?, ?, ?)
                    """, (repo_hash, test_type, self.STATUS_INVALID, reason, "{}"))
                
                conn.commit()
                logger.warning(f"⚠ Marked {test_type} as INVALID: {reason}")
        
        except Exception as e:
            logger.error(f"✗ Failed to mark invalid: {e}")
    
    def cleanup_old_cache(self, days: Optional[int] = None) -> Tuple[int, int]:
        """
        Remove cache entries older than retention period.
        
        Args:
            days: Retention period in days (default: 7)
            
        Returns:
            Tuple of (deleted_count, remaining_count)
        """
        if days is None:
            days = self.retention_days
        
        try:
            with sqlite3.connect(str(self.cache_db_path)) as conn:
                cursor = conn.cursor()
                
                # Delete old entries
                cursor.execute("""
                    DELETE FROM cache_entries
                    WHERE created_at < datetime('now', ? || ' days')
                """, (f"-{days}",))
                
                deleted = cursor.rowcount
                
                # Count remaining
                cursor.execute("SELECT COUNT(*) FROM cache_entries")
                remaining = cursor.fetchone()[0]
                
                conn.commit()
                
                logger.info(f"✓ Cache cleanup: deleted {deleted}, {remaining} remaining")
                return deleted, remaining
        
        except Exception as e:
            logger.error(f"✗ Cleanup failed: {e}")
            return 0, 0
    
    def _log_cache_access(self, repo_hash: str, test_type: str, hit: bool):
        """Log cache access (hit or miss) for analytics."""
        try:
            with sqlite3.connect(str(self.cache_db_path)) as conn:
                cursor = conn.cursor()
                cursor.execute("""
                    INSERT INTO cache_access_log (repo_hash, test_type, cache_hit)
                    VALUES (?, ?, ?)
                """, (repo_hash, test_type, hit))
                conn.commit()
        except Exception as e:
            logger.debug(f"⚠ Failed to log cache access: {e}")
    
    def get_cache_stats(self) -> Dict:
        """
        Return comprehensive cache statistics.
        
        Returns:
            Dict with cache metrics
        """
        try:
            with sqlite3.connect(str(self.cache_db_path)) as conn:
                cursor = conn.cursor()
                
                # Count entries by status
                cursor.execute("""
                    SELECT status, COUNT(*) FROM cache_entries GROUP BY status
                """)
                status_counts = dict(cursor.fetchall())
                
                # Total entries
                cursor.execute("SELECT COUNT(*) FROM cache_entries")
                total = cursor.fetchone()[0]
                
                # Cache file size
                if self.cache_db_path.exists():
                    cache_size_mb = self.cache_db_path.stat().st_size / (1024 * 1024)
                else:
                    cache_size_mb = 0.0
                
                # Hit rate (session)
                total_accesses = self.session_metadata["hits"] + self.session_metadata["misses"]
                hit_rate = (
                    self.session_metadata["hits"] / total_accesses 
                    if total_accesses > 0 else 0.0
                )
                
                return {
                    "total_entries": total,
                    "by_status": status_counts,
                    "session_hits": self.session_metadata["hits"],
                    "session_misses": self.session_metadata["misses"],
                    "session_hit_rate": f"{hit_rate*100:.1f}%",
                    "cache_size_mb": f"{cache_size_mb:.2f}",
                    "cache_path": str(self.cache_db_path),
                    "retention_days": self.retention_days,
                }
        
        except Exception as e:
            logger.error(f"✗ Failed to get cache stats: {e}")
            return {"error": str(e)}
