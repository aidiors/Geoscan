from geoplan.api.app import create_app
from geoplan.composition import create_default_dependencies

app = create_app(create_default_dependencies())
