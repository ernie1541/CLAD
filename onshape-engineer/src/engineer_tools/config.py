from __future__ import annotations

import os
from pathlib import Path
from typing import Optional

from pydantic import BaseModel, Field


class EngineerConfig(BaseModel):
    runs_dir: Path = Field(default_factory=lambda: Path(
        os.environ.get("ENGINEER_RUNS_DIR", "~/.openclaw/engineer-runs")).expanduser())
    materials_db: Optional[Path] = None

    # Parallelization: the N / M / K thresholds
    spawn_if_parts_over: int = Field(6, description="N: parallelize if unique parts > N")
    spawn_if_subassemblies_over: int = Field(2, description="M: ...or independent subassemblies > M")
    max_workers: int = Field(4, description="K: max concurrent workers per wave")

    max_iterations: int = 3
    min_safety_factor: float = 2.0
    min_wall_thickness_mm: float = 1.0
    max_rework_per_order: int = 3
