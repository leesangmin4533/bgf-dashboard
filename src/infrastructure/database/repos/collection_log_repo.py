"""
CollectionLogRepository — 수집 로그 저장소

신규 파일: SalesRepository의 log_collection/get_collection_logs 이관 예정
"""

import sqlite3
from datetime import datetime
from typing import List, Dict, Any, Optional
from pathlib import Path

from src.infrastructure.database.base_repository import BaseRepository
from src.utils.logger import get_logger

logger = get_logger(__name__)


class CollectionLogRepository(BaseRepository):
    """수집 로그 저장소"""

    db_type = "store"
    # Will be populated when SalesRepository's log_collection and get_collection_logs are moved here
