"""The base cars' catalogue (modelgen.tools.car_library): naming rules without Blender, and the
catalogue on disk against the downloaded files."""

import pytest

from modelgen.tools import car_library as cl
from modelgen.tools import car_request as rq


@pytest.mark.parametrize("stem, expected", [
    ("1990s-suv", ("suv", "1990s")), ("1950s-station-wagon", ("wagon", "1950s")),
    ("1990s-pickup-truck", ("pickup", "1990s")), ("1990s-police-car", ("police", "1990s")),
    ("1960s-microbus", ("van", "1960s")), ("1990s-delivery-van", ("van", "1990s")),
    ("minivan", ("minivan", "modern")), ("sports-car", ("sports", "modern")),
    ("sports_car_2", ("sports", "modern")), ("car_1", ("sedan", "modern")),
    ("classic-car", ("classic", "classic")), ("1950s-roadster", ("convertible", "1950s")),
    ("muscle-car", ("muscle", "modern")), ("1990s-off-roader", ("offroad", "1990s")),
])
def test_a_file_name_gives_the_type_and_the_era(stem, expected):
    assert cl.classify(stem) == expected


@pytest.fixture(scope="module")
def catalogue():
    if not cl.CATALOG.is_file():
        pytest.skip("no catalogue: run python -m modelgen.tools.car_library")
    return cl.load()


def test_every_downloaded_car_is_in_the_catalogue(catalogue):
    names = {e["file"] for e in catalogue}
    missing = [p.relative_to(cl.ASSETS).as_posix() for p in cl.files()
               if p.relative_to(cl.ASSETS).as_posix() not in names]
    assert not missing


def test_every_catalogued_car_is_a_car_sized_game_asset(catalogue):
    for e in catalogue:
        assert 2.0 < e["length"] < 8.0, e["name"]
        assert 1.2 < e["width"] < 2.5, e["name"]
        assert 0.9 < e["height"] < 2.3, e["name"]
        assert 3 <= e["wheels"] <= 6, e["name"]     # a tuk-tuk has three, a pickup with twin rear wheels six
        assert 0.0 < e["wheelbase"] < e["length"], e["name"]
        assert abs(e["ground"]) < 0.01, e["name"]
        assert e["triangles"] < 40000, e["name"]
        assert e["paint"] is not None, e["name"]


@pytest.mark.parametrize("request_", ["sedan", "suv", "hatchback", "sports car", "hypercar", "supercar",
                                      "station wagon", "pickup truck", "minivan", "van", "coupe", "convertible",
                                      "muscle car", "taxi", "police car", "off roader", "compact car",
                                      "limousine", "race car", "a car"])
def test_every_type_of_request_finds_a_base_car(catalogue, request_):
    assert rq.candidates(rq.parse(request_), catalogue)
