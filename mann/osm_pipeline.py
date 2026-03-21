import osmnx as ox
import json, os

# ── TESTING: small core area ~400m radius around Squires/McBryde/Pylons
# Switch to VT_BBOX_FULL when ready for the real demo
VT_BBOX_TEST = (37.2300, 10.1275, -80.4205, -80.4220)  # ~3-5 buildings, just Squires block
VT_BBOX_FULL = (37.2310, 37.2180, -80.4130, -80.4280)  # full campus ~280 buildings

VT_BBOX = VT_BBOX_TEST  # ← swap to VT_BBOX_FULL when done testing


def fetch_buildings():
    """Pull all buildings from OpenStreetMap for VT campus."""
    tags = {'building': True}
    gdf = ox.features_from_bbox(
        bbox=VT_BBOX,
        tags=tags
    )
    # Keep only relevant columns
    keep = ['name','building','building:material','building:levels',
            'amenity','start_date','geometry']
    cols = [c for c in keep if c in gdf.columns]
    gdf = gdf[cols].copy()
    # Convert to plain GeoJSON
    gdf = gdf[gdf.geometry.type.isin(['Polygon','MultiPolygon'])]
    gdf = gdf.reset_index(drop=True)
    gdf['building_id'] = gdf.index
    # Compute centroid for distance calculations
    gdf['centroid_lat'] = gdf.geometry.centroid.y
    gdf['centroid_lon'] = gdf.geometry.centroid.x
    return gdf


def fetch_road_graph():
    """Pull the drivable road network for A* routing."""
    G = ox.graph_from_bbox(
        bbox=VT_BBOX,
        network_type='drive'
    )
    return G


if __name__ == '__main__':
    os.makedirs('data', exist_ok=True)

    print(f'Using {"TEST (small)" if VT_BBOX == VT_BBOX_TEST else "FULL campus"} bounding box')

    print('Fetching buildings...')
    buildings = fetch_buildings()
    buildings.to_file('data/vt_buildings.geojson', driver='GeoJSON')
    print(f'Saved {len(buildings)} buildings to data/vt_buildings.geojson')

    print('Fetching road network...')
    G = fetch_road_graph()
    ox.save_graphml(G, 'data/vt_roads.graphml')
    print(f'Saved road graph: {len(G.nodes)} nodes, {len(G.edges)} edges')