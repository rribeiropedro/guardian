"""Tests for backend/services/osm.py — Overpass API fetch, parse, cache, retry."""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest


# ---------------------------------------------------------------------------
# Helpers / fixtures
# ---------------------------------------------------------------------------

def _make_way(
    osm_id: int = 1,
    name: str = "Test Building",
    levels: str = "3",
    material: str = "masonry",
    height: str | None = None,
    building_type: str = "yes",
    amenity: str | None = None,
    geom_pts: int = 4,
) -> dict:
    tags = {"building": building_type, "name": name}
    if levels:
        tags["building:levels"] = levels
    if material:
        tags["building:material"] = material
    if height:
        tags["height"] = height
    if amenity:
        tags["amenity"] = amenity
    geometry = [{"lat": 37.228 + i * 0.0001, "lon": -80.423 + i * 0.0001} for i in range(geom_pts)]
    return {"id": osm_id, "type": "way", "tags": tags, "geometry": geometry}


def _mock_client(response_json: dict, status_code: int = 200):
    """Return a patched httpx.AsyncClient context manager that returns the given response."""
    mock_response = MagicMock()
    mock_response.status_code = status_code
    mock_response.json.return_value = response_json
    mock_response.raise_for_status = MagicMock()
    if status_code >= 400:
        mock_response.raise_for_status.side_effect = httpx.HTTPStatusError(
            "error", request=MagicMock(), response=mock_response
        )

    mock_client_instance = AsyncMock()
    mock_client_instance.post = AsyncMock(return_value=mock_response)

    mock_client_cm = MagicMock()
    mock_client_cm.__aenter__ = AsyncMock(return_value=mock_client_instance)
    mock_client_cm.__aexit__ = AsyncMock(return_value=False)

    return mock_client_cm, mock_client_instance


# ---------------------------------------------------------------------------
# _parse_element unit tests (no I/O)
# ---------------------------------------------------------------------------

def test_parse_element_returns_building_data():
    from backend.services.osm import _parse_element
    el = _make_way(levels="3", material="masonry")
    result = _parse_element(el)
    assert result is not None
    assert result.levels == 3
    assert result.material == "masonry"
    assert result.height_m == 9.0
    assert len(result.footprint) == 4


def test_parse_element_estimates_height_from_levels():
    from backend.services.osm import _parse_element
    el = _make_way(levels="4", height=None)
    result = _parse_element(el)
    assert result.height_m == 12.0


def test_parse_element_uses_explicit_height():
    from backend.services.osm import _parse_element
    el = _make_way(levels="2", height="15.5")
    result = _parse_element(el)
    assert result.height_m == 15.5


def test_parse_element_defaults_on_bad_height():
    from backend.services.osm import _parse_element
    el = _make_way(levels=None, height="not_a_number")
    # no levels tag → defaults to 2; bad height → falls back to levels estimate
    result = _parse_element(el)
    assert result is not None
    assert result.height_m == 6.0  # default 2 levels * 3m


def test_parse_element_returns_none_without_geometry():
    from backend.services.osm import _parse_element
    el = _make_way()
    el["geometry"] = []
    assert _parse_element(el) is None


def test_parse_element_returns_none_with_degenerate_footprint():
    from backend.services.osm import _parse_element
    el = _make_way(geom_pts=2)
    assert _parse_element(el) is None


def test_parse_element_normalises_material_to_lowercase():
    from backend.services.osm import _parse_element
    el = _make_way(material="BRICK")
    result = _parse_element(el)
    assert result.material == "brick"


def test_parse_element_uses_fallback_name():
    from backend.services.osm import _parse_element
    el = _make_way(osm_id=99999, name=None)
    el["tags"].pop("name", None)
    result = _parse_element(el)
    assert result.name == "Building 99999"


def test_parse_element_building_type_from_amenity():
    from backend.services.osm import _parse_element
    el = _make_way(building_type=None, amenity="school")
    el["tags"].pop("building", None)
    el["tags"]["amenity"] = "school"
    result = _parse_element(el)
    assert result.building_type == "school"


def test_parse_element_extracts_start_date():
    from backend.services.osm import _parse_element
    el = _make_way()
    el["tags"]["start_date"] = "1968"
    result = _parse_element(el)
    assert result.start_date == "1968"


def test_parse_element_extracts_construction_date_fallback():
    from backend.services.osm import _parse_element
    el = _make_way()
    el["tags"]["construction_date"] = "1975"
    result = _parse_element(el)
    assert result.start_date == "1975"


def test_parse_element_start_date_empty_when_absent():
    from backend.services.osm import _parse_element
    el = _make_way()
    result = _parse_element(el)
    assert result.start_date == ""


def test_centroid_calculation():
    from backend.services.osm import _centroid
    coords = [[0.0, 0.0], [2.0, 0.0], [2.0, 2.0], [0.0, 2.0]]
    lat, lng = _centroid(coords)
    assert lat == pytest.approx(1.0)
    assert lng == pytest.approx(1.0)


def test_round_key_precision():
    from backend.services.osm import _round_key
    key = _round_key(37.22841234, -80.42341234, 500.0)
    assert key == (37.2284, -80.4234, 500.0)


def test_build_query_contains_radius_and_coords():
    from backend.services.osm import _build_query
    q = _build_query(37.22, -80.42, 300)
    assert "300" in q
    assert "37.22" in q
    assert "-80.42" in q
    assert "out body geom" in q


# ---------------------------------------------------------------------------
# fetch_buildings integration tests (mocked HTTP)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_fetch_buildings_happy_path():
    from backend.services.osm import fetch_buildings
    mock_cm, mock_client = _mock_client({"elements": [_make_way()]})
    with patch("backend.services.osm.httpx.AsyncClient", return_value=mock_cm):
        result = await fetch_buildings(37.228, -80.423, 200)
    assert len(result) == 1
    assert mock_client.post.call_count == 1


