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

    document.addEventListener("DOMContentLoaded", () => {
        initialisePeriodControls(document);
        initialiseLogoutForm();
        startSessionRefresh();
    });

    document.body.addEventListener("htmx:afterSwap", (event) => {
        initialisePeriodControls(event.target);
        initialiseLogoutForm();
    });
})();
