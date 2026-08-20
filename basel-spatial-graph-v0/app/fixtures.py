"""Small fallback fixture. Deliberately synthetic; only centered on Basel for demo purposes."""

def fixture_records():
    areas = [
        {"id":"area:a","type":"Area","name":"Fixture West","geometry":{"type":"Polygon","coordinates":[[[7.57,47.55],[7.59,47.55],[7.59,47.565],[7.57,47.565],[7.57,47.55]]]},"properties":{"fixture":True}},
        {"id":"area:b","type":"Area","name":"Fixture East","geometry":{"type":"Polygon","coordinates":[[[7.59,47.55],[7.61,47.55],[7.61,47.565],[7.59,47.565],[7.59,47.55]]]},"properties":{"fixture":True}},
    ]
    schools = [
        {"id":"school:1","type":"School","name":"Fixture School One","geometry":{"type":"Point","coordinates":[7.582,47.557]},"properties":{"fixture":True}},
        {"id":"school:2","type":"School","name":"Fixture School Two","geometry":{"type":"Point","coordinates":[7.600,47.558]},"properties":{"fixture":True}},
    ]
    accidents = [
        {"id":"accident:1","type":"Accident","name":"Fixture Accident 1","geometry":{"type":"Point","coordinates":[7.583,47.558]},"properties":{"fixture":True,"severity":"demo"}},
        {"id":"accident:2","type":"Accident","name":"Fixture Accident 2","geometry":{"type":"Point","coordinates":[7.584,47.556]},"properties":{"fixture":True,"severity":"demo"}},
        {"id":"accident:3","type":"Accident","name":"Fixture Accident 3","geometry":{"type":"Point","coordinates":[7.601,47.559]},"properties":{"fixture":True,"severity":"demo"}},
    ]
    return {"areas":areas,"schools":schools,"accidents":accidents,"mode":"fixture"}