@pytest.mark.asyncio
async def test_fetch_buildings_populates_cache():
    from backend.services.osm import fetch_buildings
    mock_cm, mock_client = _mock_client({"elements": [_make_way()]})
    with patch("backend.services.osm.httpx.AsyncClient", return_value=mock_cm):
        await fetch_buildings(37.228, -80.423, 200)
        await fetch_buildings(37.228, -80.423, 200)
    assert mock_client.post.call_count == 1


@pytest.mark.asyncio
async def test_fetch_buildings_cache_key_uses_rounded_coords():
    from backend.services.osm import fetch_buildings
    # Both coords round to (37.2284, -80.4234) at 4 decimal places
    mock_cm, mock_client = _mock_client({"elements": [_make_way()]})
    with patch("backend.services.osm.httpx.AsyncClient", return_value=mock_cm):
        await fetch_buildings(37.22841, -80.42341, 200)
        await fetch_buildings(37.22843, -80.42343, 200)
    assert mock_client.post.call_count == 1


@pytest.mark.asyncio
async def test_fetch_buildings_empty_elements():
    from backend.services.osm import fetch_buildings
    mock_cm, _ = _mock_client({"elements": []})
    with patch("backend.services.osm.httpx.AsyncClient", return_value=mock_cm):
        result = await fetch_buildings(37.228, -80.423, 200)
    assert result == []


@pytest.mark.asyncio
async def test_fetch_buildings_retries_on_429():
    from backend.services.osm import fetch_buildings

    # First response: 429; second: 200
    resp_429 = MagicMock()
    resp_429.status_code = 429
    resp_429.raise_for_status = MagicMock()

    resp_200 = MagicMock()
    resp_200.status_code = 200
    resp_200.json.return_value = {"elements": [_make_way()]}
    resp_200.raise_for_status = MagicMock()

    mock_client_instance = AsyncMock()
    mock_client_instance.post = AsyncMock(side_effect=[resp_429, resp_200])
    mock_cm = MagicMock()
    mock_cm.__aenter__ = AsyncMock(return_value=mock_client_instance)
    mock_cm.__aexit__ = AsyncMock(return_value=False)

    with patch("backend.services.osm.httpx.AsyncClient", return_value=mock_cm), \
         patch("backend.services.osm.asyncio.sleep", new=AsyncMock(return_value=None)) as mock_sleep:
        result = await fetch_buildings(37.228, -80.423, 200)

    assert mock_client_instance.post.call_count == 2
    mock_sleep.assert_called_once_with(2.0)
    assert len(result) == 1


@pytest.mark.asyncio
async def test_fetch_buildings_retries_on_5xx():
    from backend.services.osm import fetch_buildings

    def _http_err():
        mock_r = MagicMock()
        mock_r.status_code = 503
        exc = httpx.HTTPStatusError("503", request=MagicMock(), response=mock_r)
        return exc

    mock_client_instance = AsyncMock()
    mock_client_instance.post = AsyncMock(
        side_effect=[_http_err(), _http_err(), MagicMock(
            status_code=200,
            json=MagicMock(return_value={"elements": [_make_way()]}),
            raise_for_status=MagicMock(),
        )]
    )
    mock_cm = MagicMock()
    mock_cm.__aenter__ = AsyncMock(return_value=mock_client_instance)
    mock_cm.__aexit__ = AsyncMock(return_value=False)

    with patch("backend.services.osm.httpx.AsyncClient", return_value=mock_cm), \
         patch("backend.services.osm.asyncio.sleep", new=AsyncMock(return_value=None)):
        result = await fetch_buildings(37.228, -80.423, 200)

    assert mock_client_instance.post.call_count == 3
    assert len(result) == 1


@pytest.mark.asyncio
async def test_fetch_buildings_raises_after_max_retries():
    from backend.services.osm import fetch_buildings

    mock_r = MagicMock(status_code=503)
    exc = httpx.HTTPStatusError("503", request=MagicMock(), response=mock_r)

    mock_client_instance = AsyncMock()
    mock_client_instance.post = AsyncMock(side_effect=exc)
    mock_cm = MagicMock()
    mock_cm.__aenter__ = AsyncMock(return_value=mock_client_instance)
    mock_cm.__aexit__ = AsyncMock(return_value=False)

    with patch("backend.services.osm.httpx.AsyncClient", return_value=mock_cm), \
         patch("backend.services.osm.asyncio.sleep", new=AsyncMock(return_value=None)):
        with pytest.raises(httpx.HTTPStatusError):
            await fetch_buildings(37.228, -80.423, 200)


@pytest.mark.asyncio
async def test_fetch_buildings_request_error_retries():
    from backend.services.osm import fetch_buildings

    req_err = httpx.RequestError("connection failed")
    good_resp = MagicMock(
        status_code=200,
        json=MagicMock(return_value={"elements": [_make_way()]}),
        raise_for_status=MagicMock(),
    )
    mock_client_instance = AsyncMock()
    mock_client_instance.post = AsyncMock(side_effect=[req_err, good_resp])
    mock_cm = MagicMock()
    mock_cm.__aenter__ = AsyncMock(return_value=mock_client_instance)
    mock_cm.__aexit__ = AsyncMock(return_value=False)

    with patch("backend.services.osm.httpx.AsyncClient", return_value=mock_cm), \
         patch("backend.services.osm.asyncio.sleep", new=AsyncMock(return_value=None)):
        result = await fetch_buildings(37.228, -80.423, 200)

    assert mock_client_instance.post.call_count == 2
    assert len(result) == 1
