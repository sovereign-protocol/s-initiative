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
        # Two columns read as a board; three descending bars read as a
        # bar chart (U8).
        '<rect x="4" y="4" width="6" height="14" rx="2"></rect>'
        '<rect x="14" y="4" width="6" height="8" rx="2"></rect>'
        '<path d="M5.5 7.5h3"></path><path d="M15.5 7.5h3"></path>'
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
    # The standalone S-Initiative product opens with one usable initiative.
    # Bootstrap it during application activation; GET /api/initiative/board
    # remains read-only.
    logic.ensure_initiative()
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
