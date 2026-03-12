# Volcano Query — Cross-Namespace Composition

Finds volcanoes by US state and elevation using **only existing OSM event facets**. No custom event facets or handlers — the volcano namespace composes operations from the OSM geocoder infrastructure.

## Cross-namespace composition pattern

The `volcano` namespace imports and composes event facets from four OSM namespaces:

| Import | Event facets used | Purpose |
|--------|-------------------|---------|
| `osm.ops` | `Cache`, `Download` | Load OSM data for a region |
| `osm.Filters` | `FilterByOSMTag` | Filter features by `natural=volcano` tag |
| `osm.Elevation` | `FilterByMaxElevation` | Filter by elevation threshold |
| `osm.viz` | `RenderMap`, `FormatGeoJSON` | Map rendering and text formatting |

**1 composed facet:**

| Facet | Pipeline | Description |
|-------|----------|-------------|
| `LoadVolcanoData(region)` | Cache → Download | Composed facet with `andThen` body |

**1 workflow:**

| Workflow | Pipeline | Description |
|----------|----------|-------------|
| `FindVolcanoes(state, min_elevation_ft)` | Load → FilterByOSMTag → FilterByMaxElevation → FormatGeoJSON + RenderMap | Full query pipeline |

## AFL-only example

This example has **no handlers, no agent, no test runner**. It relies entirely on the existing OSM geocoder handlers. To run it, use the OSM geocoder infrastructure with the compiled volcano workflow.

## AFL source

```afl
namespace volcano {
    use osm.types
    use osm.ops
    use osm.Filters
    use osm.Elevation
    use osm.viz

    facet LoadVolcanoData(region: String = "US") => (cache: OSMCache) andThen {
        c = Cache(region = $.region)
        d = Download(cache = c.cache)
        yield LoadVolcanoData(cache = d.downloadCache)
    }

    workflow FindVolcanoes(state: String, min_elevation_ft: Long) => (map: MapResult, text: FormatResult) andThen {
        data = LoadVolcanoData(region = $.state)
        filtered = FilterByOSMTag(input_path = data.cache.path,
            tag_key = "natural", tag_value = "volcano")
        elevated = FilterByMaxElevation(input_path = filtered.result.output_path,
            min_max_elevation_ft = $.min_elevation_ft)
        fmt = FormatGeoJSON(input_path = elevated.result.output_path,
            title = $.state ++ " Volcanoes")
        map = RenderMap(geojson_path = elevated.result.output_path,
            title = $.state ++ " Volcanoes")
        yield FindVolcanoes(map = map.result, text = fmt.result)
    }
}
```

## Compile check

```bash
afl examples/volcano-query/afl/volcano.afl --check
```
