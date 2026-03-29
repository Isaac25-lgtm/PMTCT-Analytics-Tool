/**
 * Shared frontend helpers for Prompt 5 and Prompt 6.
 *
 * Keep the stack simple: Jinja2 + HTMX + Chart.js with a small amount of
 * vanilla JavaScript for login, period selectors, chart lifecycle, and export
 * downloads.
 */

(function () {
    window.appCharts = window.appCharts || {};

    function getCsrfToken() {
        const tokenTag = document.querySelector('meta[name="csrf-token"]');
        return tokenTag ? tokenTag.getAttribute("content") || "" : "";
    }

    function buildJsonHeaders() {
        const headers = { "Content-Type": "application/json" };
        const csrfToken = getCsrfToken();
        if (csrfToken) {
            headers["X-CSRF-Token"] = csrfToken;
        }
        return headers;
    }

    function showNotification(message, type) {
        const palette = {
            info: "bg-slate-900",
            success: "bg-emerald-600",
            warning: "bg-amber-500",
            error: "bg-rose-600"
        };

        const notice = document.createElement("div");
        notice.className = [
            "fixed",
            "right-4",
            "top-4",
            "z-[60]",
            "rounded-2xl",
            "px-4",
            "py-3",
            "text-sm",
            "font-medium",
            "text-white",
            "shadow-xl",
            palette[type] || palette.info
        ].join(" ");
        notice.textContent = message;
        document.body.appendChild(notice);

        window.setTimeout(() => {
            notice.remove();
        }, 4000);
    }
    window.showNotification = showNotification;

    function destroyChart(chartKey) {
        const existing = window.appCharts[chartKey];
        if (existing) {
            existing.destroy();
            delete window.appCharts[chartKey];
        }
    }

    window.renderCascadeChart = function renderCascadeChart(chartKey, canvasId, data, label) {
        destroyChart(chartKey);

        const canvas = document.getElementById(canvasId);
        if (!canvas || !window.Chart) {
            return;
        }

        window.appCharts[chartKey] = new window.Chart(canvas.getContext("2d"), {
            type: "bar",
            data: {
                labels: data.map((step) => step.name),
                datasets: [
                    {
                        label: label,
                        data: data.map((step) => step.percentage || 0),
                        backgroundColor: "#006b3f",
                        borderColor: "#004f2f",
                        borderWidth: 1,
                        borderRadius: 10
                    }
                ]
            },
            options: {
                responsive: true,
                maintainAspectRatio: false,
                scales: {
                    y: {
                        beginAtZero: true,
                        max: 100,
                        ticks: {
                            callback(value) {
                                return value + "%";
                            }
                        }
                    }
                },
                plugins: {
                    legend: {
                        display: false
                    },
                    tooltip: {
                        callbacks: {
                            label(context) {
                                const step = data[context.dataIndex];
                                const coverage = step.percentage != null ? step.percentage.toFixed(1) + "%" : "N/A";
                                const count = step.count != null ? step.count : "N/A";
                                return ["Coverage: " + coverage, "Count: " + count];
                            }
                        }
                    }
                }
            }
        });
    };

    window.renderTrendChart = function renderTrendChart(chartKey, canvasId, periodLabels, trendData) {
        destroyChart(chartKey);

        const canvas = document.getElementById(canvasId);
        if (!canvas || !window.Chart) {
            return;
        }

        const palette = [
            { line: "#006b3f", bg: "rgba(0, 107, 63, 0.12)" },
            { line: "#0072bc", bg: "rgba(0, 114, 188, 0.12)" },
            { line: "#d97706", bg: "rgba(217, 119, 6, 0.12)" },
            { line: "#e11d48", bg: "rgba(225, 29, 72, 0.12)" },
            { line: "#0f766e", bg: "rgba(15, 118, 110, 0.12)" },
            { line: "#475569", bg: "rgba(71, 85, 105, 0.12)" },
            { line: "#0891b2", bg: "rgba(8, 145, 178, 0.12)" },
            { line: "#ea580c", bg: "rgba(234, 88, 12, 0.12)" },
            { line: "#4f46e5", bg: "rgba(79, 70, 229, 0.12)" },
            { line: "#65a30d", bg: "rgba(101, 163, 13, 0.12)" }
        ];

        const allPercentages = trendData.length > 0 && trendData.every((trend) => trend.result_type === "percentage");
        const datasets = trendData.map((trend, index) => ({
            label: trend.indicator_id,
            data: trend.values,
            borderColor: palette[index % palette.length].line,
            backgroundColor: palette[index % palette.length].bg,
            fill: false,
            tension: 0.3,
            pointRadius: 4,
            pointHoverRadius: 6
        }));

        const targets = trendData.map((trend) => trend.target).filter((target) => target != null);
        const uniqueTargets = Array.from(new Set(targets));
        if (allPercentages && uniqueTargets.length === 1) {
            datasets.push({
                label: "Target",
                data: Array(periodLabels.length).fill(uniqueTargets[0]),
                borderColor: "#94a3b8",
                borderDash: [6, 4],
                borderWidth: 2,
                fill: false,
                pointRadius: 0,
                pointHoverRadius: 0
            });
        }

        window.appCharts[chartKey] = new window.Chart(canvas.getContext("2d"), {
            type: "line",
            data: {
                labels: periodLabels,
                datasets: datasets
            },
            options: {
                responsive: true,
                maintainAspectRatio: false,
                interaction: {
                    mode: "index",
                    intersect: false
                },
                scales: {
                    y: {
                        beginAtZero: true,
                        max: allPercentages ? 100 : undefined,
                        title: {
                            display: true,
                            text: allPercentages ? "Coverage (%)" : "Value"
                        }
                    }
                },
                plugins: {
                    legend: {
                        position: "top"
                    },
                    tooltip: {
                        callbacks: {
                            label(context) {
                                const trend = trendData[context.datasetIndex];
                                const value = context.parsed.y;
                                if (value == null) {
                                    return context.dataset.label + ": N/A";
                                }
                                if (!trend) {
                                    return context.dataset.label + ": " + value;
                                }
                                if (trend.result_type === "percentage") {
                                    return context.dataset.label + ": " + value.toFixed(1) + "%";
                                }
                                if (trend.result_type === "days") {
                                    return context.dataset.label + ": " + value.toFixed(0) + " days";
                                }
                                return context.dataset.label + ": " + value.toFixed(0);
                            }
                        }
                    }
                }
            }
        });
    };

    window.handleExportDownload = async function handleExportDownload(button, format) {
        const container = button.closest("[data-export-controls]");
        if (!container || container.dataset.exporting === "true") {
            return;
        }

        container.dataset.exporting = "true";
        const buttons = container.querySelectorAll("button");
        const status = container.querySelector("[data-export-status]");
        buttons.forEach((item) => {
            item.disabled = true;
        });
        if (status) {
            status.classList.remove("hidden");
        }

        const payload = {
            format: format,
            org_unit: container.dataset.orgUnit,
            period: container.dataset.period
        };

        if (container.dataset.orgUnitName) {
            payload.org_unit_name = container.dataset.orgUnitName;
        }
        if (container.dataset.cascadeType) {
            payload.cascade_type = container.dataset.cascadeType;
        }
        if (container.dataset.periodStart) {
            payload.period_start = container.dataset.periodStart;
        }
        if (container.dataset.periodEnd) {
            payload.period_end = container.dataset.periodEnd;
        }
        if (container.dataset.periodicity) {
            payload.periodicity = container.dataset.periodicity;
        }
        if (container.dataset.annualPopulation) {
            payload.annual_population = Number(container.dataset.annualPopulation);
        }
        if (container.dataset.expectedPregnancies) {
            payload.expected_pregnancies = Number(container.dataset.expectedPregnancies);
        }

        try {
            const response = await fetch("/api/exports/" + container.dataset.exportType, {
                method: "POST",
                headers: buildJsonHeaders(),
                body: JSON.stringify(payload)
            });

            if (response.status === 401) {
                window.location.href = "/login?session_expired=1";
                return;
            }

            if (!response.ok) {
                throw new Error("Export failed");
            }

            const disposition = response.headers.get("Content-Disposition") || "";
            const match = disposition.match(/filename="(.+)"/);
            const filename = match ? match[1] : "pmtct_export." + format;
            const blob = await response.blob();
            const url = window.URL.createObjectURL(blob);
            const link = document.createElement("a");
            link.href = url;
            link.download = filename;
            document.body.appendChild(link);
            link.click();
            link.remove();
            window.URL.revokeObjectURL(url);
        } catch (error) {
            showNotification("Export failed. Please try again.", "error");
        } finally {
            container.dataset.exporting = "false";
            buttons.forEach((item) => {
                item.disabled = false;
            });
            if (status) {
                status.classList.add("hidden");
            }
        }
    };

    async function loadPeriods(container) {
        const periodicitySelect = container.querySelector("[data-periodicity-select]");
        const historyDepthSelect = container.querySelector("[data-history-depth-select]");
        const periodSelect = container.querySelector("[data-period-select]");

        if (!periodicitySelect || !historyDepthSelect || !periodSelect) {
            return;
        }

        const previousValue = periodSelect.value;
        const params = new URLSearchParams({
            periodicity: periodicitySelect.value,
            history_depth: historyDepthSelect.value
        });

        try {
            const response = await fetch("/api/reports/periods?" + params.toString(), {
                headers: { "X-Requested-With": "XMLHttpRequest" }
            });

            if (!response.ok) {
                throw new Error("Failed to load periods");
            }

            const data = await response.json();
            periodSelect.innerHTML = "";
            data.periods.forEach((period) => {
                const option = document.createElement("option");
                option.value = period.id;
                option.textContent = period.name;
                if (period.id === previousValue) {
                    option.selected = true;
                }
                periodSelect.appendChild(option);
            });
        } catch (error) {
            showNotification("Unable to refresh period options.", "error");
        }
    }

    function initialisePeriodControls(root) {
        const containers = root.querySelectorAll("[data-period-controls]");
        containers.forEach((container) => {
            if (container.dataset.periodControlsBound === "true") {
                return;
            }

            container.dataset.periodControlsBound = "true";
            const periodicitySelect = container.querySelector("[data-periodicity-select]");
            const historyDepthSelect = container.querySelector("[data-history-depth-select]");

            if (periodicitySelect) {
                periodicitySelect.addEventListener("change", () => loadPeriods(container));
            }
            if (historyDepthSelect) {
                historyDepthSelect.addEventListener("change", () => loadPeriods(container));
            }
        });
    }

    function syncRangePeriod(container) {
        const startSelect = container.querySelector("[data-range-start-select]");
        const endSelect = container.querySelector("[data-range-end-select]");
        const hiddenPeriod = container.querySelector("[data-range-period-hidden]");

        if (!startSelect || !endSelect) {
            return;
        }

        if (startSelect.value && endSelect.value && startSelect.value > endSelect.value) {
            if (document.activeElement === startSelect) {
                endSelect.value = startSelect.value;
            } else {
                startSelect.value = endSelect.value;
            }
        }

        if (hiddenPeriod) {
            hiddenPeriod.value = endSelect.value || startSelect.value || "";
        }
    }

    function populateRangeSelect(select, periods, preferredValue) {
        if (!select) {
            return;
        }

        const fallbackValue = periods.length ? periods[0].id : "";
        select.innerHTML = "";
        periods.forEach((period) => {
            const option = document.createElement("option");
            option.value = period.id;
            option.textContent = period.name;
            select.appendChild(option);
        });
        select.value = periods.some((period) => period.id === preferredValue) ? preferredValue : fallbackValue;
    }

    function toggleRangePresets(container, periodicity) {
        const presets = container.querySelector("[data-range-presets]");
        if (!presets) {
            return;
        }
        presets.classList.toggle("hidden", periodicity === "weekly");
    }

    async function loadRangePeriods(container) {
        const periodicitySelect = container.querySelector("[data-periodicity-select]");
        const startSelect = container.querySelector("[data-range-start-select]");
        const endSelect = container.querySelector("[data-range-end-select]");
        if (!periodicitySelect || !startSelect || !endSelect) {
            return;
        }

        const periodicity = periodicitySelect.value || "monthly";
        const count = periodicity === "weekly" ? 52 : 60;
        const previousStart = startSelect.value;
        const previousEnd = endSelect.value;

        try {
            const response = await fetch("/api/reports/periods?periodicity=" + encodeURIComponent(periodicity) + "&count=" + count, {
                headers: { "X-Requested-With": "XMLHttpRequest" }
            });
            if (!response.ok) {
                throw new Error("Failed to load range periods");
            }

            const data = await response.json();
            const periods = data.periods || [];
            populateRangeSelect(startSelect, periods.slice().reverse(), previousStart);
            populateRangeSelect(endSelect, periods, previousEnd);
            toggleRangePresets(container, periodicity);
            syncRangePeriod(container);
        } catch (error) {
            showNotification("Unable to refresh date range options.", "error");
        }
    }

    function initialisePeriodRanges(root) {
        const containers = root.querySelectorAll("[data-period-range]");
        containers.forEach((container) => {
            if (container.dataset.periodRangeBound === "true") {
                return;
            }

            container.dataset.periodRangeBound = "true";
            const periodicitySelect = container.querySelector("[data-periodicity-select]");
            const startSelect = container.querySelector("[data-range-start-select]");
            const endSelect = container.querySelector("[data-range-end-select]");

            if (periodicitySelect) {
                periodicitySelect.addEventListener("change", () => loadRangePeriods(container));
                toggleRangePresets(container, periodicitySelect.value || "monthly");
            }
            if (startSelect) {
                startSelect.addEventListener("change", () => syncRangePeriod(container));
            }
            if (endSelect) {
                endSelect.addEventListener("change", () => syncRangePeriod(container));
            }

            syncRangePeriod(container);
        });
    }

    function initialiseLogoutForm() {
        const form = document.getElementById("logout-form");
        if (!form || form.dataset.bound === "true") {
            return;
        }

        form.dataset.bound = "true";
        form.addEventListener("submit", async (event) => {
            event.preventDefault();
            const formData = new FormData(form);
            const headers = {};
            const csrfToken = getCsrfToken();
            if (csrfToken) {
                headers["X-CSRF-Token"] = csrfToken;
            }

            try {
                await fetch(form.action, {
                    method: "POST",
                    body: formData,
                    headers: headers
                });
            } finally {
                window.location.href = "/login";
            }
        });
    }

    function startSessionRefresh() {
        if (document.body.dataset.authenticated !== "true") {
            return;
        }

        window.setInterval(async () => {
            try {
                const headers = {};
                const csrfToken = getCsrfToken();
                if (csrfToken) {
                    headers["X-CSRF-Token"] = csrfToken;
                }

                const response = await fetch("/auth/refresh", {
                    method: "POST",
                    headers: headers
                });
                if (response.status === 401) {
                    window.location.href = "/login?session_expired=1";
                }
            } catch (error) {
                // Ignore transient refresh failures and let normal requests surface them.
            }
        }, 30 * 60 * 1000);
    }

    document.body.addEventListener("htmx:configRequest", (event) => {
        event.detail.headers["X-Requested-With"] = "XMLHttpRequest";
        if (!["GET", "HEAD", "OPTIONS"].includes(String(event.detail.verb || "").toUpperCase())) {
            const csrfToken = getCsrfToken();
            if (csrfToken) {
                event.detail.headers["X-CSRF-Token"] = csrfToken;
            }
        }
    });

    document.body.addEventListener("htmx:responseError", (event) => {
        if (event.detail.xhr.status === 401) {
            window.location.href = "/login?session_expired=1";
            return;
        }

        if (event.detail.xhr.status >= 500) {
            showNotification("A server error occurred. Please try again.", "error");
        }
    });

    document.body.addEventListener("htmx:beforeSwap", (event) => {
        if (event.detail.xhr.status === 401) {
            event.detail.shouldSwap = false;
            window.location.href = "/login?session_expired=1";
        }
    });

    // -- Period range preset buttons --
    function initialisePeriodPresets(root) {
        root.querySelectorAll(".preset-btn").forEach((btn) => {
            if (btn.dataset.presetBound === "true") return;
            btn.dataset.presetBound = "true";
            btn.addEventListener("click", () => {
                const fromSel = document.getElementById(btn.dataset.from);
                const toSel = document.getElementById(btn.dataset.to);
                if (!fromSel || !toSel) return;

                const toVal = toSel.value;
                if (!toVal || !/^\d{6}$/.test(toVal)) return;

                const toYear = parseInt(toVal.substring(0, 4));
                const toMonth = parseInt(toVal.substring(4, 6));
                let fromYear = toYear;
                let fromMonth = toMonth;

                const preset = btn.dataset.preset;
                if (preset === "quarter") {
                    fromMonth = toMonth - 2;
                } else if (preset === "6months") {
                    fromMonth = toMonth - 5;
                } else if (preset === "fy") {
                    // Uganda FY: July to June
                    if (toMonth >= 7) {
                        fromYear = toYear;
                        fromMonth = 7;
                    } else {
                        fromYear = toYear - 1;
                        fromMonth = 7;
                    }
                }

                while (fromMonth <= 0) {
                    fromMonth += 12;
                    fromYear -= 1;
                }

                const fromId = String(fromYear) + String(fromMonth).padStart(2, "0");
                const fromOpt = fromSel.querySelector('option[value="' + fromId + '"]');
                if (fromOpt) {
                    fromSel.value = fromId;
                }

                fromSel.dispatchEvent(new Event("change", { bubbles: true }));
                toSel.dispatchEvent(new Event("change", { bubbles: true }));
            });
        });
    }

    // -- Scorecard target gap chart --
    window.renderScorecardGapChart = function renderScorecardGapChart(chartKey, canvasId, indicators) {
        destroyChart(chartKey);
        const canvas = document.getElementById(canvasId);
        if (!canvas || !window.Chart) return;

        const filtered = indicators.filter(function (ind) {
            return ind.target != null && ind.value != null;
        });
        if (!filtered.length) return;

        const labels = filtered.map(function (ind) { return ind.id; });
        const values = filtered.map(function (ind) { return ind.value; });
        const targets = filtered.map(function (ind) { return ind.target; });
        const gaps = filtered.map(function (ind) { return Math.max(0, ind.target - ind.value); });
        const colors = filtered.map(function (ind) {
            if (ind.status === "success") return "#059669";
            if (ind.status === "warning") return "#d97706";
            return "#dc2626";
        });

        window.appCharts[chartKey] = new window.Chart(canvas.getContext("2d"), {
            type: "bar",
            data: {
                labels: labels,
                datasets: [
                    {
                        label: "Achieved",
                        data: values,
                        backgroundColor: colors.map(function (c) { return c + "33"; }),
                        borderColor: colors,
                        borderWidth: 1,
                    },
                    {
                        label: "Gap to target",
                        data: gaps,
                        backgroundColor: "rgba(220, 38, 38, 0.15)",
                        borderColor: "rgba(220, 38, 38, 0.4)",
                        borderWidth: 1,
                    },
                ],
            },
            options: {
                indexAxis: "y",
                responsive: true,
                maintainAspectRatio: false,
                scales: {
                    x: {
                        stacked: true,
                        max: 100,
                        title: { display: true, text: "Percentage (%)" },
                    },
                    y: { stacked: true },
                },
                plugins: {
                    legend: { display: true, position: "top" },
                    tooltip: {
                        callbacks: {
                            label: function (ctx) {
                                return ctx.dataset.label + ": " + ctx.parsed.x.toFixed(1) + "%";
                            },
                        },
                    },
                },
            },
        });
    };

    // -- Supply stockout chart --
    window.renderSupplyChart = function renderSupplyChart(chartKey, canvasId, commodities) {
        destroyChart(chartKey);
        const canvas = document.getElementById(canvasId);
        if (!canvas || !window.Chart || !commodities || !commodities.length) return;

        const labels = commodities.map(function (c) { return c.commodity; });
        const dou = commodities.map(function (c) { return c.days_of_use || 0; });
        const stockout = commodities.map(function (c) { return c.stockout_days || 0; });
        const douColors = dou.map(function (d) {
            if (d <= 0) return "#dc2626";
            if (d < 14) return "#dc2626";
            if (d < 30) return "#d97706";
            return "#059669";
        });

        window.appCharts[chartKey] = new window.Chart(canvas.getContext("2d"), {
            type: "bar",
            data: {
                labels: labels,
                datasets: [
                    {
                        label: "Days of use",
                        data: dou,
                        backgroundColor: douColors.map(function (c) { return c + "44"; }),
                        borderColor: douColors,
                        borderWidth: 1,
                    },
                    {
                        label: "Stockout days",
                        data: stockout,
                        backgroundColor: "rgba(220, 38, 38, 0.2)",
                        borderColor: "#dc2626",
                        borderWidth: 1,
                    },
                ],
            },
            options: {
                responsive: true,
                maintainAspectRatio: false,
                plugins: {
                    legend: { display: true, position: "top" },
                },
                scales: {
                    y: { title: { display: true, text: "Days" } },
                },
            },
        });
    };

    window.toggleIndicatorFormulaModal = function toggleIndicatorFormulaModal(modalId, show) {
        const modal = document.getElementById(modalId);
        if (!modal) {
            return;
        }
        modal.classList.toggle("hidden", !show);
        document.body.classList.toggle("overflow-hidden", !!show);
    };

    // -- Lightweight markdown to HTML for AI insight content --
    function renderMarkdown(root) {
        root.querySelectorAll("[data-markdown-content]").forEach(function (el) {
            if (el.dataset.markdownRendered === "true") return;
            el.dataset.markdownRendered = "true";

            var text = el.textContent || "";
            // Escape HTML entities first
            var html = text
                .replace(/&/g, "&amp;")
                .replace(/</g, "&lt;")
                .replace(/>/g, "&gt;");

            // Headers: ### Header
            html = html.replace(/^### (.+)$/gm, "<h3>$1</h3>");
            html = html.replace(/^## (.+)$/gm, "<h3>$1</h3>");

            // Bold: **text**
            html = html.replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>");

            // Italic: *text*
            html = html.replace(/(?<!\*)\*([^*]+)\*(?!\*)/g, "<em>$1</em>");

            // Numbered lists: 1. item
            html = html.replace(/^(\d+)\.\s+(.+)$/gm, "<li>$2</li>");
            html = html.replace(/(<li>.*<\/li>\n?)+/g, function (match) {
                return "<ol>" + match + "</ol>";
            });

            // Bullet lists: - item
            html = html.replace(/^[-*]\s+(.+)$/gm, "<li>$1</li>");
            html = html.replace(/(<li>.*<\/li>\n?)+/g, function (match) {
                if (match.indexOf("<ol>") === -1) {
                    return "<ul>" + match + "</ul>";
                }
                return match;
            });

            // Paragraphs: double newlines
            html = html.replace(/\n\n+/g, "</p><p>");
            html = "<p>" + html + "</p>";

            // Clean up empty paragraphs
            html = html.replace(/<p>\s*<\/p>/g, "");
            html = html.replace(/<p>\s*(<h3>)/g, "$1");
            html = html.replace(/(<\/h3>)\s*<\/p>/g, "$1");
            html = html.replace(/<p>\s*(<[ou]l>)/g, "$1");
            html = html.replace(/(<\/[ou]l>)\s*<\/p>/g, "$1");

            el.innerHTML = html;
        });
    }

    document.addEventListener("DOMContentLoaded", () => {
        initialisePeriodControls(document);
        initialisePeriodRanges(document);
        initialisePeriodPresets(document);
        initialiseLogoutForm();
        startSessionRefresh();
        renderMarkdown(document);
    });

    document.body.addEventListener("htmx:afterSwap", (event) => {
        initialisePeriodControls(event.target);
        initialisePeriodRanges(event.target);
        initialisePeriodPresets(event.target);
        initialiseLogoutForm();
        renderMarkdown(event.target);
    });
})();
