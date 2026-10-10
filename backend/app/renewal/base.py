"""Executor + secret-store interfaces for certificate renewal."""
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional, Protocol

from app import models


@dataclass
class PlanSpec:
    """Result of a dry run: what *would* happen. Producing it must not change anything."""
    params: Dict[str, Any]
    steps: List[str]
    warnings: List[str] = field(default_factory=list)
    executable: bool = True
    not_executable_reason: Optional[str] = None


@dataclass
class ExecResult:
    success: bool
    action_taken: bool = True
    serial_number: Optional[str] = None
    not_after: Optional[datetime] = None
    secret_ref: Optional[str] = None  # where the key/cert were stored (never the secret itself)
    detail: Dict[str, Any] = field(default_factory=dict)
    error: Optional[str] = None


class RenewalExecutor(ABC):
    name: str

    @abstractmethod
    def plan(self, cert: models.Certificate) -> PlanSpec:
        """Dry run. May read (config checks, directory reachability) but must not issue or modify anything."""

    @abstractmethod
    def execute(self, cert: models.Certificate, params: Dict[str, Any]) -> ExecResult:
        """Perform the renewal. Only called by RenewalService for an approved, unexpired, unused plan."""


class SecretStore(Protocol):
    def put(self, name: str, data: Dict[str, str]) -> str:
        """Store a secret; return an opaque reference (e.g. a Vault path). Never returns the data."""


class SecretStoreError(Exception):
    pass
