#!/usr/bin/env nextflow

/*
 * The nuclear-reactor world map, expressed as a Nextflow pipeline.
 *
 * The Nextflow counterpart of save_earth's BuildNuclearReactorMap
 * (fwh_save_earth/src/save_earth/ffl/save_earth.ffl) and of the Snakefile
 * beside this file, so all three engines can be timed and their outputs diffed.
 *
 * As with the Snakemake version, both processes call the SAME handler functions
 * Facetwork's runner dispatches to (via scripts/, shared by all three engines).
 * Reimplementing the Overpass query here would compare my Groovy to their
 * Python and tell us nothing about the orchestrators.
 *
 *   nextflow run main.nf --outdir runs/nextflow
 *   nextflow run main.nf --outdir runs/nextflow --use_mock true
 *   nextflow run main.nf --outdir runs/nextflow -resume
 *
 * See README.md for the comparison procedure.
 */

nextflow.enable.dsl = 2

params.outdir = 'runs/nextflow'
params.python = "${System.getProperty('user.home')}/facetwork/.venv/bin/python3"
params.use_mock = false
params.force = false

// Mirrors BuildNuclearReactorMap's FFL defaults. Repeated rather than imported,
// because the comparison is only meaningful if every engine is handed the same
// inputs explicitly.
params.region = 'nuclear'
params.center_lat = 25.0
params.center_lon = 10.0
params.zoom = 2.0
params.only_layers = 'nuclear-reactors'

// Byte-for-byte the FFL workflow's attribution strings. They name the FFL
// workflow because the goal is a DIFFABLE reproduction of its output; a
// "produced by Nextflow" marker would guarantee a diff on every run and defeat
// the comparison. These maps are local artifacts, not published ones.
params.attribution_workflow = 'save_earth.workflows.BuildNuclearReactorMap'
params.attribution_ffl_url = 'https://github.com/rlemke/fwh_save_earth/blob/main/src/save_earth/ffl/save_earth.ffl'
params.description = 'Nuclear power reactors and plants worldwide, from OpenStreetMap. Click any site for its full details (operator, electrical output, start date, reactor type, status, ...).'

// Processes execute in their own work directories, so a relative --outdir would
// scatter output across task dirs instead of landing in one storage root.
// These are functions rather than top-level assignments because DSL2 rejects
// bare statements outside a process/workflow/function.
def outdirAbs() { file(params.outdir).toAbsolutePath().toString() }

// `--use_mock false` on the command line arrives as the STRING "false", and a
// non-empty string is truthy in Groovy — so a plain `params.use_mock ? ...`
// silently ran EVERY run against mock data while reporting success. Coerce
// explicitly. (The symptom was a "live" run producing a 1.7 KB, 5-feature
// dataset; only the output comparison against the other engines caught it.)
def asBool(v) { v?.toString()?.toLowerCase() in ['true', '1', 'yes'] }


process DOWNLOAD_REACTORS {
    // save_earth.sources.DownloadNuclearReactors — worldwide OSM nuclear features.
    //
    // The handler owns the on-disk layout (that contract is identical for all
    // three engines), so it writes directly into the storage root rather than
    // into the task work dir. What is emitted is therefore a path HANDLE, not a
    // staged file — see the README's note on what that costs us.
    output:
    val "${params.outdir}/cache/save-earth/nuclear/reactors.geojson"

    script:
    def mock = asBool(params.use_mock) ? '--use-mock' : ''
    def force = asBool(params.force) ? '--force' : ''
    """
    ${params.python} ${projectDir}/scripts/download_reactors.py \
        --outdir '${outdirAbs()}' ${mock} ${force}
    """
}


process BUILD_MAP {
    // save_earth.maps.BuildMap — render the MapLibre HTML for the nuclear layer.
    //
    // Taking the GeoJSON handle as input is what orders this after the
    // download. In FFL the same ordering is expressed by referencing the
    // download's feature_count, because the real hand-off happens through the
    // cache rather than through a value — each engine states the same fact in
    // its own terms.
    input:
    val geojson

    output:
    val "${params.outdir}/cache/save-earth/maps/${params.region}/index.html"

    script:
    """
    ${params.python} ${projectDir}/scripts/build_map.py \
        --outdir '${outdirAbs()}' \
        --region '${params.region}' \
        --center-lat ${params.center_lat} \
        --center-lon ${params.center_lon} \
        --zoom ${params.zoom} \
        --only-layers '${params.only_layers}' \
        --attribution-workflow '${params.attribution_workflow}' \
        --attribution-ffl-url '${params.attribution_ffl_url}' \
        --description '${params.description}'
    """
}


workflow {
    BUILD_MAP(DOWNLOAD_REACTORS())
}
