import pytest
from pydantic import ValidationError

from routewise.schemas import OptimizeRequest


def body(**overrides):
    data = {
        "depot": {"lat": 6.5, "lng": 3.4},
        "stops": [
            {"id": "a", "lat": 6.51, "lng": 3.41},
            {"id": "b", "lat": 6.52, "lng": 3.42},
        ],
    }
    data.update(overrides)
    return data


def test_defaults():
    request = OptimizeRequest.model_validate(body())
    assert request.num_vehicles == 1
    assert request.distance_mode == "haversine"
    assert request.graph_id is None


def test_integer_stop_ids_are_accepted():
    request = OptimizeRequest.model_validate(
        body(stops=[{"id": 1, "lat": 6.5, "lng": 3.4}, {"id": 2, "lat": 6.6, "lng": 3.5}])
    )
    assert [s.id for s in request.stops] == [1, 2]


def test_duplicate_stop_ids_are_rejected():
    stops = [{"id": "a", "lat": 6.5, "lng": 3.4}, {"id": "a", "lat": 6.6, "lng": 3.5}]
    with pytest.raises(ValidationError, match="duplicate"):
        OptimizeRequest.model_validate(body(stops=stops))


def test_graph_mode_requires_a_graph_id_and_haversine_forbids_one():
    with pytest.raises(ValidationError, match="graph_id"):
        OptimizeRequest.model_validate(body(distance_mode="graph"))
    with pytest.raises(ValidationError, match="graph_id"):
        OptimizeRequest.model_validate(body(graph_id="abc"))
    ok = OptimizeRequest.model_validate(body(distance_mode="graph", graph_id="abc"))
    assert ok.graph_id == "abc"


def test_coordinates_must_be_valid():
    with pytest.raises(ValidationError):
        OptimizeRequest.model_validate(body(depot={"lat": 95.0, "lng": 3.4}))
    with pytest.raises(ValidationError):
        OptimizeRequest.model_validate(body(depot={"lat": 6.5, "lng": 200.0}))
    with pytest.raises(ValidationError):
        OptimizeRequest.model_validate(body(depot={"lat": float("nan"), "lng": 3.4}))


def test_structural_validation():
    with pytest.raises(ValidationError):
        OptimizeRequest.model_validate(body(stops=[]))
    with pytest.raises(ValidationError):
        OptimizeRequest.model_validate(body(num_vehicles=0))
    with pytest.raises(ValidationError):
        OptimizeRequest.model_validate(body(distance_mode="teleport"))
    with pytest.raises(ValidationError):
        OptimizeRequest.model_validate(body(num_vehicle=2))  # misspelt field
    with pytest.raises(ValidationError):
        OptimizeRequest.model_validate(body(stops=[{"id": "", "lat": 6.5, "lng": 3.4}]))  # empty id


def test_check_limits():
    request = OptimizeRequest.model_validate(body(num_vehicles=3))
    request.check_limits(max_stops=2, max_vehicles=3)
    with pytest.raises(ValueError, match="stops"):
        request.check_limits(max_stops=1, max_vehicles=3)
    with pytest.raises(ValueError, match="vehicles"):
        request.check_limits(max_stops=2, max_vehicles=2)
