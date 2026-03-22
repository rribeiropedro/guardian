import osmnx as ox
import networkx as nx
import json, math


# Load road graph (do this once at startup)
def load_graph(path='data/vt_roads.graphml'):
    return ox.load_graphml(path)


def add_debris_weights(G, triage_results, debris_radius_m=80):
    """
    Increase edge weights near RED/ORANGE buildings.
    A road within 80m of a RED building costs 5x more to traverse.
    """
    from shapely.geometry import Point
    red_buildings   = [b for b in triage_results if b['color'] == 'RED']
    orange_buildings= [b for b in triage_results if b['color'] == 'ORANGE']


    for u, v, k, data in G.edges(data=True, keys=True):
        # Edge midpoint
        n1 = G.nodes[u]; n2 = G.nodes[v]
        mid_lat = (n1['y'] + n2['y']) / 2
        mid_lon = (n1['x'] + n2['x']) / 2
        base_len = data.get('length', 1)
        penalty = 1.0


        for b in red_buildings:
            d = haversine_m(mid_lat, mid_lon, b['lat'], b['lon'])
            if d < debris_radius_m: penalty = max(penalty, 5.0)
        for b in orange_buildings:
            d = haversine_m(mid_lat, mid_lon, b['lat'], b['lon'])
            if d < debris_radius_m: penalty = max(penalty, 2.0)


        G[u][v][k]['weight'] = base_len * penalty
    return G


def haversine_m(lat1, lon1, lat2, lon2):
    R = 6_371_000
    dlat = math.radians(lat2-lat1)
    dlon = math.radians(lon2-lon1)
    a = (math.sin(dlat/2)**2 +
         math.cos(math.radians(lat1))*math.cos(math.radians(lat2))*math.sin(dlon/2)**2)
    return R * 2 * math.asin(math.sqrt(a))


def calculate_route(start_lat, start_lon, end_lat, end_lon,
                    triage_results, graph_path='data/vt_roads.graphml'):
    G = load_graph(graph_path)
    G = add_debris_weights(G, triage_results)


    # Snap start/end to nearest graph nodes
    orig = ox.nearest_nodes(G, X=start_lon, Y=start_lat)
    dest = ox.nearest_nodes(G, X=end_lon,   Y=end_lat)


    # A* pathfinding
    path_nodes = nx.astar_path(G, orig, dest, weight='weight')


    # Extract lat/lon for each node in path
    coords = [(G.nodes[n]['y'], G.nodes[n]['x']) for n in path_nodes]


    # Sample waypoints every ~50m
    waypoints = sample_waypoints(coords, interval_m=50)
    return waypoints


def sample_waypoints(coords, interval_m=50):
    """Downsample a path to waypoints every interval_m meters."""
    waypoints = [{'lat': coords[0][0], 'lon': coords[0][1]}]
    accum = 0.0
    for i in range(1, len(coords)):
        d = haversine_m(coords[i-1][0], coords[i-1][1],
                        coords[i][0],   coords[i][1])
        accum += d
        if accum >= interval_m:
            # Compute heading toward next point
            heading = bearing(coords[i-1], coords[i])
            waypoints.append({'lat': coords[i][0], 'lon': coords[i][1],
                               'heading': round(heading)})
            accum = 0.0
    waypoints.append({'lat': coords[-1][0], 'lon': coords[-1][1],
                       'heading': 0, 'label': 'ARRIVAL'})
    return waypoints


def bearing(p1, p2):
    """Compass bearing from p1 to p2 (lat,lon tuples)."""
    dlat = math.radians(p2[0]-p1[0])
    dlon = math.radians(p2[1]-p1[1])
    return math.degrees(math.atan2(dlon, dlat)) % 360


if __name__ == '__main__':
    import json
    triage = json.load(open('data/triage_scored.json'))
    # Staging area → Squires Student Center
    wps = calculate_route(37.2235, -80.4250,   # staging lot
                          37.2296, -80.4222,   # Squires
                          triage)
    print(f'Route: {len(wps)} waypoints')
    for wp in wps[:5]:
        print(f'  {wp}')
