"""Domain enums. StrEnum members are str, so they compare, serialize and format as their plain values."""
from enum import StrEnum


class Status(StrEnum):
    PENDING = "PENDING"
    AVAILABLE = "AVAILABLE"


class Visibility(StrEnum):
    PUBLIC = "public"
    PRIVATE = "private"
