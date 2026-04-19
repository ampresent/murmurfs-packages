"""MurmurFS — A FUSE filesystem where AI agents store intent, not content."""

__version__ = "0.1.0"

from murmurfs.intent import IntentLayer, IntentStack
from murmurfs.meta import Manifest, FileEntry, DirectoryEntry
from murmurfs.ops import init_project, write_intent, read_intent, list_intents, get_file_status, get_project_status
from murmurfs.config import Config
from murmurfs.llm import LLMClient, MockLLMClient, OpenAILLMClient, LLMError
from murmurfs.squash import squash_file
from murmurfs.sync import sync_file, sync_all
from murmurfs.merge import merge_intents
from murmurfs.branch import BranchManager, BranchError

__all__ = [
    # Data models
    "IntentLayer", "IntentStack",
    "Manifest", "FileEntry", "DirectoryEntry",
    # Core operations
    "init_project", "write_intent", "read_intent", "list_intents",
    "get_file_status", "get_project_status",
    # LLM
    "LLMClient", "MockLLMClient", "OpenAILLMClient", "LLMError",
    # High-level operations
    "squash_file", "sync_file", "sync_all", "merge_intents",
    # Branching
    "BranchManager", "BranchError",
    # Config
    "Config",
]
