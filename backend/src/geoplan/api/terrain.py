from fastapi import APIRouter

from geoplan.domain.enums import ErrorCode
from geoplan.domain.terrain import GeoTiffTerrainSource
from geoplan.errors import PlanningError
from geoplan.flight.geotiff import GeoTiffReader, TerrainInspection
from geoplan.flight.terrain_errors import InvalidTerrainSourceError


def create_terrain_router() -> APIRouter:
    router = APIRouter(prefix="/v1/terrain", tags=["terrain"])
    reader = GeoTiffReader()

    @router.post("/inspect")
    def inspect(source: GeoTiffTerrainSource) -> TerrainInspection:
        try:
            return reader.inspect(source)
        except InvalidTerrainSourceError as error:
            raise PlanningError(ErrorCode.TERRAIN_DATA_UNAVAILABLE, str(error)) from error

    return router
