"""S-Kanban reference application."""

__version__ = "0.1.0a2"

from .facade import KANBAN_FACADE_API_VERSION, KanbanFacade

__all__ = ["KANBAN_FACADE_API_VERSION", "KanbanFacade", "__version__"]
