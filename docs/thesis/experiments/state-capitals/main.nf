#!/usr/bin/env nextflow

/*
 * US state capitals from cached OSM data — the Nextflow version.
 *
 * The same three stages as capitals.ffl: one cached fetch, a 50-way fan-out,
 * then a fan-in. Every process shells out to scripts/, which call the same
 * capitals_lib.py that Facetwork's handlers and the Snakemake rules call, so
 * the only difference between the three runs is what schedules the work.
 *
 *   nextflow run main.nf --outdir "$PWD/runs/nextflow"
 *   nextflow run main.nf --outdir "$PWD/runs/nextflow" -resume
 *
 * The fan-out is a CHANNEL: `Channel.fromList(states)` and a process that
 * consumes one element at a time. Nextflow then runs 50 tasks. The fan-in is
 * `.collect()`, which waits for every element — the same wait FFL spells
 * `after resolved` and Snakemake spells `expand(...)`. Three notations for the
 * one idea.
 */

nextflow.enable.dsl = 2

params.outdir = 'runs/nextflow'
params.python = "${System.getProperty('user.home')}/facetwork/.venv/bin/python3"
params.force = false

// The cache is SHARED by all three engines and warmed once (see README). Each
// engine gets its own outdir for the fan-out and fan-in, but re-downloading per
// engine would put an unthrottled network in the middle of the measurement.
params.cache_dir = "${projectDir}/cache"


// Processes run in their own work dirs, so a relative --outdir would scatter
// output across task dirs instead of landing in one root.
def outdirAbs() { file(params.outdir).toAbsolutePath().toString() }
def cacheAbs() { file(params.cache_dir).toAbsolutePath().toString() }

// A command-line `--force false` arrives as the STRING "false", which is truthy
// in Groovy. Coerce explicitly — the same trap silently made every run of the
// nuclear-map pipeline use mock data.
def asBool(v) { v?.toString()?.toLowerCase() in ['true', '1', 'yes'] }

// The SAME 50 codes the other two engines use. Read from capitals_lib.py rather
// than duplicated here, so the three fan-outs cannot drift to different widths.
def stateCodes() {
    def out = ["${params.python}", '-c',
               'import sys; sys.path.insert(0, "' + "${projectDir}" + '"); ' +
               'import capitals_lib; print("\\n".join(capitals_lib.STATE_CODES))'].execute().text
    return out.trim().split('\n') as List
}


process FETCH_STATES {
    // The ONE networked step.
    output:
    val "${params.cache_dir}/osm_states.json"

    script:
    def force = asBool(params.force) ? '--force' : ''
    """
    ${params.python} ${projectDir}/scripts/fetch_states.py \
        --cache-dir '${cacheAbs()}' ${force}
    """
}


process STATE_CAPITAL {
    // The fan-out body: one task per state, reading ONLY the cache.
    //
    // Taking the cache handle as an input is what orders this after the fetch,
    // and taking `state` from a channel is what makes it fifty tasks.
    input:
    val cache
    val state

    output:
    val state

    script:
    """
    ${params.python} ${projectDir}/scripts/state_capital.py \
        --cache-dir '${cacheAbs()}' \
        --state '${state}' \
        --out-dir '${outdirAbs()}/states'
    """
}


process COMBINE {
    // Fan-in. `.collect()` upstream is what makes this wait for all fifty.
    input:
    val states

    output:
    val "${params.outdir}/capitals.json"

    script:
    """
    ${params.python} ${projectDir}/scripts/combine.py \
        --states-dir '${outdirAbs()}/states' \
        --out '${outdirAbs()}/capitals.json'
    """
}


workflow {
    cache = FETCH_STATES()
    // first: the cache handle is a single value consumed by 50 tasks, so it
    // must not be paired off element-wise with the state channel.
    resolved = STATE_CAPITAL(cache.first(), Channel.fromList(stateCodes()))
    COMBINE(resolved.collect())
}
