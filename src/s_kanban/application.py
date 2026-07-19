"""S-Kanban manifest and host wiring."""

from sovereign import (
    ApplicationFacade, ApplicationInstance, ApplicationManifest,
    ApplicationServices,
)

from .controller import build_routes
from .facade import KANBAN_FACADE_API_VERSION, KanbanFacade
from .logic import KanbanLogic


APPLICATION_MANIFEST = ApplicationManifest(
    application_id="kanban",
    display_name="S-Kanban",
    data_schema_version=1,
    asset_package="s_kanban.assets",
    ui_file="kanban.html",
    css_file="kanban.css",
)


def create_application(services: ApplicationServices) -> ApplicationInstance:
    logic = KanbanLogic(
        services.session,
        dict(services.settings),
        services.channel_manager,
    )
    return ApplicationInstance(
        manifest=APPLICATION_MANIFEST,
        logic=logic,
        registration=logic.application_registration(),
        controllers=tuple(build_routes(logic, services, dict(services.settings))),
        facade=ApplicationFacade(
            application_id=APPLICATION_MANIFEST.application_id,
            facade_api_version=KANBAN_FACADE_API_VERSION,
            api=KanbanFacade(logic),
        ),
    )
