"""S-Initiative manifest and host wiring."""

from sovereign import (
    ApplicationFacade, ApplicationInstance, ApplicationManifest,
    ApplicationServices,
)

from .controller import build_routes
from .facade import INITIATIVE_FACADE_API_VERSION, InitiativeFacade
from .logic import InitiativeLogic


APPLICATION_MANIFEST = ApplicationManifest(
    application_id="initiative",
    display_name="S-Initiative",
    data_schema_version=1,
    asset_package="s_initiative.assets",
    icon=(
        '<rect x="4" y="4" width="4" height="16" rx="1"></rect>'
        '<rect x="10" y="4" width="4" height="11" rx="1"></rect>'
        '<rect x="16" y="4" width="4" height="7" rx="1"></rect>'
    ),
    ui_file="initiative.html",
    css_file="initiative.css",
)


def create_application(services: ApplicationServices) -> ApplicationInstance:
    logic = InitiativeLogic(
        services.session,
        dict(services.settings),
        services.collaboration,
    )
    # The standalone Kanban product opens with one usable board. Bootstrap it
    # during application activation; GET /api/initiative/board remains read-only.
    logic.ensure_board()
    return ApplicationInstance(
        manifest=APPLICATION_MANIFEST,
        logic=logic,
        registration=logic.application_registration(),
        controllers=tuple(build_routes(logic, services)),
        facade=ApplicationFacade(
            application_id=APPLICATION_MANIFEST.application_id,
            facade_api_version=INITIATIVE_FACADE_API_VERSION,
            api=InitiativeFacade(logic),
        ),
    )
