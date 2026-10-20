from geoplan.domain.geo import Coordinate


class TerrainDataUnavailableError(RuntimeError):
    def __init__(self, coordinate: Coordinate, nearest_distance_m: float) -> None:
        self.coordinate = coordinate
        self.nearest_distance_m = nearest_distance_m
        super().__init__("Для точки маршрута нет достаточно близкой отметки рельефа.")


class VerticalRateExceededError(RuntimeError):
    def __init__(
        self,
        required_climb_rate_mps: float,
        required_descent_rate_mps: float,
        maximum_climb_rate_mps: float,
        maximum_descent_rate_mps: float,
    ) -> None:
        self.required_climb_rate_mps = required_climb_rate_mps
        self.required_descent_rate_mps = required_descent_rate_mps
        self.maximum_climb_rate_mps = maximum_climb_rate_mps
        self.maximum_descent_rate_mps = maximum_descent_rate_mps
        super().__init__("Вертикальный профиль превышает предел набора или снижения.")


class InvalidTerrainSourceError(RuntimeError):
    pass
