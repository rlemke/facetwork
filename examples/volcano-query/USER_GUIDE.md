# Volcano Query — User Guide

> See also: [Examples Guide](../doc/GUIDE.md) | [README](README.md)

## When to Use This Example

Use this as your starting point if you are:
- Learning how to **compose existing event facets** into new workflows
- Building workflows that **reuse handlers from other namespaces** without writing new ones
- Understanding **cross-namespace imports** with `use`

## What You'll Learn

1. How to import event facets from other namespaces with `use`
2. How to compose a multi-step pipeline from existing facets
3. How to build composed facets with `andThen` bodies
4. How string concatenation (`++`) works in AFL

## Step-by-Step Walkthrough

### 1. The Problem

You want to find volcanoes in a US state above a certain elevation. The OSM geocoder already has facets for caching region data, filtering by OSM tags, filtering by elevation, and rendering maps. You just need to compose them.

### 2. Importing Existing Facets

```afl
namespace volcano {
    use osm.types              // OSMCache schema
    use osm.ops     // Cache, Download
    use osm.Filters        // FilterByOSMTag
    use osm.Elevation      // FilterByMaxElevation
    use osm.viz  // RenderMap, FormatGeoJSON
```

The `use` statement imports all public names from a namespace, making them available without qualification.

### 3. Building a Composed Facet

The `LoadVolcanoData` facet wraps two steps into a reusable unit:

```afl
facet LoadVolcanoData(region: String = "US") => (cache: OSMCache) andThen {
    c = Cache(region = $.region)
    d = osm.ops.DownloadPBF(cache = c.cache)
    yield LoadVolcanoData(cache = d.downloadCache)
}
```

Note: This is a **regular facet** (not `event facet`), so it doesn't trigger agent execution directly — its internal steps (`Cache`, `Download`) are the event facets that pause.

### 4. The Query Workflow

```afl
workflow FindVolcanoes(state: String, min_elevation_ft: Long) => (...) andThen {
    data = LoadVolcanoData(region = $.state)          // Load OSM data
    filtered = FilterByOSMTag(...)                     // Filter to volcanoes
    elevated = FilterByMaxElevation(...)               // Filter by elevation
    fmt = FormatGeoJSON(...)                           // Text output
    map = RenderMap(...)                               // Map visualization
    yield FindVolcanoes(map = map.result, text = fmt.result)
}
```

The pipeline flows: **Load data -> Filter by tag -> Filter by elevation -> Format + Render**

### 5. Compile Check

```bash
source .venv/bin/activate
python -m afl.cli examples/volcano-query/afl/volcano.afl --check
```

This will report validation warnings about unresolved facets (because the OSM facets aren't included as libraries), but the AFL syntax is valid.

## Key Concepts

### Cross-Namespace Composition

The power of this pattern: **you write zero handler code**. The volcano namespace composes operations from 4 existing OSM namespaces. When the workflow runs, the OSM geocoder agent handles all the event facets.

### Composed Facets

A facet with an `andThen` body acts like a function — it encapsulates multiple steps into a reusable unit. Unlike an `event facet`, it doesn't pause for an agent; instead, the runtime expands its steps inline.

### String Concatenation

```afl
title = $.state ++ " Volcanoes"
```

The `++` operator concatenates strings at runtime. It's used here to build dynamic titles from workflow inputs.

## Adapting for Your Use Case

### Compose a different query

Replace the filter criteria to find different features:

```afl
// Find hospitals instead of volcanoes
filtered = FilterByOSMTag(input_path = data.cache.path,
    tag_key = "amenity", tag_value = "hospital")
```

### Add more steps to the pipeline

```afl
workflow FindAndCountVolcanoes(...) => (...) andThen {
    data = LoadVolcanoData(region = $.state)
    filtered = FilterByOSMTag(...)
    elevated = FilterByMaxElevation(...)
    stats = CountFeatures(input_path = elevated.result.output_path)
    yield FindAndCountVolcanoes(count = stats.result.total, ...)
}
```

### Build your own composed namespace

```afl
namespace myquery {
    use osm.ops
    use osm.Filters

    facet LoadData(region: String) => (cache: OSMCache) andThen {
        c = Cache(region = $.region)
        d = Download(cache = c.cache)
        yield LoadData(cache = d.downloadCache)
    }

    workflow MyQuery(region: String, tag: String) => (...) andThen {
        data = LoadData(region = $.region)
        filtered = FilterByOSMTag(input_path = data.cache.path,
            tag_key = $.tag, tag_value = "yes")
        yield MyQuery(...)
    }
}
```

## Next Steps

- **[hello-agent](../hello-agent/USER_GUIDE.md)** — if you need to understand the basic execution model first
- **[genomics](../genomics/USER_GUIDE.md)** — for foreach parallel processing
- **[jenkins](../jenkins/USER_GUIDE.md)** — for mixin composition patterns
