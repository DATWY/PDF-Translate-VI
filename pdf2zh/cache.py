import atexit
import json
import logging
import os
import threading
from typing import Optional

from peewee import SQL, AutoField, CharField, Model, SqliteDatabase, TextField

# we don't init the database here
db = SqliteDatabase(None)
logger = logging.getLogger(__name__)
_cache_write_lock = threading.Lock()


class _TranslationCache(Model):
    id = AutoField()
    translate_engine = CharField(max_length=20)
    translate_engine_params = TextField()
    original_text = TextField()
    translation = TextField()

    class Meta:
        database = db
        constraints = [SQL("""
            UNIQUE (
                translate_engine,
                translate_engine_params,
                original_text
                )
            ON CONFLICT REPLACE
            """)]


class TranslationCache:
    @staticmethod
    def _sort_dict_recursively(obj):
        if isinstance(obj, dict):
            return {
                k: TranslationCache._sort_dict_recursively(v)
                for k in sorted(obj.keys())
                for v in [obj[k]]
            }
        elif isinstance(obj, list):
            return [TranslationCache._sort_dict_recursively(item) for item in obj]
        return obj

    def __init__(self, translate_engine: str, translate_engine_params: dict = None):
        assert (
            len(translate_engine) < 20
        ), "current cache require translate engine name less than 20 characters"
        self.translate_engine = translate_engine
        self.replace_params(translate_engine_params)
        self._mem_cache: dict[str, str] = {}

    def replace_params(self, params: dict = None):
        if params is None:
            params = {}
        self.params = params
        params = self._sort_dict_recursively(params)
        self.translate_engine_params = json.dumps(params)
        self._mem_cache = {}

    def update_params(self, params: dict = None):
        if params is None:
            params = {}
        self.params.update(params)
        self.replace_params(self.params)

    def add_params(self, k: str, v):
        self.params[k] = v
        self.replace_params(self.params)

    def get(self, original_text: str) -> Optional[str]:
        # Fast Tier 1: In-memory RAM lookup (0.0001 ms)
        if original_text in self._mem_cache:
            return self._mem_cache[original_text]

        # Tier 2: SQLite database lookup
        try:
            result = _TranslationCache.get_or_none(
                translate_engine=self.translate_engine,
                translate_engine_params=self.translate_engine_params,
                original_text=original_text,
            )
            if result:
                if len(self._mem_cache) < 100000:
                    self._mem_cache[original_text] = result.translation
                return result.translation
        except Exception:
            pass
        return None

    def set(self, original_text: str, translation: str):
        # Store in Tier 1 RAM
        if len(self._mem_cache) < 100000:
            self._mem_cache[original_text] = translation

        # Store in Tier 2 SQLite
        try:
            with _cache_write_lock:
                _TranslationCache.create(
                    translate_engine=self.translate_engine,
                    translate_engine_params=self.translate_engine_params,
                    original_text=original_text,
                    translation=translation,
                )
        except Exception as e:
            logger.debug(f"Error setting cache: {e}")


def init_db(remove_exists=False):
    cache_folder = os.path.join(os.path.expanduser("~"), ".cache", "pdf2zh")
    os.makedirs(cache_folder, exist_ok=True)
    # The current version does not support database migration, so add the version number to the file name.
    cache_db_path = os.path.join(cache_folder, "cache.v1.db")
    if remove_exists and os.path.exists(cache_db_path):
        os.remove(cache_db_path)
    db.init(
        cache_db_path,
        pragmas={
            "journal_mode": "wal",
            "busy_timeout": 5000,
            "cache_size": -64000,
            "synchronous": "normal",
        },
    )
    db.create_tables([_TranslationCache], safe=True)


def close_db():
    try:
        active_db = getattr(_TranslationCache._meta, "database", None)
        if active_db and not active_db.is_closed():
            active_db.close()
        if db and not db.is_closed():
            db.close()
    except Exception:
        pass


def init_test_db():
    import tempfile

    cache_db_path = tempfile.mktemp(suffix=".db")
    test_db = SqliteDatabase(
        cache_db_path,
        pragmas={
            "journal_mode": "wal",
            "busy_timeout": 1000,
        },
    )
    test_db.bind([_TranslationCache], bind_refs=False, bind_backrefs=False)
    test_db.connect()
    test_db.create_tables([_TranslationCache], safe=True)
    return test_db


def clean_test_db(test_db):
    test_db.drop_tables([_TranslationCache])
    test_db.close()
    db_path = test_db.database
    if os.path.exists(db_path):
        os.remove(test_db.database)
    wal_path = db_path + "-wal"
    if os.path.exists(wal_path):
        os.remove(wal_path)
    shm_path = db_path + "-shm"
    if os.path.exists(shm_path):
        os.remove(shm_path)


init_db()
atexit.register(close_db)

