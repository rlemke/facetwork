/**
 * Stock Groups — profit/loss over the holding period.
 *
 * One chart, two series, ONE axis: the group and its benchmark are both percent
 * change from entry, so they are directly comparable. A second y-axis here would
 * let any two lines be made to cross wherever the scales happened to fall.
 *
 * Colors come from the CSS custom properties defined in stocks/_shared.html
 * (categorical slots 1 and 2, stepped per theme), so light/dark and any future
 * palette change happen in one place.
 *
 * @license Apache-2.0
 */

let plChart = null;

function cssVar(name, fallback) {
    const value = getComputedStyle(document.documentElement).getPropertyValue(name);
    return (value || "").trim() || fallback;
}

function themeColors() {
    // Read from an element inside .stk so the app-scoped series vars resolve.
    const scope = document.querySelector(".stk") || document.documentElement;
    const cs = getComputedStyle(scope);
    const pick = (name, fallback) => (cs.getPropertyValue(name) || "").trim() || fallback;
    return {
        series1: pick("--series-1", "#3987e5"),
        series2: pick("--series-2", "#d95926"),
        text: cssVar("--text-2", "#aab2c5"),
        muted: cssVar("--muted", "#6b7488"),
        grid: cssVar("--border", "rgba(255,255,255,.08)"),
        surface: cssVar("--surface", "#11151e"),
    };
}

function fmtPct(value) {
    if (value === null || value === undefined) return "n/a";
    return `${value >= 0 ? "+" : ""}${value.toFixed(2)}%`;
}

function render(series) {
    const canvas = document.getElementById("pl-chart");
    if (!canvas) return;
    const c = themeColors();

    // A single point draws nothing useful — say so instead of showing an empty box.
    if (series.dates.length < 2) {
        const note = document.getElementById("chart-empty");
        if (note) note.style.display = "block";
        canvas.style.display = "none";
        return;
    }

    const hasBenchmark = series.benchmark_pct.some(v => v !== null && v !== undefined);

    const datasets = [{
        label: series.name || "Group",
        data: series.pl_pct,
        borderColor: c.series1,
        backgroundColor: c.series1,
        borderWidth: 2,
        pointRadius: 0,
        pointHoverRadius: 5,
        pointHoverBorderWidth: 2,
        pointHoverBorderColor: c.surface,
        tension: 0.25,
        fill: false,
    }];

    if (hasBenchmark) {
        datasets.push({
            label: `${series.benchmark} (benchmark)`,
            data: series.benchmark_pct,
            borderColor: c.series2,
            backgroundColor: c.series2,
            borderWidth: 2,
            borderDash: [5, 4],   // secondary encoding: readable without color
            pointRadius: 0,
            pointHoverRadius: 5,
            pointHoverBorderWidth: 2,
            pointHoverBorderColor: c.surface,
            tension: 0.25,
            fill: false,
            spanGaps: true,
        });
    }

    if (plChart) plChart.destroy();
    plChart = new Chart(canvas.getContext("2d"), {
        type: "line",
        data: { labels: series.dates, datasets },
        options: {
            responsive: true,
            maintainAspectRatio: false,
            // Crosshair + shared tooltip: hovering anywhere reads both series at
            // that date, which is the comparison the page exists to make.
            interaction: { mode: "index", intersect: false },
            plugins: {
                // The page renders its own legend beside the title.
                legend: { display: false },
                tooltip: {
                    backgroundColor: c.surface,
                    borderColor: c.grid,
                    borderWidth: 1,
                    titleColor: cssVar("--text", "#e7ebf3"),
                    bodyColor: c.text,
                    padding: 10,
                    displayColors: true,
                    callbacks: {
                        label: (ctx) => {
                            const value = ctx.parsed.y;
                            const base = `${ctx.dataset.label}: ${fmtPct(value)}`;
                            if (ctx.datasetIndex !== 0) return base;
                            const total = series.total_value[ctx.dataIndex];
                            if (total === null || total === undefined) return base;
                            return `${base}  ($${total.toLocaleString(undefined, {
                                minimumFractionDigits: 2, maximumFractionDigits: 2,
                            })})`;
                        },
                    },
                },
            },
            scales: {
                x: {
                    grid: { display: false },
                    ticks: { color: c.muted, maxRotation: 0, autoSkipPadding: 20 },
                    border: { color: c.grid },
                },
                y: {
                    title: { display: true, text: "% change from entry", color: c.muted },
                    grid: { color: c.grid, drawTicks: false },
                    ticks: { color: c.muted, callback: (v) => `${v > 0 ? "+" : ""}${v}%` },
                    border: { display: false },
                },
            },
        },
    });
}

export async function initStockChart(groupId) {
    try {
        const resp = await fetch(`/stocks/api/groups/${encodeURIComponent(groupId)}/series`);
        if (!resp.ok) throw new Error(`series request failed: ${resp.status}`);
        render(await resp.json());
    } catch (err) {
        // Fail visibly rather than leaving an unexplained blank box.
        console.error("[stocks] could not load the P/L series", err);
        const note = document.getElementById("chart-empty");
        if (note) {
            note.textContent = "Could not load the profit/loss series. See the browser console.";
            note.style.display = "block";
        }
    }
}
